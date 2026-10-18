"""LaTeX-Projekte: \\cite-Schlüssel, .bib-Einträge, Sätze und wörtliche Zitate (offline)."""

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from benthic import latex

MAIN = r"""\documentclass{article}
\addbibresource{refs.bib}
\begin{document}
\section{Introduction}
Residual networks with 152 layers won the ILSVRC 2015 classification task \parencite{he2016}. % \cite{ghost}
As \textcite[p.~357]{harris2020} put it, ``NumPy is the primary array programming library for the Python language''.
Missing works show up too \cite{nobody2021}. Transformers changed NLP~\citep[see][p. 2]{vaswani2017}.
\input{chapters/two}
\end{document}"""
TWO = r"""\subsection{More} Deep learning \cite{lecun2015,he2016} is everywhere.
\enquote{Deep learning allows computational models that are composed of multiple processing layers} \cite[436]{lecun2015}."""
BIB = r"""@article{harris2020, title={Array programming with {NumPy}}, author={Harris, Charles R. and Millman, K. Jarrod and van der Walt, Stefan}, journal={Nature}, year={2020}, doi={10.1038/s41586-020-2649-2}}
@inproceedings{he2016, title={Deep Residual Learning for Image Recognition}, author={He, Kaiming and Zhang, Xiangyu}, booktitle={CVPR}, year={2016}, doi={10.1109/CVPR.2016.90}}
@article{lecun2015, title={Deep learning}, author={LeCun, Yann and Bengio, Yoshua and Hinton, Geoffrey}, journal={Nature}, year={2015}, doi={10.1038/nature14539}}
@article{vaswani2017, title={Attention is all you need}, author={Vaswani, Ashish}, year={2017}, eprint={1706.03762}, archivePrefix={arXiv}}
@book{unused2010, title={A book nobody cites}, author={Doe, Jane}, year={2010}}"""
FILES = {"main.tex": MAIN, "chapters/two.tex": TWO, "refs.bib": BIB}


class Projekt(unittest.TestCase):
    def test_keys_entries_and_cross_check(self):
        a = latex.analyse(dict(FILES))
        self.assertEqual(a["style"], "latex")
        self.assertEqual((a["entries"], a["bib_entries"]), (4, 5))      # nur zitierte Einträge werden geprüft
        self.assertEqual([m["label"] for m in a["missing_in_list"]], ["nobody2021"])
        self.assertEqual([u["key"] for u in a["uncited"]], ["unused2010"])
        self.assertNotIn("ghost", [c["key"] for c in a["claims"]])     # Kommentare zählen nicht
        self.assertEqual(next(r for r in a["refs"] if r["key"] == "vaswani2017")["doi"], "10.48550/arXiv.1706.03762")

    def test_sentences_pages_and_quotes(self):
        a = latex.analyse(dict(FILES))
        harris = next(c for c in a["claims"] if c["key"] == "harris2020")
        self.assertEqual(harris["page"], "357")
        self.assertTrue(harris["sentence"].startswith("As Harris et al. put it"))
        he = next(c for c in a["claims"] if c["key"] == "he2016")
        self.assertEqual(he["sentence"], "Residual networks with 152 layers won the ILSVRC 2015 classification task.")
        self.assertEqual([(q["label"], q["page"]) for q in a["quotes"]], [("harris2020", "357"), ("lecun2015", "436")])

    def test_nocite_all(self):
        files = dict(FILES)
        files["main.tex"] = files["main.tex"].replace(r"\end{document}", r"\nocite{*}\end{document}")
        a = latex.analyse(files)
        self.assertEqual(a["entries"], 5)
        self.assertEqual(a["uncited"], [])

    def test_zip_and_folder(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, text in FILES.items():
                z.writestr("thesis/" + name, text)
            z.writestr("__MACOSX/thesis/._main.tex", "junk")
        files = latex.files_from_upload([("thesis.zip", buf.getvalue())])
        self.assertEqual(latex.analyse(files)["entries"], 4)
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in FILES.items():
                (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / name).write_text(text)
            self.assertEqual(latex.analyse(latex.files_from_path(Path(tmp) / "main.tex"))["entries"], 4)
            self.assertEqual(latex.analyse(latex.files_from_path(Path(tmp)))["entries"], 4)

    def test_without_tex(self):
        with self.assertRaises(ValueError):
            latex.analyse({"refs.bib": BIB})


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod, verify
        fake = lambda ref, email="", on_event=None: {"identifier": ref.get("label"), "verdict": "verified",
                                                     "status": "verified", "note": "", "score": 100, "flags": []}
        self.patch = mock.patch.object(verify, "verify_ref", side_effect=fake)
        self.patch.start()
        self.client = appmod.app.test_client()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_upload_tex_and_bib(self):
        data = {"file": [(io.BytesIO(MAIN.replace(r"\input{chapters/two}", TWO).encode()), "main.tex"),
                         (io.BytesIO(BIB.encode()), "refs.bib")]}
        r = self.client.post("/api/document", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j["total"], 4)
        self.assertEqual(j["document"]["name"], "main.tex")
        self.assertEqual(len(j["document"]["quotes"]), 2)

    def test_tex_without_bib(self):
        r = self.client.post("/api/document", data={"file": [(io.BytesIO(MAIN.encode()), "main.tex")]},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
