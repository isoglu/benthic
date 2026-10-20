"""Abrufe fremder Adressen nur ins Internet: kein localhost, kein Heim- oder Firmennetz.

Links aus Literaturangaben (verify._fetch_page) und aus Grimpos Fundstücken (clips.doi_of_page) ruft Benthic
selbst auf, um Seitentitel und DOI zu lesen. Ohne Schutz könnte eine präparierte Angabe so Geräte im eigenen
Netz oder Dienste auf diesem Mac abfragen (SSRF). ``public_only()`` lässt in seinem Block nur Verbindungen zu
öffentlichen Adressen zu: Der Rechnername wird einmal aufgelöst, jede Adresse geprüft und genau die geprüfte
Adresse verbunden, auch nach jeder Weiterleitung. Läuft der Abruf über einen Proxy (Einstellung von macOS),
entscheidet der Proxy; geprüft wird dann nur die Adresse selbst.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

import urllib3.util.connection as _connection

_local = threading.local()
_original = _connection.create_connection


class Blocked(OSError):
    """Ziel liegt nicht im öffentlichen Internet."""


def is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).is_global and not (mapped or addr).is_multicast


def url_allowed(url: str) -> bool:
    """Vorprüfung ohne Netz: http(s), ein Rechnername, kein localhost und keine nicht öffentliche IP."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        parsed.port  # noqa: B018 - ValueError bei kaputtem Port
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True  # Rechnername: entscheidet die Auflösung in _guarded
    return is_public(host)


def _guarded(address, *args, **kwargs):
    state = getattr(_local, "state", None)
    if state is None:
        return _original(address, *args, **kwargs)
    host, port = address
    host = host.strip("[]")
    infos = socket.getaddrinfo(host, port, _connection.allowed_gai_family(), socket.SOCK_STREAM)
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if not ips or not all(is_public(ip) for ip in ips):
        state["blocked"] = True
        raise Blocked(f"{host} liegt nicht im öffentlichen Internet")
    error: OSError = OSError(f"keine Verbindung zu {host}")
    for ip in ips:  # genau die geprüfte Adresse verbinden; TLS prüft weiter den Rechnernamen
        try:
            return _original((ip, port), *args, **kwargs)
        except OSError as exc:
            error = exc
    raise error


def install() -> None:
    """Einmal beim Import: urllib3 verbindet über _guarded (außerhalb von public_only() wie bisher)."""
    if _connection.create_connection is not _guarded:
        _connection.create_connection = _guarded


@contextmanager
def public_only(url: str = "") -> Iterator[dict]:
    """Block, in dem requests nur öffentliche Adressen erreicht; ``state["blocked"]`` sagt, ob etwas abgewiesen wurde."""
    import requests

    state = {"blocked": False}
    if url and requests.utils.get_environ_proxies(url):
        state["proxy"] = True  # der Proxy verbindet, nicht dieser Mac
        yield state
        return
    previous = getattr(_local, "state", None)
    _local.state = state
    try:
        yield state
    finally:
        _local.state = previous


install()
