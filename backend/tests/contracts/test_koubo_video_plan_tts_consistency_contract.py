from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.koubo.koubo_storyboard import tts_selection_recovery, tts_workflow_services, video_plan_signature_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.io_utils import read_json, safe_workspace_rel, write_json  # noqa: E402


WORKING_REL = "SessionOutput/storyboard/Working"


def text(value, default="") -> str:
    return str(value if value is not None else default).strip()


def number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def asset_source_type(rel_path: str, explicit: str = "", **_kwargs) -> str:
    if explicit in {"upload", "generated", "original", "history"}:
        return explicit
    return "generated" if text(rel_path).startswith(f"{WORKING_REL}/") else "upload"


def make_storyboard(*, selection: bool = True, audio_path: str = "", source_type: str = "") -> dict:
    payload = {
        "schema_version": "koubo_storyboard_edit_0.1",
        "shots": [{
            "shot_id": "shot_001",
            "scenes": [{
                "scene_id": "scene_001",
                "dialogues": [{
                    "dialogue_id": "dialogue_001",
                    "dialogue_asset_key": "dak_voice_test",
                    "text": "这是需要使用所选克隆声音生成的对白。",
                    "working_assets": {
                        "audio": {"slot": "Audio_Final", "source_type": source_type, "path": audio_path},
                        "images": [],
                        "video": {},
                    },
                }],
            }],
        }],
    }
    if selection:
        payload["storyboard_tts_selection"] = {
            "provider": "heygen",
            "model": "heygen-voice-clone-v3",
            "voice_id": "clone_voice_real_001",
            "voice": "clone_voice_real_001",
            "voice_source": "cloud_clone",
            "candidate_id": "clone_candidate_001",
            "voice_label": "Customer Clone",
            "prompt": "自然、清晰地朗读。",
            "tempo": 1,
        }
    return payload


def make_video_plan(*, existing_audio_path: str = "", need_audio: bool = True) -> dict:
    return {
        "shots": [{
            "shot_id": "shot_001",
            "scenes": [{
                "scene_id": "scene_001",
                "segments": [{
                    "segment_id": "segment_001",
                    "dialogue_audio_tasks": [{
                        "srt_id": "dialogue_001",
                        "dialogue_asset_key": "dak_voice_test",
                        "need_audio": need_audio,
                        "existing_audio_path": existing_audio_path,
                        "planned_audio_path": f"{WORKING_REL}/dak_voice_test_Audio_Final.wav",
                    }],
                }],
            }],
        }],
    }


def make_preflight_services() -> SimpleNamespace:
    return SimpleNamespace(
        ctx=SimpleNamespace(),
        text=text,
        number=number,
        read_json=read_json,
        safe_workspace_rel=safe_workspace_rel,
        asset_source_type=asset_source_type,
    )


def trust_storyboard_selection(workspace: Path, storyboard: dict) -> None:
    selection = storyboard["storyboard_tts_selection"]
    write_json(
        workspace / "SessionOutput/tts/tts_builder_candidates.json",
        {"candidates": [selection], "selected_tts_candidate": selection},
    )


def test_video_plan_missing_audio_uses_selected_clone_instead_of_default_tts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        storyboard = make_storyboard()
        trust_storyboard_selection(workspace, storyboard)
        result = tts_workflow_services.video_plan_tts_audio_preflight(
            workspace,
            make_video_plan(),
            storyboard,
            sc=make_preflight_services(),
        )

    assert result["requires_generation"] is True
    assert result["generation_count"] == 1
    assert result["reasons"] == ["missing_audio"]
    assert result["voice_label"] == "Customer Clone"


