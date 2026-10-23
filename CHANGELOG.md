# Changelog

All notable changes to Benthic, newest first. Version numbers follow
[semantic versioning](https://semver.org). The bold lead-ins of the newest
version become the update notice inside the app; the website shows this file as
its version history. The German twin is [CHANGELOG.de.md](CHANGELOG.de.md).

## 1.1.6

Every check report now carries a signed seal that anyone can verify,
institutions can hand out their rules as one file, and a thesis check shows
signs of manipulation.

- **A seal anyone can check.** Every PDF report carries a signed seal with a
  QR code on its cover and its number in every footer. The QR code opens
  benthic.isoglu.com/v, which shows whether the report is unchanged since it
  was made and looks up each DOI again at Crossref and DataCite, in the
  reader's own browser. A thesis file dropped there is compared with the
  report by its fingerprint. The seal proves that a report is unchanged, not
  who made it.
- **House rules from your institution.** A library or writing center can hand
  out one file with its name, proxy link, contact address, OpenAlex key,
  required citation style and ranking lists. Load it under Settings › Sources
  and access; Benthic takes nothing else from it, and the house style then
  decides the style check.
- **Signs of manipulation.** A thesis check shows invisible characters inside
  words, letters from another script that look Latin, text hidden in a Word
  file (hidden, white or tiny) and tortured phrases, the odd synonyms a
  paraphrasing tool leaves behind. Each finding shows where it is, to check in
  the file; it is never a score.

## 1.1.5

Benthic now makes sure a downloaded PDF is the paper you cited, says which
version of it you have, compares every detail of a reference with the record,
and finds older PubMed articles by their title.

- **The right PDF, or none.** Before Benthic checks a quotation or borrowed
  wording against a downloaded PDF, it looks for the cited DOI or title on the
  first pages. A file that turns out to be a different paper is not saved and
  not used, and Benthic names the paper it actually is.
- **Which version you are reading.** Preprints, working papers, accepted
  manuscripts, technical reports, summaries and paywall previews are named as
  such. A quotation missing from a summary, a preview or a slide deck reads
  "Not in this copy" instead of counting as not found.
- **Quotations found in another source.** When a quotation is not in the cited
  paper but appears word for word in another paper of the same check, Benthic
  says where it really comes from.
- **Every detail compared.** First author, author order, volume, issue and pages
  are compared with the record and shown next to the verdict, without changing
  it, and so is a cited page outside the article's page range. Names in other
  spellings, such as Müller and Mueller, count as the same.
- **One DOI, one work.** In a reference list or a course folder, Benthic
  points out a DOI printed with different details and two works given the same
  pages in one volume.
- **Better suggestions for a missing DOI.** Corrections, replies and retraction
  notices are no longer offered as the work itself, and a journal article wins
  over its working paper or dataset of the same title.
- **PubMed by title.** The title search now asks Europe PMC, which holds all of
  PubMed, so older medical articles without a DOI are found and linked to their
  PubMed page. The notes for 1.1.4 already announced this search; it now runs.
- **Fixes.** Smith 2020a and Smith 2020b no longer merge in the check of a
  thesis, the report's list of databases now names every database Benthic asks,
  and the depth gauge of the first start no longer stalls above the surface.

## 1.1.4

Six bibliographic databases in the check cascade, annotated Word documents and
cleaned `.bib` files for theses, and imports from EndNote and Citavi.

- **Six databases for every check.** Alongside Crossref, OpenAlex, DataCite and
  the Internet Archive, Benthic now queries PubMed and Semantic Scholar by title
  when a medical or computer-science paper is cited without a DOI or PMID.
- **Annotated Word files.** When you check a `.docx` thesis, Benthic can export
  a copy with native Word margin comments right on the cited sentences,
  unreferenced entries, year mismatches and uncited statistical claims, without
  touching your formatting.
- **Cleaned `.bib` files and an interactive LaTeX file graph.** For LaTeX
  projects and `.bib` files, Benthic exports a cleaned `.bib` file that keeps
  your `\cite{key}` identifiers, fills in verified DOIs and comments out
  retracted or unconfirmed entries. The project structure view now renders an
  interactive SVG graph of `\input` and `\include` files.
- **Writing signals for theses.** Document checks now point out secondary
  citations ("quoted in", "zit. nach") and empirical or statistical statements
  that carry no citation nearby.
- **Clearer evidence in full texts.** When searching a downloaded PDF for a
  claim, Benthic highlights overlapping words and shows the page badge and a
  one-click copy button for the passage with its citation.
- **EndNote XML and Citavi.** Reference checks and the local library now read
  EndNote XML (`.xml`) as well as Citavi 6 projects (`.ctv6` and
  `.ctv6archive`).

## 1.1.3

A welcome for new users and a guided tour, and the intro's sound now ends
properly.

- **A welcome on the first start.** Setting up Benthic is now a short dive
  with a depth gauge: language, contact, download folder and library, each a
  little deeper.
- **A guided tour.** Two minutes on the real interface, from a live sample
  check down to the floor of the Challenger Deep at 10,935 m. Take it again
  any time from ⌘K or the settings.
- **The intro's last chord rings out.** The sound at the end of the intro no
  longer cuts off when the app appears.

## 1.1.2

A security update after a review of 1.1.1: Benthic now guards its local service
and its updates more strictly. The app also plays an intro when it starts.

- **Web pages can no longer use the app.** Benthic's local service answers only
  the app, its AI connection and the Zotero plug-in. A web page open in your
  browser can no longer read your settings, start checks or change your library.
  The settings show API keys only masked.
- **Signed updates.** Every update now carries a signature, and the app
  installs it only if the signature matches the key built into Benthic.
- **Careful with foreign data.** Names of downloaded PDFs, links from metadata,
  very large or packed files and spreadsheet exports can no longer write outside
  the download folder, open other apps, stall Benthic or run formulas. Links in
  references are opened only when they lead to the public internet.
- **An intro when the app starts.** While Benthic starts, it dives from the
  surface down to the lit deepest point, with sound, and lands on the logo in
  the sidebar. The first start shows the whole dive, later starts a short
  version. Escape skips it; with reduced motion turned on, there is none.
- **No stray link at the top.** The "Skip to content" link from 1.1.0 is hidden
  again and only appears when you press Tab.

## 1.1.1

A fix for the update inside the app.

- **Updates take effect.** If an AI assistant such as Claude Desktop had
  started Benthic's background service, the app kept using that older service
  after an update: it still showed the old version and offered the same update
  again. Benthic now replaces an older service when it starts.

## 1.1.0

Benthic now helps you act on what it finds, checks more of a thesis, and brings
two companions: Grimpo on your desk and a plug-in for Zotero. Three features
reach new services, and only with what they need: "Find sources" sends the
claim you type to OpenAlex, a dead web link is looked up at the Internet
Archive, and a link you drop on Grimpo is opened once to read its DOI.

- **What now, for every finding.** Each flagged source says in plain words what
  to do, in the app and in the report. For references that do not exist,
  Benthic suggests real papers on the topic (OpenAlex and Crossref, never
  retracted ones) as reading, not as replacements to cite unread.
- **A corrected list and a way to the full text.** The Citation style tab
  rebuilds the whole reference list from the real metadata and keeps retracted
  and unconfirmed entries apart. When a paper has no free version, Benthic
  drafts a polite request to the corresponding author, and the library setting
  also accepts an OpenURL link resolver.
- **Find sources.** Type a claim and Benthic looks for real, unretracted papers
  with a DOI that cover it, with the closest passage. On Macs with Apple
  Intelligence, the on-device model can add a hint whether a passage supports
  the claim.
- **More checks for a thesis.** Citations whose year differs from the list are
  flagged. Cited web pages are opened: a reachable page with the cited title
  counts as confirmed, and a dead link gets its nearest Internet Archive copy.
  A new check compares the thesis with the full texts of its own sources and
  lists wording taken over without quotation marks; it is not a web plagiarism
  check.
- **For lecturers.** Batch check takes the "Download all submissions" ZIP of a
  Moodle or ILIAS assignment and shows, without names, how many submissions
  share each problem and which invented references turn up in several of them.
- **Grimpo.** A small dumbo octopus in a floating window keeps screenshots
  (text and DOIs are recognised on your Mac), links, text and the open browser
  tab, and guides you through a paper in nine steps for each project.
- **Zotero plug-in.** In Zotero 7 and 8, "Check with Benthic" tags the selected
  items with their verdict. It talks only to Benthic on your Mac and comes as
  a file on the release page.
- **A designed PDF report.** A cover, an overview with figures and what to look
  at first, contents and the verdict legend, then compact listings. The app and
  the browser print the same pages.
- **Projects as a file.** "Share as a file" saves a project for a supervisor
  or a study group, and "Open project file" opens it in another Benthic,
  without an account.
- **More for AI assistants.** Five new tools, 24 in all. Notes in a tool's
  answer are in English even when the app runs in German, and an answer says
  so when the connection still runs an older Benthic after an update.
- **Easier with a keyboard and a screen reader.** A skip link, live status
  while a check runs and arrow keys in option groups.

## 1.0.1

Fixes and clearer settings. A correction to the 1.0.0 notes: projects export
bibliographies (BibTeX, RIS and formatted), not CSV or Excel; those are for
check reports, downloads, the history and batch checks.

- **University access for downloads.** When a paper has no free copy, the
  download result now links through your university library's proxy, as
  "Check only (with OA)" already did, also for AI assistants.
- **Correct versions in every style.** Vancouver works, and Harvard, MLA,
  Vancouver and DIN versions no longer carry empty editor fields. IEEE and
  Vancouver bibliographies are numbered, and DIN 1505 always uses German
  terms.
- **DIN lists from Word.** Surnames set in small caps are read as capitals, so
  a DIN 1505 reference list in a Word file is recognised as DIN.
- **Consistent answers for AI assistants.** Citation style notes in a tool's
  answer are in English, like the rest of the answer.
- **Clearer setup.** Setup and settings say that the contact e-mail goes to
  Unpaywall, Crossref and OpenAlex, and the setup no longer asks for a name,
  which no feature used.

## 1.0.0

First public release.

- **Check references.** Paste an answer from a language model, a reading list,
  BibTeX or RIS. Benthic finds every reference and gives it one of seven
  verdicts: verified, DOI corrected, deviates, uncertain, mismatch, not found
  or retracted, and shows which sources were asked and what they found.
- **Check a whole thesis.** PDF, Word, Markdown or LaTeX (a `.tex` file with
  its `.bib`, or the whole project as a `.zip`): every entry of the
  bibliography is checked and the list is compared with the citations in the
  text.
- **Quote check.** Every direct quotation is looked up word for word in the
  downloaded full text of its source, including the cited page.
- **Citation style.** The check report names the style the list follows (APA
  7, Harvard, Chicago, IEEE, Vancouver or DIN 1505), shows which entries break
  it and which fields are missing, and gives a correct version of each entry.
- **Find the passage.** For a confirmed source, Benthic searches the
  downloaded full text for the passages closest to your sentence, with page
  numbers, across languages on macOS 15 or later. Whether one supports the
  claim is yours to judge.
- **Rate a whole list.** Authenticity, the age of the sources, venues with
  OpenAlex metrics and JUFO levels, journals flagged by DOAJ, your own ranking
  lists, published versions of preprints and key papers you may be missing.
- **Fetch open-access papers.** Your library first, then eight open-access
  sources (arXiv, OpenAlex, Unpaywall, Europe PMC, Semantic Scholar, CORE,
  Elsevier and Crossref), each labelled as preprint, accepted manuscript or
  publisher version. Without a free copy, a link through your university
  library.
- **Retraction watch.** Once a day Benthic rechecks the sources of your
  projects and tells you about retractions, corrections and expressions of
  concern.
- **Batch check for lecturers.** A folder of submissions at once: one row and
  one PDF report per submission, export as CSV or Excel. The submissions stay
  on the Mac.
- **Projects and Zotero.** One project per research question, bibliographies
  in common citation styles, export as BibTeX, RIS, CSV or Excel, PDF reports,
  a file that is rechecked every time you save it, and Zotero in both
  directions.
- **Work with AI assistants.** Tools over MCP for Claude Desktop, Claude Code,
  ChatGPT with Codex, Codex CLI, Gemini CLI, Antigravity, Grok Build, Cursor,
  VS Code and LM Studio, most of them connected in one click.
- **Send from any app.** Services › Check References with Benthic, a
  bookmarklet for your browser, or a `benthic://` link.
- **Mac app.** English and German, universal for Apple Silicon and Intel,
  macOS 13 or later, dark and light appearance, the limits of every service
  kept, and updates that install from inside the app.
