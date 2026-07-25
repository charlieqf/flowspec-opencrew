from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any


TOOL_ID = "12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate"
TOOL_NAME = "Shot Plan D Codex Image Gen First Frame Generate"
TOOL_VERSION = "1.0.0"
DEFAULT_VARIANT_ID = "variant_001"
PROMPT_PACKAGE_VERSION = "final_v1"


class ToolError(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def resolve_workspace_path(workspace: Path, value: str | None) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else workspace / path


def load_source_package(workspace: Path) -> dict[str, Any]:
    for path in (workspace / "source_package.json", workspace / "rebuild" / "source_package.json"):
        if path.exists():
            payload = read_json(path)
            return payload if isinstance(payload, dict) else {}
    return {}


def analysis_workspace_from_source_package(workspace: Path, source_package: dict[str, Any], plan: dict[str, Any] | None = None) -> Path | None:
    source = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
    for value in (source.get("analysis_workspace"), source_package.get("analysis_workspace")):
        raw = str(value or "").strip()
        if raw:
            path = Path(raw).expanduser()
            return path if path.is_absolute() else workspace / path
    if isinstance(plan, dict):
        session_id = str(plan.get("analysis_session_id") or "").strip()
        if session_id:
            return workspace.parents[1] / session_id / "workspace"
    return None


def resolve_reference_image(workspace: Path, source_package: dict[str, Any], value: str | None, plan: dict[str, Any] | None = None) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    workspace_candidate = workspace / raw
    if workspace_candidate.exists():
        return workspace_candidate
    if raw.startswith("source.analysis_workspace/"):
        analysis_workspace = analysis_workspace_from_source_package(workspace, source_package, plan)
        if analysis_workspace:
            return analysis_workspace / raw.removeprefix("source.analysis_workspace/")
    analysis_workspace = analysis_workspace_from_source_package(workspace, source_package, plan)
    if analysis_workspace:
        analysis_candidate = analysis_workspace / raw
        if analysis_candidate.exists():
            return analysis_candidate
    return workspace_candidate


def workspace_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists() or not path.is_dir():
        raise ToolError(f"Workspace directory not found: {path}")
    return path.resolve()


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "").strip()


def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("scene_id") or mark.get("id") or "").strip()


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
    return [item for item in marks if isinstance(item, dict)]


def find_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any]:
    shot = next((item for item in plan.get("shots") or [] if isinstance(item, dict) and shot_id_of(item) == shot_id), None)
    if not shot:
        raise ToolError(f"Shot not found in rebuild_shot_plan.json: {shot_id}")
    return shot


def variant_scene_dir(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str) -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id) / safe_name(scene_mark_id)


def first_frame_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str) -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "first.png"


def scene_asset_manifest_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str) -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "asset_manifest.json"


def final_prompt_package_path(workspace: Path, shot: dict[str, Any], shot_id: str, variant_id: str) -> Path:
    ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
    rel_path = str(ref.get("path") or f"Assets/{variant_id}/{safe_name(shot_id)}/final_prompt_package.json").strip()
    return resolve_workspace_path(workspace, rel_path) or (workspace / rel_path)


def scene_package_rows(package: dict[str, Any], scene_mark_id: str = "") -> list[dict[str, Any]]:
    rows = [item for item in package.get("scenes") or [] if isinstance(item, dict)]
    if scene_mark_id:
        rows = [item for item in rows if str(item.get("scene_mark_id") or "") == scene_mark_id]
        if not rows:
            raise ToolError(f"Scene not found in final prompt package: {scene_mark_id}")
    return rows


def codex_prompt(job: dict[str, Any]) -> str:
    return "\n\n".join([
        "Use case: identity-preserve",
        "Asset type: Plan D replacement first frame for a vertical 9:16 short-video Scene",
        "Primary request:\nGenerate the new Scene first-frame image using the model prompt below.",
        "Input images:\n"
        f"- Image 1, SOURCE_FIRST_FRAME: {job['source_reference_image_path']} ; this is the current Task/current Scene editable base frame. Preserve its composition, camera angle, room geometry, head/shoulder placement, pose category, hand positions, product positions, scale, perspective, occlusion, lighting, shadows, and phone-video texture. Do not redesign the room or recompose the shot.\n"
        f"- Image 2, HOST_REFERENCE: {job['host_reference_path']} ; use only for current Task host identity and appearance consistency.\n"
        f"- Image 3, PRODUCT_REFERENCE: {job['product_reference_path']} ; use only for current Task product/package identity consistency. Match the packaging identity exactly rather than making a similar green product.",
        "Reference interpretation:\nUse SOURCE_FIRST_FRAME as the base scene, not as a loose inspiration image. Replace only the old host identity and old product identity. Keep the original camera, background, hand/product placement, scale, perspective, occlusion, shadows, and casual phone-video feel unless the model prompt explicitly says otherwise.",
        f"Model prompt:\n{job['image_prompt']}",
        "Output constraints:\nReturn one realistic vertical 9:16 bitmap image. No subtitles, no watermark, no UI overlay, no unrelated logo, no added claim text. If the room, framing, product position, product package identity, or hand occlusion is redesigned, the result is invalid. Save the selected final image to the requested output path after generation.",
    ]).strip()


