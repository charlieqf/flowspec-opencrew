from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import base64
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Image_GPT.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Image_GPT.md"


class ToolError(RuntimeError):
    pass


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def redact_secret_text(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\/", "/")
    import re

    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    text = re.sub(r"(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    return text


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def post_multipart_request(url: str, fields: dict[str, str], files: list[tuple[str, Path]], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    boundary = f"----OpenCrewBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode("utf-8"), b"\r\n"])
    for field_name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n"])
    chunks.append(f"--{boundary}--\r\n".encode())
    request_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **headers}
    request = urllib.request.Request(url, data=b"".join(chunks), headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def image_b64_from_response(provider: str, payload: dict[str, Any]) -> str:
    if provider in {"openai", "xai"}:
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("b64_json"):
                return str(item["b64_json"])
    if provider in {"gemini", "google"}:
        for candidate in payload.get("candidates") or []:
            content = dict_value(candidate.get("content")) if isinstance(candidate, dict) else {}
            for part in list_value(content.get("parts")):
                inline = dict_value(part.get("inlineData") or part.get("inline_data")) if isinstance(part, dict) else {}
                if inline.get("data"):
                    return str(inline["data"])
    raise ToolError(f"Image provider response did not include image data: {json.dumps(payload, ensure_ascii=False)[:1200]}")


def prompt_text_from_dialogues(segment: dict[str, Any], dialogue_index: dict[str, dict[str, Any]]) -> str:
    lines = []
    for dialogue_id in list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")):
        item = dialogue_index.get(text_value(dialogue_id), {})
        dialogue = dict_value(item.get("dialogue"))
        text = text_value(dialogue.get("dialogue") or dialogue.get("text"))
        if text:
            lines.append(text)
    return "\n".join(lines)


def segment_is_cutaway(segment: dict[str, Any]) -> bool:
    tasks = dict_value(segment.get("tasks"))
    reason = text_value(tasks.get("lipsync_reason")).lower()
    source = text_value(tasks.get("lipsync_decision_source")).lower()
    return reason in {"user_marked_cutaway", "cutaway", "product_closeup", "no_visible_face", "no_face"} or source in {"user_marked_cutaway", "product_closeup"}


def reference_by_kind(references: list[dict[str, str]], kind: str) -> dict[str, str]:
    for reference in references:
        if reference.get("kind") == kind:
            return reference
    return {}


def template_snapshot_text(context: dict[str, Any], default_name: str) -> str:
    prompt_dir = Path(context.get("prompt_dir") or "")
    template_name = text_value(context.get("template_name") or default_name)
    candidate = prompt_dir / template_name
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    source_value = text_value(context.get("template_source_path"))
    source = Path(source_value) if source_value else None
    if source and source.exists() and source.is_file():
        return source.read_text(encoding="utf-8")
    return ""


def base_image_fields(context: dict[str, Any]) -> dict[str, Any]:
    segment = dict_value(context.get("segment"))
    shot = dict_value(context.get("shot"))
    scene = dict_value(context.get("scene"))
    dialogue_index = dict_value(context.get("dialogue_index"))
    references = list_value(context.get("references"))
    cutaway = segment_is_cutaway(segment)
    target = reference_by_kind(references, "target_frame")
    host = {} if cutaway else reference_by_kind(references, "host")
    product = reference_by_kind(references, "product")
    text = prompt_text_from_dialogues(segment, dialogue_index)
    return {
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_text": text,
        "cutaway": cutaway,
        "target": target,
        "host": host,
        "product": product,
        "references": references,
        "reference_manifests": dict_value(context.get("reference_manifests")),
    }


def _write_prompt_package_file(prompt_dir: Path, asset_key: str, kind: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_{kind}Prompt.json"
    write_json(rendered_path, package)
    return rendered_path


def read_prompt_text(prompt_path: Path) -> str:
    payload = read_json(prompt_path)
    if not isinstance(payload, dict):
        raise ToolError(f"Prompt file must contain a JSON object: {prompt_path}")
    prompt = text_value(payload.get("prompt"))
    if not prompt:
        raise ToolError(f"Prompt file does not contain prompt text: {prompt_path}")
    return prompt


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Image GPT template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def _render(text: str, variables: dict[str, str]) -> str:
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered.strip()


def _join(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name))


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    fields = base_image_fields({**context, "template_name": TEMPLATE_NAME})
    segment = fields["segment"]
    shot = fields["shot"]
    scene = fields["scene"]
    cutaway = bool(fields["cutaway"])
    host = fields["host"]
    product = fields["product"]
    dialogue_text = text_value(fields["dialogue_text"])
    template_text = _template_text(context)
    variables = {
        "shot_summary": text_value(shot.get("summary")),
        "scene_summary": text_value(scene.get("summary") or scene.get("title")),
        "dialogue_text": dialogue_text,
        "cutaway_mode": "product_only_cutaway" if cutaway else "talking_head_or_standard_frame",
        "reference_summary": ", ".join(text_value(item.get("role")) for item in fields["references"] if isinstance(item, dict)) or "none",
    }
    positive_blocks = [
        "IMAGE_GPT_POSITIVE_BASE",
        "IMAGE_GPT_HOST_CUTAWAY" if cutaway else ("IMAGE_GPT_HOST_PRESENT" if host else "IMAGE_GPT_HOST_MISSING"),
        "IMAGE_GPT_PRODUCT_PRESENT" if product else "IMAGE_GPT_PRODUCT_MISSING",
        "IMAGE_GPT_POSITIVE_CUTAWAY" if cutaway else "",
        "IMAGE_GPT_CONTEXT",
    ]
    negative_blocks = ["IMAGE_GPT_NEGATIVE_BASE", "IMAGE_GPT_NEGATIVE_CUTAWAY" if cutaway else "", "IMAGE_GPT_PITFALLS_APPEND_ONLY"]
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "IMAGE_GPT_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    return {
        "schema_version": "analysis_v1_05_02_image_prompt_gpt_0.1",
        "prompt_type": "image_replacement",
        "provider_profile": "image_gpt",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "target_frame_path": fields["target"].get("working_path", ""),
        "host_reference_path": host.get("working_path", ""),
        "product_reference_path": product.get("working_path", ""),
        "reference_images": fields["references"],
        "reference_priority": {
            "target_frame": "composition_pose_hands_camera_lighting_texture",
            "host_reference": "not_used_for_product_only_cutaway" if cutaway else "identity_only",
            "product_reference": "complete_package_identity",
        },
        "reference_manifests": fields["reference_manifests"],
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["IMAGE_GPT_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"shot_id": text_value(shot.get("shot_id")), "scene_id": text_value(scene.get("scene_id")), "dialogue_text": dialogue_text, "cutaway": cutaway},
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    return _write_prompt_package_file(prompt_dir, asset_key, "Image", package)


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    config = dict_value(context.get("config"))
    api_key = text_value(config.get("api_key"))
    model = text_value(config.get("model"))
    if not api_key:
        raise ToolError(f"Missing image API key for openai/{model}.")
    prompt = read_prompt_text(prompt_path)
    reference_paths = [Path(path) for path in list_value(context.get("reference_paths")) if Path(path).exists()]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if reference_paths:
        image_field = "image[]" if len(reference_paths) > 1 else "image"
        payload = post_multipart_request(
            "https://api.openai.com/v1/images/edits",
            {"model": model, "prompt": prompt, "size": "1024x1536"},
            [(image_field, path) for path in reference_paths],
            {"Authorization": f"Bearer {api_key}"},
            timeout=int(context.get("timeout_seconds") or 120),
        )
    else:
        payload = post_json_request(
            "https://api.openai.com/v1/images/generations",
            {"model": model, "prompt": prompt, "size": "1024x1536"},
            {"Authorization": f"Bearer {api_key}"},
            timeout=int(context.get("timeout_seconds") or 120),
        )
    image_bytes = base64.b64decode(image_b64_from_response("openai", payload))
    output_path.write_bytes(image_bytes)
    return {"provider": "openai", "model": model, "output_path": str(output_path), "bytes": len(image_bytes), "reference_used": bool(reference_paths), "reference_count": len(reference_paths), "reference_paths": [str(path) for path in reference_paths]}
