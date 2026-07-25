from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
ANALYSIS_V1_PYTHON_ENV = "OPENCREW_ANALYSIS_V1_PYTHON"
ANALYSIS_V1_RUNTIME_DIR_ENV = "OPENCREW_ANALYSIS_V1_RUNTIME_DIR"
OPENCREW_DATA_DIR_ENV = "OPENCREW_DATA_DIR"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from opcrew_backend.tool_sessions.schemas import (
        DependencyCheckResult,
        MissingDependency,
        OutputFileEntry,
        OutputManifest,
        ToolResult,
    )
except Exception as exc:  # pragma: no cover - framework runs set PYTHONPATH=backend
    raise RuntimeError("Analysis_V1 framework bridge requires opcrew_backend on PYTHONPATH") from exc


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_json_from_stdout(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _parse_framework_args(argv: list[str]) -> argparse.Namespace | None:
    if "--tool-session-root" not in argv:
        return None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--tool-session-root", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--tool-id", required=True)
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--force-rerun", action="store_true")
    ns, _unknown = parser.parse_known_args(argv)
    return ns


def _tool_use_session_id(root: Path) -> str:
    variables_path = root / "0_SessionContext" / "Variables.json"
    if variables_path.exists():
        try:
            value = str(_read_json(variables_path).get("tool_use_session_id") or "").strip()
            if value:
                return value
        except Exception:
            pass
    return root.name


def _prepare_legacy_context(root: Path) -> None:
    context_dir = root / "0_SessionContext"
    legacy_context_dir = root / "SessionContext"
    if not context_dir.exists():
        return
    legacy_context_dir.mkdir(parents=True, exist_ok=True)
    for source in context_dir.iterdir():
        target = legacy_context_dir / source.name
        if target.exists():
            continue
        if source.is_file():
            shutil.copy2(source, target)


def _collect_result_paths(root: Path, legacy_result: dict[str, Any], tool_name: str) -> list[str]:
    candidates: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value:
            candidates.append(value)
        elif isinstance(value, list):
            for item in value:
                add(item)
        elif isinstance(value, dict):
            for item in value.values():
                add(item)

    add(legacy_result.get("outputs"))
    add(legacy_result.get("created_files"))
    for report in root.glob(f"S*_{tool_name}/Report/Result.json"):
        candidates.append(report.relative_to(root).as_posix())

    seen: set[str] = set()
    paths: list[str] = []
    for raw in candidates:
        rel = str(raw).replace("\\", "/").strip().lstrip("/")
        if not rel or rel in seen:
            continue
        if (root / rel).exists():
            seen.add(rel)
            paths.append(rel)
    return paths


def _output_manifest(root: Path, *, tool_use_session_id: str, step_id: str, tool_id: str, status: str, result_paths: list[str]) -> OutputManifest:
    files: list[OutputFileEntry] = []
    for rel in result_paths:
        path = root / rel
        if not path.exists() or not path.is_file():
            continue
        files.append(
            OutputFileEntry(
                path=rel,
                kind="artifact",
                schema_name=path.name if path.suffix.lower() == ".json" else "",
                sha256=_sha256_file(path),
                size=path.stat().st_size,
                visibility="public" if rel.startswith("SessionOutput/") else "internal",
                sensitivity="normal",
                downloadable=1 if rel.startswith("SessionOutput/") else 0,
            )
        )
    return OutputManifest(
        tool_use_session_id=tool_use_session_id,
        step_id=step_id,
        tool_id=tool_id,
        status=status,
        files=files,
    )


def _blocked_dependency_payload(legacy_result: dict[str, Any]) -> dict[str, Any]:
    missing: list[MissingDependency] = []
    for item in legacy_result.get("blocked_reasons") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "blocked")
        message = str(item.get("message") or code)
        missing.append(
            MissingDependency(
                kind=code,
                required_from=code,
                suggested_action=message,
            )
        )
    if not missing:
        missing.append(
            MissingDependency(
                kind="legacy_blocked",
                required_from="legacy_result",
                suggested_action="Legacy Analysis_V1 tool returned blocked without structured blocked_reasons.",
            )
        )
    return DependencyCheckResult(status="blocked", missing_dependencies=missing).model_dump()


def _legacy_script_supports_flag(script_path: Path, flag: str) -> bool:
    try:
        return flag in script_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _legacy_subprocess_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    pythonpath = [
        str(BACKEND_ROOT),
        str(REPO_ROOT),
        str(REPO_ROOT.parent),
    ]
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault(OPENCREW_DATA_DIR_ENV, str(Path.home() / ".opencrew"))
    return env


