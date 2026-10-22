"""Ausweis einer geladenen PDF: Ist sie das zitierte Werk, und welche Fassung liegt vor?

Keine Prüfung darf ihre Wahrheit aus dem nehmen, was sie prüft. Bevor Benthic Zitate oder Aussagen in einer PDF sucht,
muss die PDF zeigen, dass sie das zitierte Werk ist; sonst kommt ein richtiges Zitat aus der falschen Datei als
„nicht gefunden“ zurück. Drei Ergebnisse:

* ``verified``: die DOI steht am Anfang des Textes, oder der Titel aus den Metadaten steht dort Wort für Wort in der
  richtigen Reihenfolge (in einem begrenzten Fenster, damit verstreute Wörter nicht zählen; mindestens vier Wörter).
* ``contradicted``: die erste Seite nennt die DOI eines anderen Werks, und dessen registrierter Titel steht dort auch;
  die zitierte DOI steht nirgends, der zitierte Titel fehlt, und die Datei ist keine andere Fassung (Preprint, SSRN,
  Arbeitspapier, Bericht, akzeptiertes Manuskript). Die Datei ist ein anderes Werk und wird nicht verwendet.
* ``unprovable``: alles andere. Kein Hinweis auf einen Fehler; im Zweifel immer dieses Ergebnis, nie ``contradicted``.

Dazu die Fassung, gelesen von der ersten Seite (Arbeitspapier, technischer Bericht, SSRN, akzeptiertes Manuskript,
Preprint, Vorschau hinter einer Bezahlschranke, Zusammenfassung eines Dritten, Kommentar oder Korrektur) und aus der
Textmenge: ohne Textebene, wenig Text je Seite (Folien, Poster, Scan), oder weniger als 120 Wörter je Seite des Werks
laut Metadaten (dann ist es eine Zusammenfassung oder ein Auszug, nicht das Werk).

Vorbild: isoglu.com, scripts/check-bibliography.py und intake-receipts.py.
"""

from __future__ import annotations

import html
import io
import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import unquote

VERIFIED, CONTRADICTED, UNPROVABLE = "verified", "contradicted", "unprovable"
HEAD_CHARS = 12000        # Titel und eigene DOI stehen am Anfang
MIN_TITLE = 4             # kürzere Titel kommen in fremden Texten zu leicht vor
MIN_WORDS_PER_PAGE = 120  # weniger Text je Seite des Werks: Zusammenfassung oder Auszug
SAMPLE = 3                # so viele Seiten vom Anfang und vom Ende lesen

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)
_YEAR = r"\(\s*(?:1[89]|20)\d{2}[a-z]?\s*\)"
_AUTHORS = r"(?:\bet al\.|\b[A-Z]\.,)"
_REFERENCE = re.compile(rf"10\.\d{{4,9}}/|{_YEAR}.*{_AUTHORS}|{_AUTHORS}.*{_YEAR}")  # eine Zeile wie ein Literaturverweis
_WORD = re.compile(r"\w+")
# Vorabfassungen (bioRxiv und medRxiv, SSRN, Research Square, Preprints.org, arXiv, Nature Precedings): eine solche DOI
# ist eine andere Fassung desselben Werks, kein anderes Werk.
PREPRINT_PREFIXES = ("10.1101/", "10.2139/", "10.21203/", "10.20944/", "10.48550/", "10.1038/npre")
# Daten und Material (Zenodo, figshare, Dryad, Mendeley Data, Dataverse) auf Seite 1 sind nie die DOI der Arbeit.
DATA_PREFIXES = ("10.5281/", "10.6084/", "10.5061/", "10.17632/", "10.7910/")

