"""Prüfkorpus: echte und erfundene Literaturangaben gegen aufgezeichnete Antworten der Verzeichnisse.

Jeder Fall, der ein Problem melden muss (role "fire"), hat einen Zwilling, der ähnlich aussieht und still
bleiben muss (role "silent"). Die Antworten von Crossref, OpenAlex und den übrigen Quellen liegen in
tests/corpus/responses, so läuft der Test offline und sieht immer dieselben Daten. Eine Anfrage, die nicht
aufgezeichnet ist, lässt den Fall scheitern: dann hat sich die Kaskade geändert und der Fall muss neu
aufgezeichnet werden.

    python -m tests.test_corpus --record [id ...]   Antworten neu aufzeichnen (braucht Netz)
    python -m tests.test_corpus --top-up [id ...]   nur fehlende Anfragen nachholen (neue Quelle in der Kaskade)
    python -m tests.test_corpus --score             Urteile, Trefferquote und Fehlalarme ausgeben
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent / "corpus"
CASES = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
INVENTED = {"not_found", "mismatch"}  # Urteile, die sagen: so gibt es das Werk nicht
# Felder, die verify.py nie liest: Abstracts, Literaturlisten, Schlagwörter und Affiliationen der gefundenen
# Werke. Sie machen den Großteil der Antworten aus und tragen fremden Fließtext.
STRIPPED = ("abstract", "abstract_inverted_index", "reference", "referenced_works", "related_works",
            "concepts", "topics", "primary_topic", "keywords", "mesh", "counts_by_year", "best_oa_location",
            "institutions", "affiliations", "raw_affiliation_strings", "raw_affiliation_string")


def _strip(body: str) -> str:
    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in STRIPPED}
        return [walk(v) for v in node] if isinstance(node, list) else node
    try:
        return json.dumps(walk(json.loads(body)), ensure_ascii=False)
    except ValueError:
        return body  # XML (arXiv, DNB) bleibt, wie es kam


def _body(resp) -> str:
    """Antworttext; XML ohne Zeichensatz im Content-Type (K10plus) ist UTF-8, nicht das Latin-1 von requests."""
    kind = resp.headers.get("Content-Type", "").lower()
    if "charset" not in kind and resp.content[:5] == b"<?xml":
        return resp.content.decode("utf-8", "replace")
    return resp.text


def _key(url, params) -> str:
    return url + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)


class _Reply:
    """Das, was verify.py von einer requests.Response liest."""

    def __init__(self, call):
        self.status_code = call["status"]
        self.text = call["body"]
        self.content = self.text.encode("utf-8")
        self.headers = {"Content-Type": call.get("type") or ""}
        self.url = call["url"]

    def json(self):
        return json.loads(self.text)


def _verify(case, api):
    from benthic import oa, refs, verify
    verify._cache.clear()  # jeder Fall sieht nur seine eigenen Antworten
    oa._work_cache.clear()
    with mock.patch("benthic.oa._api_get", side_effect=api):
        return verify.verify_ref(refs.parse(case["input"])[0])


def replay(case):
    """(Ergebnis, nicht aufgezeichnete Anfragen) für einen Fall, ganz ohne Netz."""
    calls = {_key(c["url"], c["params"]): c
             for c in json.loads((HERE / "responses" / f"{case['id']}.json").read_text(encoding="utf-8"))["calls"]}
    missing = []

    def api(url, params=None, headers=None, timeout=20.0):
        call = calls.get(_key(url, params))
        if call is None:
            missing.append(_key(url, params))
            return None
        return None if call["status"] is None else _Reply(call)

    def no_web(*_a, **_k):
        missing.append("requests.get")
        raise AssertionError("Seitenabruf im Korpus")
    with mock.patch("time.sleep"), mock.patch("requests.get", side_effect=no_web):
        return _verify(case, api), missing


def problems(case, res):
    """Was am Urteil nicht stimmt; leer heißt: wie erwartet."""
    out = []
    if res["verdict"] not in case["expect"]:
        out.append(f"Urteil {res['verdict']}, erwartet {'/'.join(case['expect'])}")
    if case.get("pattern") and res.get("pattern") != case["pattern"]:
        out.append(f"Muster {res.get('pattern')}, erwartet {case['pattern']}")
    out += [f"Abweichung {f} fehlt" for f in case.get("flags", []) if f not in (res.get("flags") or [])]
    found = str((res.get("found") or {}).get("doi") or "").lower()
    if case.get("found_doi") and found != case["found_doi"].lower():
        out.append(f"gefundene DOI {found or '-'}, erwartet {case['found_doi']}")
    if case.get("found_source") and (res.get("found") or {}).get("source") != case["found_source"]:
        out.append(f"Quelle {(res.get('found') or {}).get('source')}, erwartet {case['found_source']}")
    # Feldhinweise (Band, Seiten, Reihenfolge …): ein stiller Fall darf nur die erlaubten zeigen, ein Fall
    # mit "diffs" muss genau diese zeigen. Sie ändern das Urteil nicht, sind aber für Studierende ein Vorwurf.
    shown = {k for k, d in (res.get("field_diffs") or {}).items() if isinstance(d, dict) and d.get("status") != "ok"}
    out += [f"Feldhinweis {k} fehlt" for k in case.get("diffs", []) if k not in shown]
    if case["role"] == "silent":
        out += [f"Feldhinweis {k} bei richtiger Angabe" for k in sorted(shown - set(case.get("diffs_ok", [])))]
    return out


class Korpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_pairs(self):
        by_id = {c["id"]: c for c in CASES}
        self.assertEqual(len(by_id), len(CASES), "doppelte id")
        for c in CASES:
            twin = by_id.get(c["twin"])
            self.assertIsNotNone(twin, c["id"])
            self.assertEqual(twin["twin"], c["id"], c["id"])
            self.assertEqual({c["role"], twin["role"]}, {"fire", "silent"}, c["id"])
            if c["role"] == "silent":
                self.assertTrue(c["real"], f"{c['id']}: ein stiller Fall ist ein echtes Werk")

    def test_stripped_fields_stay_unread(self):
        """Liest verify.py eines der weggelassenen Felder, wären die Aufzeichnungen still unvollständig."""
        src = (HERE.parent.parent / "benthic" / "verify.py").read_text(encoding="utf-8")
        for field in STRIPPED:
            self.assertNotIn(f'"{field}"', src, f"verify.py liest {field}: aus STRIPPED nehmen und neu aufzeichnen")

    def test_cases(self):
        for case in CASES:
            with self.subTest(case["id"]):
                res, missing = replay(case)
                self.assertEqual(missing, [], f"nicht aufgezeichnet, neu: python -m tests.test_corpus --record {case['id']}")
                found = problems(case, res)
                if case.get("known"):
                    self.assertTrue(found, f"{case['id']} geht jetzt richtig aus: 'known' entfernen und "
                                           f"{case['known']} in tests/escapes.json diesen Test als owner geben")
                else:
                    self.assertEqual(found, [], f"{case['id']}: {res.get('note')}")


def score(results) -> str:
    real = [(c, r) for c, r in results if c["real"]]
    made_up = [(c, r) for c, r in results if not c["real"]]
    false_alarms = [c["id"] for c, r in real if r["verdict"] == "not_found"]  # "mismatch" kann bei echtem Werk stimmen
    caught = [c["id"] for c, r in made_up if r["verdict"] in INVENTED]
    lines = [f"{c['id']:26} {r['verdict']:10} {'ok' if not problems(c, r) else '; '.join(problems(c, r))}"
             for c, r in results]
    lines += ["", f"erfundene Angaben erkannt: {len(caught)} von {len(made_up)}",
              f"echte Werke als erfunden gemeldet: {len(false_alarms)} von {len(real)} {false_alarms or ''}".rstrip()]
    return "\n".join(lines)


def record(ids) -> int:
    """Fragt die echten Dienste und speichert jede Antwort. Ein Fall mit Ausfällen wird nicht gespeichert."""
    from benthic import __version__, oa
    real_get = oa._api_get
    out_dir = HERE / "responses"
    out_dir.mkdir(exist_ok=True)
    failed_any = 0
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": tmp}):
        for case in [c for c in CASES if not ids or c["id"] in ids]:
            for attempt in range(3):  # arXiv und Semantic Scholar fallen oft kurz aus: nach einer Pause noch einmal
                if attempt:
                    time.sleep(60)
                calls, failed = [], []

                def api(url, params=None, headers=None, timeout=20.0):
                    # Für die Aufnahme zählt die Antwort, nicht die Dauer: langsamen Diensten mehr Zeit lassen.
                    resp = real_get(url, params=params, headers=headers, timeout=max(timeout, 60.0))
                    status = None if resp is None else resp.status_code
                    if status is None or status == 429 or status >= 500:
                        failed.append(f"{url} {status}")
                    calls.append({"url": url, "params": params or {}, "status": status,
                                  "type": "" if resp is None else resp.headers.get("Content-Type", ""),
                                  "body": "" if resp is None else _strip(_body(resp))})
                    return resp
                res = _verify(case, api)
                if not failed:
                    break
            if failed:
                failed_any += 1
                print(f"{case['id']}: nicht gespeichert, Ausfälle: {failed}", file=sys.stderr)
                continue
            (out_dir / f"{case['id']}.json").write_text(json.dumps(
                {"case": case["id"], "recorded": time.strftime("%Y-%m-%d"), "benthic": __version__, "calls": calls},
                ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
            print(f"{case['id']:26} {res['verdict']:10} {'; '.join(problems(case, res)) or 'ok'}")
    return 1 if failed_any else 0


def top_up(ids) -> int:
    """Spielt die Aufzeichnung ab und holt nur Anfragen, die darin fehlen, live nach. So bleiben die alten Antworten,
    wie sie waren, und eine neue Quelle am Ende der Kaskade (lobid, K10plus) braucht kein OpenAlex-Budget."""
    from benthic import oa
    real_get = oa._api_get
    failed_any = 0
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": tmp}):
        for case in [c for c in CASES if not ids or c["id"] in ids]:
            path = HERE / "responses" / f"{case['id']}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            calls = {_key(c["url"], c["params"]): c for c in data["calls"]}
            added, failed = [], []

            def api(url, params=None, headers=None, timeout=20.0):
                key = _key(url, params)
                if key not in calls:
                    resp = real_get(url, params=params, headers=headers, timeout=max(timeout, 60.0))
                    status = None if resp is None else resp.status_code
                    if status is None or status == 429 or status >= 500:
                        failed.append(f"{url} {status}")
                    calls[key] = {"url": url, "params": params or {}, "status": status,
                                  "type": "" if resp is None else resp.headers.get("Content-Type", ""),
                                  "body": "" if resp is None else _strip(_body(resp))}
                    added.append(calls[key])
                call = calls[key]
                return None if call["status"] is None else _Reply(call)
            res = _verify(case, api)
            if failed:
                failed_any += 1
                print(f"{case['id']}: nicht gespeichert, Ausfälle: {failed}", file=sys.stderr)
                continue
            if added:
                data["calls"] += added
                data["topped_up"] = time.strftime("%Y-%m-%d")
                path.write_text(json.dumps(data, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
            print(f"{case['id']:26} +{len(added):<3} {res['verdict']:10} {'; '.join(problems(case, res)) or 'ok'}")
    return 1 if failed_any else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--record"]:
        sys.exit(record(sys.argv[2:]))
    if sys.argv[1:2] == ["--top-up"]:
        sys.exit(top_up(sys.argv[2:]))
    if sys.argv[1:2] == ["--score"]:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": tmp}):
            print(score([(c, replay(c)[0]) for c in CASES]))
        sys.exit(0)
    unittest.main()
