---
name: benthic
description: Check reference lists for invented, misattributed and retracted sources, and fetch the real papers through open access, with the Benthic app for macOS. Use whenever you cite literature, write or review a bibliography, answer with academic sources, or the user pastes references, DOIs, BibTeX or RIS.
---

# Benthic

Benthic is a macOS app that checks academic references and fetches open-access papers into the
user's own library. Its MCP server is called `benthic`; the app must be installed
(https://benthic.isoglu.com/download/).

## When to use it

- Before presenting sources: run `verify_references` on your own reading list and drop or correct
  what it flags.
- When the user pastes a reference list, an LLM answer, BibTeX or RIS: check it the same way.
- When the user shares a whole thesis or paper (PDF, Word, or a LaTeX project with its .bib):
  `check_document` with the file or folder path. It checks every bibliography entry and lists works
  cited in the text but missing from the list, and entries never cited. Then `check_quotes` with the
  job_id compares every direct quotation with the full text of its source.
- Before you quote a source word for word: `check_quotes` with the DOI and the quote.
- When the user wants the papers: `fetch_papers` with the DOIs of verified sources.
- When a sentence cites a paper: `find_support` with the DOI and the sentence returns the closest
  passages with page numbers. Read them before saying the source supports the claim.

## How to work with it

1. `verify_references` with the full text of the references. Each one gets a verdict: verified,
   DOI corrected, deviates, uncertain, mismatch, not found or retracted.
2. Never present a reference as real unless `verify_references` confirmed it. Say plainly which
   ones are not found or retracted; never cite a retracted paper as evidence.
3. `fetch_papers` downloads open-access PDFs into the user's library. Benthic never bypasses
   paywalls; for the rest it offers the user's university proxy link.
4. `get_fulltext` reads a downloaded paper, with page markers for quoting.
5. Keep sources per research question with `create_project`, `list_projects` and
   `project_status`, and write the bibliography with `export_bibliography` (BibTeX, RIS, APA,
   Harvard, Chicago, IEEE or DIN 1505). `send_to_zotero` puts verified sources into the
   collection open in Zotero.
6. `suggest_missing_sources` lists works the user's sources cite often but the list lacks; verify
   any suggestion before recommending it. For lecturers, `check_folder` checks a folder of
   submissions.
7. Benthic rechecks project sources once a day. `retraction_watch` lists retractions,
   expressions of concern and corrections found since; `check_zotero_library` checks the whole
   Zotero library the same way.

Runs that take longer return a `job_id`; follow up with `get_job`. Everything also appears live in
the Benthic window, so the user can watch the check.
