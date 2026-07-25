#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_model_leakage_guard import scan_structured_payload  # noqa: E402
from opcrew_backend.model_leakage_guard import sanitize_customer_text  # noqa: E402


DEFAULT_AGENT_KEY = "asset_video"


def request_bytes(base_url: str, path: str, *, cookie: str = "", timeout: float = 10.0, max_bytes: int = 512_000) -> tuple[int, dict[str, str], bytes]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {"Accept": "application/json, text/event-stream;q=0.9, */*;q=0.1"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes)
            return int(response.status), {key.lower(): value for key, value in response.headers.items()}, body
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes)
        return int(exc.code), {key.lower(): value for key, value in exc.headers.items()}, body


def decode_response(headers: dict[str, str], body: bytes) -> Any:
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "text/event-stream":
        values: list[Any] = []
        for frame in body.decode("utf-8", "replace").split("\n\n"):
            data_lines = []
            for line in frame.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if not data_lines:
                continue
            data = "\n".join(data_lines)
            try:
                values.append(json.loads(data))
            except json.JSONDecodeError:
                values.append(sanitize_customer_text(data))
        return values
    if content_type == "application/json" or content_type.endswith("+json") or body[:1] in {b"{", b"["}:
        return json.loads(body.decode("utf-8") or "null")
    return sanitize_customer_text(body.decode("utf-8", "replace"))


def smoke_paths(task_id: str, agent_key: str, search_id: str, generation_id: str) -> list[str]:
    paths = ["/api/koubo-storyboard/tasks"]
    if not task_id:
        return paths
    prefix = f"/api/koubo-storyboard/tasks/{urllib.parse.quote(task_id, safe='')}"
    paths.extend(
        [
            prefix,
            f"{prefix}/tts-builder-candidates",
            f"/api/openclip/tasks/{urllib.parse.quote(task_id, safe='')}/analysis-v1/one-click-movie",
            f"{prefix}/asset-library/image-model-config",
            f"{prefix}/asset-library/video-model-config",
            f"{prefix}/asset-library/tts-model-config",
            f"{prefix}/asset-library-search/runs",
            f"{prefix}/asset-library/digital-human/settings",
            f"{prefix}/asset-library/digital-human/voices",
            f"{prefix}/asset-library/digital-human/avatars",
            f"{prefix}/agents/{urllib.parse.quote(agent_key, safe='')}/chat/messages",
            f"{prefix}/clean-image/generations",
        ]
    )
    if search_id:
        paths.append(f"{prefix}/asset-library-search/runs/{urllib.parse.quote(search_id, safe='')}")
    if generation_id:
        paths.append(f"{prefix}/clean-image/{urllib.parse.quote(generation_id, safe='')}/image")
    return paths


def run_smoke(args: argparse.Namespace) -> int:
    failures: list[str] = []
    skipped: list[str] = []
    checked = 0
    for path in smoke_paths(args.task_id, args.agent_key, args.search_id, args.generation_id):
        status, headers, body = request_bytes(args.base_url, path, cookie=args.cookie, timeout=args.timeout)
        if status in {404, 405} and args.skip_missing:
            skipped.append(f"{status} {path}")
            continue
        if status == 401:
            failures.append(f"{path}: unauthorized; pass --cookie with an authenticated user session")
            continue
        if status >= 500:
            failures.append(f"{path}: HTTP {status}")
            continue
        try:
            decoded = decode_response(headers, body)
        except Exception as exc:
            failures.append(f"{path}: could not decode response: {exc}")
            continue
        findings = scan_structured_payload(decoded)
        if findings:
            failures.extend(f"{path}: {finding}" for finding in findings)
        checked += 1
    print(json.dumps({"checked": checked, "skipped": skipped, "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke live OpenCrew customer routes for model/provider leakage after C0 sanitization.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8011", help="Backend base URL, for example http://127.0.0.1:8011")
    parser.add_argument("--cookie", default="", help="Cookie header value, for example opencrew_session=...")
    parser.add_argument("--task-id", default="", help="Koubo StoryBoard task id to smoke. Without this, only the task list is checked.")
    parser.add_argument("--agent-key", default=DEFAULT_AGENT_KEY)
    parser.add_argument("--search-id", default="")
    parser.add_argument("--generation-id", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-missing", action="store_true", help="Treat 404/405 as skipped for optional endpoints.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_smoke(parse_args()))