def _analysis_v1_runtime_python_candidates(env: dict[str, str]) -> list[Path]:
    candidates: list[Path | None] = [
        Path(env[ANALYSIS_V1_PYTHON_ENV]).expanduser() if env.get(ANALYSIS_V1_PYTHON_ENV) else None,
        Path(env[ANALYSIS_V1_RUNTIME_DIR_ENV]).expanduser() / "bin" / "python" if env.get(ANALYSIS_V1_RUNTIME_DIR_ENV) else None,
        Path(env.get(OPENCREW_DATA_DIR_ENV) or (Path.home() / ".opencrew")).expanduser() / "runtimes" / "analysis_v1_py312" / "bin" / "python",
        Path.home().expanduser() / ".opencrew" / "runtimes" / "analysis_v1_py312" / "bin" / "python",
        REPO_ROOT / ".venv" / "bin" / "python",
        REPO_ROOT / "backend" / ".venv" / "bin" / "python",
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _select_legacy_python(env: dict[str, str]) -> str:
    python = next((candidate for candidate in _analysis_v1_runtime_python_candidates(env) if candidate.exists()), None)
    if python is None:
        return sys.executable
    if not env.get(ANALYSIS_V1_PYTHON_ENV):
        env[ANALYSIS_V1_PYTHON_ENV] = str(python)
    if not env.get(ANALYSIS_V1_RUNTIME_DIR_ENV) and python.name == "python" and python.parent.name == "bin":
        env[ANALYSIS_V1_RUNTIME_DIR_ENV] = str(python.parent.parent)
    return str(python)


def _run_legacy_script(script_path: Path, root: Path, *, force_rerun: bool) -> tuple[int, str, str, dict[str, Any] | None]:
    env = _legacy_subprocess_env(root)
    python = _select_legacy_python(env)
    command = [
        python,
        str(script_path),
        "--workspace",
        str(root),
        "--print-json",
    ]
    if not force_rerun and _legacy_script_supports_flag(script_path, "--resume"):
        command.append("--resume")
    if force_rerun:
        command.append("--force")
    completed = subprocess.run(command, cwd=str(root), env=env, capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout, completed.stderr, _parse_json_from_stdout(completed.stdout)


def _framework_prepare_result(root: Path, *, tool_use_session_id: str, step_id: str, tool_id: str, tool_name: str) -> ToolResult:
    result_paths = [
        rel
        for rel in (
            "0_SessionContext/Variables.json",
            "0_SessionContext/InputManifest.json",
        )
        if (root / rel).exists()
    ]
    manifest = _output_manifest(
        root,
        tool_use_session_id=tool_use_session_id,
        step_id=step_id,
        tool_id=tool_id,
        status="completed",
        result_paths=result_paths,
    )
    tool_dir = root / f"{step_id}_{tool_name}"
    _write_json(tool_dir / "Output" / "OutputManifest.json", manifest.model_dump())
    return ToolResult(
        tool_id=tool_id,
        tool_name=tool_name,
        step_id=step_id,
        status="completed",
        outputs={"prepared_context": True},
        result_paths=result_paths,
    )


def maybe_run_framework_bridge(argv: list[str], *, script_path: Path, tool_name: str) -> int | None:
    framework_args = _parse_framework_args(argv)
    if framework_args is None:
        return None

    root = Path(str(framework_args.tool_session_root)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _prepare_legacy_context(root)

    tool_use_session_id = _tool_use_session_id(root)
    step_id = str(framework_args.step_id)
    tool_id = str(framework_args.tool_id)
    tool_dir = root / f"{step_id}_{tool_name}"
    for part in ("Working", "Output", "Report", "Prompt"):
        (tool_dir / part).mkdir(parents=True, exist_ok=True)

    if tool_name == "00_PrepareSessionVariables":
        tool_result = _framework_prepare_result(
            root,
            tool_use_session_id=tool_use_session_id,
            step_id=step_id,
            tool_id=tool_id,
            tool_name=tool_name,
        )
        if framework_args.print_json:
            print(json.dumps(tool_result.model_dump(), ensure_ascii=True, separators=(",", ":")))
        return 0

    returncode, stdout, stderr, legacy_result = _run_legacy_script(script_path, root, force_rerun=bool(framework_args.force_rerun))

    warnings: list[str] = []
    errors: list[str] = []
    outputs: dict[str, Any] = {}
    status = "completed"
    if legacy_result is None:
        status = "failed"
        errors.append((stderr or stdout or f"legacy tool exited {returncode} without JSON").strip())
        legacy_result = {}
    else:
        legacy_status = str(legacy_result.get("status") or "").strip()
        if legacy_status in {"completed", "blocked", "failed", "completed_with_failed_items"}:
            status = legacy_status
        elif returncode != 0:
            status = "failed"
        if isinstance(legacy_result.get("warnings"), list):
            warnings = [json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item for item in legacy_result.get("warnings") or []]
        if status == "blocked":
            outputs["dependency_check"] = _blocked_dependency_payload(legacy_result)
        elif returncode != 0 and status != "failed":
            warnings.append(f"Legacy tool exited {returncode} with status {status}.")
        if status == "failed":
            errors.append((stderr or "").strip() or str(legacy_result.get("error") or legacy_result.get("message") or "legacy tool failed"))

    result_paths = _collect_result_paths(root, legacy_result, tool_name)
    manifest = _output_manifest(
        root,
        tool_use_session_id=tool_use_session_id,
        step_id=step_id,
        tool_id=tool_id,
        status=status,
        result_paths=result_paths,
    )
    _write_json(tool_dir / "Output" / "OutputManifest.json", manifest.model_dump())
    _write_json(tool_dir / "Report" / "legacy_result.json", legacy_result)

    tool_result = ToolResult(
        tool_id=tool_id,
        tool_name=tool_name,
        step_id=step_id,
        status=status,
        outputs=outputs,
        warnings=warnings,
        errors=[item for item in errors if item],
        result_paths=result_paths,
    )
    if framework_args.print_json:
        print(json.dumps(tool_result.model_dump(), ensure_ascii=True, separators=(",", ":")))
    return 0
