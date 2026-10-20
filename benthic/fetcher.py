"""Downloader: Bibliothek, dann Open Access, dann optionale Zusatzwege.

Die Kaskade prüft zuerst die eigene Bibliothek, dann alle
Open-Access-Quellen (siehe ``oa``). Ist eine OA-Kopie bekannt, aber nicht
automatisch abrufbar, bleibt es beim Hinweis „im Browser öffnen“. Erst wenn
keine OA-Kopie bekannt ist, kommen angemeldete Zusatzwege an die Reihe
(``routes``), und nur, wenn sie eingeschaltet sind.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from . import crossref, library, oa, routes
from .i18n import tr

logger = logging.getLogger("benthic")


class BenthicDownloader:
    """Batch-Downloader: Bibliothek, Open Access, dann (falls an) Zusatzwege."""

    def __init__(
        self,
        output_dir: Path,
        oa_providers: Optional[List[str]] = None,
        oa_email: str = oa.DEFAULT_EMAIL,
        extra_routes: bool = False,
        skip_existing: bool = True,
        prefer_published: bool = True,
        core_api_key: str = "",
        elsevier_api_key: str = "",
        proxy_prefix: str = "",
        route_list: Optional[List[routes.Route]] = None,
    ):
        if route_list is None:
            route_list = routes.registered() if extra_routes else []
        self.routes = list(route_list)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.oa_providers = oa_providers or list(oa.PROVIDERS)
        self.oa_email = oa_email
        self.skip_existing = skip_existing
        self.prefer_published = prefer_published
        self.core_api_key = core_api_key
        self.elsevier_api_key = elsevier_api_key
        self.proxy_prefix = (proxy_prefix or "").strip()

    def download_one(self, identifier: str, on_event=None) -> Dict:
        """Lädt einen Identifier (Open Access zuerst) und liefert ein Ergebnis-Dict.

        Ergebnisform::
            {
                "identifier": str,
                "status": "ok" | "skipped" | "blocked" | "absent" | "error",
                "filename": str | None,
                "source": str | None,     # OA-Quelle oder Ziel des Zusatzwegs
                "channel": "oa" | "library" | <ID des Zusatzwegs> | None,
                "legal": bool | None,
                "title": str | None,
                "proxy_url": str | None,  # ohne freie Kopie: Link über den Proxy der Hochschule
                "error": str | None,
            }
        """
        identifier = identifier.strip()

        def emit(phase: str, **kw) -> None:
            if on_event is None:
                return
            ev = {"phase": phase, "identifier": identifier}
            ev.update(kw)
            try:
                on_event(ev)
            except Exception:
                pass

        emit("start")
        result = {
            "identifier": identifier,
            "status": "error",
            "filename": None,
            "source": None,
            "channel": None,
            "legal": None,
            "version": None,
            "title": None,
            "landing_url": f"https://doi.org/{identifier}" if _is_doi(identifier) else None,
            "proxy_url": None,
            "retryable": False,
            "error": None,
        }

        def finish_error(msg: str, retryable: bool = False) -> Dict:
            result["error"] = msg
            result["retryable"] = retryable
            emit("done", status="error", error=msg, landing_url=result.get("landing_url"))
            return result

        # 0) Bereits in der Bibliothek? (lernende Deduplikation)
        if self.skip_existing and _is_doi(identifier):
            existing = library.get(identifier)
            if existing:
                result.update(
                    {
                        "status": "skipped",
                        "source": existing.get("filename") or existing.get("title") or "Bibliothek",
                        "channel": "library",
                        "title": existing.get("title") or None,
                        "error": tr("bereits in der Bibliothek"),
                    }
                )
                emit("done", status="skipped", channel="library", source="Bibliothek")
                return result

        # 1) Open-Access-Quellen zuerst (alle Kandidaten, mit Fallback).
        oa_known = False
        if _is_doi(identifier):
            candidates, pages = oa.resolve_candidates(
                identifier,
                self.oa_providers,
                self.oa_email,
                prefer_published=self.prefer_published,
                core_api_key=self.core_api_key,
                elsevier_api_key=self.elsevier_api_key,
                on_event=on_event,
            )
            oa_known = bool(candidates)
            if pages:
                result["landing_url"] = pages[0]
            for cand in candidates:
                emit("download_start", source=cand["source"], version=cand.get("version"))
                content = oa.download_pdf(cand["url"], headers=cand.get("headers"))
                if content is None:
                    emit("download_fail", source=cand["source"])
                    continue  # toter Link / Landingpage → nächster Kandidat
                meta = oa.resolve_metadata(identifier, self.oa_email)
                filename = self._save(content, identifier, meta)
                if not filename:
                    emit("download_fail", source=cand["source"], reason="speichern")
                    continue
                result.update(
                    {
                        "status": "ok",
                        "filename": filename,
                        "source": cand["source"],
                        "channel": "oa",
                        "legal": True,
                        "version": cand.get("version") or "unknown",
                        "title": (meta or {}).get("title") or None,
                    }
                )
                library.add(
                    identifier,
                    title=(meta or {}).get("title"),
                    filename=filename,
                    origin="download",
                    channel="oa",
                )
                emit("download_ok", source=cand["source"], version=cand.get("version"), filename=filename)
                emit("done", status="ok", channel="oa", source=cand["source"],
                     version=cand.get("version"), filename=filename)
                return result

        # 2) OA bekannt, aber Download technisch blockiert → keine Zusatzwege.
        #    Eigener Status "blocked": frei verfügbar, braucht nur einen manuellen Schritt.
        if oa_known:
            best = candidates[0]
            msg = tr("Open Access bekannt, PDF aber nicht automatisch abrufbar: im Browser öffnen") \
                if result.get("landing_url") else tr("Open Access bekannt, PDF aber nicht automatisch abrufbar")
            result.update(
                status="blocked",
                source=best["source"],
                channel="oa",
                legal=True,
                version=best.get("version") or "unknown",
                error=msg,
            )
            emit("done", status="blocked", error=msg, landing_url=result.get("landing_url"))
            return result

        # 3) Keine freie Kopie: Link über den Proxy der Hochschule, wie bei check_one.
        if self.proxy_prefix and _is_doi(identifier):
            result["proxy_url"] = self._proxy_url(identifier)

        # 4) Zusatzwege (nur wenn eingeschaltet und angemeldet).
        if not self.routes:
            result.update(status="absent", error=tr("Keine Open-Access-Kopie gefunden"))
            emit("done", status="absent", error=result["error"], landing_url=result.get("landing_url"))
            return result

        misses: List[str] = []
        retryable = False
        for route in self.routes:
            label = route.label or route.id

            def route_emit(phase: str, target: str = "", reason: Optional[str] = None,
                           _route=route, _label=label) -> None:
                emit(f"route_{phase}", route=_route.id, route_label=_label, target=target or _label,
                     reason=reason)

            try:
                hit = route.fetch(identifier, route_emit)
            except routes.RouteMiss as exc:
                retryable = retryable or exc.retryable
                misses.append(f"{label}: {exc}")
                continue
            except Exception as exc:  # ein defekter Weg darf den Lauf nicht stoppen
                logger.exception("Zusatzweg %s fehlgeschlagen", route.id)
                misses.append(f"{label}: {type(exc).__name__}")
                continue

            meta = oa.resolve_metadata(identifier, self.oa_email) if _is_doi(identifier) else None
            filename = self._save(hit.content, identifier, meta)
            if not filename:
                misses.append(f"{label}: speichern fehlgeschlagen")
                continue

            result.update(
                {
                    "status": "ok",
                    "filename": filename,
                    "source": hit.source,
                    "channel": route.id,
                    "route_label": label,
                    "legal": route.legal,
                    "title": (meta or {}).get("title") or None,
                }
            )
            library.add(
                identifier,
                title=(meta or {}).get("title"),
                filename=filename,
                origin="download",
                channel=route.id,
            )
            emit("route_ok", route=route.id, route_label=label, target=hit.source, filename=filename)
            emit("done", status="ok", channel=route.id, route_label=label, source=hit.source, filename=filename)
            return result

        msg = "Keine Open-Access-Kopie; Zusatzwege ohne Erfolg: " + "; ".join(misses)
        return finish_error(msg, retryable=retryable)

    def check_one(self, identifier: str) -> Dict:
        """Prüft eine DOI ohne Download: bereits vorhanden? OA verfügbar?"""
        identifier = identifier.strip()
        result = {
            "identifier": identifier,
            "status": "absent",
            "present": False,
            "oa": False,
            "source": None,
            "channel": None,
            "version": None,
            "title": None,
            "filename": None,
            "proxy_url": None,
            "landing_url": None,
            "error": None,
        }

        existing = library.get(identifier) if _is_doi(identifier) else None
        if existing:
            result.update(
                status="present",
                present=True,
                source="Bibliothek",
                channel="library",
                title=existing.get("title"),
                filename=existing.get("filename"),
            )
            return result

        if _is_doi(identifier):
            candidates, pages = oa.resolve_candidates(
                identifier,
                self.oa_providers,
                self.oa_email,
                prefer_published=self.prefer_published,
                core_api_key=self.core_api_key,
                elsevier_api_key=self.elsevier_api_key,
            )
            if pages:
                result["landing_url"] = pages[0]
            if candidates:
                best = candidates[0]
                meta = oa.resolve_metadata(identifier, self.oa_email) or {}
                result.update(
                    status="oa",
                    oa=True,
                    source=best["source"],
                    channel="oa",
                    version=best.get("version") or "unknown",
                    title=meta.get("title") or None,
                )
                return result

        # Kein OA: ggf. Proxy-Link für die Uni-Bibliothek anbieten.
        if self.proxy_prefix:
            result["proxy_url"] = self._proxy_url(identifier)
        result["error"] = tr("kein Open-Access-Zugriff gefunden")
        return result

    def _proxy_url(self, identifier: str) -> Optional[str]:
        """Link über die Hochschule: ein Proxy-Präfix (EZproxy, OpenAthens) bekommt die DOI angehängt; eine Adresse
        mit ``{doi}`` (etwa der OpenURL-Link-Resolver der Bibliothek) bekommt sie an dieser Stelle, kodiert."""
        if not self.proxy_prefix:
            return None
        if "{doi}" in self.proxy_prefix:
            return self.proxy_prefix.replace("{doi}", quote(identifier, safe=""))
        return f"{self.proxy_prefix}{identifier}"

    def _save(self, content: bytes, identifier: str, meta: Optional[Dict]) -> Optional[str]:
        """Speichert den Inhalt unter einem Crossref-/OpenAlex-Dateinamen."""
        filename = crossref.build_filename(identifier, meta)
        filename = self._dedupe_filename(filename)
        try:
            target = (self.output_dir / filename).resolve()
            if target.parent != self.output_dir.resolve():  # nie außerhalb des Zielordners schreiben
                return None
            target.write_bytes(content)
            return filename
        except OSError:
            return None

    def _dedupe_filename(self, filename: str) -> str:
        """Vermeidet Kollisionen durch einen Hash-Suffix."""
        target = self.output_dir / filename
        if not target.exists():
            return filename
        stem = target.stem
        suffix = target.suffix
        digest = hashlib.md5(filename.encode("utf-8")).hexdigest()[:6]
        return f"{stem}_{digest}{suffix}"


def _is_doi(identifier: str) -> bool:
    """Erkennt DOI-Strings (z. B. 10.1000/xyz123)."""
    return bool(re.match(r"^10\.\d{4,9}/", identifier.strip()))
