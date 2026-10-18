# Contributing

Thanks for helping. Benthic is small, so the process is too.

- **Ideas:** open an issue with the **Idea** form. Ideas carry the `idea`
  label and appear on the [roadmap page](https://benthic.isoglu.com/roadmap/),
  where 👍 and 👎 on the issue count as votes.
- **Bugs:** open a **Bug report**: what you did, what you expected and what
  happened. For a wrong verdict, include the reference text and the DOI (no
  private files).
- **Code:** Benthic does not accept outside code contributions for now.
  Sinan Isoglu holds the copyright to all of it, which keeps the option of
  offering Benthic under other terms besides the AGPL. To suggest a change,
  describe it in an issue. Pull requests will be closed without review.
- **Your own copy:** the AGPL lets you change Benthic and share your
  version under the same licence. Before you share a build, run the tests and
  the trace check:

  ```bash
  .venv/bin/python -m unittest discover -s tests -t .
  ./scripts/trace_check.sh
  ```

- **Open access only.** Benthic fetches papers through open access and the
  user's own institutional access. Benthic will not add sources outside
  these routes.
- **Style:** match the surrounding code. The interface and code comments are
  German; the README and these docs are English.
- **Dependencies:** a new bundled package needs an entry in
  `THIRD_PARTY_NOTICES.md` (`scripts/make_notices.py` regenerates it after a
  build); `macos/build_app.sh` fails if one is missing.

Benthic is licensed under the AGPL-3.0; see [LICENSE](LICENSE).
