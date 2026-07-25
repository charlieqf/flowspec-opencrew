from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "OpenCrew" / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"
HELPER = Path(__file__).with_name("test_video_plan_executor.py")


def load_tool():
    spec = importlib.util.spec_from_file_location("analysis_v1_video_plan_executor_real", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_helper():
    spec = importlib.util.spec_from_file_location("analysis_v1_video_plan_executor_helper", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def active_config_from_database(module, kind: str) -> tuple[str, str, int, str]:
    conn = module.postgres_connect(os.environ.get("OPENCREW_DATABASE_URL", "") or module.DEFAULT_OPENCREW_DATABASE_URL)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT provider, model, length(coalesce(api_key_ciphertext, '')), coalesce(api_key_ref, '')
FROM tool_media_provider_configs
WHERE kind = %s AND enabled = TRUE
ORDER BY active DESC, id ASC
LIMIT 1
""",
                (kind,),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        pytest.fail(f"Real model test could not find an enabled {kind} config in tool_media_provider_configs.")
    provider, model, secret_length, api_key_ref = row
    return str(provider or "").strip(), str(model or "").strip(), int(secret_length or 0), str(api_key_ref or "").strip()


def test_real_image_video_lipsync_model_chain(tmp_path) -> None:
    if os.environ.get("OPENCREW_REAL_MODEL_TESTS") != "1":
        pytest.skip("Set OPENCREW_REAL_MODEL_TESTS=1 to run paid real image/video/lipsync provider calls.")
    module = load_tool()
    helper = load_helper()
    workspace = helper.make_workspace(tmp_path, need_lipsync=True)
    variables_path = workspace / "SessionContext" / "Variables.json"
    variables = json.loads(variables_path.read_text(encoding="utf-8"))
    for kind in ("image", "video", "lipsync"):
        provider = os.environ.get(f"OPENCREW_{kind.upper()}_PROVIDER", "").strip()
        model = os.environ.get(f"OPENCREW_{kind.upper()}_MODEL", "").strip()
        key = os.environ.get(f"OPENCREW_{kind.upper()}_API_KEY", "").strip()
        has_key = bool(key)
        if not (provider and model and key):
            provider, model, secret_length, api_key_ref = active_config_from_database(module, kind)
            if secret_length <= 0:
                pytest.fail(f"Real model test found {kind} config {provider}/{model}, but it has no stored API key.")
            has_key = True
        else:
            api_key_ref = variables[f"default_{kind}_config"].get("api_key_ref", "")
        if not provider or not model or not has_key:
            pytest.fail(f"Real model test requires either env config or enabled database config for {kind}.")
        variables[f"default_{kind}_config"].update({"provider": provider, "model": model, "api_key_ref": api_key_ref, "has_api_key": True})
    variables_path.write_text(json.dumps(variables, ensure_ascii=False, indent=2), encoding="utf-8")

    result = module.run(helper.args_for(module, workspace, provider_timeout_seconds=int(os.environ.get("OPENCREW_REAL_MODEL_TIMEOUT_SECONDS", "1800"))))

    assert result["status"] == "completed", json.dumps(result, ensure_ascii=False, indent=2)
    segment = result["segments"][0]
    assert segment["status"] == "completed"
    assert segment["model_calls"]["video"]["response"]["provider"]
    assert segment["model_calls"]["lipsync"]["response"]["provider"]
    for rel_path in (
        "SessionOutput/storyboard/Working/srt_0001_Image_01.png",
        "SessionOutput/storyboard/Working/srt_0001_SegmentAudio_Final.wav",
        "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4",
        "SessionOutput/storyboard/Working/srt_0001_TailFrame.jpg",
    ):
        path = workspace / rel_path
        assert path.exists(), rel_path
        assert path.stat().st_size > 0, rel_path
