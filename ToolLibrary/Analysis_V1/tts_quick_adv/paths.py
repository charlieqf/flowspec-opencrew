from __future__ import annotations

from pathlib import Path

from . import TOOL_DIR_NAME


VARIABLES_REL = "SessionContext/Variables.json"
SESSION_FINAL_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
SESSION_AUDIO_REFERENCE_REL = "SessionOutput/Audio_Reference.wav"
SESSION_TTS_DIR_REL = "SessionOutput/tts"
SESSION_TTS_FINAL_REL = f"{SESSION_TTS_DIR_REL}/tts_builder_candidates.json"
SESSION_CLOUD_CLONES_REL = f"{SESSION_TTS_DIR_REL}/cloud_voice_clones.json"

WORKING_DIR_REL = f"{TOOL_DIR_NAME}/Working"
OUTPUT_DIR_REL = f"{TOOL_DIR_NAME}/Output"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
REPORT_DIR_REL = f"{TOOL_DIR_NAME}/Report"
INTERACTIVE_DIR_REL = f"{TOOL_DIR_NAME}/Interactive"

WORKING_VARIABLES_REL = f"{WORKING_DIR_REL}/InputFrom_0_Variables.json"
WORKING_FINAL_ITEMS_REL = f"{WORKING_DIR_REL}/InputFrom_4_final_srt_frame_items.json"
WORKING_REFERENCE_REL = f"{WORKING_DIR_REL}/Audio_Reference_Selected.wav"
WORKING_STATE_REL = f"{WORKING_DIR_REL}/State_progress.json"
WORKING_RAW_DIR_REL = f"{WORKING_DIR_REL}/raw_candidates"
WORKING_FIT_DIR_REL = f"{WORKING_DIR_REL}/fitted_candidates"

OUTPUT_REFERENCE_PROFILE_REL = f"{OUTPUT_DIR_REL}/reference_voice_profile.json"
OUTPUT_SAMPLING_AUDIT_REL = f"{OUTPUT_DIR_REL}/reference_sampling_audit.json"
OUTPUT_STAGE1_REL = f"{OUTPUT_DIR_REL}/catalog_stage1_resemblyzer.json"
OUTPUT_STAGE2_REL = f"{OUTPUT_DIR_REL}/catalog_stage2_speechbrain.json"
OUTPUT_FINAL_REL = f"{OUTPUT_DIR_REL}/tts_builder_adv_candidates.json"
OUTPUT_CLOUD_CLONE_REL = f"{OUTPUT_DIR_REL}/cloud_voice_clone.json"
REPORT_RESULT_REL = f"{REPORT_DIR_REL}/Result.json"
INTERACTIVE_RANKING_REL = f"{INTERACTIVE_DIR_REL}/ranking_board.json"
INTERACTIVE_STATE_REL = f"{INTERACTIVE_DIR_REL}/state.json"


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        WORKING_DIR_REL,
        OUTPUT_DIR_REL,
        PROMPT_DIR_REL,
        REPORT_DIR_REL,
        INTERACTIVE_DIR_REL,
        WORKING_RAW_DIR_REL,
        WORKING_FIT_DIR_REL,
        SESSION_TTS_DIR_REL,
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)
