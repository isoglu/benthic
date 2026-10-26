# Änderungen

Alle wichtigen Änderungen an Benthic, die neueste Version zuerst. Die
Versionsnummern folgen [Semantic Versioning](https://semver.org). Die fett
gesetzten Stichworte der neuesten Version erscheinen in der App als
Update-Hinweis; die Website zeigt diese Datei als Versionsgeschichte. Die
englische Fassung steht in [CHANGELOG.md](CHANGELOG.md).

## 1.1.9

Ein Feld zum Prüfen, Finden und Laden von Quellen, und neue Hinweise aus einem Seminar zum wissenschaftlichen Schreiben.

- **Ein Feld für alles.** Prüfen, Quellen finden und Download sind jetzt eine
  Ansicht. Füge eine Literaturliste, DOIs, eine Aussage aus deiner Arbeit oder
  einen Suchstring ein oder zieh eine ganze Arbeit hinein: Benthic erkennt,
  was es ist, und hebt den passenden Knopf hervor; die anderen beiden bleiben
  einen Klick entfernt. Ein Absatz wird Satz für Satz gesucht, und ein
  Suchstring wie in einer Fachdatenbank, etwa
  `"market segmentation" AND churn`, geht unverändert an OpenAlex.
- **Suchprotokoll.** Jede Suche hält Datum, Datenbank, exakten Suchstring,
  Trefferzahl und die übernommenen Arbeiten fest, je Projekt exportierbar für
  den Methodenteil.
- **Zitiert von.** Zu einer bestätigten Quelle zeigt Benthic neuere Arbeiten,
  die sie zitieren, die meistzitierten zuerst: die Vorwärtssuche im
  Schneeballverfahren.
- **Aufbau einer Arbeit.** Die Prüfung einer Arbeit zeigt jetzt Absätze, die
  nacheinander je nur eine Quelle nacherzählen, lange Absätze mit einem
  einzigen Beleg ganz am Ende und Quellen, die erst im Fazit auftauchen, und
  sie stellt die Forschungsfrage neben den Anfang des Fazits. Das sind
  Hinweise mit Fundstelle, keine Bewertung.
- **Übersetzt übernommen.** Sätze in einer anderen Sprache als ihre Quelle
  lassen sich auf dem Mac übersetzen (ab macOS 15) und mit der Quelle
  vergleichen. So fällt auch ein Satz auf, der Wort für Wort übersetzt ist und
  keine Anführungszeichen trägt.
- **Quellenmatrix.** Jedes Projekt exportiert eine Tabelle mit Autor:innen,
  Jahr, Titel, Zeitschrift, DOI und Urteil, dazu leere Spalten für
  Forschungsfrage der Quelle, Theorie, Methode, Kernergebnis, Grenzen und den
  Nutzen für die eigene Frage.
- **Quellenarten und Kursvorgaben.** Die Gesundheit der Literaturliste zeigt
  den Anteil von Fachartikeln, Büchern, Preprints, Berichten und Webseiten.
  Eine Hausregel-Datei kann jetzt Kursvorgaben enthalten: eine Mindestzahl an
  Quellen, Quellenarten, die der Kurs nicht vorsieht, und einen Satz der
  Lehrenden.
- **Mehr Bücher gefunden.** Nach Open Library und der Deutschen
  Nationalbibliothek fragt Benthic die Verbundkataloge des hbz (lobid) sowie
  von GBV und SWB (K10plus), zuerst mit Titel und Jahr, damit die richtige
  Auflage eines Lehrbuchs erscheint.

## 1.1.8

Plugins haben jetzt ihren eigenen Platz in den Einstellungen.

- **Plugins in den Einstellungen.** Einstellungen › Quellen und Zugang ›
  Plugins öffnet den Plugin-Ordner mit einem Klick, zeigt, ob jedes Plugin
  aktiv ist oder warum es gescheitert ist, und startet Benthic neu, damit ein
  neues Plugin wirkt. Auch der Schalter für die Zusatzwege steht dort.

## 1.1.7

Benthic lädt jetzt Plugins, die du selbst hinzufügst.

- **Eigene Plugins.** Leg ein Plugin in
  `~/Library/Application Support/Benthic/plugins` und starte Benthic neu. Ein
  Plugin kann einen Download-Weg ergänzen, der nach Open Access läuft, etwa
  den Link-Resolver deiner Hochschule, sobald du in den Einstellungen die
  Zusatzwege einschaltest. Bisher musste ein Plugin in die App eingebaut
  werden. Ein Plugin ist Code, der in Benthic läuft: Leg nur Plugins hinein,
  die du selbst geschrieben hast oder denen du vertraust. Wie man eines
  schreibt: [docs/extending.md](https://github.com/isoglu/benthic/blob/main/docs/extending.md).

## 1.1.6

Jeder Prüfbericht trägt jetzt ein signiertes Siegel, das jede Person
nachprüfen kann, Hochschulen können ihre Regeln als eine Datei weitergeben, und
die Prüfung einer Arbeit zeigt Hinweise auf Manipulation.

- **Ein Siegel, das jede Person prüfen kann.** Jeder PDF-Bericht trägt ein
  signiertes Siegel mit QR-Code auf dem Deckblatt und seiner Nummer in jeder
  Fußzeile. Der QR-Code öffnet benthic.isoglu.com/v: Die Seite zeigt, ob der
  Bericht seit seiner Erstellung unverändert ist, und fragt jede DOI noch
  einmal bei Crossref und DataCite nach, im Browser der lesenden Person. Eine
  dort abgelegte Arbeit wird über ihren Fingerabdruck mit dem Bericht
  verglichen. Das Siegel belegt, dass ein Bericht unverändert ist, nicht, wer
  ihn erstellt hat.
- **Hausregeln der Hochschule.** Eine Bibliothek oder ein Schreibzentrum kann
  eine Datei mit Name, Proxy-Link, Kontaktadresse, OpenAlex-Key, verlangtem
  Zitierstil und Rankinglisten weitergeben. Du lädst sie unter Einstellungen ›
  Quellen und Zugang; Benthic übernimmt nichts anderes daraus, und der
  Zitierstil der Hausregeln gilt dann für die Stilprüfung.
- **Hinweise auf Manipulation.** Die Prüfung einer Arbeit zeigt unsichtbare
  Zeichen mitten in Wörtern, Buchstaben aus einer anderen Schrift, die wie
  lateinische aussehen, in einer Word-Datei versteckten Text (ausgeblendet,
  weiß oder winzig) und gequälte Phrasen, die seltsamen Synonyme eines
  Umschreibprogramms. Jeder Fund zeigt die Stelle zum Nachprüfen in der Datei;
  er ist nie ein Wert für die Person.

## 1.1.5

Benthic prüft jetzt, ob ein geladenes PDF wirklich die zitierte Arbeit ist,
nennt die Fassung, vergleicht jede Angabe einer Quelle mit dem Eintrag im
Verzeichnis und findet ältere PubMed-Artikel über den Titel.

- **Das richtige PDF oder keins.** Bevor Benthic ein Zitat oder eine Übernahme
  mit einem geladenen PDF vergleicht, sucht es auf den ersten Seiten nach der
  zitierten DOI oder dem Titel. Eine Datei, die sich als andere Arbeit
  herausstellt, wird weder gespeichert noch verwendet, und Benthic nennt die
  Arbeit, die sie wirklich ist.
- **Welche Fassung vorliegt.** Preprints, Arbeitspapiere, akzeptierte
  Manuskripte, technische Berichte, Zusammenfassungen und Vorschauen hinter
  einer Bezahlschranke heißen jetzt so. Fehlt ein Zitat in einer
  Zusammenfassung, einer Vorschau oder in Folien, steht dort „Nicht in dieser
  Fassung“, statt dass es als nicht gefunden gilt.
- **Zitate aus einer anderen Quelle.** Steht ein Zitat nicht in der zitierten
  Arbeit, aber wörtlich in einer anderen Arbeit derselben Prüfung, sagt Benthic,
  woher es wirklich stammt.
- **Jede Angabe verglichen.** Erstautor:in, Reihenfolge der Autor:innen, Band,
  Heft und Seiten werden mit dem Eintrag verglichen und neben dem Urteil
  gezeigt, ohne es zu ändern, ebenso eine zitierte Seite außerhalb der Seiten
  des Artikels. Namen in anderer Schreibweise, etwa Müller und Mueller, gelten
  als derselbe Name.
- **Eine DOI, ein Werk.** In einem Literaturverzeichnis oder einem Kursordner
  weist Benthic auf eine DOI mit verschiedenen Angaben hin und auf zwei Werke
  mit denselben Seiten im selben Band.
- **Bessere Vorschläge für eine fehlende DOI.** Korrekturen, Erwiderungen und
  Rückzugsnotizen gelten nicht mehr als das Werk selbst, und ein
  Zeitschriftenartikel geht seinem gleichnamigen Arbeitspapier oder Datensatz
  vor.
- **PubMed über den Titel.** Die Titelsuche fragt jetzt auch Europe PMC, das
  ganz PubMed enthält. So findet Benthic ältere medizinische Artikel ohne DOI
  und verlinkt ihre PubMed-Seite. Die Hinweise zu 1.1.4 kündigten diese Suche
  schon an; jetzt läuft sie.
- **Korrekturen.** Smith 2020a und Smith 2020b fallen bei der Prüfung einer
  Arbeit nicht mehr zusammen, die Liste der Datenbanken im Bericht nennt jetzt
  jede, die Benthic fragt, und die Tiefenanzeige beim ersten Start bleibt über
  der Wasseroberfläche nicht mehr stehen.

## 1.1.4

Sechs Datenbanken in der Prüfkaskade, kommentierte Word-Dateien und bereinigte
`.bib`-Dateien für Abschlussarbeiten sowie Import aus EndNote und Citavi.

- **Sechs Datenbanken bei jeder Prüfung.** Neben Crossref, OpenAlex, DataCite
  und dem Internet Archive fragt Benthic jetzt auch PubMed und Semantic Scholar
  über den Titel an, wenn eine medizinische oder informatiknahe Arbeit ohne DOI
  oder PMID zitiert ist.
- **Kommentierte Word-Dateien.** Nach der Prüfung einer `.docx`-Arbeit speichert
  Benthic auf Wunsch eine Kopie mit echten Word-Randkommentaren direkt an den
  zitierten Sätzen, fehlerhaften Einträgen, Jahresabweichungen und unbelegten
  Zahlenangaben, ohne die Formatierung zu verändern.
- **Bereinigte `.bib`-Dateien und ein interaktiver LaTeX-Dateigraph.** Für
  LaTeX-Projekte und `.bib`-Dateien exportiert Benthic eine bereinigte
  `.bib`-Datei, die alle `\cite{key}`-Schlüssel behält, geprüfte DOIs ergänzt
  und zurückgezogene oder unbelegte Einträge auskommentiert. Die Projektstruktur
  zeigt `\input`- und `\include`-Dateien als interaktiven SVG-Graphen.
- **Schreibhinweise für Abschlussarbeiten.** Die Dokumentprüfung weist auf
  Zweitzitate („zit. nach“, „quoted in“) sowie auf empirische oder statistische
  Aussagen ohne Quellenangabe hin.
- **Klarere Textstellen in Volltexten.** Bei der Suche nach Belegstellen im
  geladenen PDF hebt Benthic übereinstimmende Begriffe hervor, zeigt die
  Seitenzahl als Abzeichen und kopiert die Stelle samt Zitat mit einem Klick.
- **EndNote XML und Citavi.** Die Quellenprüfung und die lokale Bibliothek lesen
  jetzt auch EndNote-XML-Dateien (`.xml`) sowie Projekte aus Citavi 6 (`.ctv6`
  und `.ctv6archive`).

## 1.1.3

Eine Begrüßung für neue Nutzer:innen und ein Rundgang, und der Ton des Intros
endet jetzt sauber.

- **Eine Begrüßung beim ersten Start.** Die Einrichtung ist jetzt ein kurzer
  Tauchgang mit Tiefenmesser: Sprache, Kontakt, Download-Ordner und
  Bibliothek, jeder Schritt etwas tiefer.
- **Ein Rundgang.** Zwei Minuten an der echten Oberfläche, von einer
  Beispielprüfung bis zum Grund des Challengertiefs in 10.935 m. Jederzeit
  wieder über ⌘K oder die Einstellungen.
- **Der Schlussakkord des Intros klingt aus.** Der Ton am Ende des Intros
  bricht nicht mehr ab, wenn die App erscheint.

## 1.1.2

Ein Sicherheitsupdate nach einer Prüfung von 1.1.1: Benthic schützt seinen
lokalen Dienst und seine Updates jetzt strenger. Dazu spielt die App beim Start
ein Intro.

- **Webseiten können die App nicht mehr nutzen.** Der lokale Dienst von Benthic
  antwortet nur noch der App, der KI-Anbindung und dem Zotero-Plug-in. Eine
  Webseite im Browser kann keine Einstellungen mehr lesen, keine Prüfungen
  starten und deine Bibliothek nicht ändern. API-Keys zeigen die Einstellungen
  nur noch verdeckt.
- **Signierte Updates.** Jedes Update trägt jetzt eine Signatur, und die App
  installiert es nur, wenn sie zum eingebauten Schlüssel von Benthic passt.
- **Vorsicht bei fremden Daten.** Namen geladener PDFs, Links aus Metadaten,
  sehr große oder gepackte Dateien und Exporte für Tabellenprogramme können
  nichts mehr außerhalb des Download-Ordners schreiben, keine anderen Apps
  öffnen, Benthic nicht lahmlegen und keine Formeln ausführen. Links aus
  Literaturangaben öffnet Benthic nur, wenn sie ins öffentliche Internet führen.
- **Ein Intro beim Start.** Während Benthic startet, taucht die App von der
  Oberfläche bis zum leuchtenden tiefsten Punkt ab, mit Ton, und landet auf dem
  Zeichen in der Seitenleiste. Beim ersten Start läuft der ganze Abstieg, danach
  eine kurze Fassung. Escape überspringt es; mit reduzierter Bewegung gibt es
  keins.
- **Kein Link mehr oben im Fenster.** Der Link „Zum Inhalt springen“ aus 1.1.0
  ist wieder verborgen und erscheint erst, wenn man mit Tab dorthin springt.

## 1.1.1

Eine Korrektur für das Update in der App.

- **Updates kommen an.** Hatte ein KI-Assistent wie Claude Desktop den
  Hintergrunddienst von Benthic gestartet, nutzte die App nach einem Update
  weiter diesen älteren Dienst: Sie zeigte die alte Version und bot dasselbe
  Update erneut an. Jetzt ersetzt Benthic beim Start einen älteren Dienst.

## 1.1.0

Benthic hilft jetzt, mit seinen Ergebnissen weiterzuarbeiten, prüft mehr an
einer Abschlussarbeit und bringt zwei Begleiter mit: Grimpo auf deinem
Schreibtisch und ein Plug-in für Zotero. Drei Funktionen fragen neue Dienste an,
und nur mit dem, was sie brauchen: „Quellen finden“ schickt die eingetippte
Aussage an OpenAlex, ein toter Weblink wird im Internet Archive nachgeschlagen,
und ein Link, den du auf Grimpo ablegst, wird einmal geöffnet, um seine DOI zu
lesen.

- **Was tun, bei jedem Befund.** Jede auffällige Quelle sagt in klaren Worten,
  was zu tun ist, in der App und im Bericht. Für Angaben, die es nicht gibt,
  schlägt Benthic echte Arbeiten zum Thema vor (OpenAlex und Crossref, nie
  zurückgezogene), zum Lesen, nicht als Ersatz, den man ungelesen zitiert.
- **Ein korrigiertes Verzeichnis und ein Weg zum Volltext.** Der Reiter
  Zitierstil baut das ganze Literaturverzeichnis aus den echten Metadaten neu
  und hält zurückgezogene und unbestätigte Einträge getrennt. Gibt es von einem
  Artikel keine freie Fassung, entwirft Benthic eine höfliche Bitte an die
  korrespondierende Autorin oder den korrespondierenden Autor, und die
  Bibliothekseinstellung nimmt auch einen OpenURL-Linkresolver an.
- **Quellen finden.** Tippe eine Aussage ein, und Benthic sucht echte, nicht
  zurückgezogene Arbeiten mit DOI, die sie abdecken, mit der passendsten
  Stelle. Auf Macs mit Apple Intelligence kann das Modell auf dem Gerät
  einschätzen, ob eine Stelle die Aussage stützt.
- **Mehr Prüfungen für Abschlussarbeiten.** Zitate, deren Jahr vom Verzeichnis
  abweicht, werden gemeldet. Zitierte Webseiten werden geöffnet: Eine
  erreichbare Seite mit dem zitierten Titel gilt als bestätigt, und ein toter
  Link bekommt die nächstgelegene Kopie aus dem Internet Archive. Eine neue
  Prüfung vergleicht die Arbeit mit den Volltexten ihrer eigenen Quellen und
  listet Wortlaut, der ohne Anführungszeichen übernommen wurde; eine
  Plagiatsprüfung gegen das Web ist sie nicht.
- **Für Lehrende.** Die Stapelprüfung nimmt die ZIP-Datei „Alle Abgaben
  herunterladen“ einer Aufgabe aus Moodle oder ILIAS und zeigt ohne Namen, wie
  viele Abgaben dasselbe Problem haben und welche erfundenen Angaben in
  mehreren auftauchen.
- **Grimpo.** Ein kleiner Dumbo-Oktopus in einem schwebenden Fenster sammelt
  Bildschirmfotos (Text und DOIs erkennt dein Mac), Links, Text und den offenen
  Browser-Tab und führt dich je Projekt in neun Schritten durch eine Arbeit.
- **Zotero-Plug-in.** In Zotero 7 und 8 versieht „Mit Benthic prüfen“ die
  ausgewählten Einträge mit ihrem Urteil als Schlagwort. Es spricht nur mit
  Benthic auf deinem Mac und liegt als Datei auf der Release-Seite.
- **Ein gestalteter PDF-Bericht.** Ein Titelblatt, eine Übersicht mit Zahlen
  und dem, was du zuerst ansehen solltest, Inhalt und Legende der Urteile,
  dann kompakte Listen. App und Browser drucken dieselben Seiten.
- **Projekte als Datei.** „Als Datei teilen“ sichert ein Projekt für die
  Betreuung oder eine Lerngruppe, und „Projektdatei öffnen“ öffnet es in einem
  anderen Benthic, ohne Konto.
- **Mehr für KI-Assistenten.** Fünf neue Werkzeuge, 24 insgesamt. Hinweise in
  der Antwort eines Werkzeugs sind auf Englisch, auch wenn die App auf Deutsch
  läuft, und eine Antwort sagt Bescheid, wenn die Verbindung nach einem Update
  noch ein älteres Benthic nutzt.
- **Leichter mit Tastatur und Screenreader.** Ein Sprunglink, ein laufender
  Status während einer Prüfung und Pfeiltasten in Auswahlgruppen.

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
