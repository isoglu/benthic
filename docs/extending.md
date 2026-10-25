# Extending Benthic with plugins

Benthic's own download cascade is fixed: your library, then the open-access
sources, then (if nothing is known) your institution's proxy link. A plugin
can add **extra routes** after open access, for example your university's
link resolver, a lab file server or a licensed publisher API you have access
to. Extra routes run only when

1. no open-access copy is known for the DOI, and
2. the user has switched on **Zusatzwege** in the settings.

AI tools connected through MCP never use extra routes, and neither does
"fetch missing" in a project.

## Where plugins are loaded from

Plugins are code: whatever sits in these places runs inside Benthic, with your
user's rights, every time Benthic starts. Only add plugins you wrote yourself
or trust.

- **Your own plugins folder**, `plugins/` in Benthic's data folder:
  `~/Library/Application Support/Benthic/plugins` for the app. In Settings ›
  Sources and access › Plugins, click **Open plugins folder**, put a plugin in
  it (a package folder or a single `.py` file) and click **Restart Benthic**.
  The same card shows whether each plugin is active or why it failed. To switch
  a plugin off, remove it or start its name with `_`. Besides its own modules, a plugin here can import only the standard
  library and the packages Benthic ships with ([requirements.txt](../requirements.txt)); a
  `requirements.txt` next to it is not installed.
- `plugins/` next to the `benthic` package. `./macos/build_app.sh --plugin=DIR`
  copies a plugin there inside the app bundle, where it is covered by the
  bundle's signature and its `requirements.txt` is installed. The folder is
  ignored by git. With `run.py` this is the same folder as your own plugins
  folder, unless `BENTHIC_DATA_DIR` points elsewhere.
- The `BENTHIC_PLUGINS` environment variable (paths separated by `:`), for
  development with `run.py`.

`macos/sign_and_notarize.sh` refuses to sign a build that contains plugins.
Builds with bundled plugins are meant for your own Mac; give them their own
bundle identifier, data folder and port so they can run next to the official
app:

```bash
./macos/build_app.sh --plugin=../my-route --name="Benthic Lab" \
  --bundle-id=org.example.benthic-lab --data-folder="Benthic Lab" --port=8328 --out=dist-lab
```

## Writing a plugin

A plugin is a Python package (or a single `.py` file) with a `register(api)`
function. A `requirements.txt` in the package folder is installed into the
bundle at build time.

```python
from benthic import routes


class LabServer(routes.Route):
    id = "labserver"          # stored as the result's channel
    label = "Lab server"      # shown in the interface
    legal = True              # for the "Open Access" column in exports; None = unknown

    def targets(self):
        return ["files.lab.example"]   # names shown in the live dive

    def fetch(self, identifier, emit):
        emit("try", "files.lab.example")
        pdf = download_somehow(identifier)          # your code
        if pdf is None:
            raise routes.RouteMiss("not on the lab server")
        return routes.Hit(pdf, "https://files.lab.example/" + identifier, "files.lab.example")


def register(api):
    api.add_route(LabServer())
```

`api` offers:

| Call | Purpose |
|---|---|
| `api.add_route(route)` | Register a `routes.Route` |
| `api.add_blueprint(bp)` | Add your own Flask endpoints, preferably under `/api/plugins/<name>/` |
| `api.add_ui(directory)` | Serve `ui.js` (and optional `ui.css`) from that folder; the interface loads them |
| `api.data_dir` | A writable folder for the plugin's own data |

`fetch` returns a `routes.Hit(content, url, source)` or raises
`routes.RouteMiss(message, retryable=False)`. Any other exception is logged
and treated as a miss, so a broken plugin cannot stop a run.

## Interface modules

`ui.js` runs in the app's web view and can use `window.Benthic`:

| Member | Purpose |
|---|---|
| `addView({id, label, icon, html, before, onShow})` | Add a sidebar view (inserted before `before`, default `history`) |
| `addSettingsCard(html)` | Add a card to the settings view |
| `onSettings(fn)` | Called with the settings whenever they are loaded |
| `settings()`, `routes()` | Current settings and registered routes |
| `$`, `chip`, `toast`, `esc`, `hostOf`, `mkBtn` | Small helpers used by the interface itself |

Controls for extra routes (the switch in the settings and on the download
view, the badge in the sidebar) appear only when at least one route is
registered.