# Was eine Datei auf ihrer ersten Seite über ihre Fassung sagt; der erste Treffer zählt. Wo es stehen muss: „head“ ist
# der Titelblock, „margins“ Titelblock und Fußzeile, „page“ die ganze Seite. Im Fließtext genannt („posted on medRxiv“,
# ein zitiertes NBER-Arbeitspapier, „comment on the debate“) sagt es nichts über diese Datei.
_COPY = [
    (r"Executive Book Summaries?|\bSoundview\b|THE SUMMARY IN BRIEF|\bgetAbstract\b|\bBlinkist\b", "summary", "page"),
    (r"^\s*Book Summary\b", "summary", "head"),
    (r"^\s*(Erratum|Corrigendum|Retraction Note|Publisher Correction|Author Correction|Correction to:)"
     r"|\bNEWS (&|AND) VIEWS\b|(?-i:^\s*Comment(ary)? on\b)", "commentary", "head"),  # „comment on the …“ im Text nicht
    (r"reserved for Subscribers|Subscribe now for unlimited access", "teaser", "page"),
    (r"Electronic copy available at.*ssrn|\bSSRN\b.*\bWorking Paper\b", "ssrn", "margins"),
    (r"NBER WORKING PAPER SERIES|\bWorking Paper\s+(No\.?\s*)?\d{3,6}\b|Discussion Paper\s+(Series|No)", "working_paper",
     "margins"),
    (r"\bTechnical Report\b|\bTech\.\s*Rep\.", "report", "head"),
    (r"\b[A-Z]{2,5}-TR-\d{2,4}-\d+\b|\bCMU-[A-Z]{2,4}-\d{2}-\d{2,4}\b", "report", "page"),  # Berichtsnummer wie MSR-TR-99-87
    (r"This is the (author'?s? )?(peer[- ]reviewed )?accepted (version|manuscript)|\bAccepted [Mm]anuscript\b"
     r"|Article \(Accepted version\)|\bPostprint\b|\bJournal Pre-proof\b", "accepted", "margins"),
    (r"\bPre[- ]?print\b|\bThis version:.*\bdraft\b", "preprint", "margins"),
    (r"\barXiv:\d{4}\.\d{4,5}(v\d+)?\s*\[[\w.-]+\]", "preprint", "page"),  # der Randstempel, nicht ein Zitat
]
INCOMPLETE = ("summary", "teaser", "no_text", "thin", "commentary")  # ein fehlendes Zitat sagt hier nichts über die Quelle
VERSIONS = ("preprint", "ssrn", "working_paper", "report", "accepted")  # andere Fassungen desselben Werks
LABELS = {"working_paper": "Arbeitspapier", "ssrn": "SSRN-Fassung", "accepted": "Akzeptiertes Manuskript",
          "preprint": "Preprint", "teaser": "Vorschau hinter einer Bezahlschranke",
          "summary": "Zusammenfassung oder Auszug, nicht das ganze Werk", "no_text": "PDF ohne Textebene",
          "report": "Technischer Bericht", "commentary": "Kommentar oder Korrektur, nicht das Werk selbst",
          "thin": "Wenig Text je Seite: Folien, Poster oder Scan"}

_lock = threading.Lock()
_cache: Dict[Tuple, Dict] = {}


def dnorm(doi: str) -> str:
    """Eine DOI so, wie zwei Quellen sie vergleichbar schreiben: dekodiert, klein, ohne Punkt am Ende."""
    return re.sub(r"[^a-z0-9./():;_\-]", "", unquote(doi or "").lower()).rstrip(".")  # „_7“: Kapitel im Buch


def _toks(text: str, joined: bool = False, ligatures: bool = False) -> List[str]:
    """Wörter zum Titelvergleich: Entities aufgelöst, Tags ohne Leerzeichen entfernt (CO<sub>2</sub> ist CO2), Akzente
    und Groß- und Kleinschreibung wie in quotes.py. ``joined``: Bindestriche verbinden („High-\nDimensional“ ist ein
    Wort), sonst trennen sie; ``ligatures``: getrennte Ligaturen wieder fügen („signifi cant“)."""
    from . import quotes
    s = re.sub(r"<[^>]+>", "", html.unescape(text or "")).replace("\u00ad", "")
    s = re.sub(r"[\u0370-\u03ff\u00b5]", "", s)  # TNF-α steht in PDFs auch als TNF-a oder TNF-alpha
    s = re.sub(r"[-\u2010\u2011]\s*", "" if joined else " ", s)
    return [w for w, _, _ in quotes._words(s, rejoin=ligatures)]


def _forms(text: str) -> List[List[str]]:
    """Die vier Lesarten eines Textes: Bindestriche verbunden oder getrennt, Ligaturen gefügt oder nicht."""
    return [_toks(text, joined, ligatures) for joined in (True, False) for ligatures in (True, False)]


def _title_in(title: str, forms: List[List[str]], min_len: int = 1) -> bool:
    """Steht der Titel in einer der Lesarten des Textes (``_forms``), gelesen auf dieselbe Weise?"""
    return any(len(need) >= min_len and _in_order(need, hay) for need, hay in zip(_forms(title), forms))


def _in_order(need: List[str], hay: List[str], slack: float = 2.5, pad: int = 8) -> bool:
    """Alle Wörter von ``need`` in Reihenfolge in einem begrenzten Fenster von ``hay``. Ohne Grenze fände sich jeder
    Titel verstreut in einem fremden Artikel; als reiner Teilstring scheitert er an einer Seitenzahl mitten im Titel."""
    if not need:
        return False
    win = int(len(need) * slack) + pad
    for start in [i for i, w in enumerate(hay) if w == need[0]][:400]:
        j, k = start, 0
        while j < len(hay) and j - start < win and k < len(need):
            if hay[j] == need[k]:
                k += 1
            j += 1
        if k == len(need):
            return True
    return False