def test_customer_hidden_preset_runtime_is_recovered_before_video_plan_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        storyboard = make_storyboard()
        selection = storyboard["storyboard_tts_selection"]
        trusted_candidate = {
            **selection,
            "candidate_id": "tts_001",
            "voice_id": "preset_voice_001",
            "voice": "preset_voice_001",
            "voice_source": "preset",
        }
        customer_candidate = {
            key: value
            for key, value in trusted_candidate.items()
            if key not in {"provider", "model"}
        }
        storyboard["storyboard_tts_selection"] = {
            **customer_candidate,
            "provider": "",
            "model": "",
            "top_candidates": [customer_candidate],
            "recommendations": [customer_candidate],
        }
        write_json(
            workspace / "SessionOutput/tts/tts_builder_candidates.json",
            {"candidates": [trusted_candidate], "selected_tts_candidate": trusted_candidate},
        )

        result = tts_workflow_services.video_plan_tts_audio_preflight(
            workspace,
            make_video_plan(),
            storyboard,
            sc=make_preflight_services(),
        )

    assert result["requires_generation"] is True
    assert result["generation_count"] == 1
    assert result["voice_label"] == "Customer Clone"


def test_video_plan_missing_audio_fails_closed_without_selected_voice() -> None:
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(HTTPException) as raised:
        tts_workflow_services.video_plan_tts_audio_preflight(
            Path(tmp),
            make_video_plan(),
            make_storyboard(selection=False),
            sc=make_preflight_services(),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "selected_tts_audio_missing"


def test_video_plan_audio_preparation_calls_selected_clone_provider() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        plan = make_video_plan()
        storyboard = make_storyboard()
        trust_storyboard_selection(workspace, storyboard)
        write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", plan)
        sc = make_preflight_services()
        sc.load_plan = lambda *_args, **_kwargs: (storyboard, {})
        sc.tts_output_lock_guard = threading.Lock()
        sc.tts_output_locks = {}
        sc.tts_output_generations = {}
        captured: dict = {}

        def fake_run(_task, _workspace, request_payload, prompt_item, output_rel, **_kwargs):
            captured.update({"request": request_payload, "prompt": prompt_item, "output": output_rel})
            return {"output": output_rel, "duration_seconds": 1.25}

        sc.run_scene_tts_candidate = fake_run
        result = tts_workflow_services.prepare_video_plan_selected_tts_audio(
            {"id": 266, "session_id": 325},
            workspace,
            sc=sc,
        )

    assert result["generated_count"] == 1
    assert captured["prompt"]["provider"] == "heygen"
    assert captured["prompt"]["model"] == "heygen-voice-clone-v3"
    assert captured["prompt"]["voice_id"] == "clone_voice_real_001"
    assert captured["output"].endswith("dak_voice_test_Audio_Final.wav")


def test_generated_audio_with_wrong_manifest_is_regenerated_but_upload_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        audio_rel = f"{WORKING_REL}/dak_voice_test_Audio_Final.wav"
        audio_path = workspace / audio_rel
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"old-default-voice")
        manifest_path = workspace / "SessionOutput/storyboard/tts_manifests/dak_voice_test_Audio_Final.json"
        write_json(manifest_path, {
            "provider": "google",
            "model": "gemini-3.1-flash-tts-preview",
            "voice_id": "Aoede",
            "text": "这是需要使用所选克隆声音生成的对白。",
            "tempo": 1,
            "output": audio_rel,
        })
        plan = make_video_plan(existing_audio_path=audio_rel, need_audio=False)
        storyboard = make_storyboard(audio_path=audio_rel, source_type="generated")
        trust_storyboard_selection(workspace, storyboard)
        generated_result = tts_workflow_services.video_plan_tts_audio_preflight(
            workspace,
            plan,
            storyboard,
            sc=make_preflight_services(),
        )
        upload_storyboard = make_storyboard(audio_path=audio_rel, source_type="upload")
        upload_result = tts_workflow_services.video_plan_tts_audio_preflight(
            workspace,
            plan,
            upload_storyboard,
            sc=make_preflight_services(),
        )

    assert generated_result["reasons"] == ["selected_voice_mismatch"]
    assert upload_result["requires_generation"] is False