def dependency_report(workspace: Path, source_package: dict[str, Any], plan: dict[str, Any], shot: dict[str, Any], package: dict[str, Any], shot_id: str, scene_mark_id: str, variant_id: str) -> dict[str, Any]:
    refs = package.get("references") if isinstance(package.get("references"), dict) else {}
    host_path = resolve_reference_image(workspace, source_package, str(refs.get("host_image") or ""), plan)
    product_path = resolve_reference_image(workspace, source_package, str(refs.get("product_image") or ""), plan)
    scenes = []
    for scene in scene_package_rows(package, scene_mark_id):
        scene_id = str(scene.get("scene_mark_id") or "").strip()
        source = resolve_reference_image(workspace, source_package, str(scene.get("reference_image") or ""), plan)
        output = first_frame_path(workspace, shot_id, scene_id, variant_id)
        scenes.append({
            "scene_mark_id": scene_id,
            "source_reference_image": str(scene.get("reference_image") or "").strip(),
            "source_reference_image_path": str(source) if source else "",
            "source_reference_exists": bool(source and source.exists() and source.is_file()),
            "output_image": rel(workspace, output),
            "output_exists": output.exists() and output.is_file(),
            "image_prompt_len": len(str(scene.get("image_prompt") or "")),
        })
    return {
        "workspace": str(workspace),
        "shot_id": shot_id,
        "variant_id": variant_id,
        "final_prompt_package_version": package.get("prompt_package_version"),
        "host_reference": {"rel_path": str(refs.get("host_image") or ""), "path": str(host_path) if host_path else "", "exists": bool(host_path and host_path.exists() and host_path.is_file())},
        "product_reference": {"rel_path": str(refs.get("product_image") or ""), "path": str(product_path) if product_path else "", "exists": bool(product_path and product_path.exists() and product_path.is_file())},
        "scenes": scenes,
        "scene_count": len(scene_marks_for_shot(shot)),
    }


