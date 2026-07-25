#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYSIS_V1_DIR = REPO_ROOT / "ToolLibrary" / "Analysis_V1"

REQUIRED_FLAGS = {
    "--tool-session-root",
    "--step-id",
    "--tool-id",
    "--print-json",
}
RECOMMENDED_FLAGS = {
    "--force-rerun",
}
FRAMEWORK_MARKERS = {
    "ToolResult",
    "OutputManifest",
    "DependencyCheckResult",
}
MODEL_BROKER_MARKERS = {
    "ModelBroker",
    "ModelBrokerRequest",
}
FRAMEWORK_BRIDGE_MARKER = "maybe_run_framework_bridge"
DIRECT_SECRET_MARKERS = {
    "api_key_ciphertext",
    "Authorization",
    "Bearer ",
    "x-api-key",
    "?key=",
    "OPENCREW_DATABASE_URL",
    "DATABASE_URL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
}
DIRECT_PROVIDER_MARKERS = {
    "api.openai.com",
    "api.x.ai",
    "generativelanguage.googleapis.com",
    "dashscope.aliyuncs.com",
    "api.sync.so",
}
LEGACY_LAYOUT_MARKERS = {
    "SessionContext/",
    "\"SessionContext\"",
    "'SessionContext'",
    "Report/Result.json",
}
PERSONAL_PATH_PATTERNS = (
    re.compile(r"/Users/[^/\\'\"\s]+/\.opencrew"),
)
TOOL_SCRIPT_NAME_PATTERN = re.compile(r"^\d{2}(?:_\d{2})?_[A-Za-z0-9_]+\.py$")
SUPPORT_MODULE_NAMES = {
    "__init__.py",
    "framework_bridge.py",
    "opencode_autoheal.py",
    "provider_audit.py",
}


@dataclass
class ScriptFinding:
    severity: str
    path: str
    message: str


@dataclass
class ScriptScan:
    path: Path
    text: str
    string_literals: set[str] = field(default_factory=set)


def iter_script_paths(root: Path, include_backup: bool) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(root.glob("*.py")):
        if path.name in SUPPORT_MODULE_NAMES:
            continue
        if not TOOL_SCRIPT_NAME_PATTERN.match(path.name):
            continue
        paths.append(path)
    if include_backup:
        paths.extend(path for path in sorted((root / "Backup").glob("*.py")) if TOOL_SCRIPT_NAME_PATTERN.match(path.name))
    return paths


def parse_string_literals(path: Path, text: str) -> set[str]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return set()
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def load_scan(path: Path) -> ScriptScan:
    text = path.read_text(encoding="utf-8")
    return ScriptScan(path=path, text=text, string_literals=parse_string_literals(path, text))


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def has_all_markers(text: str, markers: set[str]) -> bool:
    return all(marker in text for marker in markers)


def check_script(scan: ScriptScan, *, strict_provider_boundary: bool) -> list[ScriptFinding]:
    findings: list[ScriptFinding] = []
    path_text = rel(scan.path)
    uses_bridge = FRAMEWORK_BRIDGE_MARKER in scan.text

    missing_flags = sorted(flag for flag in REQUIRED_FLAGS if flag not in scan.string_literals)
    if missing_flags:
        if uses_bridge:
            findings.append(ScriptFinding("WARN", path_text, "framework CLI flags are accepted through framework_bridge"))
        else:
            findings.append(
                ScriptFinding(
                    "FAIL",
                    path_text,
                    "missing required framework CLI flags: " + ", ".join(missing_flags),
                )
            )

    missing_recommended = sorted(flag for flag in RECOMMENDED_FLAGS if flag not in scan.string_literals)
    if missing_recommended:
        findings.append(
            ScriptFinding(
                "WARN",
                path_text,
                "missing recommended rerun flags: " + ", ".join(missing_recommended),
            )
        )

    if not has_all_markers(scan.text, FRAMEWORK_MARKERS):
        missing = sorted(marker for marker in FRAMEWORK_MARKERS if marker not in scan.text)
        severity = "WARN" if uses_bridge else "FAIL"
        findings.append(ScriptFinding(severity, path_text, "framework schemas are provided through bridge; script missing direct references: " + ", ".join(missing)))

    if "OutputManifest.json" not in scan.text:
        findings.append(ScriptFinding("WARN" if uses_bridge else "FAIL", path_text, "OutputManifest.json is written through bridge" if uses_bridge else "does not write Output/OutputManifest.json"))

    if "missing_dependencies" not in scan.text:
        findings.append(ScriptFinding("WARN" if uses_bridge else "FAIL", path_text, "blocked dependency payload is normalized through bridge" if uses_bridge else "does not emit standard DependencyCheckResult missing_dependencies"))

    direct_secret_hits = sorted(marker for marker in DIRECT_SECRET_MARKERS if marker in scan.text)
    direct_provider_hits = sorted(marker for marker in DIRECT_PROVIDER_MARKERS if marker in scan.text)
    if direct_secret_hits or direct_provider_hits:
        if strict_provider_boundary and not any(marker in scan.text for marker in MODEL_BROKER_MARKERS):
            findings.append(
                ScriptFinding(
                    "FAIL",
                    path_text,
                    "direct DB/key/provider access without ModelBroker markers; hits: "
                    + ", ".join(direct_secret_hits + direct_provider_hits),
                )
            )
        else:
            findings.append(
                ScriptFinding(
                    "WARN",
                    path_text,
                    "contains direct DB/key/provider markers; verify all calls are broker-mediated: "
                    + ", ".join(direct_secret_hits + direct_provider_hits),
                )
            )

    legacy_hits = sorted(marker for marker in LEGACY_LAYOUT_MARKERS if marker in scan.text)
    if legacy_hits:
        findings.append(
            ScriptFinding(
                "WARN",
                path_text,
                "contains legacy layout markers; keep only as explicit fallback with warning/debug event: "
                + ", ".join(legacy_hits),
            )
        )

    for pattern in PERSONAL_PATH_PATTERNS:
        if pattern.search(scan.text):
            findings.append(ScriptFinding("WARN" if uses_bridge else "FAIL", path_text, "contains hard-coded personal .opencrew path in legacy code path"))
            break

    if "tool_use_session_id" in scan.text and "uuid.uuid4" in scan.text:
        findings.append(ScriptFinding("WARN" if uses_bridge else "FAIL", path_text, "legacy code path appears to generate its own tool_use_session_id"))

    return findings


