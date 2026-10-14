"""Creation tools: 3D models, images, research, documents, simulations, coding."""

from __future__ import annotations

import json

import shutil
import time
from pathlib import Path
from typing import Any

from ..coding.agent import CodingAgent
from ..coding.files import FileOps
from ..coding.project import inspect_project
from ..creation3d.modify import ModificationError
from ..creation3d.planner import PlanningError, composite_prompt, parse_composite_reply
from ..creation3d.store import ExportFailed
from ..documents import writer
from ..images.providers import ImageUnavailable, image_size, placeholder_svg
from ..llm.ollama import LLMError, LLMUnavailable
from ..research.report import ResearchReport, analysis_messages, key_sentences
from ..research.web import FetchBlocked, ResearchUnavailable
from ..security.policy import Risk
from ..security.workspace import Workspace, WorkspaceViolation
from ..simulation.sims import SIMULATIONS, parse_params, pick_simulation, run_simulation
from .base import Arg, Tool, ToolContext, ToolError, ToolResult, ToolUnavailable


async def _llm_available(ctx: ToolContext) -> bool:
    st = await ctx.services.llm.status()
    return st.available


# --------------------------------------------------------------------- 3D
class Create3DTool(Tool):
    name = "creation3d.create"
    description = "Create a 3D model (GLB) from a description, e.g. 'a red sports car' or 'a three-storey house'."
    risk = Risk.LOW
    args = {"prompt": Arg("str", max_len=500), "allow_approximation": Arg("bool", False, default=False)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = ctx.services
        ctx.progress(None, "classifying the request")
        cls = s.classifier.classify(args["prompt"])
        if cls.status != "supported":
            noun = cls.unknown_object or "that object"
            supported = ", ".join(s.generators.supported_types())
            allow = args.get("allow_approximation") or cls.approximate_allowed
            if not allow:
                hint = f" Did you mean: {', '.join(cls.suggestions)}?" if cls.suggestions else ""
                return ToolResult(False, f"I don't have a generator for '{noun}', so I won't substitute a different object.{hint} "
                                         f"I can build: {supported}. Say 'make an approximate {noun}' for a labelled primitive blockout.",
                                  {"unsupported": noun, "supported": s.generators.supported_types()},
                                  needs_clarification=f"No generator for '{noun}'.")
            if not await _llm_available(ctx):
                return ToolResult(False, f"There is no dedicated generator for '{noun}', and an approximation needs the local language model, "
                                         "which isn't available.", {"unsupported": noun}, needs_clarification=f"No generator for '{noun}'.")
            ctx.progress(None, f"planning an approximate {noun} with the local model")
            try:
                reply = await s.llm.chat(composite_prompt(args["prompt"], noun), json_mode=True, max_tokens=1500, temperature=0.2)
                title, parts = parse_composite_reply(reply)
                spec = s.planner.plan_composite(args["prompt"], title, parts, noun)
            except (PlanningError, LLMError, LLMUnavailable, ValueError) as exc:
                raise ToolError(f"could not plan an approximation: {exc}") from None
        else:
            ctx.progress(None, f"building {cls.object_type} geometry")
            spec = s.planner.plan(cls)
        ctx.checkpoint()
        ctx.progress(None, "exporting and validating GLB")
        try:
            saved = s.models3d.create(spec, ctx.task_id)
        except ExportFailed as exc:
            raise ToolError(f"export validation failed: {exc}") from None
        s.bus.publish("model3d.updated", {"artifactId": saved.artifact.id, "version": spec.version, "objectType": spec.object_type})
        st = saved.report.stats
        label = " (approximation from primitives — not a dedicated or photoreal model)" if spec.approximation else " (procedural blockout)"
        return ToolResult(True, f"Created {spec.title}{label}: {len(spec.parts)} parts, {st['triangles']} triangles. Saved {saved.path.name}.",
                          saved.to_dict(), [saved.artifact.to_dict()], verified=saved.report.ok,
                          verification=f"GLB re-read from disk: {st['meshes']} meshes, required parts present: {saved.report.ok}")


class Modify3DTool(Tool):
    name = "creation3d.modify"
    description = "Modify an existing 3D model with an instruction ('make the body red', 'bigger wheels', 'more detailed')."
    risk = Risk.LOW
    args = {"artifact_id": Arg("str", max_len=32), "instruction": Arg("str", False, default="", max_len=500),
            "operations": Arg("list", False)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = ctx.services
        try:
            saved = s.models3d.load(args["artifact_id"])
        except (FileNotFoundError, ValueError):
            raise ToolError("model not found") from None
        if args.get("operations"):
            ops = args["operations"]
            parsed = None
        else:
            parsed = s.modifier.parse(saved.spec, args.get("instruction", ""))
            if parsed.clarification:
                return ToolResult(False, parsed.clarification, {}, needs_clarification=parsed.clarification)
            if parsed.undo:
                try:
                    restored = s.models3d.undo(args["artifact_id"])
                except ValueError as exc:
                    return ToolResult(False, str(exc), {})
                s.bus.publish("model3d.updated", {"artifactId": restored.artifact.id, "version": restored.spec.version})
                return ToolResult(True, f"Undid the last change (now version {restored.spec.version}).", restored.to_dict(),
                                  [restored.artifact.to_dict()], verified=restored.report.ok, verification="restored GLB re-validated")
            ops = parsed.operations
        data: dict[str, Any] = {"operations": ops}
        artifacts = []
        model_ops = [o for o in ops if o.get("op") not in ("camera", "export", "undo")]
        result_saved = saved
        if model_ops:
            try:
                new_spec = s.modifier.apply(saved.spec, model_ops)
                result_saved = s.models3d.save_modified(args["artifact_id"], new_spec, ctx.task_id)
            except (ModificationError, PlanningError) as exc:
                raise ToolError(str(exc)) from None
            except ExportFailed as exc:
                raise ToolError(f"modified model failed validation: {exc}") from None
            artifacts.append(result_saved.artifact.to_dict())
        data.update(result_saved.to_dict())
        view = next((o["view"] for o in ops if o.get("op") == "camera"), None)
        export = next((o for o in ops if o.get("op") == "export"), None)
        msgs = []
        if model_ops:
            msgs.append(f"Updated {result_saved.spec.title} to version {result_saved.spec.version}.")
        if parsed is not None and parsed.message:
            msgs.append(parsed.message)
        if view:
            data["view"] = view
            msgs.append(f"Camera set to the {view} view.")
        if export:
            try:
                path, rep = s.models3d.export(args["artifact_id"], export.get("format", "glb"))
            except (ExportFailed, ValueError) as exc:
                raise ToolError(f"export failed: {exc}") from None
            data["export"] = {"path": str(path), "validation": rep.to_dict()}
            msgs.append(f"Exported and validated {path.name}.")
        s.bus.publish("model3d.updated", {"artifactId": args["artifact_id"], "version": result_saved.spec.version, "view": view})
        ok = result_saved.report.ok and ("export" not in data or data["export"]["validation"]["ok"])
        return ToolResult(True, " ".join(msgs) or "No change.", data, artifacts, verified=ok,
                          verification="GLB re-validated after the change" if model_ops or export else "view change only (no file change)")


class Export3DTool(Tool):
    name = "creation3d.export"
    description = "Export a 3D model as a validated GLB file."
    risk = Risk.LOW
    args = {"artifact_id": Arg("str", max_len=32), "format": Arg("str", False, default="glb", choices=("glb",))}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            path, rep = ctx.services.models3d.export(args["artifact_id"], args.get("format", "glb"))
        except FileNotFoundError:
            raise ToolError("model not found") from None
        except (ExportFailed, ValueError) as exc:
            raise ToolError(f"export failed: {exc}") from None
        return ToolResult(rep.ok, f"Exported {path.name} ({rep.stats['bytes']} bytes, {rep.stats['triangles']} triangles).",
                          {"path": str(path), "validation": rep.to_dict()}, verified=rep.ok,
                          verification="exported file re-read, validated and reopened")


# ------------------------------------------------------------------ images
class GenerateImageTool(Tool):
    name = "image.generate"
    description = "Generate an image with the configured local image generator."
    risk = Risk.LOW
    args = {"prompt": Arg("str", max_len=1000), "width": Arg("int", False, default=768), "height": Arg("int", False, default=768),
            "source_artifact": Arg("str", False, max_len=32, description="edit an existing image artifact"),
            "strength": Arg("float", False, default=0.6)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = ctx.services
        init = None
        if args.get("source_artifact"):
            art = s.artifacts.get(args["source_artifact"])
            if art is None or art.kind != "image":
                raise ToolError("source image not found")
            init = Path(art.path)
        ctx.progress(None, "contacting the image generator")
        try:
            res = await s.images.generate(args["prompt"], s.settings.artifacts_dir / "images", width=args.get("width", 768),
                                          height=args.get("height", 768), init_image=init, strength=args.get("strength", 0.6))
        except ImageUnavailable as exc:
            return ToolResult(False, f"I can't generate images right now: {exc} Nothing was created.", {"kind": "unsupported"},
                              needs_clarification="")
        art = s.artifacts.add("image", args["prompt"][:80], res.path, task_id=ctx.task_id, validated=True,
                              meta={"kind": res.kind, "provider": res.provider, "width": res.width, "height": res.height, "prompt": args["prompt"]})
        return ToolResult(True, f"{'Edited' if res.kind == 'edited' else 'Generated'} a {res.width}×{res.height} image with {res.provider}.",
                          {"kind": res.kind, "path": str(res.path)}, [art.to_dict()], verified=True, verification="PNG decoded and saved")


class ImportImageTool(Tool):
    name = "image.import_reference"
    description = "Copy a user-supplied image from the workspace into artifacts as a reference image."
    risk = Risk.LOW
    args = {"path": Arg("str")}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            src = ctx.services.workspace.resolve(args["path"], must_exist=True)
        except (WorkspaceViolation, FileNotFoundError) as exc:
            raise ToolError(str(exc)) from None
        data = src.read_bytes()
        try:
            fmt, w, h = image_size(data)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        out = ctx.services.settings.artifacts_dir / "images" / f"ref-{int(time.time())}-{src.name}"
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        art = ctx.services.artifacts.add("image", src.name, out, task_id=ctx.task_id, validated=True,
                                         meta={"kind": "reference", "width": w, "height": h, "format": fmt})
        return ToolResult(True, f"Imported reference image {src.name} ({w}×{h}). This is your image, not a generated one.",
                          {"kind": "reference", "path": str(out)}, [art.to_dict()], verified=True, verification="image decoded")


class PlaceholderImageTool(Tool):
    name = "image.placeholder"
    description = "Create a clearly labelled PLACEHOLDER graphic (only when the user asks for a placeholder)."
    risk = Risk.LOW
    args = {"label": Arg("str", max_len=80)}
    model_callable = False

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        path = ctx.services.settings.artifacts_dir / "images" / f"placeholder-{writer.slugify(args['label'])}.svg"
        writer.write_svg(path, placeholder_svg(args["label"]))
        art = ctx.services.artifacts.add("image", f"Placeholder: {args['label']}", path, task_id=ctx.task_id, validated=True,
                                         meta={"kind": "placeholder"})
        return ToolResult(True, "Created a labelled placeholder graphic (not a generated image).", {"kind": "placeholder", "path": str(path)},
                          [art.to_dict()], verified=True, verification="SVG written and re-read")


# ---------------------------------------------------------------- research
class WebResearchTool(Tool):
    name = "research.web"
    description = "Search the web, read the top pages, and save a cited research report."
    risk = Risk.LOW
    args = {"query": Arg("str", max_len=300), "max_sources": Arg("int", False, default=4)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = ctx.services
        q = args["query"]
        ctx.progress(0.05, "searching the web")
        try:
            results = await s.web.search(q, limit=max(1, min(args.get("max_sources", 4) + 2, 8)))
        except ResearchUnavailable as exc:
            return ToolResult(False, f"I couldn't search the web: {exc}. No online research was performed.", {"searched": False})
        if not results:
            return ToolResult(False, "The search returned no results; no pages were read.", {"searched": True, "results": []})
        report = ResearchReport(q, [], {}, search_results=[r.__dict__ for r in results])
        want = max(1, min(args.get("max_sources", 4), 6))
        for r in results:
            if len(report.sources) >= want:
                break
            ctx.checkpoint()
            ctx.progress(0.1 + 0.6 * len(report.sources) / want, f"reading {r.url[:80]}")
            try:
                page = await s.web.fetch(r.url)
            except (ResearchUnavailable, FetchBlocked) as exc:
                report.errors.append(f"{r.url}: {exc}")
                continue
            report.sources.append(page)
            report.excerpts[len(report.sources)] = key_sentences(page.text, q, 4) or ([r.snippet] if r.snippet else [])
        if not report.sources:
            return ToolResult(False, "I found search results but couldn't read any of the pages.", {"errors": report.errors})
        if await _llm_available(ctx):
            ctx.progress(0.8, "writing an analysis with the local model")
            try:
                report.analysis = await s.llm.chat(analysis_messages(q, report.sources, report.excerpts), max_tokens=700, temperature=0.2)
                report.analysis_by = s.settings.llm.model
            except (LLMError, LLMUnavailable) as exc:
                report.errors.append(f"analysis skipped: {exc}")
        slug = f"{time.strftime('%Y%m%d-%H%M%S')}-{writer.slugify(q)}"
        md, js = report.save(s.settings.artifacts_dir / "research", slug)
        art = s.artifacts.add("research", f"Research: {q}", md, task_id=ctx.task_id, validated=md.exists() and js.exists(),
                              meta={"sources": [p.url for p in report.sources], "json": str(js), "analysisBy": report.analysis_by})
        # Sync to the Mac UI and paired phones: only URLs that were actually fetched, never model-written links.
        summary = report.analysis.strip() if report.analysis else " ".join(next(iter(report.excerpts.values()), []))
        findings = [{"source": i, "text": sent[:300]} for i, sents in sorted(report.excerpts.items()) for sent in sents[:2]][:8]
        limits = [f"Couldn't read: {e[:160]}" for e in report.errors[:4]]
        if report.analysis:
            limits.append("The summary was written by the local model from these sources; check key facts against the links.")
        else:
            limits.append("No local model was available, so the summary is quoted from the sources.")
        s.bus.publish("research.result", {"taskId": ctx.task_id or "", "query": q, "summary": summary[:1200],
                                          "summaryKind": "local-model analysis" if report.analysis else "source excerpts",
                                          "sources": [{"title": pg.title[:200], "url": pg.url} for pg in report.sources],
                                          "findings": findings, "limitations": limits})
        cited = "\n".join(f"[{i}] {pg.title[:90]} — {pg.url}" for i, pg in enumerate(report.sources, 1))
        return ToolResult(True, f"Read {len(report.sources)} sources and saved a cited report ({md.name})."
                                + ("" if report.analysis else " No local model was available, so the report has excerpts only.")
                                + f"\nSources:\n{cited}",
                          {"report": report.to_json(), "markdown": report.to_markdown()[:6000]}, [art.to_dict()],
                          verified=md.exists(), verification="report saved with source URLs", untrusted=True)


# --------------------------------------------------------------- documents
class WriteDocumentTool(Tool):
    name = "documents.write"
    description = "Write a Markdown document (technical note, report). Uses the local model to draft when no content is given."
    risk = Risk.LOW
    args = {"title": Arg("str", max_len=200), "content": Arg("str", False, max_len=200_000), "brief": Arg("str", False, max_len=2000)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = ctx.services
        content = args.get("content") or ""
        drafted_by = ""
        if not content:
            if not await _llm_available(ctx):
                return ToolResult(False, "Drafting a document needs the local language model, which isn't available. "
                                         "Provide the content, or start Ollama.", {}, needs_clarification="")
            ctx.progress(None, "drafting with the local model")
            try:
                content = await s.llm.chat([{"role": "system", "content": "Write clear, well-structured Markdown technical documents. "
                                                                          "Do not invent facts, measurements or citations; mark assumptions."},
                                            {"role": "user", "content": f"Title: {args['title']}\nBrief: {args.get('brief') or args['title']}"}],
                                           max_tokens=1200)
            except (LLMError, LLMUnavailable) as exc:
                raise ToolError(str(exc)) from None
            drafted_by = s.settings.llm.model
        path = s.settings.artifacts_dir / "documents" / f"{writer.slugify(args['title'])}.md"
        if path.exists():
            path = path.with_name(f"{path.stem}-{int(time.time())}.md")
        meta = {"title": args["title"], "created": time.strftime("%Y-%m-%d %H:%M"), "author": "BAYMAX" + (f" (drafted by {drafted_by})" if drafted_by else "")}
        writer.write_markdown(path, args["title"], content, meta)
        art = s.artifacts.add("document", args["title"], path, task_id=ctx.task_id, validated=True, meta={"draftedBy": drafted_by})
        return ToolResult(True, f"Saved {path.name}" + (f" (drafted by {drafted_by}; please review)." if drafted_by else "."),
                          {"path": str(path)}, [art.to_dict()], verified=True, verification="file written and re-read")


class DiagramTool(Tool):
    name = "documents.diagram"
    description = "Create a flow diagram (SVG + Mermaid) from steps like 'plan -> build -> test'."
    risk = Risk.LOW
    args = {"title": Arg("str", max_len=200), "steps": Arg("str", max_len=4000)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        steps = writer.parse_steps(args["steps"])
        if len(steps) < 2:
            return await self._graph(ctx, args)
        base = ctx.services.settings.artifacts_dir / "diagrams" / writer.slugify(args["title"])
        svg = writer.write_svg(base.with_suffix(".svg"), writer.flow_diagram_svg(steps, args["title"]))
        mmd = base.with_suffix(".mmd")
        mmd.write_text(writer.mermaid_flow(steps), encoding="utf-8")
        art = ctx.services.artifacts.add("diagram", args["title"], svg, task_id=ctx.task_id, validated=True, meta={"steps": steps, "mermaid": str(mmd)})
        return ToolResult(True, f"Created a {len(steps)}-step diagram ({svg.name}).", {"steps": steps, "path": str(svg)}, [art.to_dict()],
                          verified=True, verification="SVG written and re-read")


    async def _graph(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        """No explicit steps: draw the real BAYMAX architecture, or let the local model propose a validated graph."""
        from ..documents import architecture

        topic = f"{args['title']} {args['steps']}".strip()
        source = ""
        if architecture.is_architecture_request(topic):
            title, layers, edges = "BAYMAX system architecture", architecture.LAYERS, architecture.EDGES
            source = "components and connections of this repository"
        elif await _llm_available(ctx):
            try:
                reply = await ctx.services.llm.chat([
                    {"role": "system", "content": "Design a clear diagram. Reply with JSON only: {\"title\": str, \"layers\": [[node, ...], ...], "
                                                  "\"edges\": [[from, to, label], ...]}. 2-5 layers left to right, at most 4 nodes per layer, "
                                                  "short node names, edges only between listed nodes."},
                    {"role": "user", "content": topic}], json_mode=True, max_tokens=700, temperature=0.2)
                data = json.loads(reply)
                layers = [[str(n)[:60] for n in col][:4] for col in data["layers"]][:5]
                names = {n for col in layers for n in col}
                edges = [(str(a), str(b), str(lab)[:30]) for a, b, lab in (e + [""] * (3 - len(e)) for e in data.get("edges", []))
                         if str(a) in names and str(b) in names]
                title = str(data.get("title") or args["title"])[:80]
                if len(names) < 2:
                    raise ValueError("too few nodes")
                source = f"structure proposed by the local model ({ctx.services.settings.llm.model})"
            except (LLMError, LLMUnavailable, ValueError, KeyError, TypeError) as exc:
                q = f"I couldn't work out the diagram's parts ({exc}). Which components or steps should it show?"
                return ToolResult(False, q, {}, needs_clarification=q)
        else:
            q = ("Which parts should the diagram show? List them like 'client -> API -> database' "
                 "(my local model is offline, so I won't invent the content).")
            return ToolResult(False, q, {}, needs_clarification=q)
        base = ctx.services.settings.artifacts_dir / "diagrams" / writer.slugify(title)
        svg = writer.write_svg(base.with_suffix(".svg"), writer.graph_svg(title, layers, edges))
        mmd = base.with_suffix(".mmd")
        mmd.write_text(writer.mermaid_graph(layers, edges), encoding="utf-8")
        art = ctx.services.artifacts.add("diagram", title, svg, task_id=ctx.task_id, validated=True, meta={"mermaid": str(mmd), "source": source})
        n = sum(len(c) for c in layers)
        return ToolResult(True, f"Created “{title}” ({n} components, {len(edges)} connections; {source}): {svg}", {"path": str(svg), "mermaid": str(mmd)},
                          [art.to_dict()], verified=svg.exists(), verification="SVG written and re-read")


# -------------------------------------------------------------- simulation
class SimulationTool(Tool):
    name = "simulation.run"
    description = f"Run a software simulation ({', '.join(SIMULATIONS)}) and save telemetry, CSV and a chart. Output is simulated, not measured."
    risk = Risk.LOW
    args = {"model": Arg("str", False, max_len=40), "params": Arg("dict", False), "request": Arg("str", False, max_len=500)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        name = args.get("model") or pick_simulation(args.get("request", ""))
        if not name or name not in SIMULATIONS:
            return ToolResult(False, f"Which simulation? Available: {', '.join(SIMULATIONS)}.", {},
                              needs_clarification="Which simulation should I run?")
        params = dict(args.get("params") or {})
        if args.get("request"):
            params = {**parse_params(name, args["request"]), **params}
        ctx.progress(None, f"simulating {name}")
        try:
            tel = run_simulation(name, params)
        except (ValueError, TypeError) as exc:
            raise ToolError(str(exc)) from None
        base = ctx.services.settings.artifacts_dir / "simulations" / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        writer.write_json(base.with_suffix(".json"), tel.to_dict())
        writer.write_csv(base.with_suffix(".csv"), tel.series)
        keys = [k for k in tel.series if k != "t"][:3]
        svg = writer.line_chart_svg(tel.series["t"], {k: tel.series[k] for k in keys}, title=tel.title, x_label="time (s)",
                                    y_label=", ".join(f"{k} ({tel.units.get(k, '')})" for k in keys), note="SIMULATED DATA")
        writer.write_svg(base.with_suffix(".svg"), svg)
        art = ctx.services.artifacts.add("simulation", tel.title, base.with_suffix(".json"), task_id=ctx.task_id, validated=True,
                                         meta={"source": "simulated", "csv": str(base.with_suffix(".csv")), "chart": str(base.with_suffix(".svg")),
                                               "summary": tel.summary, "model": name})
        ctx.services.bus.publish("telemetry", {"artifactId": art.id, "source": "simulated", "model": name, "title": tel.title,
                                               "series": {k: v[:: max(1, len(v) // 300)] for k, v in tel.series.items()}, "units": tel.units})
        summ = ", ".join(f"{k}={v}" for k, v in tel.summary.items())
        return ToolResult(True, f"Simulated {tel.title} ({summ}). These are simulated values, not sensor readings.",
                          {"telemetry": {**tel.to_dict(), "series": {k: v[:: max(1, len(v) // 300)] for k, v in tel.series.items()}}},
                          [art.to_dict()], verified=True, verification="simulation produced finite telemetry; files written")


# ------------------------------------------------------------------ coding
class _RepoTool(Tool):
    def repo(self, ctx: ToolContext, path: str) -> Workspace:
        p = Path(path).expanduser()
        root = ctx.services.workspace
        target = p if p.is_absolute() else root.root / p
        target = target.resolve()
        if not target.is_dir():
            raise ToolError(f"repository not found: {path}")
        return Workspace(target)

    def classify(self, args):
        path = Path(args.get("repo", ".")).expanduser()
        target = path if path.is_absolute() else self._ws_root / path
        outside = not _inside(target, self._ws_root)
        return self.risk, self.categories | ({"outside_workspace"} if outside else set())

    _ws_root: Path = Path("/nonexistent")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class InspectRepoTool(_RepoTool):
    name = "coding.inspect"
    description = "Inspect a repository: languages, build systems, frameworks, test commands."
    risk = Risk.LOW
    args = {"repo": Arg("str", False, default=".")}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        ws = self.repo(ctx, args.get("repo", "."))
        info = inspect_project(ws.root)
        return ToolResult(True, info.summary(), info.to_dict(), verified=True, verification="repository scanned")


def _changes_code(goal: str) -> bool:
    from ..coding.agent import wants_code_change

    return wants_code_change(goal)


class CodingAgentTool(_RepoTool):
    name = "coding.agent"
    description = "Run tests in a repository and, with a goal, make bounded edits to reach it (backed up, re-tested)."
    risk = Risk.MEDIUM
    args = {"repo": Arg("str", False, default="."), "goal": Arg("str", False, default="", max_len=2000)}

    def describe(self, args):
        goal = args.get("goal") or ""
        if _changes_code(goal):
            return f"Let the coding agent EDIT files in {args.get('repo', '.')} to: {goal[:300]} (backups kept, tests re-run)"
        return f"coding agent in {args.get('repo', '.')}: {goal or 'run tests'}"

    def classify(self, args):
        # Running tests is routine; changing a repository's files is high-impact and needs your approval first.
        if _changes_code(args.get("goal") or ""):
            return Risk.HIGH, frozenset({"modify_repository"})
        return self.risk, self.categories

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = ctx.services
        ws = self.repo(ctx, args.get("repo", "."))
        files = FileOps(ws, s.settings.data_dir / "backups", ctx.task_id)
        chat = None
        if await _llm_available(ctx):
            async def chat(messages):
                return await s.llm.chat(messages, json_mode=True, max_tokens=2000, temperature=0.1)
        agent = CodingAgent(s.processes, timeout=s.settings.agent.command_timeout_s, max_output=s.settings.agent.max_command_output_bytes,
                            max_attempts=s.settings.agent.max_repair_attempts)
        report = await agent.run(ws.root, files, args.get("goal", ""), chat, ctx.progress, ctx.checkpoint)
        ok = report.status in ("tests_passed", "fixed", "inspected", "no_tests", "changed_untested")  # environment_error is a failure
        verified = report.status in ("tests_passed", "fixed")
        if report.changes and not verified:
            ok = report.status == "changed_untested"  # changes exist but nothing proved them: reported as unverified, never "done"
        doc = s.settings.artifacts_dir / "code" / f"{time.strftime('%Y%m%d-%H%M%S')}-{ctx.task_id}-report.json"
        writer.write_json(doc, report.to_dict())
        art = s.artifacts.add("code", f"Coding report: {ws.root.name}", doc, task_id=ctx.task_id, validated=True, meta={"status": report.status})
        return ToolResult(ok, report.summary(), report.to_dict(), [art.to_dict()], verified=verified,
                          verification=(f"tests executed: {report.final.command} -> exit {report.final.returncode}" if report.final else "no tests executed"))


def all_creation_tools(workspace_root: Path) -> list[Tool]:
    _RepoTool._ws_root = workspace_root
    return [Create3DTool(), Modify3DTool(), Export3DTool(), GenerateImageTool(), ImportImageTool(), PlaceholderImageTool(), WebResearchTool(),
            WriteDocumentTool(), DiagramTool(), SimulationTool(), InspectRepoTool(), CodingAgentTool()]


__all__ = ["all_creation_tools", "ToolUnavailable"]