def test_unregistered_client_tts_runtime_is_rejected_and_never_preserved() -> None:
    storyboard = make_storyboard()
    selection = storyboard["storyboard_tts_selection"]
    selection.update({
        "provider": "attacker-provider",
        "model": "attacker-model",
        "voice_id": "attacker-voice",
        "voice": "attacker-voice",
        "candidate_id": "attacker-candidate",
    })
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        recovered = tts_selection_recovery.rehydrate_storyboard_tts_selection(
            SimpleNamespace(),
            workspace,
            storyboard,
            read_json=read_json,
        )
        with pytest.raises(HTTPException) as raised:
            tts_workflow_services.storyboard_tts_selection_config(
                storyboard,
                workspace=workspace,
                sc=make_preflight_services(),
            )

    recovered_selection = recovered["storyboard_tts_selection"]
    assert "provider" not in recovered_selection
    assert "model" not in recovered_selection
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "selected_tts_voice_unavailable"


def test_untrusted_client_candidate_cannot_replace_trusted_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        storyboard = make_storyboard()
        trust_storyboard_selection(workspace, storyboard)
        storyboard["storyboard_tts_selection"]["top_candidates"] = [{
            "candidate_id": "attacker-candidate",
            "provider": "attacker-provider",
            "model": "attacker-model",
            "voice_id": "attacker-voice",
        }]
        storyboard["storyboard_tts_selection"]["recommendations"] = list(
            storyboard["storyboard_tts_selection"]["top_candidates"]
        )

        recovered = tts_selection_recovery.rehydrate_storyboard_tts_selection(
            SimpleNamespace(),
            workspace,
            storyboard,
            strict=True,
            read_json=read_json,
        )

    selection = recovered["storyboard_tts_selection"]
    assert selection["provider"] == "heygen"
    assert selection["model"] == "heygen-voice-clone-v3"
    assert selection["voice_id"] == "clone_voice_real_001"
    assert selection["top_candidates"] == []


def test_talking_head_voice_can_be_rehydrated_from_trusted_session_variables() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        write_json(
            workspace / "SessionContext/Variables.json",
            {
                "default_voice_clone_config": {
                    "provider": "heygen",
                    "model": "heygen-voice-clone-v3",
                    "selected_voice_id": "clone_voice_real_001",
                    "selected_voice_label": "Customer Clone",
                },
                "talking_head_voice": {
                    "provider": "heygen",
                    "voice_id": "clone_voice_real_001",
                    "voice_label": "Customer Clone",
                },
            },
        )
        storyboard = make_storyboard()
        selection = storyboard["storyboard_tts_selection"]
        selection["provider"] = ""
        selection["model"] = ""
        selection["tempo"] = 0.7

        recovered = tts_selection_recovery.rehydrate_storyboard_tts_selection(
            SimpleNamespace(),
            workspace,
            storyboard,
            strict=True,
            read_json=read_json,
        )

    recovered_selection = recovered["storyboard_tts_selection"]
    assert recovered_selection["provider"] == "heygen"
    assert recovered_selection["model"] == "heygen-voice-clone-v3"
    assert recovered_selection["voice_id"] == "clone_voice_real_001"
    assert recovered_selection["tempo"] == 0.7


def test_disabled_talking_head_voice_config_is_not_trusted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        write_json(
            workspace / "SessionContext/Variables.json",
            {
                "default_voice_clone_config": {
                    "provider": "heygen",
                    "model": "heygen-voice-clone-v3",
                    "selected_voice_id": "clone_voice_real_001",
                    "enabled": False,
                    "active": False,
                    "has_api_key": False,
                },
            },
        )
        storyboard = make_storyboard()
        storyboard["storyboard_tts_selection"]["provider"] = ""
        storyboard["storyboard_tts_selection"]["model"] = ""

        with pytest.raises(ValueError):
            tts_selection_recovery.rehydrate_storyboard_tts_selection(
                SimpleNamespace(),
                workspace,
                storyboard,
                strict=True,
                read_json=read_json,
            )


class EventRepo:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def add_event(self, *args) -> None:
        self.events.append(args)


