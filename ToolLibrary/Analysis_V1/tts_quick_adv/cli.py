from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import TOOL_NAME
from .core import AdvArgs, BlockedError, catalog_list, rank_voices, run_full, sample_reference, state
from .io_utils import resolve_workspace
from .voice_cloning import CloneVoiceArgs, CloudCloneError, clone_voice, delete_cloned_voice, import_cloned_voice, list_cloned_voices, query_cloned_voice


DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_CLONE_TARGET_MODEL = "cosyvoice-v3.5-flash"
DEFAULT_CLONE_API_KEY_ENV = "DASHSCOPE_API_KEY"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advanced quick TTS voice matching and preview support.")
    subparsers = parser.add_subparsers(dest="command")
    for command in ("run", "state", "sample-reference", "catalog-list", "rank", "clone-voice", "clone-list", "clone-query", "clone-import", "clone-delete"):
        add_common_args(subparsers.add_parser(command))
    add_common_args(parser)
    return parser


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--voice-catalog-dir", default="", help="Voice catalog directory. Defaults to ToolLibrary/Analysis_V1/VoiceCatalog/<model>.")
    parser.add_argument("--providers", default="google", help="Provider allowlist, for example google or qwen.")
    parser.add_argument("--model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--voices", default="", help="Optional comma-separated voice allowlist.")
    parser.add_argument("--reference-start", type=float, default=0.0)
    parser.add_argument("--reference-duration", type=float, default=0.0)
    parser.add_argument("--stage1-count", type=int, default=24)
    parser.add_argument("--stage2-count", type=int, default=6)
    parser.add_argument("--final-count", type=int, default=3)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--disable-speechbrain", action="store_true")
    parser.add_argument("--clone-provider", default="aliyun", help="Cloud clone provider. Supports CozyVoice/DashScope, HeyGen, and MiniMax.")
    parser.add_argument("--clone-api-key", default="", help="Explicit cloud clone API key. Prefer --clone-api-key-env in normal use.")
    parser.add_argument("--clone-api-key-env", default=DEFAULT_CLONE_API_KEY_ENV)
    parser.add_argument("--clone-workspace-id", default="", help="Optional DashScope workspace id.")
    parser.add_argument("--clone-enrollment-model", default="voice-enrollment")
    parser.add_argument("--clone-target-model", default=DEFAULT_CLONE_TARGET_MODEL)
    parser.add_argument("--clone-prefix", default="ocadv", help="Lowercase alnum prefix for generated voice_id.")
    parser.add_argument("--clone-audio-path", default="", help="Local audio path. Defaults to SessionOutput/Audio_Reference.wav.")
    parser.add_argument("--clone-audio-url", default="", help="Existing URL/oss:// URL for cloud clone audio.")
    parser.add_argument("--clone-language-hints", default="zh", help="Comma-separated language hints, for example zh,en.")
    parser.add_argument("--clone-max-prompt-audio-length", type=float, default=16.0)
    parser.add_argument("--clone-page-index", type=int, default=0)
    parser.add_argument("--clone-page-size", type=int, default=10)
    parser.add_argument("--clone-voice-id", default="")
    parser.add_argument("--clone-consent-confirmed", action="store_true", help="Required for clone-voice. Confirms the user has rights to upload and clone the reference audio.")
    parser.add_argument("--clone-consent-actor", default="cli")
    parser.add_argument("--clone-consent-note", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")


def args_from_namespace(ns: argparse.Namespace) -> AdvArgs:
    return AdvArgs(
        workspace=str(ns.workspace or ""),
        voice_catalog_dir=str(ns.voice_catalog_dir or ""),
        providers=str(ns.providers or "google"),
        model=str(ns.model or DEFAULT_TTS_MODEL),
        voices=str(ns.voices or ""),
        reference_start=float(ns.reference_start or 0.0),
        reference_duration=float(ns.reference_duration or 0.0),
        stage1_count=int(ns.stage1_count or 24),
        stage2_count=int(ns.stage2_count or 6),
        final_count=int(ns.final_count or 3),
        database_url=str(ns.database_url or ""),
        database_url_env=str(ns.database_url_env or DEFAULT_DATABASE_URL_ENV),
        disable_speechbrain=bool(ns.disable_speechbrain),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def clone_args_from_namespace(ns: argparse.Namespace) -> CloneVoiceArgs:
    return CloneVoiceArgs(
        provider=str(ns.clone_provider or "aliyun"),
        api_key=str(ns.clone_api_key or ""),
        api_key_env=str(ns.clone_api_key_env or DEFAULT_CLONE_API_KEY_ENV),
        workspace_id=str(ns.clone_workspace_id or ""),
        enrollment_model=str(ns.clone_enrollment_model or "voice-enrollment"),
        target_model=str(ns.clone_target_model or DEFAULT_CLONE_TARGET_MODEL),
        prefix=str(ns.clone_prefix or "ocadv"),
        audio_path=str(ns.clone_audio_path or ""),
        audio_url=str(ns.clone_audio_url or ""),
        language_hints=str(ns.clone_language_hints or ""),
        max_prompt_audio_length=float(ns.clone_max_prompt_audio_length or 0.0),
        page_index=int(ns.clone_page_index or 0),
        page_size=int(ns.clone_page_size or 10),
        voice_id=str(ns.clone_voice_id or ""),
        consent_confirmed=bool(ns.clone_consent_confirmed),
        consent_actor=str(ns.clone_consent_actor or "cli"),
        consent_note=str(ns.clone_consent_note or ""),
        force=bool(ns.force),
    )


def print_result(payload: dict, print_json: bool) -> None:
    if print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {payload.get('status') or 'completed'}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    command = ns.command or "run"
    args = args_from_namespace(ns)
    workspace = resolve_workspace(args.workspace)
    try:
        if command == "state":
            payload = state(workspace, args)
        elif command == "sample-reference":
            payload = {"ok": True, **sample_reference(workspace, args)}
        elif command == "catalog-list":
            payload = {"ok": True, **catalog_list(workspace, args)}
        elif command == "rank":
            payload = {"ok": True, **rank_voices(workspace, args)}
        elif command == "clone-voice":
            payload = clone_voice(workspace, clone_args_from_namespace(ns))
        elif command == "clone-list":
            payload = list_cloned_voices(clone_args_from_namespace(ns))
        elif command == "clone-query":
            payload = query_cloned_voice(clone_args_from_namespace(ns))
        elif command == "clone-import":
            payload = import_cloned_voice(workspace, clone_args_from_namespace(ns))
        elif command == "clone-delete":
            payload = delete_cloned_voice(workspace, clone_args_from_namespace(ns))
        else:
            payload = run_full(workspace, args)
    except BlockedError as exc:
        payload = {"ok": False, "status": "blocked", "blocked_reasons": [{"code": exc.code, "message": exc.message}]}
    except CloudCloneError as exc:
        status = "failed" if exc.failed else "blocked"
        payload = {"ok": False, "status": status, "blocked_reasons": [{"code": exc.code, "message": exc.message}]}
    print_result(payload, args.print_json)
    status = str(payload.get("status") or "completed")
    if status == "failed":
        return 1
    return 0 if status in {"completed", "blocked"} else 1
