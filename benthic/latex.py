"""LaTeX-Projekte: \\cite-Schlüssel gegen die .bib-Datei abgleichen und die zitierten Einträge prüfen.

Eingaben: eine .tex-Datei (mit \\input, \\include, \\subfile und den .bib-Dateien aus
\\bibliography{…} oder \\addbibresource{…}), dazu .bib-Dateien, oder ein ganzes Projekt als .zip.
Der Text wird für Sätze und wörtliche Zitate in Klartext übersetzt; LaTeX selbst wird nicht ausgeführt.

Anders als bei Word oder PDF ist ein nicht zitierter .bib-Eintrag normal (geteilte .bib-Dateien):
er erscheint nicht im Verzeichnis. Geprüft werden deshalb die zitierten Einträge (bei \\nocite{*} alle).
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from . import document, refs

MAX_FILES = 400
_CMD_CITE = re.compile(r"\\([A-Za-z]*[cC]ite[A-Za-z]*|nocite)\*?")
_PAGE = re.compile(r"(\d{1,5})")
_INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
_BIBS = re.compile(r"\\(?:bibliography|addbibresource|addglobalbib)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_MARK = re.compile(r"\x00(\d+)\x00")


def is_latex(name: str) -> bool:
    return name.lower().endswith((".tex", ".bib", ".zip", ".ltx"))


# --------------------------------------------------------------------------
# Dateien sammeln
# --------------------------------------------------------------------------
def files_from_upload(items: List[Tuple[str, bytes]]) -> Dict[str, str]:
    """Hochgeladene Dateien (auch .zip) als {relativer Pfad: Text}."""
    out: Dict[str, str] = {}
    for name, data in items:
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for info in z.infolist()[:MAX_FILES * 4]:
                        n = info.filename
                        if info.is_dir() or "__MACOSX" in n or not n.lower().endswith((".tex", ".bib", ".ltx")):
                            continue
                        if info.file_size > document.MAX_BYTES:
                            continue
                        out[str(PurePosixPath(n))] = z.read(info).decode("utf-8", errors="replace")
            except zipfile.BadZipFile as exc:
                raise ValueError("unreadable") from exc
        elif name.lower().endswith((".tex", ".bib", ".ltx")):
            out[PurePosixPath(name).name] = data.decode("utf-8", errors="replace")
    if len(out) > MAX_FILES:
        raise ValueError("size")
    return out


def files_from_path(path: Path) -> Dict[str, str]:
    """Eine .tex-Datei oder ein Projektordner auf diesem Mac als {relativer Pfad: Text}."""
    root = path if path.is_dir() else path.parent
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if len(out) >= MAX_FILES:
            break
        if p.is_file() and p.suffix.lower() in (".tex", ".bib", ".ltx") and p.stat().st_size <= document.MAX_BYTES:
            rel = p.relative_to(root).as_posix()
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
    if path.is_file():
        out["\x00main"] = path.relative_to(root).as_posix()
    return out


def _strip_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", tex)


def _find(files: Dict[str, str], name: str, base: str, ext: str) -> Optional[str]:
    name = name.strip()
    cands = [name, name + ext]
    folder = str(PurePosixPath(base).parent)
    for c in cands:
        for key in (str(PurePosixPath(folder) / c) if folder != "." else c, c):
            key = str(PurePosixPath(key))
            if key in files:
                return key
    for c in cands:  # zuletzt nur über den Dateinamen
        hit = next((k for k in files if PurePosixPath(k).name == PurePosixPath(c).name), None)
        if hit:
            return hit
    return None


def _main_file(files: Dict[str, str]) -> Optional[str]:
    if files.get("\x00main"):
        return files["\x00main"]
    texs = [k for k in files if k.lower().endswith((".tex", ".ltx")) and not k.startswith("\x00")]
    with_doc = [k for k in texs if "\\documentclass" in files[k] or "\\begin{document}" in files[k]]
    pool = with_doc or texs
    return min(pool, key=lambda k: (k.count("/"), len(k))) if pool else None


def _expand(files: Dict[str, str], key: str, seen: Optional[set] = None) -> str:
    seen = seen if seen is not None else set()
    if key in seen:
        return ""
    seen.add(key)
    tex = _strip_comments(files[key])

    def repl(m: "re.Match") -> str:
        hit = _find(files, m.group(1), key, ".tex")
        return "\n" + _expand(files, hit, seen) + "\n" if hit else " "
    return _INPUT.sub(repl, tex)


# --------------------------------------------------------------------------
# Klartext mit Markierungen für die Zitate
# --------------------------------------------------------------------------
def _cite_commands(tex: str, cites: List[Dict]) -> str:
    """Ersetzt \\cite-Befehle durch Markierungen und merkt sich Schlüssel und Seite."""
    out, i = [], 0
    for m in _CMD_CITE.finditer(tex):
        if m.start() < i:
            continue
        j = m.end()
        opts: List[str] = []
        groups: List[str] = []
        while True:  # [prenote][postnote]{keys}, bei \cites auch mehrere Gruppen
            k = j
            while k < len(tex) and tex[k] in " \t\n":
                k += 1
            if k < len(tex) and tex[k] == "[":
                end = tex.find("]", k)
                if end < 0:
                    break
                opts.append(tex[k + 1:end])
                j = end + 1
                continue
            if k < len(tex) and tex[k] == "{":
                end = tex.find("}", k)
                if end < 0:
                    break
                groups.append(tex[k + 1:end])
                j = end + 1
                if not m.group(1).endswith("s"):
                    break
                continue
            break
        if not groups:
            continue
        keys = [k.strip() for g in groups for k in g.split(",") if k.strip()]
        post = opts[-1] if opts else ""
        page = _PAGE.search(post)
        cites.append({"command": m.group(1), "keys": keys, "page": page.group(1) if page else None})
        out.append(tex[i:m.start()])
        out.append(f" \x00{len(cites) - 1}\x00")
        i = j
    out.append(tex[i:])
    return "".join(out)


def _plain(tex: str) -> str:
    body = tex
    m = re.search(r"\\begin\{document\}(.*?)(?:\\end\{document\}|$)", tex, re.S)
    if m:
        body = m.group(1)
    body = re.sub(r"\\begin\{(equation|align|gather|multline|eqnarray|displaymath|tikzpicture|lstlisting|verbatim|minted)\*?\}.*?\\end\{\1\*?\}",
                  " ", body, flags=re.S)
    body = re.sub(r"\$\$.*?\$\$|\\\[.*?\\\]|\$[^$\n]{0,300}\$", " ", body, flags=re.S)
    # Anführungszeichen vereinheitlichen, damit document.find_quotes sie erkennt
    body = re.sub(r"\\enquote\*?\{([^{}]*)\}", r"“\1”", body)
    body = re.sub(r"\\(?:glqq|quotedblbase)\s*(.*?)\\grqq\{?\}?", r"„\1“", body, flags=re.S)
    body = body.replace("``", "“").replace("''", "”").replace('"`', "„").replace("\"'", "“")
    body = re.sub(r"\\(?:sub)*section\*?(?:\[[^\]]*\])?\{([^}]*)\}", r"\n\n\1\n\n", body)
    body = re.sub(r"\\(?:chapter|paragraph|part)\*?(?:\[[^\]]*\])?\{([^}]*)\}", r"\n\n\1\n\n", body)
    body = re.sub(r"\\item\b", "\n", body)
    body = re.sub(r"\\(?:label|ref|eqref|autoref|cref|Cref|pageref|url|includegraphics|vspace|hspace)\*?(?:\[[^\]]*\])?\{[^}]*\}", " ", body)
    body = re.sub(r"\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?", "\n", body)
    body = body.replace("~", " ").replace("\\%", "%").replace("\\&", "&").replace("---", "–").replace("--", "–")
    body = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", body)  # übrige Befehle, ihr Inhalt bleibt
    body = re.sub(r"[{}]", "", body)
    body = re.sub(r"[ \t]+", " ", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _narrative_name(cite: Dict, entries: Dict[str, str]) -> str:
    """Bei \\textcite, \\citet und \\citeauthor steht der Name im Satz: "Harris et al.", "LeCun und Hinton"."""
    if not re.match(r"(?i)(text|citet|citeauthor|citealt)", cite["command"]):
        return ""
    names = []
    for key in cite["keys"]:
        parsed = refs.parse_bibtex(entries[key]) if key in entries else []
        authors = (parsed[0].get("authors") or []) if parsed else []
        if authors:
            names.append(authors[0] + (" et al." if len(authors) > 2 else (" and " + authors[1] if len(authors) == 2 else "")))
    return "; ".join(names)


def _bib_entries(text: str) -> List[Tuple[str, str]]:
    out = []
    for entry in refs._bibtex_entries(text):
        m = re.match(r"@\s*(\w+)\s*[{(]\s*([^,\s]+)\s*,", entry)
        if m and m.group(1).lower() not in ("comment", "string", "preamble"):
            out.append((m.group(2), entry))
    return out


# --------------------------------------------------------------------------
# Abgleich
# --------------------------------------------------------------------------
def analyse(files: Dict[str, str]) -> Dict:
    """Wie document.analyse, für ein LaTeX-Projekt. ``refs`` sind die zu prüfenden Einträge."""
    main = _main_file(files)
    if not main:
        raise ValueError("no-tex")
    tex = _expand(files, main)
    bib_keys: List[str] = []
    for m in _BIBS.finditer(tex):
        for name in m.group(1).split(","):
            hit = _find(files, name, main, ".bib")
            if hit and hit not in bib_keys:
                bib_keys.append(hit)
    if not bib_keys:  # ohne Angabe: alle .bib-Dateien des Projekts
        bib_keys = [k for k in files if k.lower().endswith(".bib")]
    entries: Dict[str, str] = {}
    for key in bib_keys:
        for cite_key, raw in _bib_entries(files[key]):
            entries.setdefault(cite_key, raw)

    cites: List[Dict] = []
    marked = _plain(_cite_commands(tex, cites))
    marked = re.sub(r"\s*(\x00\d+\x00)\s*(?=[.,;:!?)])", r"\1", marked)  # "Aufgabe \cite{x}." ohne Leerzeichen
    # Markierungen entfernen, Position jedes Zitats im Klartext merken
    text_parts, positions, last, length = [], {}, 0, 0
    for m in _MARK.finditer(marked):
        text_parts.append(marked[last:m.start()])
        length += m.start() - last
        n = int(m.group(1))
        positions[n] = length
        name = _narrative_name(cites[n], entries)  # \textcite: der Name gehört zum Satz
        if name:
            text_parts.append(name)
            length += len(name)
        last = m.end()
    text_parts.append(marked[last:])
    plain = "".join(text_parts)

    all_cited = any(c["command"] == "nocite" and "*" in c["keys"] for c in cites)
    cited_keys: List[str] = []
    for c in cites:
        for k in c["keys"]:
            if k != "*" and k not in cited_keys:
                cited_keys.append(k)
    to_check = list(entries) if all_cited else [k for k in cited_keys if k in entries]
    index = {k: i for i, k in enumerate(to_check)}

    parsed = []
    for i, key in enumerate(to_check):
        r = refs.parse_bibtex(entries[key])
        ref = r[0] if r else refs._empty("bibtex", entries[key])
        if ref.get("arxiv") and not ref.get("doi"):
            ref["doi"] = refs.arxiv_doi(str(ref["arxiv"]))
        ref.update(entry=i, number=None, key=key)
        ref["label"] = refs.label(ref) or key
        parsed.append(ref)

    citations, missing = [], {}
    for n, c in enumerate(cites):
        if c["command"] == "nocite":
            continue
        pos = positions.get(n, 0)
        sentence = document._sentence(plain, pos)
        for k in c["keys"]:
            item = {"kind": "latex", "key": k, "label": k, "pos": pos, "page": c["page"], "sentence": sentence,
                    "entry": index.get(k)}
            citations.append(item)
            if k not in entries:
                m = missing.setdefault(k, {"label": k, "count": 0, "sentence": sentence})
                m["count"] += 1
    uncited = [] if all_cited else [{"entry": None, "label": refs.label(r) or k, "key": k}
                                    for k, r in ((k, (refs.parse_bibtex(entries[k]) or [{}])[0]) for k in entries)
                                    if k not in cited_keys][:200]
    quotes = document.find_quotes(plain, citations)
    return {
        "has_bibliography": bool(entries),
        "style": "latex",
        "entries": len(parsed),
        "bib_entries": len(entries),
        "citations": len(citations),
        "cross_check": True,
        "missing_in_list": sorted(missing.values(), key=lambda m: -m["count"])[:200],
        "uncited": uncited,
        "claims": [c for c in citations if c.get("entry") is not None][:600],
        "quotes": quotes,
        "main_file": main,
        "bib_files": bib_keys,
        "refs_text": "\n".join(entries[k] for k in to_check),
        "refs": parsed,
    }
