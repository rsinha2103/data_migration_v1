"""The local web app (FastAPI + server-rendered pages). Start it with `dmc ui`."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text

from data_migration_concept.config import ConfigError
from data_migration_concept.config.models import SqlSource
from data_migration_concept.proposals import KIND_LABELS, KINDS, propose
from data_migration_concept.report import ReportError, collect_report
from data_migration_concept.transform.errors import TransformError
from data_migration_concept.transform.generator import q
from data_migration_concept.transform.preview import preview_structure
from data_migration_concept.ui import editor, mapping_sheet, pipeline, proposals_service, targets
from data_migration_concept.ui.editor import EditError
from data_migration_concept.ui.workspace import Ctx, Workspace, WorkspaceError, open_project

HERE = Path(__file__).parent
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}


# Starlette refuses forms with more than 1,000 fields by default. The mapping page posts about a
# dozen inputs per target field, so structures with a hundred or more fields (SAP tables have
# hundreds) need a higher limit. This is a local single-user tool, so the limit is generous.
MAX_FORM_FIELDS = 200_000


async def read_form(request: Request) -> Any:
    return await request.form(max_fields=MAX_FORM_FIELDS)


class Flash:
    """One-shot messages shown after a redirect."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def put(self, kind: str, title: str, lines: list[str] | None = None) -> str:
        token = secrets.token_urlsafe(8)
        if len(self._store) > 200:
            self._store.clear()
        self._store[token] = {"kind": kind, "title": title, "lines": lines or []}
        return token

    def pop(self, token: str | None) -> dict[str, Any] | None:
        return self._store.pop(token, None) if token else None


