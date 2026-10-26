"""Hinweise zum Aufbau einer Arbeit: Aneinanderreihung, Sammelbeleg am Absatzende, neue Quellen im Fazit und die
Frage neben dem Fazit (document._structure_signals, offline)."""

import unittest

from benthic import document

FILLER = ("Dabei geht es um die Frage, wie Unternehmen ihre Kundschaft nach Nutzen statt nach Alter oder Wohnort "
          "einteilen und welche Folgen das für die Bindung hat. ")

INTRO = ("1 Einleitung\n"
         "Spezialitätenkaffee im Abonnement wächst, doch viele Abos enden nach wenigen Monaten. " + FILLER * 2 + "\n"
         "Die Forschungsfrage dieser Arbeit lautet: Welche Kriterien der nutzenbasierten Segmentierung senken die "
         "Kündigungsrate im Kaffee-Abonnement? Sie wird mit einer Literaturarbeit beantwortet. " + FILLER + "\n")

SERIAL = ("2 Grundlagen\n"
          "Smith (1956) beschreibt die Marktsegmentierung als Anpassung des Angebots an eine uneinheitliche Nachfrage. "
          + FILLER * 2 + "Das gilt nach Smith (1956) für alle Märkte.\n"
          "Dolnicar (2018) unterscheidet die Segmentierung nach gesundem Menschenverstand von der datengetriebenen. "
          + FILLER * 2 + "Beide Wege haben nach Dolnicar (2018) ihre Grenzen.\n"
          "Reinartz (2004) zeigt, dass Technik allein die Kundenbeziehung nicht verbessert. "
          + FILLER * 2 + "Entscheidend ist laut Reinartz (2004) der Prozess.\n")

END_CITED = ("3 Analyse\n"
             "Die Kennzahlen der Kundenbindung lassen sich auf mehreren Ebenen messen. Zuerst zählt die Zahl der "
             "verlängerten Abos. Danach folgt der Anteil der Kundschaft, die eine zweite Sorte bestellt. Ein dritter "
             "Wert ist die Zeit bis zur ersten Pause. Zuletzt zeigt der Wert je Kunde über die gesamte Laufzeit, ob "
             "sich die Bindung rechnet. " + FILLER * 3 + "Diese Werte ergeben zusammen ein vollständiges Bild der "
             "Bindung (vgl. Oliver, 1999).\n"
             "Payne und Frow (2005) sowie Reinartz (2004) und Smith (1956) zeigen gemeinsam, dass Segmentierung und "
             "Prozess zusammengehören, auch wenn Dolnicar (2018) auf Grenzen der Daten hinweist. " + FILLER * 2 + "\n")

CONCLUSION = ("5 Fazit und Ausblick\n"
              "Die Arbeit hat gezeigt, welche Kriterien der Segmentierung die Kündigungsrate senken. Flexible Pausen "
              "wirken stärker als Rabatte (Smith, 1956). Neu ist der Blick auf Gerechtigkeit im Service (Tax, 1998).\n")

BIB = ("Literaturverzeichnis\n"
       "Dolnicar, S. (2018). Market segmentation analysis. Springer.\n"
       "Oliver, R. L. (1999). Whence consumer loyalty? Journal of Marketing, 63, 33-44.\n"
       "Payne, A. (2005). A strategic framework for customer relationship management. Journal of Marketing, 69, 167-176.\n"
       "Reinartz, W. (2004). The customer relationship management process. Journal of Marketing Research, 41, 293-305.\n"
       "Smith, W. R. (1956). Product differentiation and market segmentation. Journal of Marketing, 21, 3-8.\n"
       "Tax, S. S. (1998). Customer evaluations of service complaint experiences. Journal of Marketing, 62, 60-76.\n")


class Structure(unittest.TestCase):
    def setUp(self):
        self.doc = document.analyse(INTRO + SERIAL + END_CITED + CONCLUSION + BIB)

    def test_serial_paragraphs(self):
        serial = self.doc["serial_paragraphs"]
        self.assertEqual(len(serial), 1)
        self.assertEqual(serial[0]["paragraphs"], 3)
        self.assertEqual(serial[0]["author_led"], 3)
        self.assertTrue(serial[0]["sentence"].startswith("Smith (1956) beschreibt"))
        self.assertEqual(serial[0]["labels"], ["Smith (1956)", "Dolnicar (2018)", "Reinartz (2004)"])

    def test_one_citation_at_the_end(self):
        ends = self.doc["end_citations"]
        self.assertEqual([e["label"] for e in ends], ["Oliver, 1999"])
        self.assertTrue(ends[0]["sentence"].startswith("Die Kennzahlen der Kundenbindung"))

    def test_new_source_in_conclusion(self):
        self.assertEqual([c["label"] for c in self.doc["conclusion_sources"]], ["Tax, 1998"])  # Smith stand schon vorn

    def test_question_pair(self):
        pair = self.doc["question_pair"]
        self.assertIn("Welche Kriterien der nutzenbasierten Segmentierung", pair["question"])
        self.assertTrue(pair["conclusion"].startswith("Die Arbeit hat gezeigt"))
        self.assertEqual(pair["heading"], "5 Fazit und Ausblick")
        self.assertIn("nutzenbasierten", pair["missing"])     # das Fazit nimmt das Wort nicht auf
        self.assertNotIn("Kriterien", pair["missing"])
        self.assertIn("Kaffee", pair["missing"])  # wie in der Frage geschrieben

    def test_synthesis_and_wrapped_pdf_lines(self):
        # Absätze mit mehreren Quellen sind keine Aneinanderreihung, auch wenn das PDF jede Zeile umbricht.
        synth = ("Payne und Frow (2005) sowie Reinartz (2004) zeigen gemeinsam, dass Segmentierung und Prozess "
                 "zusammengehören, auch wenn Dolnicar (2018) auf Grenzen hinweist. " + FILLER * 2 + "\n")
        text = INTRO + "2 Grundlagen\n" + synth * 3 + BIB
        wrapped = "\n".join(text[i:i + 80] for i in range(0, len(text), 80)).replace("\nLiteraturverzeichnis", "\n\nLiteraturverzeichnis")
        for t in (text, wrapped):
            doc = document.analyse(t)
            self.assertEqual(doc["serial_paragraphs"], [])
            self.assertEqual(doc["conclusion_sources"], [])
            self.assertIsNone(doc["question_pair"])  # ohne Fazit kein Paar

    def test_paragraphs_in_a_pdf(self):
        text = ("1 Einleitung\n"
                "Eine lange Zeile aus dem Satzspiegel, die mitten im Satz umbricht und weitergeht\n"
                "auf der nächsten Zeile und endet.\n"
                "12\n"
                "Ein zweiter Absatz beginnt hier und läuft ebenfalls über mehrere Zeilen des\n"
                "Satzspiegels, bis er kurz endet.\n")
        paras = document.paragraphs(text)
        self.assertEqual([p["heading"] for p in paras], [True, False, False])
        self.assertTrue(text[paras[1]["start"]:paras[1]["end"]].endswith("auf der nächsten Zeile und endet."))


if __name__ == "__main__":
    unittest.main()
