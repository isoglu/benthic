# Änderungen

Alle wichtigen Änderungen an Benthic, die neueste Version zuerst. Die
Versionsnummern folgen [Semantic Versioning](https://semver.org). Die fett
gesetzten Stichworte der neuesten Version erscheinen in der App als
Update-Hinweis; die Website zeigt diese Datei als Versionsgeschichte. Die
englische Fassung steht in [CHANGELOG.md](CHANGELOG.md).

## 1.0.1

Korrekturen und klarere Einstellungen. Eine Berichtigung zu 1.0.0: Projekte
exportieren Literaturverzeichnisse (BibTeX, RIS und formatiert), nicht CSV oder
Excel; die gibt es für Prüfberichte, Downloads, den Verlauf und
Stapelprüfungen.

- **Hochschulzugang beim Laden.** Gibt es von einem Artikel keine freie
  Fassung, verlinkt das Ergebnis jetzt über den Proxy deiner
  Hochschulbibliothek, wie es „Nur prüfen (mit OA)“ schon tat, auch für
  KI-Assistenten.
- **Korrekte Fassung in jedem Stil.** Vancouver funktioniert, und Harvard, MLA,
  Vancouver und DIN kommen ohne leere Herausgeberfelder. Literaturverzeichnisse
  in IEEE und Vancouver sind nummeriert, und DIN 1505 nutzt immer deutsche
  Begriffe.
- **DIN-Listen aus Word.** Nachnamen in Kapitälchen liest Benthic als
  Großbuchstaben, sodass ein Literaturverzeichnis nach DIN 1505 in einer
  Word-Datei als DIN erkannt wird.
- **Einheitliche Antworten für KI-Assistenten.** Hinweise zum Zitierstil stehen
  in der Antwort eines Werkzeugs auf Englisch, wie der Rest der Antwort.
- **Klarere Einrichtung.** Einrichtung und Einstellungen sagen, dass die
  Kontakt-E-Mail an Unpaywall, Crossref und OpenAlex geht, und die Einrichtung
  fragt nicht mehr nach einem Namen, den keine Funktion nutzte.

## 1.0.0

Erste öffentliche Version.

- **Quellen prüfen.** Antwort eines Sprachmodells, Literaturliste, BibTeX oder
  RIS einfügen. Benthic findet jede Angabe und gibt ihr eines von sieben
  Urteilen: bestätigt, DOI korrigiert, weicht ab, unsicher, passt nicht, nicht
  auffindbar oder zurückgezogen, und zeigt, welche Quellen gefragt wurden und
  was sie fanden.
- **Ganze Arbeiten prüfen.** PDF, Word, Markdown oder LaTeX (eine `.tex`-Datei
  mit ihrer `.bib`, oder das ganze Projekt als `.zip`): Jeder Eintrag des
  Literaturverzeichnisses wird geprüft und die Liste mit den Zitaten im Text
  abgeglichen.
- **Wörtliche Zitate prüfen.** Jedes wörtliche Zitat sucht Benthic Wort für
  Wort im geladenen Volltext der Quelle, samt angegebener Seite.
- **Zitierstil.** Der Prüfbericht nennt den Stil der Liste (APA 7, Harvard,
  Chicago, IEEE, Vancouver oder DIN 1505), zeigt, welche Einträge davon
  abweichen und welche Angaben fehlen, und liefert jeden Eintrag in korrekter
  Form.
- **Belegstellen finden.** Zu einer bestätigten Quelle sucht Benthic im
  geladenen Volltext die Passagen, die deinem Satz am nächsten kommen, mit
  Seitenzahl, ab macOS 15 auch über Sprachgrenzen. Ob eine davon die Aussage
  stützt, entscheidest du.
- **Listen bewerten.** Echtheit, Alter der Quellen, Publikationsorte mit
  Kennzahlen von OpenAlex und JUFO-Stufen, von DOAJ markierte Zeitschriften,
  eigene Rankinglisten, veröffentlichte Fassungen von Preprints und
  Schlüsselwerke, die fehlen könnten.
- **Open Access laden.** Erst die eigene Bibliothek, dann acht
  Open-Access-Quellen (arXiv, OpenAlex, Unpaywall, Europe PMC, Semantic
  Scholar, CORE, Elsevier und Crossref), jeweils als Preprint, akzeptiertes
  Manuskript oder Verlagsversion gekennzeichnet. Ohne freie Fassung ein Link
  über die Bibliothek deiner Hochschule.
- **Rückzugswache.** Einmal am Tag prüft Benthic die Quellen deiner Projekte
  neu und meldet Rückzüge, Korrekturen und Mitteilungen über Bedenken.
- **Stapelprüfung für Lehrende.** Ein Ordner mit Abgaben auf einmal: je Abgabe
  eine Zeile und ein PDF-Bericht, Export als CSV oder Excel. Die Abgaben
  bleiben auf dem Mac.
- **Projekte und Zotero.** Ein Projekt je Forschungsfrage,
  Literaturverzeichnisse in gängigen Zitierstilen, Export als BibTeX, RIS, CSV
  oder Excel, PDF-Berichte, eine Datei, die bei jedem Sichern neu geprüft
  wird, und Zotero in beide Richtungen.
- **Mit KI-Assistenten arbeiten.** Werkzeuge über MCP für Claude Desktop,
  Claude Code, ChatGPT mit Codex, Codex CLI, Gemini CLI, Antigravity, Grok
  Build, Cursor, VS Code und LM Studio, die meisten mit einem Klick verbunden.
- **Aus jeder App senden.** Dienste › Check References with Benthic, ein
  Lesezeichen für den Browser oder ein `benthic://`-Link.
- **Mac-App.** Englisch und Deutsch, universell für Apple Silicon und Intel,
  ab macOS 13, dunkles und helles Erscheinungsbild, die Grenzen jedes Dienstes
  eingehalten, und Updates, die sich in der App installieren.
