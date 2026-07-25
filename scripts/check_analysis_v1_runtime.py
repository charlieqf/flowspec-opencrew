#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
REQUIRED_IMPORTS = [
    "opcrew_backend.tool_sessions.schemas",
    "pydantic",
    "psycopg",
    "sqlalchemy",
    "cv2",
    "numpy",
    "moviepy",
    "scenedetect",
    "dashscope",
]
OCR_IMPORTS = ["rapidocr_onnxruntime", "rapidocr", "paddleocr", "easyocr"]


def managed_python_path() -> Path:
    if os.environ.get("OPENCREW_ANALYSIS_V1_PYTHON"):
        return Path(os.environ["OPENCREW_ANALYSIS_V1_PYTHON"]).expanduser()
    if os.environ.get("OPENCREW_ANALYSIS_V1_RUNTIME_DIR"):
        return Path(os.environ["OPENCREW_ANALYSIS_V1_RUNTIME_DIR"]).expanduser() / "bin" / "python"
    data_dir = Path(os.environ.get("OPENCREW_DATA_DIR") or (Path.home() / ".opencrew")).expanduser()
    return data_dir / "runtimes" / "analysis_v1_py312" / "bin" / "python"


def run_json(python: Path, code: str) -> dict:
    env = runner_like_env()
    completed = subprocess.run([str(python), "-c", code], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {"ok": False, "error": (completed.stderr or completed.stdout).strip()}
    try:
        return json.loads(completed.stdout)
    except Exception as exc:
        return {"ok": False, "error": f"invalid JSON from runtime check: {exc}"}


def check_imports(python: Path, modules: list[str]) -> dict[str, bool]:
    code = (
        "import importlib,json;"
        f"mods={modules!r};"
        "out={};"
        "\nfor name in mods:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "        out[name]=True\n"
        "    except Exception:\n"
        "        out[name]=False\n"
        "print(json.dumps(out))"
    )
    result = run_json(python, code)
    if result.get("ok") is False:
        return {name: False for name in modules}
    return {name: bool(result.get(name)) for name in modules}


def check_python_version(python: Path) -> tuple[bool, str]:
    code = "import json,sys; print(json.dumps({'version': '.'.join(map(str, sys.version_info[:3])), 'major': sys.version_info[0], 'minor': sys.version_info[1]}))"
    result = run_json(python, code)
    version = str(result.get("version") or result.get("error") or "unknown")
    return bool(result.get("major") == 3 and result.get("minor") == 12), version


def check_database(python: Path, database_url: str) -> dict:
    code = r"""
import json, sys
from urllib.parse import urlparse
url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://", 1)
try:
    import psycopg
    with psycopg.connect(url, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_database(), inet_server_port()")
            row = cur.fetchone()
    print(json.dumps({"ok": True, "database": row[0], "port": row[1]}))
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)[:500]}))
"""
    completed = subprocess.run([str(python), "-c", code, database_url], env=runner_like_env(), capture_output=True, text=True, check=False)
    try:
        return json.loads(completed.stdout)
    except Exception:
        return {"ok": False, "error": (completed.stderr or completed.stdout).strip()[:500]}


def check_opencode(python: Path, database_url: str) -> dict:
    code = r"""
import base64, json, sys
from urllib.request import Request, urlopen
url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://", 1)
try:
    import psycopg
    with psycopg.connect(url, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("select base_url, auth_username, auth_password from opencode_runtime where id = 1 limit 1")
            row = cur.fetchone()
    if not row:
        print(json.dumps({"ok": False, "status": "missing_runtime"}))
        raise SystemExit(0)
    base_url, username, password = [str(item or "").rstrip("/") for item in row]
    req = Request(f"{base_url}/global/health", headers={"Accept": "application/json"})
    if username and password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")
    with urlopen(req, timeout=3) as res:
        payload = json.loads(res.read().decode("utf-8", errors="replace"))
    print(json.dumps({"ok": bool(payload.get("healthy")), "status": "healthy" if payload.get("healthy") else "unhealthy", "base_url": base_url}))
except Exception as exc:
    print(json.dumps({"ok": False, "status": "error", "error": str(exc)[:500]}))
"""
    completed = subprocess.run([str(python), "-c", code, database_url], env=runner_like_env(), capture_output=True, text=True, check=False)
    try:
        return json.loads(completed.stdout)
    except Exception:
        return {"ok": False, "status": "error", "error": (completed.stderr or completed.stdout).strip()[:500]}


def check_media_binaries(python: Path) -> dict:
    code = (
        "import json;"
        "from ToolLibrary.Analysis.media_binaries import media_dependency_status;"
        "print(json.dumps(media_dependency_status()))"
    )
    return run_json(python, code)


def redact_database_url(database_url: str) -> str:
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
    default_parsed = urlparse(DEFAULT_DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1))
    host = parsed.hostname or "unknown-host"
    port = parsed.port or default_parsed.port or 5433
    name = parsed.path.lstrip("/") or "unknown-db"
    return f"{parsed.scheme}://***@{host}:{port}/{name}"


def runner_like_env() -> dict[str, str]:
    env = dict(os.environ)
    pythonpath = [
        str(REPO_ROOT / "backend"),
        str(REPO_ROOT),
        str(REPO_ROOT.parent),
    ]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env.setdefault("OPENCREW_DATA_DIR", str(Path.home() / ".opencrew"))
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check the managed Analysis_V1 Python runtime.")
    parser.add_argument("--python", default=str(managed_python_path()))
    parser.add_argument("--database-url", default=os.environ.get("OPENCREW_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-opencode", action="store_true")
    args = parser.parse_args(argv)

    python = Path(args.python).expanduser()
    failures: list[str] = []
    warnings: list[str] = []

    if not python.exists():
        failures.append(f"managed python is missing: {python}")
    else:
        ok_version, version = check_python_version(python)
        if not ok_version:
            failures.append(f"managed python must be 3.12.x, got {version}")

        imports = check_imports(python, REQUIRED_IMPORTS)
        missing = [name for name, ok in imports.items() if not ok]
        if missing:
            failures.append("missing Python packages: " + ", ".join(missing))

        ocr_imports = check_imports(python, OCR_IMPORTS)
        has_python_ocr = any(ocr_imports.values())
        has_tesseract = shutil.which("tesseract") is not None
        if not has_python_ocr and not has_tesseract:
            failures.append("no OCR runtime found: install rapidocr_onnxruntime, paddleocr, easyocr, or tesseract")

        media = check_media_binaries(python)
        media_items = media.get("items") if isinstance(media, dict) else {}
        missing_media = [
            name
            for name in ("ffmpeg", "ffprobe")
            if not isinstance(media_items, dict) or not bool((media_items.get(name) or {}).get("available"))
        ]
        if missing_media:
            warnings.append("missing project media binaries: " + ", ".join(missing_media))

        if not args.skip_db:
            db = check_database(python, args.database_url)
            if not db.get("ok"):
                failures.append(f"database check failed for {redact_database_url(args.database_url)}: {db.get('error') or db}")

        if not args.skip_opencode:
            opencode = check_opencode(python, args.database_url)
            if not opencode.get("ok"):
                warnings.append(f"OpenCode health check failed: {opencode.get('status')} {opencode.get('error') or ''}".strip())

    result = {
        "ok": not failures,
        "python": str(python),
        "failures": failures,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