def make_generation_services() -> SimpleNamespace:
    repo = EventRepo()
    ns = SimpleNamespace(
        ctx=SimpleNamespace(session_repo=repo),
        text=text,
        number=number,
        read_json=read_json,
        write_json=write_json,
        safe_workspace_rel=safe_workspace_rel,
        tts_output_lock_guard=threading.Lock(),
        tts_output_locks={},
        tts_output_generations={},
        load_tts_config=lambda provider, model, **_kwargs: {"provider": provider, "model": model, "api_key": "test"},
        audio_duration_seconds=lambda path: 1.0 if Path(path).exists() else 0.0,
        tts_duration_is_suspicious=lambda *_args: False,
        spoken_char_count=lambda value: len(text(value)),
        tempo_stretch_audio=lambda raw, output, tempo: {"raw_duration": 1.0, "speed_factor": tempo, "tempo": tempo, "stretched": True, "warnings": []},
        locked_tts_cache_text_matches=lambda *_args: True,
    )
    return ns


def test_duplicate_tts_requests_share_one_generation_and_matching_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        sc = make_generation_services()
        calls = {"count": 0}

        def generate(_config, _text, _voice, _prompt, output_path, **_kwargs):
            calls["count"] += 1
            time.sleep(0.05)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"clone-audio")
            return ""

        sc.generate_tts_audio = generate
        output_rel = f"{WORKING_REL}/dak_voice_test_Audio_Final.wav"
        manifest_rel = "SessionOutput/storyboard/tts_manifests/dak_voice_test_Audio_Final.json"
        config_key = json.dumps({"voiceId": "clone_voice_real_001", "text": "测试对白"}, ensure_ascii=False)
        token_a = tts_workflow_services.reserve_tts_output_generation(workspace, output_rel, config_key, sc=sc)
        token_b = tts_workflow_services.reserve_tts_output_generation(workspace, output_rel, config_key, sc=sc)
        assert token_a == token_b
        request = {
            "workflow_id": "duplicate_tts_test",
            "srt_text": "测试对白",
            "use_locked_cache": True,
            "locked_output": output_rel,
            "locked_manifest": manifest_rel,
            "locked_config_key": config_key,
            "output_generation_token": token_a,
        }
        prompt = {
            "provider": "heygen",
            "model": "heygen-voice-clone-v3",
            "voice_id": "clone_voice_real_001",
            "prompt": "自然朗读",
            "text": "测试对白",
            "tempo": 1,
        }
        task = {"id": 266, "session_id": 325, "latest_attempt_id": 1}
        with patch.object(tts_workflow_services, "record_storyboard_usage", return_value={"local_usage_id": "usage_1"}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(tts_workflow_services.run_scene_tts_candidate, task, workspace, request, prompt, output_rel, sc=sc)
                    for _ in range(2)
                ]
                results = [future.result() for future in futures]

        manifest = read_json(workspace / manifest_rel)
        assert calls["count"] == 1
        assert sum(bool(item.get("cache_hit")) for item in results) == 1
        assert (workspace / output_rel).read_bytes() == b"clone-audio"
        assert manifest["config_key"] == config_key
        assert manifest["provider"] == "heygen"
        assert manifest["voice_id"] == "clone_voice_real_001"
        assert manifest["output_sha256"] == tts_workflow_services.file_sha256(workspace / output_rel)


def test_video_plan_executor_is_never_allowed_to_fallback_to_default_tts() -> None:
    source = (REPO_ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tool_runner_services.py").read_text(encoding="utf-8")

    assert '"--no-execute-audio"' in source
    assert "prepare_video_plan_selected_tts_audio" in source


def test_selected_voice_change_invalidates_video_plan_signature() -> None:
    target = {"target_type": "task", "shot_id": "", "scene_id": ""}
    sc = SimpleNamespace(text=text, number=number)
    first = make_storyboard()
    second = json.loads(json.dumps(first))
    second["storyboard_tts_selection"]["voice_id"] = "clone_voice_real_002"
    second["storyboard_tts_selection"]["voice"] = "clone_voice_real_002"

    first_snapshot = video_plan_signature_services.video_plan_input_snapshot(first, target, sc=sc)
    second_snapshot = video_plan_signature_services.video_plan_input_snapshot(second, target, sc=sc)

    assert first_snapshot["tts_selection"]["voice_id"] == "clone_voice_real_001"
    assert second_snapshot["tts_selection"]["voice_id"] == "clone_voice_real_002"
    assert video_plan_signature_services.stable_video_plan_hash(first_snapshot) != video_plan_signature_services.stable_video_plan_hash(second_snapshot)