def _dois(text: str) -> List[str]:
    """DOIs im Text, auch als doi.org/10.1080%2F… und am Zeilenende nach einem Punkt oder Strich umbrochen."""
    text = re.sub(r"(10\.\d{4,9}/\S*[./_-])[ \t]*\n[ \t]*(?=[a-z0-9])", r"\1", unquote(text or ""))
    # Angehängtes ohne Leerzeichen gehört nicht dazu: bioRxiv druckt „10.1101/2020.02.07.937862doi:“
    return list(dict.fromkeys(dnorm(re.split(r"doi:|https?:", m.group(0), flags=re.I)[0]) for m in _DOI.finditer(text)))


def _same(want: str, found: List[str]) -> bool:
    """Dieselbe DOI, auch mit angehängtem Text oder abgeschnitten (Kapitel im Buch, Umbruch), aber nie mitten in einer
    Zahl und abgeschnitten erst ab fünf Zeichen nach dem Schrägstrich: „10.1016/j.“ passt sonst auf jede Elsevier-DOI."""
    def starts(longer: str, prefix: str) -> bool:
        return longer.startswith(prefix) and (len(longer) == len(prefix) or not longer[len(prefix)].isdigit())
    return any(starts(f, want) or (starts(want, f) and len(f.split("/", 1)[-1]) >= 5) for f in found)


def _first(texts: List[str]) -> str:
    """Die erste Seite mit Text, aber nur unter den ersten: bei einem Scan nie das Literaturverzeichnis am Ende."""
    return next((t for t in texts[:SAMPLE] if len(t.strip()) > 100), "")


def _head(first: str) -> Tuple[str, int]:
    """Der Titelblock der ersten Seite und wie viele Zeilen er hat: die ersten acht Zeilen mit Inhalt, höchstens 1.500
    Zeichen. Fußnotenzeichen der Autor:innen, die pypdf auf eigene Zeilen setzt („z“, „/?“), zählen nicht mit."""
    lines, full = [], 0
    for line in (line for line in first.splitlines() if line.strip()):
        lines.append(line)
        full += len(line.strip()) >= 4
        if full == 8:
            break
    return "\n".join(lines)[:1500], len(lines)


def _phrase(first: str) -> Optional[str]:
    """Was die erste Seite über die Fassung sagt (``_COPY``), sonst None."""
    head, used = _head(first)
    foot = [line for line in first.splitlines() if line.strip()][used:][-6:]
    places = {"page": first, "head": head, "margins": head + "\n" + "\n".join(foot)[-600:]}
    return next((kind for pattern, kind, where in _COPY if re.search(pattern, places[where], re.I | re.M)), None)


def judge(texts: List[str], doi: str, title: str) -> Dict:
    """Ausweis aus dem Rohtext der Seiten (die ersten ``SAMPLE`` vom Anfang, dann die letzten): ``status`` und bei
    ``contradicted`` die DOIs der ersten Seite."""
    head = "\n".join(texts[:SAMPLE])[:HEAD_CHARS]
    want, forms = dnorm(doi), _forms(head)
    if want and _same(want, _dois(head)):
        return {"status": VERIFIED}
    if _title_in(title, forms, MIN_TITLE):
        return {"status": VERIFIED}
    first = _first(texts)
    own = [d for d in _dois(first) if not d.startswith(DATA_PREFIXES)]
    if (want and own and title.strip() and not _same(want, _dois("\n".join(texts))) and not _title_in(title, forms)
            and _phrase(first) not in VERSIONS and not any(d.startswith(PREPRINT_PREFIXES) for d in own + [want])):
        return {"status": CONTRADICTED, "dois": own[:3]}  # vorläufig: check() verlangt den Titel dieser DOI auf Seite 1
    return {"status": UNPROVABLE}


def expected_pages(pages_field: object) -> Optional[int]:
    """Seitenzahl des Werks aus einer Angabe wie „357–362“, sonst None."""
    m = re.match(r"\s*[A-Za-z]?(\d{1,6})\s*[-–]\s*[A-Za-z]?(\d{1,6})\s*$", str(pages_field or ""))
    span = int(m.group(2)) - int(m.group(1)) + 1 if m else 0
    return span if 0 < span <= 3000 else None


