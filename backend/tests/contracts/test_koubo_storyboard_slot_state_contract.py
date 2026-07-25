from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
CASE_DOC = REPO_ROOT / "docs" / "SessionDesign-R2" / "Koubo_VideoPlan_槽位颜色测试案例表.md"
SLOT_STATE_PATH = BACKEND_PATH / "opcrew_backend" / "koubo" / "koubo_storyboard" / "slot_state_services.py"


def load_slot_state_module():
    spec = importlib.util.spec_from_file_location("koubo_slot_state_contract_module", SLOT_STATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SLOT_STATE = load_slot_state_module()
BLOCKED_WAITING_INPUT = SLOT_STATE.BLOCKED_WAITING_INPUT
SKIPPED_CONSUMED_BY_DOWNSTREAM = SLOT_STATE.SKIPPED_CONSUMED_BY_DOWNSTREAM
SlotInputs = SLOT_STATE.SlotInputs
derive_image_plan_slot_states = SLOT_STATE.derive_image_plan_slot_states
derive_video_only_plan_slot_states = SLOT_STATE.derive_video_only_plan_slot_states
derive_video_plan_slot_states = SLOT_STATE.derive_video_plan_slot_states
slot_inputs_from_vector = SLOT_STATE.slot_inputs_from_vector


def parse_slots(raw: str) -> tuple[int, int, int, int, int]:
    parsed = ast.literal_eval(raw)
    assert isinstance(parsed, list) and len(parsed) == 5
    return tuple(1 if bool(item) else 0 for item in parsed)  # type: ignore[return-value]


def markdown_rows() -> dict[str, list[dict[str, str]]]:
    rows: dict[str, list[dict[str, str]]] = {
        "video_basic": [],
        "video_prompt": [],
        "image_basic": [],
        "image_prompt": [],
        "video_only_basic": [],
        "video_only_prompt": [],
    }
    for line in CASE_DOC.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not (value.startswith("| VP-") or value.startswith("| IP-") or value.startswith("| VOP-")):
            continue
        cells = [cell.strip() for cell in value.strip("|").split("|")]
        case_id = cells[0]
        if case_id.startswith("VP-S") and len(cells) >= 7:
            rows["video_basic"].append({"case_id": case_id, "slots": cells[1], "audio": cells[2], "image": cells[3], "raw_video": cells[4], "final_video": cells[5]})
        elif case_id.startswith("VP-P") and len(cells) >= 7:
            rows["video_prompt"].append({"case_id": case_id, "scene": cells[1], "input": cells[2], "prompt_exists": cells[3], "prompt": cells[4]})
        elif case_id.startswith("IP-S") and len(cells) >= 5:
            rows["image_basic"].append({"case_id": case_id, "slots": cells[1], "image_prompt": cells[2], "image": cells[3]})
        elif case_id.startswith("IP-P") and len(cells) >= 6:
            rows["image_prompt"].append({"case_id": case_id, "slots": cells[1], "prompt_exists": cells[2], "image_prompt": cells[3], "image": cells[4]})
        elif case_id.startswith("VOP-S") and len(cells) >= 8:
            rows["video_only_basic"].append({"case_id": case_id, "slots": cells[1], "audio": cells[2], "image": cells[3], "video_prompt": cells[4], "raw_video": cells[5], "copy_final": cells[6]})
        elif case_id.startswith("VOP-P") and len(cells) >= 8:
            rows["video_only_prompt"].append({"case_id": case_id, "slots": cells[1], "prompt_exists": cells[2], "video_prompt": cells[3], "raw_video": cells[4], "copy_final": cells[5]})
    return rows


def color_zh(states: dict[str, dict], key: str) -> str:
    return str(states[key]["color_zh"])


def video_prompt_slots(input_text: str) -> tuple[int, int, int, int, int]:
    if "原图存在，新图/Raw/Final 不存在" in input_text:
        return (0, 1, 0, 0, 0)
    if "新图存在，Raw/Final 不存在" in input_text:
        return (0, 0, 1, 0, 0)
    if "Raw 存在，Final 不存在" in input_text:
        return (0, 0, 0, 1, 0)
    raise AssertionError(f"Cannot infer prompt row input: {input_text}")


class KouboStoryboardSlotStateContractTest(unittest.TestCase):
    def test_video_plan_basic_cases_match_markdown_table(self) -> None:
        for row in markdown_rows()["video_basic"]:
            with self.subTest(row["case_id"]):
                states = derive_video_plan_slot_states(slot_inputs_from_vector(parse_slots(row["slots"])))
                self.assertEqual(color_zh(states, "audio"), row["audio"])
                self.assertEqual(color_zh(states, "image"), row["image"])
                self.assertEqual(color_zh(states, "raw_video"), row["raw_video"])
                self.assertEqual(color_zh(states, "final_video"), row["final_video"])

    def test_video_plan_prompt_cases_match_markdown_table(self) -> None:
        for row in markdown_rows()["video_prompt"]:
            with self.subTest(row["case_id"]):
                prompt_exists = row["prompt_exists"] == "存在"
                kwargs = {"image_prompt_exists": prompt_exists} if row["scene"].startswith("Image Prompt") else {"video_prompt_exists": prompt_exists}
                states = derive_video_plan_slot_states(slot_inputs_from_vector(video_prompt_slots(row["input"]), **kwargs))
                key = "image_prompt" if row["scene"].startswith("Image Prompt") else "video_prompt"
                self.assertEqual(color_zh(states, key), row["prompt"])

    def test_image_plan_cases_match_markdown_table(self) -> None:
        rows = markdown_rows()
        for row in rows["image_basic"]:
            with self.subTest(row["case_id"]):
                states = derive_image_plan_slot_states(slot_inputs_from_vector(parse_slots(row["slots"])))
                self.assertEqual(color_zh(states, "image_prompt"), row["image_prompt"])
                self.assertEqual(color_zh(states, "image"), row["image"])
        for row in rows["image_prompt"]:
            with self.subTest(row["case_id"]):
                states = derive_image_plan_slot_states(slot_inputs_from_vector(parse_slots(row["slots"]), image_prompt_exists=row["prompt_exists"] == "存在"))
                self.assertEqual(color_zh(states, "image_prompt"), row["image_prompt"])
                self.assertEqual(color_zh(states, "image"), row["image"])

    def test_video_only_plan_cases_match_markdown_table(self) -> None:
        rows = markdown_rows()
        for row in rows["video_only_basic"]:
            with self.subTest(row["case_id"]):
                states = derive_video_only_plan_slot_states(slot_inputs_from_vector(parse_slots(row["slots"])))
                self.assertEqual(color_zh(states, "audio"), row["audio"])
                self.assertEqual(color_zh(states, "image"), row["image"])
                self.assertEqual(color_zh(states, "video_prompt"), row["video_prompt"])
                self.assertEqual(color_zh(states, "raw_video"), row["raw_video"])
                self.assertEqual(color_zh(states, "copy_final"), row["copy_final"])
        for row in rows["video_only_prompt"]:
            with self.subTest(row["case_id"]):
                states = derive_video_only_plan_slot_states(slot_inputs_from_vector(parse_slots(row["slots"]), video_prompt_exists=row["prompt_exists"] == "存在"))
                self.assertEqual(color_zh(states, "video_prompt"), row["video_prompt"])
                self.assertEqual(color_zh(states, "raw_video"), row["raw_video"])
                self.assertEqual(color_zh(states, "copy_final"), row["copy_final"])

    def test_running_and_failed_never_override_existing_file_green(self) -> None:
        no_file = slot_inputs_from_vector([0, 0, 1, 0, 0])
        running = derive_video_plan_slot_states(no_file, {"raw_video": "running"})
        failed = derive_video_plan_slot_states(no_file, {"raw_video": "failed"})
        self.assertEqual(running["raw_video"]["color_zh"], "黄")
        self.assertEqual(failed["raw_video"]["color_zh"], "红")
        with_file = slot_inputs_from_vector([0, 0, 1, 1, 0])
        green = derive_video_plan_slot_states(with_file, {"raw_video": "failed"})
        self.assertEqual(green["raw_video"]["color_zh"], "绿")

    def test_gray_reasons_distinguish_blocked_from_consumed(self) -> None:
        blocked = derive_image_plan_slot_states(slot_inputs_from_vector([0, 0, 0, 0, 0]))
        consumed = derive_image_plan_slot_states(slot_inputs_from_vector([0, 0, 0, 1, 0]))
        self.assertEqual(blocked["image"]["reason"], BLOCKED_WAITING_INPUT)
        self.assertEqual(consumed["image"]["reason"], SKIPPED_CONSUMED_BY_DOWNSTREAM)

    def test_final_file_unbound_stays_green_with_repair_reason(self) -> None:
        video_states = derive_video_plan_slot_states(SlotInputs(final_exists=True, binding_missing=True))
        self.assertEqual(video_states["final_video"]["color_zh"], "绿")
        self.assertEqual(video_states["final_video"]["ui_tone"], "done")
        self.assertEqual(video_states["final_video"]["binding_consistency"], "file_exists_unbound")

        video_only_states = derive_video_only_plan_slot_states(SlotInputs(final_exists=True, binding_missing=True))
        self.assertEqual(video_only_states["copy_final"]["color_zh"], "绿")
        self.assertEqual(video_only_states["copy_final"]["ui_tone"], "done")
        self.assertEqual(video_only_states["copy_final"]["binding_consistency"], "file_exists_unbound")


if __name__ == "__main__":
    unittest.main()
