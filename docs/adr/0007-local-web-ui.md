# ADR 0007: Local web UI

Status: accepted

## Decision
- `dmc ui` serves a FastAPI app on localhost with server-rendered Jinja pages and a small amount of vanilla JavaScript (show/hide fields, add a mapping row, confirm dialogs). Every page also works as a plain HTML form. HTMX was the original idea; adding it needs a vendored JS file and the pages do not need it yet.
- The UI is a thin layer over the library: it calls the same step functions as the CLI and reads the run log. No pipeline logic lives in the UI.
- A **workspace** folder holds projects. UI-created projects use a SQLite working database in the project folder unless the environment variable named in `project.yaml` is already set.
- **Guided editing writes the same YAML files** the CLI uses. Each write goes through `apply_changes`: files are written, the whole project is loaded, and if that adds a problem that did not exist before, the files (and any new folders) are restored and the problems are shown. A first object is created from an uploaded file with a mapping that moves every column, so the project is valid from the first step.
- The raw file editor is limited to known project file patterns (YAML, SQL, XREF CSV) and uses the same validation.
- Safety for a tool that writes files: it only answers for `localhost` host names, refuses cross-origin POSTs, confines uploads and file access to the project folder, limits uploads to 200 MB and known extensions, escapes all data in pages, and serves HTML reports with a restrictive Content-Security-Policy. It is single-user: one project operation at a time.

## Consequences
Saving from a form rewrites a YAML file without its comments. Long steps run inside the request, so the page waits until they finish. Multi-user access, authentication and background jobs are out of scope.