def create_app(workspace: Path) -> FastAPI:
    ws = Workspace(workspace)
    app = FastAPI(title="Data_Migration_Concept", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["fromjson"] = lambda s: json.loads(s or "[]")
    flash = Flash()
    pending = targets.PendingUploads()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # --- protection for a tool that writes files: local use only ----------------------

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Response:
        raw = request.headers.get("host") or ""
        host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.split(":")[0]
        if host.lower() not in LOCAL_HOSTS:
            return HTMLResponse("This tool only answers on localhost.", status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return HTMLResponse("Cross-site request refused.", status_code=403)
        return await call_next(request)

    # --- helpers ---------------------------------------------------------------------

    def page(request: Request, template: str, **context: Any) -> HTMLResponse:
        context.setdefault("flash", flash.pop(request.query_params.get("f")))
        context["request"] = request
        return templates.TemplateResponse(request, template, context)

    def back(url: str, kind: str, title: str, lines: list[str] | None = None) -> RedirectResponse:
        sep = "&" if "?" in url else "?"
        token = flash.put(kind, title, lines)
        base, _, anchor = url.partition("#")
        target = f"{base}{sep}f={token}" + (f"#{anchor}" if anchor else "")
        return RedirectResponse(target, status_code=303)

    def ok(url: str, title: str, lines: list[str] | None = None) -> RedirectResponse:
        return back(url, "ok", title, lines)

    def fail(url: str, title: str, lines: list[str] | None = None) -> RedirectResponse:
        return back(url, "error", title, lines)

    def project_dir(p: str) -> Path:
        return ws.project_path(p)

    def object_names(path: Path) -> list[str]:
        d = path / "objects"
        return sorted(x.name for x in d.iterdir() if x.is_dir()) if d.is_dir() else []

    def need_object(ctx: Ctx, o: str) -> None:
        if o not in ctx.project.objects:
            raise WorkspaceError(f"Unknown object '{o}'.")

    @app.exception_handler(WorkspaceError)
    async def workspace_error(request: Request, exc: WorkspaceError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "error.html", {"request": request, "title": "Not found", "lines": [str(exc)],
                                    "flash": None, "project": None}, status_code=404,
        )  # fmt: skip

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError) -> HTMLResponse:
        parts = request.url.path.split("/")
        project = parts[2] if len(parts) > 2 and parts[1] == "p" else None
        return templates.TemplateResponse(
            request, "error.html", {"request": request, "flash": None, "project": project,
                                    "title": "This project has problems", "lines": exc.errors,
                                    "files": project is not None}, status_code=422,
        )  # fmt: skip

    # --- home and projects -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html", projects=ws.list_projects(), workspace=ws.root,
                    project=None)  # fmt: skip

    @app.get("/how-it-works", response_class=HTMLResponse)
    def how(request: Request) -> HTMLResponse:
        return page(request, "how.html", project=None, steps=pipeline.DESCRIPTIONS)

    @app.post("/projects/create")
    def create_project(name: str = Form(...), title: str = Form("")) -> RedirectResponse:
        try:
            ws.create_project(name.strip(), title)
        except WorkspaceError as exc:
            return fail("/", "Could not create the project.", [str(exc)])
        return ok(f"/p/{name.strip()}", "Project created. Add your first object below.")

    @app.post("/projects/demo")
    def create_demo(name: str = Form("acme_demo"), defects: str = Form("")) -> RedirectResponse:
        try:
            ws.create_demo(name.strip(), defects=bool(defects))
        except WorkspaceError as exc:
            return fail("/", "Could not create the demo.", [str(exc)])
        return ok(
            f"/p/{name.strip()}", "Demo project created. Open an object and press 'Run everything'."
        )

    @app.post("/projects/add")
    def add_project(folder: str = Form(...)) -> RedirectResponse:
        try:
            info = ws.add_existing(folder)
        except WorkspaceError as exc:
            return fail("/", "Could not add the project.", [str(exc)])
        return ok(f"/p/{info.name}", "Project added.")

    @app.post("/p/{p}/forget")
    def forget(p: str) -> RedirectResponse:
        try:
            ws.forget(p)
        except WorkspaceError as exc:
            return fail(f"/p/{p}", "Could not remove it.", [str(exc)])
        return ok("/", "Removed from the list. The folder was not deleted.")

    @app.get("/p/{p}", response_class=HTMLResponse)
    def project_page(request: Request, p: str) -> HTMLResponse:
        path = project_dir(p)
        with open_project(path) as ctx:
            objects = []
            for name in sorted(ctx.project.objects):
                cards = pipeline.pipeline_state(ctx, name)
                objects.append({"name": name, "cards": cards,
                                "description": ctx.project.objects[name].config.description})  # fmt: skip
            return page(request, "project.html", project=p, ctx=ctx, objects=objects,
                        external=any(i.name == p and i.external for i in ws.list_projects()))  # fmt: skip

    @app.post("/p/{p}/objects/add")
    def add_object(p: str, name: str = Form(...), sheet: str = Form(""),
                   file: UploadFile = File(...)) -> RedirectResponse:  # fmt: skip
        path = project_dir(p)
        content = file.file.read(editor.MAX_UPLOAD + 1)
        try:
            editor.add_object(
                path, name.strip(), file.filename or "upload", content, sheet.strip() or None
            )
        except EditError as exc:
            return fail(f"/p/{p}", "Could not create the object.", exc.errors)
        return ok(f"/p/{p}/o/{name.strip()}/structures",
                  "Object created with its source. Next, define the target structure: "
                  "upload a template, paste a field list, or copy the source columns.")  # fmt: skip

    # --- flow ------------------------------------------------------------------------

    @app.get("/p/{p}/o/{o}", response_class=HTMLResponse)
    def flow(request: Request, p: str, o: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            cards = pipeline.pipeline_state(ctx, o)
            try:
                report = collect_report(ctx.runlog, ctx.project.config.name, o, max_rejects=5)
            except ReportError:
                report = None
            cfg = ctx.project.objects[o].config
            return page(request, "flow.html", project=p, obj=o, ctx=ctx, cards=cards, report=report,
                        sources=cfg.sources, structures=cfg.structures, loads=cfg.loads,
                        tab="flow")  # fmt: skip

    @app.post("/p/{p}/o/{o}/run/{step}")
    def run(p: str, o: str, step: str) -> RedirectResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            if step == "all":
                outcomes = pipeline.run_all(ctx, o)
            else:
                outcomes = [pipeline.run_step(ctx, o, step)]
        lines = []
        for out in outcomes:
            lines.append(f"{'OK' if out.ok else 'PROBLEM'} - {out.step}: {out.summary}")
            lines.extend(f"    {d}" for d in out.details[:12])
        good = all(x.ok for x in outcomes)
        make = ok if good else fail
        title = "Finished." if good else "Finished with problems - see below."
        return make(f"/p/{p}/o/{o}", title, lines)

    # --- sources ---------------------------------------------------------------------

    @app.get("/p/{p}/o/{o}/sources", response_class=HTMLResponse)
    def sources(request: Request, p: str, o: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            cfg = ctx.project.objects[o].config
            return page(request, "sources.html", project=p, obj=o, sources=cfg.sources,
                        connections=sorted(ctx.project.config.connections), tab="sources")  # fmt: skip

    @app.post("/p/{p}/o/{o}/sources/upload")
    def source_upload(
        p: str, o: str, sheet: str = Form(""), file: UploadFile = File(...)
    ) -> RedirectResponse:
        content = file.file.read(editor.MAX_UPLOAD + 1)
        try:
            editor.add_file_source(
                project_dir(p), o, file.filename or "upload", content, sheet.strip() or None
            )
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/sources", "Could not add the source.", exc.errors)
        return ok(f"/p/{p}/o/{o}/sources", "Source added. Run Extract to stage it.")

    @app.post("/p/{p}/o/{o}/sources/sql")
    def source_sql(p: str, o: str, name: str = Form(...), connection: str = Form(...),
                   table: str = Form(""), query: str = Form("")) -> RedirectResponse:  # fmt: skip
        try:
            editor.add_sql_source(project_dir(p), o, name, connection, table, query)
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/sources", "Could not add the source.", exc.errors)
        return ok(f"/p/{p}/o/{o}/sources", "Database source added.")

    @app.post("/p/{p}/o/{o}/sources/remove")
    def source_remove(p: str, o: str, name: str = Form(...)) -> RedirectResponse:
        try:
            editor.remove_source(project_dir(p), o, name)
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/sources", "Could not remove the source.", exc.errors)
        return ok(f"/p/{p}/o/{o}/sources", "Source removed.")

    # --- structures and mapping ------------------------------------------------------

    def source_columns(ctx: Ctx, o: str, source_name: str) -> list[str]:
        obj = ctx.project.objects[o]
        src = next((s for s in obj.config.sources if s.name == source_name), None)
        if src is None:
            raise EditError(f"Unknown source '{source_name}'.")
        if isinstance(src, SqlSource):
            table = f"{ctx.project.config.staging_prefix}{o}_{src.name}"
            if table not in inspect(ctx.engine).get_table_names():
                raise EditError(
                    "Run Extract once so the columns of this database source are known."
                )
            return [c["name"] for c in inspect(ctx.engine).get_columns(table)
                    if c["name"] not in ("_dmc_row_no", "_dmc_run_id")]  # fmt: skip
        return editor.file_columns(obj.root, src.model_dump(exclude_none=True))

    @app.get("/p/{p}/o/{o}/structures", response_class=HTMLResponse)
    def structures(request: Request, p: str, o: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            obj = ctx.project.objects[o]
            cfg = obj.config
            progress = {}
            for st in cfg.structures:
                mapping = editor.read_yaml(obj.root / st.mapping) if st.mapping else None
                progress[st.name] = editor.mapping_summary(mapping or {})
            return page(request, "structures.html", project=p, obj=o, structures=cfg.structures,
                        sources=cfg.sources, progress=progress, tab="structures")  # fmt: skip

    @app.get("/templates/target-structure.{ext}")
    def blank_template(ext: str) -> Response:
        if ext == "xlsx":
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            body = targets.blank_template_xlsx()
        elif ext == "csv":
            media, body = "text/csv", targets.blank_template_csv()
        else:
            raise WorkspaceError("Unknown template format.")
        return Response(
            body, media_type=media,
            headers={"Content-Disposition": f'attachment; filename="target-structure-template.{ext}"'},
        )  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/add")
    def structure_add(p: str, o: str, name: str = Form(...), target_table: str = Form(...),
                      source: str = Form(...)) -> RedirectResponse:  # fmt: skip
        try:
            with open_project(project_dir(p)) as ctx:
                need_object(ctx, o)
                columns = source_columns(ctx, o, source)
            editor.add_structure(
                project_dir(p), o, name.strip(), target_table.strip(), source,
                editor.mapping_for(columns),
            )  # fmt: skip
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/structures", "Could not add the structure.", exc.errors)
        return ok(
            f"/p/{p}/o/{o}/structures/{name.strip()}",
            "Structure created by copying the source columns (every field moves its own column).",
        )

    # --- target structure from a template --------------------------------------------

    def read_upload(file: UploadFile | None, pasted: str) -> tuple[str, bytes]:
        if file is not None and file.filename:
            content = file.file.read(targets.MAX_TEMPLATE_BYTES + 1)
            if len(content) > targets.MAX_TEMPLATE_BYTES:
                raise EditError("That file is too large for a template.")
            return file.filename, content
        if pasted.strip():
            return "pasted.csv", pasted.encode("utf-8")
        raise EditError("Choose a file or paste a list of fields.")

    def parse_roles(params: Any) -> dict[str, int | None] | None:
        found: dict[str, int | None] = {}
        for role in editor.ROLES:
            raw = params.get(f"role_{role}")
            if raw is not None:
                found[role] = int(raw) if str(raw).strip().isdigit() else None
        return found or None

    def parsed_pending(item: targets.Pending, params: Any) -> targets.ParsedTargets:
        return targets.parse_targets(
            item.filename, item.content, sheet=str(params.get("sheet") or "") or None,
            roles=parse_roles(params), default_name=str(params.get("structure_name") or ""),
        )  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/template")
    def template_upload(p: str, o: str, pasted: str = Form(""), structure_name: str = Form(""),
                        file: UploadFile | None = File(None)) -> RedirectResponse:  # fmt: skip
        back_url = f"/p/{p}/o/{o}/structures"
        try:
            filename, content = read_upload(file, pasted)
            item = targets.Pending(p, o, filename, content, "create")
            parsed_pending(item, {"structure_name": structure_name})  # fail early if unreadable
        except EditError as exc:
            return fail(back_url, "Could not read the target structure.", exc.errors)
        token = pending.put(item)
        sep = f"?structure_name={quote(structure_name)}" if structure_name.strip() else ""
        return RedirectResponse(f"{back_url}/template/{token}{sep}", status_code=303)

    @app.get("/p/{p}/o/{o}/structures/template/{token}", response_class=HTMLResponse)
    def template_preview(request: Request, p: str, o: str, token: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            cfg = ctx.project.objects[o].config
            try:
                item = pending.get(token, p, o)
                parsed = parsed_pending(item, request.query_params)
            except EditError as exc:
                return fail(f"/p/{p}/o/{o}/structures", "Could not use that file.", exc.errors)  # type: ignore[return-value]
            existing = {s.name for s in cfg.structures}
            used_tables = {s.target_table for s in cfg.structures}
            rows = []
            for i, st in enumerate(parsed.structures):
                table_name = editor.identifier(st.name.lower(), "t")
                clash = st.name in existing or table_name in used_tables
                rows.append({"i": i, "s": st, "table": table_name, "clash": clash,
                             "checked": (st.required or len(parsed.structures) == 1) and not clash,
                             "match": item.mode == "merge" and item.structure.lower()
                             in (st.name.lower(), st.label.lower())})  # fmt: skip
            return page(request, "target_preview.html", project=p, obj=o, token=token, item=item,
                        parsed=parsed, rows=rows, sources=cfg.sources, roles_all=editor.ROLES,
                        role_labels=editor.ROLE_LABELS, structure_name=request.query_params.get("structure_name", ""),
                        tab="structures")  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/template/{token}")
    async def template_confirm(request: Request, p: str, o: str, token: str) -> RedirectResponse:
        form = await read_form(request)
        back_url = f"/p/{p}/o/{o}/structures"
        try:
            item = pending.get(token, p, o)
            parsed = parsed_pending(item, form)
            if item.mode == "merge":
                pick = int(str(form.get("pick", "0")) or 0)
                if not 0 <= pick < len(parsed.structures):
                    raise EditError("Choose which structure of the file to use.")
                result = editor.merge_template(
                    project_dir(p), o, item.structure, parsed.structures[pick].fields
                )
                pending.drop(token)
                return ok(
                    f"{back_url}/{item.structure}",
                    f"Target structure updated: {result['added']} field(s) added, "
                    f"{result['updated']} detail(s) updated, "
                    f"{result['not_in_template']} field(s) not in the template (kept).",
                )
            chosen = []
            for i, st in enumerate(parsed.structures):
                if form.get(f"inc_{i}"):
                    chosen.append({
                        "name": str(form.get(f"name_{i}", st.name)).strip(),
                        "target_table": str(form.get(f"table_{i}", "")).strip(),
                        "source": str(form.get(f"source_{i}", "")),
                        "mapping": {"fields": st.fields},
                    })  # fmt: skip
            editor.add_structures(project_dir(p), o, chosen)
        except (EditError, ValueError) as exc:
            lines = exc.errors if isinstance(exc, EditError) else [str(exc)]
            return fail(f"{back_url}/template/{token}", "Nothing was created.", lines)
        pending.drop(token)
        count = len(chosen)
        return ok(
            back_url,
            f"{count} target structure(s) created. Open one to map its fields from the source.",
        )

    @app.post("/p/{p}/o/{o}/structures/{s}/template")
    def template_merge_upload(p: str, o: str, s: str, pasted: str = Form(""),
                              file: UploadFile | None = File(None)) -> RedirectResponse:  # fmt: skip
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            filename, content = read_upload(file, pasted)
            item = targets.Pending(p, o, filename, content, "merge", s)
            parsed_pending(item, {"structure_name": s})
        except EditError as exc:
            return fail(url, "Could not read the template.", exc.errors)
        token = pending.put(item)
        return RedirectResponse(
            f"/p/{p}/o/{o}/structures/template/{token}?structure_name={quote(s)}", status_code=303
        )

    @app.post("/p/{p}/o/{o}/structures/{s}/source")
    def structure_source(p: str, o: str, s: str, source: str = Form(...)) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            editor.set_structure_source(project_dir(p), o, s, source)
        except EditError as exc:
            return fail(url, "Source not changed.", exc.errors)
        return ok(url, "This structure now reads from that source. Re-run Extract and Transform.")

    @app.post("/p/{p}/o/{o}/structures/{s}/joins")
    async def join_add(request: Request, p: str, o: str, s: str) -> RedirectResponse:
        form = await read_form(request)
        url = f"/p/{p}/o/{o}/structures/{s}"
        pairs = [(str(form.get(f"main{i}", "")), str(form.get(f"other{i}", ""))) for i in range(3)]
        try:
            editor.add_join(
                project_dir(p),
                o,
                s,
                str(form.get("source", "")),
                pairs,
                str(form.get("type", "left")),
                str(form.get("on_duplicates", "error")),
            )
        except EditError as exc:
            return fail(url, "Join not added.", exc.errors)
        return ok(url, "Source joined. Its columns can now be used as source.COLUMN.")

    @app.post("/p/{p}/o/{o}/structures/{s}/joins/remove")
    def join_remove(p: str, o: str, s: str, source: str = Form(...)) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            editor.remove_join(project_dir(p), o, s, source)
        except EditError as exc:
            return fail(url, "Join not removed.", exc.errors)
        return ok(url, "Join removed; fields that used it are now not mapped.")

    @app.post("/p/{p}/o/{o}/structures/{s}/rowsets")
    async def rowset_add(request: Request, p: str, o: str, s: str) -> RedirectResponse:
        form = await read_form(request)
        url = f"/p/{p}/o/{o}/structures/{s}"
        pairs = [(str(form.get(f"main{i}", "")), str(form.get(f"other{i}", ""))) for i in range(3)]
        try:
            editor.add_row_set(
                project_dir(p),
                o,
                s,
                str(form.get("name", "")),
                str(form.get("source", "")),
                str(form.get("join_source", "")),
                pairs,
                str(form.get("type", "left")),
                str(form.get("on_duplicates", "error")),
            )
        except EditError as exc:
            return fail(url, "Row set not added.", exc.errors)
        return ok(url, "Row set added: its rows are put below the main rows (UNION ALL). "
                       "Give the fields that differ their own rule, then re-run Extract/Transform.")  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/{s}/rowsets/remove")
    def rowset_remove(p: str, o: str, s: str, name: str = Form(...)) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            editor.remove_row_set(project_dir(p), o, s, name)
        except EditError as exc:
            return fail(url, "Row set not removed.", exc.errors)
        return ok(url, "Row set removed.")

    @app.post("/p/{p}/o/{o}/structures/{s}/rowsets/{name}/rules")
    async def rowset_rules(request: Request, p: str, o: str, s: str, name: str) -> RedirectResponse:
        form = await read_form(request)
        url = f"/p/{p}/o/{o}/structures/{s}"
        indexes = sorted({int(m.group(1)) for k in form if (m := re.match(r"^o(\d+)_field$", k))})
        rules: list[tuple[str, dict[str, Any]]] = []
        try:
            for i in indexes:

                def get(key: str, i: int = i) -> str:
                    return str(form.get(f"o{i}_{key}", "") or "")

                rules.append((get("field"), editor.rule_from_form(
                    get("rule") or "MOVE", get("src"), get("a1"), get("a2"), get("a3"))))  # fmt: skip
            own = editor.save_row_set_rules(project_dir(p), o, s, name, rules)
        except (EditError, ValueError) as exc:
            lines = exc.errors if isinstance(exc, EditError) else [str(exc)]
            return fail(url, f"Row set '{name}' not saved.", lines)
        return ok(url, f"Row set '{name}' saved: {own} field(s) with their own rule, the rest "
                       "follow the main mapping. Re-run Transform to apply it.")  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/{s}/rowsets/override")
    def rowset_override(
        p: str, o: str, s: str, row_set: str = Form(...), field: str = Form(...),
        rule: str = Form("MOVE"), src: str = Form(""), a1: str = Form(""),
        a2: str = Form(""), a3: str = Form(""), remove: str = Form(""),
    ) -> RedirectResponse:  # fmt: skip
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            new_rule = None if remove else editor.rule_from_form(rule, src, a1, a2, a3)
            editor.set_override(project_dir(p), o, s, row_set, field, new_rule)
        except (EditError, ValueError) as exc:
            lines = exc.errors if isinstance(exc, EditError) else [str(exc)]
            return fail(url, "Rule not changed.", lines)
        return ok(
            url,
            f"{field} in '{row_set}' "
            + ("uses the main rule again." if remove else "has its own rule."),
        )

    @app.post("/p/{p}/o/{o}/structures/remove")
    def structure_remove(p: str, o: str, name: str = Form(...)) -> RedirectResponse:
        try:
            editor.remove_structure(project_dir(p), o, name)
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/structures", "Could not remove the structure.", exc.errors)
        return ok(f"/p/{p}/o/{o}/structures", "Structure removed (its loads too).")

    def mapping_context(ctx: Ctx, o: str, s: str) -> dict[str, Any]:
        obj = ctx.project.objects[o]
        st = next((x for x in obj.config.structures if x.name == s), None)
        if st is None:
            raise WorkspaceError(f"Unknown structure '{s}'.")
        return {"structure": st, "obj_root": obj.root}

    @app.get("/p/{p}/o/{o}/structures/{s}", response_class=HTMLResponse)
    def mapping_page(request: Request, p: str, o: str, s: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            info = mapping_context(ctx, o, s)
            st = info["structure"]
            rows: list[dict[str, Any]] = []
            if st.mapping:
                rows = editor.mapping_rows(editor.read_yaml(info["obj_root"] / st.mapping) or {})
            try:
                columns = source_columns(ctx, o, st.source)
            except EditError:
                columns = []
            summary = editor.mapping_summary(
                {
                    "fields": [
                        {
                            "mandatory": r["mandatory"],
                            "review": r["review"],
                            "rule": {"type": r["rule"]},
                        }
                        for r in rows
                    ]
                }
            )
            join_columns: dict[str, list[str]] = {}
            for j in st.joins:
                try:
                    join_columns[j.source] = source_columns(ctx, o, j.source)
                except EditError:
                    join_columns[j.source] = []
            all_columns = columns + [f"{a}.{c}" for a, cs in join_columns.items() for c in cs]
            joinable = [
                x.name
                for x in ctx.project.objects[o].config.sources
                if x.name != st.source and x.name not in join_columns
            ]
            union_info = []
            for u in st.unions:
                cols: list[str] = []
                try:
                    cols = source_columns(ctx, o, u.source)
                    for j in u.joins:
                        cols += [f"{j.source}.{c}" for c in source_columns(ctx, o, j.source)]
                except EditError:
                    pass
                own = {t: r.model_dump(exclude_none=True) for t, r in u.overrides.items()}
                main_fields = (
                    editor.read_yaml(info["obj_root"] / st.mapping) or {} if st.mapping else {}
                ).get("fields", [])
                grid = editor.mapping_rows({
                    "fields": [
                        {**f, "rule": own.get(f["target_field"], f.get("rule", {}))}
                        for f in main_fields
                    ]
                })  # fmt: skip
                for r in grid:
                    r["own"] = r["target"] in own
                union_info.append({
                    "name": u.name, "source": u.source, "joins": u.joins, "columns": cols,
                    "rows": grid, "own": len(own),
                })  # fmt: skip
            return page(request, "mapping.html", project=p, obj=o, structure=st, rows=rows,
                        columns=columns, all_columns=all_columns, join_columns=join_columns,
                        joinable=joinable, unions=union_info,
                        all_sources=ctx.project.objects[o].config.sources,
                        target_names=[r["target"] for r in rows], xrefs=editor.list_xrefs(project_dir(p), o),
                        rule_types=editor.RULE_TYPES, data_types=editor.DATA_TYPES,
                        summary=summary, sources=ctx.project.objects[o].config.sources,
                        tab="structures")  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/{s}")
    async def mapping_save(request: Request, p: str, o: str, s: str) -> RedirectResponse:
        form = await read_form(request)
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            mapping = editor.mapping_from_form(form)
            if form.get("action") == "add_missing":
                with open_project(project_dir(p)) as ctx:
                    need_object(ctx, o)
                    cols = source_columns(ctx, o, mapping_context(ctx, o, s)["structure"].source)
                used = {f["rule"].get("source_field") for f in mapping["fields"]}
                used |= {c for f in mapping["fields"] for c in f["rule"].get("source_fields", [])}
                extra = [c for c in cols if c not in used]
                mapping["fields"] += editor.mapping_for(extra)["fields"]
            mapped_by_name = 0
            if form.get("action") == "auto_map":
                with open_project(project_dir(p)) as ctx:
                    need_object(ctx, o)
                    cols = source_columns(ctx, o, mapping_context(ctx, o, s)["structure"].source)
                mapped_by_name = editor.auto_map_by_name(mapping, cols)
            editor.save_mapping(project_dir(p), o, s, mapping)
        except EditError as exc:
            return fail(url, "Mapping not saved.", exc.errors)
        if form.get("action") == "auto_map":
            return ok(url, f"{mapped_by_name} field(s) mapped by matching names. Check them, then "
                           "re-run Transform.")  # fmt: skip
        return ok(url, "Mapping saved. Re-run Transform to apply it.")

    # --- preview of the result before running -------------------------------------------

    @app.get("/p/{p}/o/{o}/structures/{s}/preview", response_class=HTMLResponse)
    def mapping_preview(request: Request, p: str, o: str, s: str, rows: int = 10) -> HTMLResponse:
        result = None
        problem = ""
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            st = mapping_context(ctx, o, s)["structure"]
            try:
                result = preview_structure(
                    ctx.project, ctx.project.objects[o], st, ctx.engine, rows
                )
            except TransformError as exc:
                problem = str(exc)
        return page(request, "mapping_preview.html", project=p, obj=o, structure=s, result=result,
                    problem=problem, rows=rows, tab="structures")  # fmt: skip

    # --- mapping specification for business review (Excel) -----------------------------

    def current_mapping(ctx: Ctx, o: str, s: str) -> dict[str, Any]:
        need_object(ctx, o)
        st = mapping_context(ctx, o, s)["structure"]
        if not st.mapping:
            raise WorkspaceError(
                f"Structure '{s}' is built by custom SQL and has no mapping table."
            )
        return editor.read_yaml(ctx.project.objects[o].root / st.mapping) or {}  # type: ignore[no-any-return]

    def current_row_sets(ctx: Ctx, o: str, s: str) -> dict[str, dict[str, Any]]:
        """Row set name -> its overrides as plain rule dicts."""
        st = mapping_context(ctx, o, s)["structure"]
        return {
            u.name: {t: r.model_dump(exclude_none=True) for t, r in u.overrides.items()}
            for u in st.unions
        }

    @app.get("/p/{p}/o/{o}/structures/{s}/spec.xlsx")
    def spec_download(p: str, o: str, s: str) -> Response:
        with open_project(project_dir(p)) as ctx:
            mapping = current_mapping(ctx, o, s)
            overrides = current_row_sets(ctx, o, s)
            st = mapping_context(ctx, o, s)["structure"]
            reads = {
                u.name: f"reads {u.source}"
                + "".join(
                    f", joined with {j.source} on "
                    + ", ".join(f"{k.main} = {k.other}" for k in j.keys)
                    for j in u.joins
                )
                for u in st.unions
            }
        rules = [f.get("rule", {}) for f in mapping.get("fields", [])]
        rules += [r for ov in overrides.values() for r in ov.values()]
        used = {r.get("xref") for r in rules if r.get("type") == "XREF"}
        lists = {
            n: editor.read_xref(project_dir(p), o, n) for n in editor.list_xrefs(project_dir(p), o)
            if n in used
        }  # fmt: skip
        row_sets = [
            {"name": n, "reads": reads[n], "rows": mapping_sheet.row_set_rows(mapping, ov)}
            for n, ov in overrides.items()
        ]
        body = mapping_sheet.export_xlsx(
            editor.mapping_rows(mapping), f"{o} / {s}", lists, row_sets
        )
        return Response(
            body,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="mapping-{o}-{s}.xlsx"'},
        )

    def spec_diff(p: str, o: str, s: str, filename: str, content: bytes) -> mapping_sheet.SheetDiff:
        with open_project(project_dir(p)) as ctx:
            mapping = current_mapping(ctx, o, s)
            row_sets = current_row_sets(ctx, o, s)
        return mapping_sheet.diff_sheet(filename, content, mapping, row_sets)

    @app.post("/p/{p}/o/{o}/structures/{s}/spec")
    def spec_upload(p: str, o: str, s: str, file: UploadFile = File(...)) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/structures/{s}"
        content = file.file.read(editor.MAX_UPLOAD + 1)
        try:
            spec_diff(p, o, s, file.filename or "spec.xlsx", content)  # fail early
        except EditError as exc:
            return fail(url, "Could not read the mapping file.", exc.errors)
        token = pending.put(targets.Pending(p, o, file.filename or "spec.xlsx", content, "spec", s))
        return RedirectResponse(f"{url}/spec/{token}", status_code=303)

    @app.get("/p/{p}/o/{o}/structures/{s}/spec/{token}", response_class=HTMLResponse)
    def spec_preview(request: Request, p: str, o: str, s: str, token: str) -> HTMLResponse:
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            item = pending.get(token, p, o)
            diff = spec_diff(p, o, s, item.filename, item.content)
        except EditError as exc:
            return fail(url, "Could not read the mapping file.", exc.errors)  # type: ignore[return-value]
        return page(request, "mapping_import.html", project=p, obj=o, structure=s, token=token,
                    diff=diff, filename=item.filename, tab="structures")  # fmt: skip

    @app.post("/p/{p}/o/{o}/structures/{s}/spec/{token}")
    def spec_apply(p: str, o: str, s: str, token: str) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/structures/{s}"
        try:
            item = pending.get(token, p, o)
            diff = spec_diff(p, o, s, item.filename, item.content)
            if not diff.changes:
                pending.drop(token)
                return ok(url, "Nothing to import - the file matches the mapping.")
            editor.save_mapping_and_row_sets(
                project_dir(p), o, s, diff.mapping, diff.row_set_overrides
            )
        except EditError as exc:
            return fail(url, "Import not applied.", exc.errors)
        pending.drop(token)
        return ok(url, f"{len(diff.changes)} change(s) imported. Re-run Transform to apply them.")

    # --- XREF ------------------------------------------------------------------------

    @app.get("/p/{p}/o/{o}/xref", response_class=HTMLResponse)
    def xref_list(request: Request, p: str, o: str) -> HTMLResponse:
        path = project_dir(p)
        with open_project(path) as ctx:
            need_object(ctx, o)
            used: dict[str, list[str]] = {}
            for st, mapping in ctx.project.objects[o].mappings.items():
                for f in mapping.fields:
                    x = getattr(f.rule, "xref", None)
                    if x:
                        used.setdefault(x, []).append(f"{st}.{f.target_field}")
            items = [{"name": n, "rows": len(editor.read_xref(path, o, n)), "used": used.get(n, [])}
                     for n in editor.list_xrefs(path, o)]  # fmt: skip
            return page(request, "xref_list.html", project=p, obj=o, items=items, tab="xref")

    @app.post("/p/{p}/o/{o}/xref/new")
    def xref_new(
        p: str, o: str, name: str = Form(...), content: str = Form("")
    ) -> RedirectResponse:
        try:
            editor.save_xref(project_dir(p), o, name.strip(), content)
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/xref", "Could not create the XREF.", exc.errors)
        return ok(f"/p/{p}/o/{o}/xref/{name.strip()}", "XREF created.")

    def unmapped_values(ctx: Ctx, o: str, name: str) -> list[str]:
        obj = ctx.project.objects[o]
        known = {a for a, _ in editor.read_xref(ctx.path, o, name)}
        found: list[str] = []
        existing = set(inspect(ctx.engine).get_table_names())
        for st in obj.config.structures:
            mapping = obj.mappings.get(st.name)
            table = f"{ctx.project.config.staging_prefix}{o}_{st.source}"
            if mapping is None or table not in existing:
                continue
            for f in mapping.fields:
                if getattr(f.rule, "xref", None) == name:
                    col = f.rule.source_field  # type: ignore[union-attr]
                    if "." in col:  # a joined source's column lives in another staging table
                        continue
                    sql = (f"SELECT DISTINCT CAST({q(col)} AS TEXT) FROM {q(table)} "
                           f"WHERE {q(col)} IS NOT NULL ORDER BY 1 LIMIT 2000")  # fmt: skip
                    with ctx.engine.connect() as conn:
                        for (v,) in conn.execute(text(sql)):
                            if v.strip() and v.strip() not in known and v.strip() not in found:
                                found.append(v.strip())
        return found

    @app.get("/p/{p}/o/{o}/xref/{name}", response_class=HTMLResponse)
    def xref_edit(request: Request, p: str, o: str, name: str) -> HTMLResponse:
        path = project_dir(p)
        with open_project(path) as ctx:
            need_object(ctx, o)
            rows = editor.read_xref(path, o, name)
            if not (path / "objects" / o / "xref" / f"{name}.csv").is_file():
                raise WorkspaceError(f"Unknown XREF '{name}'.")
            missing = unmapped_values(ctx, o, name)
            return page(request, "xref_edit.html", project=p, obj=o, name=name,
                        content=editor.xref_text(rows), missing=missing, tab="xref")  # fmt: skip

    @app.post("/p/{p}/o/{o}/xref/{name}")
    def xref_save(p: str, o: str, name: str, content: str = Form("")) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/xref/{name}"
        try:
            editor.save_xref(project_dir(p), o, name, content)
        except EditError as exc:
            return fail(url, "XREF not saved.", exc.errors)
        return ok(url, "XREF saved. Re-run Transform to apply it.")

    @app.post("/p/{p}/o/{o}/xref/{name}/upload")
    def xref_upload(p: str, o: str, name: str, file: UploadFile = File(...)) -> RedirectResponse:
        url = f"/p/{p}/o/{o}/xref/{name}"
        raw = file.file.read(5 * 1024 * 1024)
        try:
            editor.save_xref(project_dir(p), o, name, raw.decode("utf-8-sig", errors="replace"))
        except EditError as exc:
            return fail(url, "File not used.", exc.errors)
        return ok(url, "XREF replaced from the uploaded file.")

    @app.post("/p/{p}/o/{o}/xref/{name}/delete")
    def xref_delete(p: str, o: str, name: str) -> RedirectResponse:
        try:
            editor.delete_xref(project_dir(p), o, name)
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/xref/{name}", "Could not delete it.", exc.errors)
        return ok(f"/p/{p}/o/{o}/xref", "XREF deleted.")

    # --- checks, reconciliations, loads ---------------------------------------------

    def structure_rule_counts(ctx: Ctx, o: str) -> dict[str, int]:
        """How many target fields each kind of structure-based rule would apply to."""
        counts = dict.fromkeys(editor.FROM_MAPPING_KINDS, 0)
        for mapping in ctx.project.objects[o].mappings.values():
            for f in mapping.fields:
                if type(f.rule).__name__ == "NotMigratedRule":
                    continue
                counts["mandatory"] += bool(f.mandatory)
                counts["length"] += f.length is not None
                counts["data_type"] += f.data_type != "string"
                counts["key"] += bool(f.key)
        return counts

    def rules_page(request: Request, p: str, o: str, kind: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            cfg = ctx.project.objects[o].config
            checks = kind == "checks"
            return page(
                request, "rules.html", project=p, obj=o, kind=kind, tab=kind,
                entries=editor.list_entries(
                    project_dir(p), o, "checks.yaml" if checks else "recon.yaml",
                    "checks" if checks else "recons"),
                specs=editor.CHECK_SPECS if checks else editor.RECON_SPECS,
                common=[] if checks else editor.RECON_COMMON,
                structures=[s.name for s in cfg.structures],
                from_mapping=editor.read_from_mapping(project_dir(p), o),
                rule_counts=structure_rule_counts(ctx, o),
            )  # fmt: skip

    @app.get("/p/{p}/o/{o}/checks", response_class=HTMLResponse)
    def checks_page(request: Request, p: str, o: str) -> HTMLResponse:
        return rules_page(request, p, o, "checks")

    @app.get("/p/{p}/o/{o}/recon", response_class=HTMLResponse)
    def recon_page(request: Request, p: str, o: str) -> HTMLResponse:
        return rules_page(request, p, o, "recon")

    async def rules_add(request: Request, p: str, o: str, kind: str) -> RedirectResponse:
        form = await read_form(request)
        checks = kind == "checks"
        url = f"/p/{p}/o/{o}/{kind}"
        try:
            entry = editor.entry_from_form(
                form, str(form.get("type", "")),
                editor.CHECK_SPECS if checks else editor.RECON_SPECS,
                [] if checks else editor.RECON_COMMON,
            )  # fmt: skip
            editor.add_entry(project_dir(p), o, "checks.yaml" if checks else "recon.yaml",
                             "checks" if checks else "recons", entry)  # fmt: skip
        except EditError as exc:
            return fail(url, "Not added.", exc.errors)
        return ok(url, "Added. Re-run the step to see the effect.")

    @app.post("/p/{p}/o/{o}/checks/from-mapping")
    async def checks_from_mapping(request: Request, p: str, o: str) -> RedirectResponse:
        form = await read_form(request)
        wanted = {k: str(form.get(k, "off")) for k in editor.FROM_MAPPING_KINDS}
        url = f"/p/{p}/o/{o}/checks"
        try:
            editor.set_from_mapping(project_dir(p), o, wanted)
        except EditError as exc:
            return fail(url, "Not saved.", exc.errors)
        on = [k for k, v in wanted.items() if v in ("error", "warn")]
        return ok(url, "Saved. Confirmed rules: " + (", ".join(on) if on else "none") + ".")

    @app.post("/p/{p}/o/{o}/checks/add")
    async def checks_add(request: Request, p: str, o: str) -> RedirectResponse:
        return await rules_add(request, p, o, "checks")

    @app.post("/p/{p}/o/{o}/recon/add")
    async def recon_add(request: Request, p: str, o: str) -> RedirectResponse:
        return await rules_add(request, p, o, "recon")

    def rules_delete(p: str, o: str, kind: str, name: str) -> RedirectResponse:
        checks = kind == "checks"
        url = f"/p/{p}/o/{o}/{kind}"
        try:
            editor.delete_entry(project_dir(p), o, "checks.yaml" if checks else "recon.yaml",
                                "checks" if checks else "recons", name)  # fmt: skip
        except EditError as exc:
            return fail(url, "Not deleted.", exc.errors)
        return ok(url, "Deleted.")

    @app.post("/p/{p}/o/{o}/checks/delete")
    def checks_delete(p: str, o: str, name: str = Form(...)) -> RedirectResponse:
        return rules_delete(p, o, "checks", name)

    @app.post("/p/{p}/o/{o}/recon/delete")
    def recon_delete(p: str, o: str, name: str = Form(...)) -> RedirectResponse:
        return rules_delete(p, o, "recon", name)

    @app.get("/p/{p}/o/{o}/loads", response_class=HTMLResponse)
    def loads_page(request: Request, p: str, o: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            cfg = ctx.project.objects[o].config
            confirmed = editor.read_from_mapping(project_dir(p), o)
            explicit = editor.list_entries(project_dir(p), o, "checks.yaml", "checks")
            return page(request, "loads.html", project=p, obj=o, loads=cfg.loads,
                        gating={"rules": len(confirmed), "checks": len(explicit)},
                        structures=[s.name for s in cfg.structures],
                        connections=sorted(ctx.project.config.connections), tab="loads")  # fmt: skip

    @app.post("/p/{p}/o/{o}/loads/add")
    async def loads_add(request: Request, p: str, o: str) -> RedirectResponse:
        form = await read_form(request)
        url = f"/p/{p}/o/{o}/loads"
        try:
            editor.add_load(project_dir(p), o, editor.load_from_form(form))
        except EditError as exc:
            return fail(url, "Not added.", exc.errors)
        return ok(url, "Load destination added.")

    @app.post("/p/{p}/o/{o}/loads/delete")
    def loads_delete(p: str, o: str, index: int = Form(...)) -> RedirectResponse:
        try:
            editor.delete_load(project_dir(p), o, index)
        except EditError as exc:
            return fail(f"/p/{p}/o/{o}/loads", "Not deleted.", exc.errors)
        return ok(f"/p/{p}/o/{o}/loads", "Deleted.")

    # --- proposals: suggestions that are only applied when the user accepts them ------

    @app.get("/p/{p}/o/{o}/proposals", response_class=HTMLResponse)
    def proposals_page(request: Request, p: str, o: str) -> HTMLResponse:
        path = project_dir(p)
        with open_project(path) as ctx:
            need_object(ctx, o)
            facts = proposals_service.build_facts(ctx, o)
            dismissed = proposals_service.load_dismissed(path, o)
            everything = propose(facts)
            shown = [x for x in everything if x.id not in dismissed]
            hidden = [x for x in everything if x.id in dismissed]
            groups = [
                {"kind": k, "label": KIND_LABELS[k], "items": [x for x in shown if x.kind == k]}
                for k in KINDS
            ]
            gaps = [
                {
                    "structure": s.name,
                    "fields": [
                        f.name for f in s.fields if f.mandatory and f.rule.get("type") == "UNMAPPED"
                    ],
                }
                for s in facts.structures
            ]
            gaps = [g for g in gaps if g["fields"]]
            return page(request, "proposals.html", project=p, obj=o, gaps=gaps,
                        groups=[g for g in groups if g["items"]], total=len(shown), hidden=hidden,
                        extracted=bool(facts.profiles), structures=len(facts.structures),
                        tab="proposals")  # fmt: skip

    def selected_proposals(ctx: Ctx, o: str, ids: list[str]) -> list[Any]:
        """Re-create the proposals on the server and pick the accepted ones by id, so what is
        applied is always what the engine proposes, never what a form claims."""
        wanted = set(ids)
        return [x for x in propose(proposals_service.build_facts(ctx, o)) if x.id in wanted]

    @app.post("/p/{p}/o/{o}/proposals/apply")
    async def proposals_apply(request: Request, p: str, o: str) -> RedirectResponse:
        ids = [str(i) for i in (await read_form(request)).getlist("ids")]
        url = f"/p/{p}/o/{o}/proposals"
        if not ids:
            return fail(url, "Nothing was selected.")
        path = project_dir(p)
        with open_project(path) as ctx:
            need_object(ctx, o)
            chosen = selected_proposals(ctx, o, ids)
            if not chosen:
                return fail(url, "Those proposals are no longer available.")
            result = proposals_service.apply_proposals(path, o, chosen)
        lines = [f"applied: {t}" for t in result.applied]
        lines += [f"skipped: {t}" for t in result.skipped]
        lines += [f"refused: {t} - {'; '.join(errs)}" for t, errs in result.failed]
        if result.failed:
            return fail(url, f"{len(result.applied)} applied, {len(result.failed)} refused.", lines)
        return ok(
            url,
            f"{len(result.applied)} proposal(s) applied. Re-run Transform, then Check, to see "
            "their effect.",
            lines,
        )

    @app.post("/p/{p}/o/{o}/proposals/dismiss")
    async def proposals_dismiss(request: Request, p: str, o: str) -> RedirectResponse:
        ids = [str(i) for i in (await read_form(request)).getlist("ids")]
        url = f"/p/{p}/o/{o}/proposals"
        if not ids:
            return fail(url, "Nothing was selected.")
        n = proposals_service.dismiss(project_dir(p), o, ids)
        return ok(url, f"{n} proposal(s) dismissed. You can bring them back below.")

    @app.post("/p/{p}/o/{o}/proposals/restore")
    async def proposals_restore(request: Request, p: str, o: str) -> RedirectResponse:
        ids = [str(i) for i in (await read_form(request)).getlist("ids")]
        n = proposals_service.restore(project_dir(p), o, ids or None)
        return ok(f"/p/{p}/o/{o}/proposals", f"{n} proposal(s) brought back.")

    # --- data, results, SQL ----------------------------------------------------------

    @app.get("/p/{p}/o/{o}/data", response_class=HTMLResponse)
    def data_tables(request: Request, p: str, o: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            tables = pipeline.object_tables(ctx, o)
            counts = {}
            for t in tables:
                with ctx.engine.connect() as conn:
                    counts[t["table"]] = conn.execute(
                        text(f"SELECT COUNT(*) FROM {q(t['table'])}")
                    ).scalar_one()
            return page(request, "data.html", project=p, obj=o, tables=tables, counts=counts,
                        preview=None, tab="data")  # fmt: skip

    @app.get("/p/{p}/o/{o}/data/{table}", response_class=HTMLResponse)
    def data_preview(request: Request, p: str, o: str, table: str) -> HTMLResponse:
        needle = request.query_params.get("q", "")
        number = int(request.query_params.get("page", "1") or 1)
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            try:
                preview = pipeline.preview_table(ctx, o, table, page=number, needle=needle)
            except ValueError as exc:
                raise WorkspaceError(str(exc)) from exc
            return page(request, "data.html", project=p, obj=o, tables=pipeline.object_tables(ctx, o),
                        counts={}, preview=preview, tab="data")  # fmt: skip

    @app.get("/p/{p}/o/{o}/results", response_class=HTMLResponse)
    def results(request: Request, p: str, o: str) -> HTMLResponse:
        check = request.query_params.get("check", "")
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            try:
                report = collect_report(ctx.runlog, ctx.project.config.name, o, max_rejects=500)
            except ReportError:
                report = None
            rejects = [
                r
                for r in (report or {}).get("rejects", [])
                if not check or r["check_name"] == check
            ]
            return page(request, "results.html", project=p, obj=o, report=report, rejects=rejects,
                        check=check, tab="results")  # fmt: skip

    @app.get("/p/{p}/o/{o}/sql", response_class=HTMLResponse)
    def sql_page(request: Request, p: str, o: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            need_object(ctx, o)
            return page(request, "sql.html", project=p, obj=o, items=pipeline.generated_sql(ctx, o),
                        tab="sql")  # fmt: skip

    @app.get("/p/{p}/runs", response_class=HTMLResponse)
    def runs(request: Request, p: str) -> HTMLResponse:
        with open_project(project_dir(p)) as ctx:
            rows = []
            for run in ctx.runlog.list_runs(ctx.project.config.name, limit=60):
                steps = ctx.runlog.get_steps(run["run_id"])
                rows.append({**run, "steps": [s["step_name"] for s in steps],
                             "problem": any(s["status"] == "FAILED" for s in steps)})  # fmt: skip
            return page(request, "runs.html", project=p, runs=rows)

    # --- files -----------------------------------------------------------------------

    @app.get("/p/{p}/files", response_class=HTMLResponse)
    def files(request: Request, p: str) -> HTMLResponse:
        path = project_dir(p)
        rel = request.query_params.get("path", "")
        content = ""
        if rel:
            try:
                content = editor.read_raw(path, rel)
            except EditError as exc:
                raise WorkspaceError(str(exc)) from exc
        return page(request, "files.html", project=p, files=editor.editable_files(path), rel=rel,
                    content=content)  # fmt: skip

    @app.post("/p/{p}/files")
    def files_save(p: str, rel: str = Form(...), content: str = Form("")) -> RedirectResponse:
        url = f"/p/{p}/files?path={quote(rel)}"
        try:
            editor.save_raw(project_dir(p), rel, content)
        except EditError as exc:
            return fail(url, "Not saved - the file was left as it was.", exc.errors)
        return ok(url, "Saved.")

    @app.get("/p/{p}/download")
    def download(p: str, path: str, inline: int = 0) -> Response:
        base = project_dir(p).resolve()
        target = (base / path).resolve()
        allowed = [(base / "output").resolve(), (base / "reports").resolve()]
        if not target.is_file() or not any(target.is_relative_to(a) for a in allowed):
            raise WorkspaceError("That file is not available.")
        if inline and target.suffix == ".html":
            return HTMLResponse(
                target.read_text(encoding="utf-8"),
                headers={
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"
                },
            )
        return FileResponse(target, filename=target.name)

    return app