def check_registry(root: Path) -> list[ScriptFinding]:
    registry_path = root / "tool_registry.json"
    if not registry_path.exists():
        return [ScriptFinding("FAIL", rel(registry_path), "Analysis_V1 registry is missing")]

    findings: list[ScriptFinding] = []
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [ScriptFinding("FAIL", rel(registry_path), f"registry is not valid JSON: {exc}")]

    if registry.get("schema_version") != "1.0":
        findings.append(ScriptFinding("FAIL", rel(registry_path), 'registry schema_version must be "1.0"'))

    tools = registry.get("tools")
    if not isinstance(tools, list) or not tools:
        findings.append(ScriptFinding("FAIL", rel(registry_path), "registry must contain non-empty tools[]"))
        return findings

    required_fields = {
        "id",
        "name",
        "script",
        "stage",
        "hard_dependencies",
        "soft_dependencies",
        "main_outputs",
        "uses_llm",
        "uses_vlm",
        "supports_resume",
        "cost_level",
        "estimated_runtime",
    }
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            findings.append(ScriptFinding("FAIL", rel(registry_path), f"tools[{index}] must be an object"))
            continue
        missing = sorted(field for field in required_fields if field not in tool)
        if missing:
            findings.append(
                ScriptFinding(
                    "FAIL",
                    rel(registry_path),
                    f"tool {tool.get('id', index)!r} missing registry fields: " + ", ".join(missing),
                )
            )
        script = str(tool.get("script") or "")
        if "ToolLibrary/Analysis_V1/" not in script:
            findings.append(
                ScriptFinding(
                    "FAIL",
                    rel(registry_path),
                    f"tool {tool.get('id', index)!r} script must point to ToolLibrary/Analysis_V1/",
                )
            )
        estimated_runtime = tool.get("estimated_runtime")
        if not isinstance(estimated_runtime, dict) or not estimated_runtime.get("relative"):
            findings.append(
                ScriptFinding(
                    "FAIL",
                    rel(registry_path),
                    f"tool {tool.get('id', index)!r} estimated_runtime.relative is required",
                )
            )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Analysis_V1 scripts against Tool Use Session framework contracts.")
    parser.add_argument("--root", default=str(DEFAULT_ANALYSIS_V1_DIR), help="Path to ToolLibrary/Analysis_V1.")
    parser.add_argument("--include-backup", action="store_true", help="Also scan Backup/*.py.")
    parser.add_argument("--strict-provider-boundary", action="store_true", help="Fail direct provider/key access instead of warning.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable findings.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    findings: list[ScriptFinding] = []
    if not root.exists():
        findings.append(ScriptFinding("FAIL", rel(root), "Analysis_V1 root does not exist"))
    else:
        findings.extend(check_registry(root))
        for path in iter_script_paths(root, include_backup=bool(args.include_backup)):
            findings.extend(check_script(load_scan(path), strict_provider_boundary=bool(args.strict_provider_boundary)))

    failures = [item for item in findings if item.severity == "FAIL"]
    warnings = [item for item in findings if item.severity == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "status": "failed" if failures else "passed",
                    "failures": [item.__dict__ for item in failures],
                    "warnings": [item.__dict__ for item in warnings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        status = "failed" if failures else "passed"
        print(f"Analysis_V1 contract check: {status} ({len(failures)} failures, {len(warnings)} warnings)")
        for item in findings:
            print(f"{item.severity}: {item.path}: {item.message}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