def grade(texts: List[str], n_pages: int, expected: Optional[int] = None) -> Optional[str]:
    """Fassung, wenn die Datei nicht einfach der Volltext ist (siehe ``_COPY``, dazu ``no_text``), sonst None."""
    if not texts:
        return None
    per_page = sum(len(_WORD.findall(t)) for t in texts) / len(texts)
    if per_page < 40:
        return "no_text"
    kind = _phrase(_first(texts))
    if kind:
        return kind
    if expected and expected >= 4 and (n_pages * 2 < expected or per_page * n_pages / expected < MIN_WORDS_PER_PAGE):
        return "summary"
    # Mehr Seiten als das Werk sind kein Befund (Deckblatt, Manuskript mit doppeltem Zeilenabstand, Bericht), weniger
    # Text je eigener Seite schon: Folien, Poster oder ein schlecht erkannter Scan.
    return "thin" if per_page < MIN_WORDS_PER_PAGE else None


def _read(source: Union[Path, bytes]) -> Tuple[List[str], int]:
    """Rohtext der ersten und letzten Seiten und die Seitenzahl. Roh, weil die eigene DOI oft in der Kopfzeile steht,
    die ``support._pages`` als wiederkehrende Zeile entfernt."""
    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(io.BytesIO(source) if isinstance(source, bytes) else str(source))
    n = len(reader.pages)
    picks = sorted(set(range(min(n, SAMPLE))) | set(range(max(0, n - SAMPLE), n)))
    return [reader.pages[i].extract_text() or "" for i in picks], n


def _own_work(dois: List[str], first: str) -> Optional[Dict]:
    """Welches Werk die Datei ist: eine DOI der ersten Seite, deren registrierter Titel (aus den Metadaten) im Titelblock
    steht. Ohne diesen Nachweis kein Widerspruch: eine DOI auf Seite 1 kann auch eine zitierte Arbeit oder ein Datensatz
    sein, und ein Literaturverweis in einer Fußnote trägt Titel und DOI zugleich. Zeilen, die nach Verweis aussehen
    (mit DOI, oder Jahr in Klammern mit „et al.“ oder Initialen), zählen deshalb auch im Titelblock nicht."""
    from . import oa
    block = "\n".join(line for line in _head(first)[0].splitlines() if not _REFERENCE.search(line))
    forms = _forms(block)
    for d in dois:
        try:
            title = str((oa.resolve_metadata(d) or {}).get("title") or "")
        except Exception:  # ohne Netz kein Nachweis
            title = ""
        if title and _title_in(title, forms, MIN_TITLE):
            return {"file_doi": d, "file_title": title}
    return None


def check(source: Union[Path, bytes], doi: str, title: Optional[str] = None, pages: object = None) -> Dict:
    """Ausweis und Fassung einer PDF (Pfad oder Inhalt): status, copy, pdf_pages, bei Widerspruch file_doi, file_title.
    Eine unlesbare Datei ist ``unprovable``: Sie beweist nichts, weder dafür noch dagegen."""
    try:
        texts, n = _read(source)
    except Exception:
        return {"status": UNPROVABLE, "copy": None}
    out = judge(texts, doi, title or "")
    if out["status"] == CONTRADICTED:
        own = _own_work(out.pop("dois"), _first(texts))
        out = {"status": CONTRADICTED, **own} if own else {"status": UNPROVABLE}
    out.update(copy=grade(texts, n, expected_pages(pages)), pdf_pages=n)
    return out


def note(info: Dict, lang: Optional[str] = None) -> str:
    """Was der Mensch über eine Datei liest, die ein anderes Werk ist; leer bei jeder anderen."""
    from .i18n import tr
    if not info.get("file_title"):  # nur ein Widerspruch (oder ein Zitat dazu) trägt den Titel des anderen Werks
        return ""
    return tr("Das PDF ist ein anderes Werk: DOI {doi}, „{title}“.", lang, doi=info.get("file_doi"),
              title=info["file_title"])


def held(doi: str, title: Optional[str] = None, pages: object = None) -> Tuple[Optional[Path], Dict]:
    """Die geladene PDF zu einer DOI und ihr Ausweis, zwischengespeichert, solange die Datei gleich bleibt. Titel und
    Seitenangabe kommen aus den Metadaten (Bibliothek oder Prüfergebnis), nie aus der PDF selbst."""
    from . import biblio, library
    path = biblio.pdf_path(doi)
    if not path:
        return None, {}
    entry = library.get(doi) or {}
    title, pages = title or entry.get("title"), pages or entry.get("pages")
    try:
        key = (str(path), path.stat().st_mtime_ns, dnorm(doi), title, str(pages))
    except OSError:
        return path, {"status": UNPROVABLE, "copy": None}
    with _lock:
        if key in _cache:
            return path, _cache[key]
    info = check(path, doi, title, pages)
    with _lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = info
    return path, info
