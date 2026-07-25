from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.services.local_secrets import LocalSecretStore  # noqa: E402


MODEL = "gemini-omni-flash-preview"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
MODEL_URL = f"{API_ROOT}/models/{MODEL}"
INTERACTIONS_URL = f"{API_ROOT}/interactions"
PAID_GATE = "OPENCREW_RUN_PAID_GEMINI_OMNI_SMOKE"
ISOLATED_KEY_GATE = "OPENCREW_GEMINI_OMNI_TEST_KEY_ISOLATED"
PAID_BUDGET = "OPENCREW_GEMINI_OMNI_SMOKE_MAX_USD"
PAID_MAX_CALLS = "OPENCREW_GEMINI_OMNI_SMOKE_MAX_CALLS"
PAID_MAX_SECONDS = "OPENCREW_GEMINI_OMNI_SMOKE_MAX_TOTAL_SECONDS"
SMOKE_DURATION_SECONDS = 3
SCRIPT_MAX_USD = 1.20
SCRIPT_MAX_CALLS = 2
SCRIPT_MAX_SECONDS = SMOKE_DURATION_SECONDS * SCRIPT_MAX_CALLS
VIDEO_OUTPUT_PRICE_SNAPSHOT_USD_PER_SECOND = 0.10
SMOKE_PRICE_SAFETY_MULTIPLIER = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gemini Omni Flash live probe. The default model mode does not generate media. "
            "The generate and chain modes are paid and require all explicit budget gates."
        )
    )
    parser.add_argument("--mode", choices=("model", "generate", "chain"), default="model")
    parser.add_argument("--key-ref", default="video_gemini_key")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("OPENCREW_DATA_DIR") or Path.home() / ".opencrew"),
    )
    parser.add_argument("--artifact", type=Path)
    parser.add_argument(
        "--media-dir",
        type=Path,
        help="Optional directory for the generated non-customer smoke-test MP4 files.",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def secret_for(args: argparse.Namespace) -> str:
    environment_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    key = LocalSecretStore(args.data_dir).get(args.key_ref).strip()
    if not key:
        raise RuntimeError(f"Gemini key is unavailable for secret ref {args.key_ref!r}")
    return key


def redact_text(value: str, secret: str) -> str:
    output = str(value or "")
    if secret:
        output = output.replace(secret, "[REDACTED]")
    return output[:1000]


def request_json(
    url: str,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
    method: str | None = None,
    timeout: int = 120,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        method=method or ("POST" if payload is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = redact_text(error.read().decode("utf-8", errors="replace"), key)
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"error": {"message": raw}}
        return error.code, detail


def model_projection(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "http_status": status,
        "name": payload.get("name"),
        "display_name": payload.get("displayName"),
        "version": payload.get("version"),
        "supported_generation_methods": payload.get("supportedGenerationMethods") or [],
    }


def video_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        mime = str(value.get("mime_type") or value.get("mimeType") or "").lower()
        if str(value.get("type") or "").lower() == "video" or mime.startswith("video/"):
            found.append(value)
            return
        for key, item in value.items():
            if key not in {"error", "input", "request"}:
                visit(item)

    visit(payload)
    return found


def sanitized_error_message(value: Any) -> str | None:
    if not value:
        return None
    message = str(value)[:1000]
    message = re.sub(r"https?://\S+", "[provider URL]", message)
    message = re.sub(
        r"\b(?:interactions?|files?)/[A-Za-z0-9._~-]+",
        "[provider state]",
        message,
    )
    return message


def generation_projection(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    parts = video_parts(payload)
    inline_sizes: list[int] = []
    inline_hashes: list[str] = []
    for part in parts:
        encoded = part.get("data")
        if not isinstance(encoded, str) or not encoded:
            continue
        content = base64.b64decode(encoded, validate=True)
        inline_sizes.append(len(content))
        inline_hashes.append(hashlib.sha256(content).hexdigest())
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return {
        "http_status": status,
        "status": payload.get("status"),
        "model": payload.get("model"),
        "interaction_id_present": bool(payload.get("id")),
        "video_part_count": len(parts),
        "has_uri_video": any(bool(part.get("uri")) for part in parts),
        "inline_video_bytes": inline_sizes,
        "inline_video_sha256": inline_hashes,
        "error_status": error.get("status"),
        "error_message": sanitized_error_message(error.get("message")),
    }


def require_paid_gates(
    *,
    calls_used: int = 1,
    requested_seconds: int = SMOKE_DURATION_SECONDS,
) -> dict[str, Any]:
    if os.environ.get(PAID_GATE, "").strip() != "1":
        raise RuntimeError(f"paid generation is disabled; set {PAID_GATE}=1 explicitly")
    if os.environ.get(ISOLATED_KEY_GATE, "").strip() != "1":
        raise RuntimeError(
            f"paid generation requires an isolated test key; set {ISOLATED_KEY_GATE}=1 only after verification"
        )
    try:
        max_usd = float(os.environ.get(PAID_BUDGET, "0"))
        max_calls = int(os.environ.get(PAID_MAX_CALLS, "0"))
        max_seconds = int(os.environ.get(PAID_MAX_SECONDS, "0"))
    except ValueError as error:
        raise RuntimeError("paid smoke budget gates must be numeric") from error
    estimated_usd = round(
        VIDEO_OUTPUT_PRICE_SNAPSHOT_USD_PER_SECOND
        * SMOKE_PRICE_SAFETY_MULTIPLIER
        * requested_seconds,
        2,
    )
    if not estimated_usd <= max_usd <= SCRIPT_MAX_USD:
        raise RuntimeError(
            f"{PAID_BUDGET} must be between {estimated_usd:.2f} "
            f"and {SCRIPT_MAX_USD:.2f}"
        )
    if not calls_used <= max_calls <= SCRIPT_MAX_CALLS:
        raise RuntimeError(
            f"{PAID_MAX_CALLS} must be between {calls_used} and {SCRIPT_MAX_CALLS}"
        )
    if not requested_seconds <= max_seconds <= SCRIPT_MAX_SECONDS:
        raise RuntimeError(
            f"{PAID_MAX_SECONDS} must be between {requested_seconds} and {SCRIPT_MAX_SECONDS}"
        )
    return {
        "max_usd": max_usd,
        "max_calls": max_calls,
        "max_total_seconds": max_seconds,
        "estimated_usd": estimated_usd,
        "calls_used": calls_used,
        "requested_seconds": requested_seconds,
    }


def interaction_request(
    prompt: str,
    *,
    store: bool,
    previous_interaction_id: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "input": prompt,
        "response_format": {
            "type": "video",
            "delivery": "inline",
            "aspect_ratio": "16:9",
            "duration": f"{SMOKE_DURATION_SECONDS}s",
        },
        "store": store,
        "background": False,
    }
    if previous_interaction_id:
        payload["previous_interaction_id"] = previous_interaction_id
    else:
        payload["generation_config"] = {
            "video_config": {
                "task": "text_to_video",
            }
        }
    return payload


def cleanup_interactions(key: str, interaction_ids: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for interaction_id in dict.fromkeys(reversed(interaction_ids)):
        if not interaction_id:
            continue
        try:
            status, payload = request_json(
                f"{INTERACTIONS_URL}/{urllib.parse.quote(interaction_id, safe='')}",
                key=key,
                method="DELETE",
            )
        except Exception as exc:
            results.append(
                {
                    "http_status": None,
                    "deleted": False,
                    "error_status": "CLEANUP_REQUEST_FAILED",
                    "error_message": sanitized_error_message(exc),
                }
            )
            continue
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        results.append(
            {
                "http_status": status,
                "deleted": status in {200, 204, 404},
                "error_status": error.get("status"),
                "error_message": sanitized_error_message(error.get("message")),
            }
        )
    return results


def persist_inline_videos(
    payload: dict[str, Any],
    media_dir: Path | None,
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    if media_dir is None:
        return []
    media_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for index, part in enumerate(video_parts(payload), start=1):
        encoded = part.get("data")
        if not isinstance(encoded, str) or not encoded:
            continue
        content = base64.b64decode(encoded, validate=True)
        output_path = media_dir / f"{prefix}-{index}.mp4"
        temporary = output_path.with_suffix(".mp4.tmp")
        temporary.write_bytes(content)
        temporary.replace(output_path)
        artifacts.append(
            {
                "file": output_path.name,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return artifacts


def write_artifact(path: Path, payload: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise RuntimeError(f"artifact already exists: {path}; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    key = secret_for(args)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "probe": "gemini_omni_live_probe",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "model": MODEL,
    }
    if args.mode == "model":
        status, payload = request_json(MODEL_URL, key=key)
        artifact["evidence_scope"] = "model_metadata_only"
        artifact["interactions_endpoint_proven"] = False
        artifact["request"] = {
            "method": "GET",
            "url": MODEL_URL,
            "paid_generation": False,
        }
        artifact["response_projection"] = model_projection(status, payload)
        artifact["ok"] = status == 200 and payload.get("name") == f"models/{MODEL}"
    else:
        chain_mode = args.mode == "chain"
        call_count = 2 if chain_mode else 1
        budget = require_paid_gates(
            calls_used=call_count,
            requested_seconds=call_count * SMOKE_DURATION_SECONDS,
        )
        artifact["evidence_scope"] = "paid_interactions_live_probe"
        request_payload = interaction_request(
            (
                "Create a three-second minimal test video: a solid blue circle centered on a white "
                "background, static camera, no motion, no text, no dialogue, no music. "
                "One continuous shot."
            ),
            store=chain_mode,
        )
        status, payload = request_json(
            INTERACTIONS_URL,
            key=key,
            payload=request_payload,
            timeout=900,
        )
        artifact["request"] = {
            "method": "POST",
            "url": INTERACTIONS_URL,
            "scenario": (
                "two_turn_three_second_state_chain_no_customer_media"
                if chain_mode
                else "three_second_blue_circle_no_customer_media"
            ),
            "response_format": request_payload["response_format"],
            "generation_config": request_payload["generation_config"],
            "store": chain_mode,
            "background": False,
            "paid_generation": True,
            "budget": budget,
        }
        projection = generation_projection(status, payload)
        artifact["media_artifacts"] = persist_inline_videos(
            payload,
            args.media_dir,
            prefix="turn-1-blue-circle",
        )
        first_ok = (
            status == 200
            and projection["status"] == "completed"
            and projection["video_part_count"] >= 1
            and bool(projection["inline_video_sha256"])
        )
        artifact["response_projection"] = projection
        interaction_ids = [str(payload.get("id") or "")]
        if chain_mode and first_ok and interaction_ids[0]:
            continuation_payload = interaction_request(
                (
                    "Edit the previous test video only: change the centered circle from blue to "
                    "green. Keep the white background, static camera, duration, and all other "
                    "properties unchanged."
                ),
                store=True,
                previous_interaction_id=interaction_ids[0],
            )
            second_status, second_payload = request_json(
                INTERACTIONS_URL,
                key=key,
                payload=continuation_payload,
                timeout=900,
            )
            interaction_ids.append(str(second_payload.get("id") or ""))
            second_projection = generation_projection(second_status, second_payload)
            artifact["media_artifacts"].extend(
                persist_inline_videos(
                    second_payload,
                    args.media_dir,
                    prefix="turn-2-green-circle",
                )
            )
            second_ok = (
                second_status == 200
                and second_projection["status"] == "completed"
                and second_projection["video_part_count"] >= 1
                and bool(second_projection["inline_video_sha256"])
            )
            artifact["continuation"] = {
                "request_projection": {
                    "scenario": "change_blue_circle_to_green_from_first_turn",
                    "previous_interaction_id_present": True,
                    "generation_config_present": "generation_config" in continuation_payload,
                    "store": True,
                    "background": False,
                },
                "response_projection": second_projection,
                "ok": second_ok,
            }
            artifact["ok"] = first_ok and second_ok
        else:
            if chain_mode:
                artifact["continuation"] = {
                    "skipped": True,
                    "reason": "first interaction did not complete with a provider state id",
                }
            artifact["ok"] = first_ok
        artifact["cleanup"] = cleanup_interactions(key, interaction_ids)
        artifact["interactions_endpoint_proven"] = artifact["ok"]

    serialized = json.dumps(artifact, ensure_ascii=False, indent=2)
    if key and key in serialized:
        raise RuntimeError("probe output unexpectedly contains the API key")
    if args.artifact:
        write_artifact(args.artifact, artifact, force=args.force)
    print(serialized)
    return 0 if artifact["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
