from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.adapters.opencode import (  # noqa: E402
    OpenCodeSessionClient,
    discover_opencode_servers,
)
from opcrew_backend.media_library_analysis.contracts import result_hash  # noqa: E402
from opcrew_backend.media_library_analysis.visual_semantic import (  # noqa: E402
    PROMPT_VERSION_DEFAULT,
    SYSTEM_PROMPT,
    VisualSemanticToolAdapter,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send one real OpenCode multimodal request containing the four "
            "registered frames for one media-library visual fragment."
        )
    )
    parser.add_argument("--structure-json", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--fragment-id", default="")
    return parser.parse_args()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_capability(model: dict[str, Any]) -> bool:
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    inputs = capabilities.get("input")
    return isinstance(inputs, dict) and inputs.get("image") is True


def locate_model(
    provider_payload: dict[str, Any], provider_id: str, model_id: str
) -> dict[str, Any]:
    for provider in provider_payload.get("all") or []:
        if not isinstance(provider, dict) or provider.get("id") != provider_id:
            continue
        models = provider.get("models")
        if isinstance(models, dict):
            model = models.get(model_id)
            if isinstance(model, dict):
                return model
    raise RuntimeError(f"model unavailable: {provider_id}/{model_id}")


def write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    structure_path = args.structure_json.resolve()
    structure = read_object(structure_path)
    if structure.get("schema_version") != "media_library_visual_structure_v2":
        raise RuntimeError("visual structure v2 is required")
    if structure.get("sampling_strategy") != "scene_uniform_4_v1":
        raise RuntimeError("scene_uniform_4_v1 is required")
    items = [item for item in structure.get("items") or [] if isinstance(item, dict)]
    if args.fragment_id:
        items = [item for item in items if item.get("fragment_id") == args.fragment_id]
    if not items:
        raise RuntimeError("requested visual fragment is unavailable")
    authoritative = items[0]
    keyframes = authoritative.get("keyframes")
    if not isinstance(keyframes, list) or len(keyframes) != 4:
        raise RuntimeError("exactly four keyframes are required")

    tool_root = structure_path.parents[2]
    image_paths: list[Path] = []
    frame_evidence: list[dict[str, Any]] = []
    for keyframe in keyframes:
        if not isinstance(keyframe, dict):
            raise RuntimeError("keyframe must be an object")
        image_path = (tool_root / str(keyframe.get("image_path") or "")).resolve()
        if not image_path.is_relative_to(tool_root) or not image_path.is_file():
            raise RuntimeError("keyframe path is missing or unsafe")
        actual_hash = sha256_file(image_path)
        if actual_hash != keyframe.get("image_sha256"):
            raise RuntimeError("keyframe hash mismatch")
        image_paths.append(image_path)
        frame_evidence.append(
            {
                "keyframe_id": keyframe.get("keyframe_id"),
                "keyframe_time_ms": keyframe.get("keyframe_time_ms"),
                "image_path": str(keyframe.get("image_path") or ""),
                "image_sha256": actual_hash,
                "size_bytes": image_path.stat().st_size,
            }
        )

    discovery = discover_opencode_servers()
    selected = discovery.get("selected")
    if not isinstance(selected, dict) or selected.get("healthy") is not True:
        raise RuntimeError("healthy OpenCode server was not discovered")
    client = OpenCodeSessionClient(
        str(selected.get("base_url") or ""),
        str(selected.get("username") or ""),
        str(selected.get("password") or ""),
        str(tool_root),
    )
    provider_payload = client.providers()
    model_catalog_item = locate_model(provider_payload, args.provider, args.model)
    if not image_capability(model_catalog_item):
        raise RuntimeError("selected model does not advertise image input")

    model_session = client.create_session(
        "Media library DSCF0157 four-image capability smoke"
    )
    model_session_id = str(model_session.get("id") or "")
    if not model_session_id:
        raise RuntimeError("OpenCode model session id is missing")
    adapter = VisualSemanticToolAdapter.__new__(VisualSemanticToolAdapter)
    started_at = datetime.now(timezone.utc)
    status = "passed"
    error = None
    candidate: dict[str, Any] | None = None
    validated: dict[str, Any] | None = None
    try:
        candidate = adapter._model_call(
            client=client,
            model_session_id=model_session_id,
            model={"providerID": args.provider, "modelID": args.model},
            authoritative=authoritative,
            image_paths=image_paths,
        )
        validated = adapter._validate_description(authoritative, candidate)
    except Exception as exc:  # live evidence must retain structured failure
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
        }

    artifact = {
        "schema_version": "media_library_visual_four_image_live_probe_v1",
        "executed_at": started_at.isoformat(),
        "status": status,
        "source": {
            "asset_id": structure.get("asset_id"),
            "source_version": structure.get("source_version"),
            "visual_structure_run_id": structure.get("analysis_run_id"),
            "visual_structure_result_hash": result_hash(structure),
            "structure_json": str(structure_path),
        },
        "fragment": {
            "fragment_id": authoritative.get("fragment_id"),
            "start_ms": authoritative.get("start_ms"),
            "end_ms": authoritative.get("end_ms"),
            "sampling_strategy": structure.get("sampling_strategy"),
            "frames": frame_evidence,
        },
        "model": {
            "provider_id": args.provider,
            "model_id": args.model,
            "catalog_image_input": True,
            "catalog_max_images_per_request": None,
            "capability_proof": "one successful live request containing four images",
        },
        "request": {
            "model_session_id": model_session_id,
            "model_call_count": 1,
            "text_part_count": 1,
            "image_part_count": 4,
            "ordered_keyframe_ids": [
                frame["keyframe_id"] for frame in frame_evidence
            ],
            "prompt_version": PROMPT_VERSION_DEFAULT,
            "system_prompt_sha256": hashlib.sha256(
                SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "tools_enabled": False,
            "customer_video_uploaded": False,
            "data_urls_retained": False,
        },
        "response": {
            "candidate": candidate,
            "validated_item": validated,
            "error": error,
        },
        "security": {
            "credentials_included": False,
            "raw_image_bytes_included": False,
            "only_four_registered_keyframes_sent": True,
        },
    }
    write_artifact(args.artifact.resolve(), artifact)
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