def blocking_errors(dependencies: dict[str, Any], package: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if dependencies.get("final_prompt_package_version") != PROMPT_PACKAGE_VERSION:
        errors.append(f"final_prompt_package.json is not {PROMPT_PACKAGE_VERSION}.")
    if not dependencies.get("host_reference", {}).get("exists"):
        errors.append(f"Missing host reference image: {dependencies.get('host_reference', {}).get('rel_path')}")
    if not dependencies.get("product_reference", {}).get("exists"):
        errors.append(f"Missing product reference image: {dependencies.get('product_reference', {}).get('rel_path')}")
    if not dependencies.get("scenes"):
        errors.append("No scenes found in final_prompt_package.json.")
    for scene in dependencies.get("scenes") or []:
        if not scene.get("source_reference_exists"):
            errors.append(f"Missing source first-frame reference for {scene.get('scene_mark_id')}: {scene.get('source_reference_image')}")
        if not scene.get("image_prompt_len"):
            errors.append(f"Missing image_prompt for {scene.get('scene_mark_id')}.")
    return errors


def build_jobs(workspace: Path, dependencies: dict[str, Any], package: dict[str, Any], shot_id: str, scene_mark_id: str, variant_id: str, force: bool) -> list[dict[str, Any]]:
    refs = package.get("references") if isinstance(package.get("references"), dict) else {}
    deps_by_scene = {str(item.get("scene_mark_id") or ""): item for item in dependencies.get("scenes") or []}
    jobs = []
    for scene in scene_package_rows(package, scene_mark_id):
        scene_id = str(scene.get("scene_mark_id") or "").strip()
        scene_deps = deps_by_scene.get(scene_id, {})
        output = first_frame_path(workspace, shot_id, scene_id, variant_id)
        status = "ready"
        if output.exists() and not force:
            status = "skipped_existing"
        job = {
            "job_id": f"{TOOL_ID}:{shot_id}:{scene_id}",
            "status": status,
            "shot_id": shot_id,
            "scene_mark_id": scene_id,
            "mode": "codex_builtin_image_gen",
            "use_case": "identity-preserve",
            "source_reference_image": str(scene.get("reference_image") or "").strip(),
            "source_reference_image_path": str(scene_deps.get("source_reference_image_path") or ""),
            "host_reference": str(refs.get("host_image") or "").strip(),
            "host_reference_path": str(dependencies.get("host_reference", {}).get("path") or ""),
            "product_reference": str(refs.get("product_image") or "").strip(),
            "product_reference_path": str(dependencies.get("product_reference", {}).get("path") or ""),
            "image_prompt": str(scene.get("image_prompt") or "").strip(),
            "output_image": rel(workspace, output),
            "asset_manifest": rel(workspace, scene_asset_manifest_path(workspace, shot_id, scene_id, variant_id)),
        }
        job["codex_imagegen_prompt"] = codex_prompt(job)
        jobs.append(job)
    return jobs


def sync_reference_image_paths(package: dict[str, Any], dependencies: dict[str, Any]) -> bool:
    scenes = package.get("scenes") if isinstance(package.get("scenes"), list) else []
    deps_by_scene = {str(item.get("scene_mark_id") or ""): item for item in dependencies.get("scenes") or []}
    changed = False
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_mark_id") or "").strip()
        scene_deps = deps_by_scene.get(scene_id, {})
        next_paths = {
            "target_frame": str(scene_deps.get("source_reference_image_path") or ""),
            "host_reference": str(dependencies.get("host_reference", {}).get("path") or ""),
            "product_reference": str(dependencies.get("product_reference", {}).get("path") or ""),
        }
        if scene.get("reference_image_paths") != next_paths:
            scene["reference_image_paths"] = next_paths
            changed = True
    return changed


def prepare_jobs(workspace: Path, plan_path: Path, shot_id: str, scene_mark_id: str, variant_id: str, force: bool, write_outputs: bool = True) -> dict[str, Any]:
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        raise ToolError("rebuild_shot_plan.json must be a JSON object.")
    source_package = load_source_package(workspace)
    shot = find_shot(plan, shot_id)
    package_path = final_prompt_package_path(workspace, shot, shot_id, variant_id)
    if not package_path.exists():
        raise ToolError(f"Missing final prompt package: {rel(workspace, package_path)}")
    package = read_json(package_path)
    if not isinstance(package, dict):
        raise ToolError(f"final_prompt_package.json must be a JSON object: {package_path}")
    dependencies = dependency_report(workspace, source_package, plan, shot, package, shot_id, scene_mark_id, variant_id)
    errors = blocking_errors(dependencies, package)
    if errors:
        return {"status": "blocked", "tool": TOOL_ID, "blocking_errors": errors, "dependencies": dependencies}
    package_reference_paths_updated = sync_reference_image_paths(package, dependencies)
    jobs = build_jobs(workspace, dependencies, package, shot_id, scene_mark_id, variant_id, force)
    generated_at = now_ms()
    for job in jobs:
        prompt_path = variant_scene_dir(workspace, shot_id, job["scene_mark_id"], variant_id) / "codex_imagegen_prompt.txt"
        job["prompt_file"] = rel(workspace, prompt_path)
        if write_outputs:
            write_text(prompt_path, job["codex_imagegen_prompt"])
    jobs_path = workspace / "Assets" / variant_id / safe_name(shot_id) / "codex_imagegen_jobs.json"
    report_path = workspace / "Assets" / variant_id / safe_name(shot_id) / "reports" / "plan_d_12_02_codex_imagegen_first_frame_jobs.json"
    payload = {
        "status": "ready",
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": generated_at,
        "execution_note": "Python cannot directly call Codex built-in image_gen. Run these jobs from Codex with the imagegen skill, then import each generated image with --mode import.",
        "shot_id": shot_id,
        "variant_id": variant_id,
        "dependencies": dependencies,
        "jobs": jobs,
        "jobs_path": rel(workspace, jobs_path),
        "report": rel(workspace, report_path),
    }
    if write_outputs:
        if package_reference_paths_updated:
            write_json(package_path, package)
        write_json(jobs_path, payload)
        write_json(report_path, payload)
    return payload


def import_result(workspace: Path, plan_path: Path, shot_id: str, scene_mark_id: str, variant_id: str, generated_image: str, provider: str, model: str) -> dict[str, Any]:
    if not scene_mark_id:
        raise ToolError("--scene-mark-id is required in import mode.")
    source = Path(generated_image).expanduser()
    if not source.is_absolute():
        source = workspace / source
    if not source.exists() or not source.is_file():
        raise ToolError(f"Generated image not found: {source}")
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        raise ToolError("rebuild_shot_plan.json must be a JSON object.")
    shot = find_shot(plan, shot_id)
    package_path = final_prompt_package_path(workspace, shot, shot_id, variant_id)
    package = read_json(package_path) if package_path.exists() else {}
    scene = next((item for item in package.get("scenes") or [] if isinstance(item, dict) and str(item.get("scene_mark_id") or "") == scene_mark_id), {})
    output = first_frame_path(workspace, shot_id, scene_mark_id, variant_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    timestamp = now_ms()
    manifest_path = scene_asset_manifest_path(workspace, shot_id, scene_mark_id, variant_id)
    manifest = {
        "status": "completed",
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": timestamp,
        "shot_id": shot_id,
        "scene_mark_id": scene_mark_id,
        "source": "codex_builtin_image_gen",
        "selected_image": rel(workspace, output),
        "imported_from": rel(workspace, source),
        "reference_image": str(scene.get("reference_image") or ""),
        "reference_used": bool(scene.get("reference_image")),
        "provider": provider or "codex_image_gen_skill",
        "model": model or "builtin_image_gen",
        "bytes": output.stat().st_size,
        "prompt": str(scene.get("image_prompt") or ""),
        "prompt_package": rel(workspace, package_path),
    }
    write_json(manifest_path, manifest)
    for mark in scene_marks_for_shot(shot):
        if scene_id_of(mark) != scene_mark_id:
            continue
        mark.setdefault("plan_d", {})["replacement_first_frame"] = {
            "selected_image": rel(workspace, output),
            "manifest": rel(workspace, manifest_path),
            "source": "codex_builtin_image_gen",
            "updated_at": timestamp,
        }
        # Keep the shared scene_asset pointer aligned because downstream Plan D video tools read canonical first.png.
        mark.setdefault("plan_a", {})["scene_asset"] = {
            "selected_image": rel(workspace, output),
            "manifest": rel(workspace, manifest_path),
            "source": "codex_builtin_image_gen",
            "provider": provider or "codex_image_gen_skill",
            "model": model or "builtin_image_gen",
        }
    write_json(plan_path, plan)
    return {"status": "completed", "tool": TOOL_ID, "shot_id": shot_id, "scene_mark_id": scene_mark_id, "output_image": rel(workspace, output), "manifest": rel(workspace, manifest_path)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("--workspace", required=True, help="Session workspace directory.")
    parser.add_argument("--task-id", default="", help="OpenCrew task id, recorded for caller context.")
    parser.add_argument("--session-id", default="", help="OpenCrew session id, recorded for caller context.")
    parser.add_argument("--shot-id", action="append", default=[], help="Target shot id. Required exactly once.")
    parser.add_argument("--scene-mark-id", default="", help="Optional scene filter for prepare mode; required for import mode.")
    parser.add_argument("--variant-id", default=DEFAULT_VARIANT_ID)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--mode", choices=("prepare", "import"), default="prepare")
    parser.add_argument("--generated-image", default="", help="Image file produced by Codex image_gen, required in import mode.")
    parser.add_argument("--provider", default="codex_image_gen_skill")
    parser.add_argument("--model", default="builtin_image_gen")
    parser.add_argument("--force", action="store_true", help="Prepare jobs even if the target first.png already exists.")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if len(args.shot_id) != 1:
            raise ToolError("Exactly one --shot-id is required.")
        workspace = workspace_path(args.workspace)
        plan_path = workspace / args.input
        if not plan_path.exists():
            raise ToolError(f"Input shot plan not found: {plan_path}")
        shot_id = str(args.shot_id[0]).strip()
        if args.mode == "prepare":
            payload = prepare_jobs(workspace, plan_path, shot_id, args.scene_mark_id, args.variant_id, args.force, write_outputs=not args.check_dependencies_only)
            if args.check_dependencies_only and payload.get("status") == "ready":
                payload = {"status": "ready", "tool": TOOL_ID, "dependencies": payload.get("dependencies")}
        else:
            if args.check_dependencies_only:
                raise ToolError("--check-dependencies-only is only valid in prepare mode.")
            payload = import_result(workspace, plan_path, shot_id, args.scene_mark_id, args.variant_id, args.generated_image, args.provider, args.model)
        if args.print_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{TOOL_ID}: {payload.get('status')}")
        return 2 if payload.get("status") == "blocked" else 0
    except Exception as exc:
        payload = {"status": "failed", "tool": TOOL_ID, "error": str(exc)}
        if args.print_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
