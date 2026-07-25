#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_DOC = ROOT / "docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md"

GREEN = "绿"
WHITE = "白"
GRAY = "灰"

COLOR_AUTO = "auto"
COLOR_ALWAYS = "always"
COLOR_NEVER = "never"
ANSI_RESET = "\033[0m"
ANSI_COLORS = {
    GREEN: "\033[32m",
    WHITE: "\033[97m",
    GRAY: "\033[90m",
}


@dataclass(frozen=True)
class SlotState:
    audio: str
    image: str
    raw_video: str
    final_video: str
    image_prompt: str
    video_prompt: str


@dataclass(frozen=True)
class ImagePlanState:
    image_prompt: str
    image: str


@dataclass(frozen=True)
class VideoOnlyPlanState:
    audio: str
    image: str
    video_prompt: str
    raw_video: str
    copy_final: str


def normalize_bool(value: str | bool | int) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "有", "存在"}:
        return True
    if normalized in {"0", "false", "no", "n", "无", "不存在"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def normalize_color_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {COLOR_AUTO, COLOR_ALWAYS, COLOR_NEVER}:
        return normalized
    raise ValueError("color must be one of: auto, always, never")


def should_colorize(color_mode: str) -> bool:
    normalized = normalize_color_mode(color_mode)
    if normalized == COLOR_ALWAYS:
        return True
    if normalized == COLOR_NEVER:
        return False
    return sys.stdout.isatty()


def colorize_state(value: str, *, color_enabled: bool) -> str:
    if not color_enabled:
        return value
    prefix = ANSI_COLORS.get(value)
    if not prefix:
        return value
    return f"{prefix}{value}{ANSI_RESET}"


def state_token(value: str, *, color_enabled: bool) -> str:
    return f"【{colorize_state(value, color_enabled=color_enabled)}】"


def normalize_content_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"口播", "talking", "talking_head", "lipsync", "sync"}:
        return "talking"
    if normalized in {"空镜", "cutaway", "broll", "audio_compose", "audio_replace_retime", "compose"}:
        return "cutaway"
    raise ValueError("content type must be one of: 口播/talking/lipsync, 空镜/cutaway/audio_compose")


def video_plan_final_step_label(content_type: str) -> str:
    return "音频匹配" if normalize_content_type(content_type) == "talking" else "音频合成"


def parse_slots(raw: str) -> tuple[int, int, int, int, int]:
    try:
        parsed = ast.literal_eval(raw)
    except Exception as exc:
        raise ValueError("slots must look like [0, 0, 0, 0, 0]") from exc
    if not isinstance(parsed, (list, tuple)) or len(parsed) != 5:
        raise ValueError("slots must contain exactly 5 values: [audio, source, image, raw, final]")
    values = []
    for item in parsed:
        if item not in (0, 1, False, True):
            raise ValueError("each slot value must be 0 or 1")
        values.append(1 if bool(item) else 0)
    return tuple(values)  # type: ignore[return-value]


def derive_state(
    slots: tuple[int, int, int, int, int],
    *,
    image_prompt_exists: bool = False,
    video_prompt_exists: bool = False,
) -> SlotState:
    audio_exists, source_exists, image_exists, raw_exists, final_exists = [bool(item) for item in slots]

    audio = GREEN if audio_exists else WHITE

    if image_exists:
        image = GREEN
    elif raw_exists or final_exists:
        image = GRAY
    elif source_exists:
        image = WHITE
    else:
        image = GRAY

    if raw_exists:
        raw_video = GREEN
    elif final_exists:
        raw_video = GRAY
    elif image_exists:
        raw_video = WHITE
    else:
        raw_video = GRAY

    if final_exists:
        final_video = GREEN
    elif raw_exists and audio_exists:
        final_video = WHITE
    else:
        final_video = GRAY

    if image_prompt_exists:
        image_prompt = GREEN
    elif image_exists or raw_exists or final_exists:
        image_prompt = GRAY
    elif source_exists:
        image_prompt = WHITE
    else:
        image_prompt = GRAY

    if video_prompt_exists:
        video_prompt = GREEN
    elif raw_exists or final_exists:
        video_prompt = GRAY
    elif image_exists:
        video_prompt = WHITE
    else:
        video_prompt = GRAY

    return SlotState(
        audio=audio,
        image=image,
        raw_video=raw_video,
        final_video=final_video,
        image_prompt=image_prompt,
        video_prompt=video_prompt,
    )


def derive_image_plan_state(
    slots: tuple[int, int, int, int, int],
    *,
    image_prompt_exists: bool = False,
) -> ImagePlanState:
    _audio_exists, source_exists, image_exists, raw_exists, final_exists = [bool(item) for item in slots]

    if image_prompt_exists:
        image_prompt = GREEN
    elif source_exists and not image_exists and not raw_exists and not final_exists:
        image_prompt = WHITE
    else:
        image_prompt = GRAY

    if image_exists:
        image = GREEN
    elif raw_exists or final_exists:
        image = GRAY
    elif image_prompt_exists and source_exists:
        image = WHITE
    else:
        image = GRAY

    return ImagePlanState(image_prompt=image_prompt, image=image)


def derive_video_only_plan_state(
    slots: tuple[int, int, int, int, int],
    *,
    video_prompt_exists: bool = False,
) -> VideoOnlyPlanState:
    audio_exists, source_exists, image_exists, raw_exists, final_exists = [bool(item) for item in slots]

    audio = GREEN if audio_exists else WHITE

    if image_exists:
        image = GREEN
    elif raw_exists or final_exists:
        image = GRAY
    elif source_exists:
        image = WHITE
    else:
        image = GRAY

    if video_prompt_exists:
        video_prompt = GREEN
    elif raw_exists or final_exists:
        video_prompt = GRAY
    elif image_exists:
        video_prompt = WHITE
    else:
        video_prompt = GRAY

    if raw_exists:
        raw_video = GREEN
    elif final_exists:
        raw_video = GRAY
    elif video_prompt_exists and image_exists:
        raw_video = WHITE
    else:
        raw_video = GRAY

    if final_exists:
        copy_final = GREEN
    elif raw_exists:
        copy_final = WHITE
    else:
        copy_final = GRAY

    return VideoOnlyPlanState(
        audio=audio,
        image=image,
        video_prompt=video_prompt,
        raw_video=raw_video,
        copy_final=copy_final,
    )


def parse_markdown_rows(path: Path) -> dict[str, list[dict[str, str]]]:
    rows: dict[str, list[dict[str, str]]] = {
        "video_basic": [],
        "video_prompt": [],
        "image_basic": [],
        "image_prompt": [],
        "video_only_basic": [],
        "video_only_prompt": [],
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not (value.startswith("| VP-") or value.startswith("| IP-") or value.startswith("| VOP-")) or value.startswith("| 用例编号"):
            continue
        cells = [cell.strip() for cell in value.strip("|").split("|")]
        if cells[0].startswith("VP-S") and len(cells) >= 7:
            rows["video_basic"].append({
                "case_id": cells[0],
                "slots": cells[1],
                "audio": cells[2],
                "image": cells[3],
                "raw_video": cells[4],
                "final_video": cells[5],
            })
        elif cells[0].startswith("VP-P") and len(cells) >= 7:
            rows["video_prompt"].append({
                "case_id": cells[0],
                "scene": cells[1],
                "input": cells[2],
                "prompt_exists": cells[3],
                "prompt": cells[4],
                "related": cells[5],
            })
        elif cells[0].startswith("IP-S") and len(cells) >= 5:
            rows["image_basic"].append({
                "case_id": cells[0],
                "slots": cells[1],
                "image_prompt": cells[2],
                "image": cells[3],
            })
        elif cells[0].startswith("IP-P") and len(cells) >= 6:
            rows["image_prompt"].append({
                "case_id": cells[0],
                "slots": cells[1],
                "prompt_exists": cells[2],
                "image_prompt": cells[3],
                "image": cells[4],
            })
        elif cells[0].startswith("VOP-S") and len(cells) >= 8:
            rows["video_only_basic"].append({
                "case_id": cells[0],
                "slots": cells[1],
                "audio": cells[2],
                "image": cells[3],
                "video_prompt": cells[4],
                "raw_video": cells[5],
                "copy_final": cells[6],
            })
        elif cells[0].startswith("VOP-P") and len(cells) >= 8:
            rows["video_only_prompt"].append({
                "case_id": cells[0],
                "slots": cells[1],
                "prompt_exists": cells[2],
                "video_prompt": cells[3],
                "raw_video": cells[4],
                "copy_final": cells[5],
            })
    return rows


def infer_prompt_row(row: dict[str, str]) -> tuple[str, SlotState]:
    scene = row["scene"]
    prompt_exists = row["prompt_exists"] == "存在"
    if "原图存在，新图/Raw/Final 不存在" in row["input"]:
        slots = (0, 1, 0, 0, 0)
    elif "新图存在，Raw/Final 不存在" in row["input"]:
        slots = (0, 0, 1, 0, 0)
    elif "Raw 存在，Final 不存在" in row["input"]:
        slots = (0, 0, 0, 1, 0)
    else:
        raise ValueError(f"Cannot infer prompt row input for {row['case_id']}: {row['input']}")
    if scene.startswith("Image Prompt"):
        return "image_prompt", derive_state(slots, image_prompt_exists=prompt_exists)
    if scene.startswith("Video Prompt"):
        return "video_prompt", derive_state(slots, video_prompt_exists=prompt_exists)
    raise ValueError(f"Cannot infer prompt type for {row['case_id']}: {scene}")


def compare_doc(path: Path) -> list[dict[str, str]]:
    rows = parse_markdown_rows(path)
    differences: list[dict[str, str]] = []

    for row in rows["video_basic"]:
        slots = parse_slots(row["slots"])
        state = derive_state(slots)
        expected = {
            "audio": row["audio"],
            "image": row["image"],
            "raw_video": row["raw_video"],
            "final_video": row["final_video"],
        }
        actual = {
            "audio": state.audio,
            "image": state.image,
            "raw_video": state.raw_video,
            "final_video": state.final_video,
        }
        for key in expected:
            if expected[key] != actual[key]:
                differences.append({
                    "case_id": row["case_id"],
                    "field": key,
                    "expected": expected[key],
                    "actual": actual[key],
                    "slots": row["slots"],
                })

    for row in rows["video_prompt"]:
        prompt_key, state = infer_prompt_row(row)
        actual = state.image_prompt if prompt_key == "image_prompt" else state.video_prompt
        if row["prompt"] != actual:
            differences.append({
                "case_id": row["case_id"],
                "field": prompt_key,
                "expected": row["prompt"],
                "actual": actual,
                "slots": row["input"],
            })

    for row in rows["image_basic"]:
        slots = parse_slots(row["slots"])
        state = derive_image_plan_state(slots, image_prompt_exists=False)
        expected = {"image_prompt": row["image_prompt"], "image": row["image"]}
        actual = {"image_prompt": state.image_prompt, "image": state.image}
        for key in expected:
            if expected[key] != actual[key]:
                differences.append({
                    "case_id": row["case_id"],
                    "field": key,
                    "expected": expected[key],
                    "actual": actual[key],
                    "slots": row["slots"],
                })

    for row in rows["image_prompt"]:
        slots = parse_slots(row["slots"])
        state = derive_image_plan_state(slots, image_prompt_exists=row["prompt_exists"] == "存在")
        expected = {"image_prompt": row["image_prompt"], "image": row["image"]}
        actual = {"image_prompt": state.image_prompt, "image": state.image}
        for key in expected:
            if expected[key] != actual[key]:
                differences.append({
                    "case_id": row["case_id"],
                    "field": key,
                    "expected": expected[key],
                    "actual": actual[key],
                    "slots": row["slots"],
                })

    for row in rows["video_only_basic"]:
        slots = parse_slots(row["slots"])
        state = derive_video_only_plan_state(slots, video_prompt_exists=False)
        expected = {
            "audio": row["audio"],
            "image": row["image"],
            "video_prompt": row["video_prompt"],
            "raw_video": row["raw_video"],
            "copy_final": row["copy_final"],
        }
        actual = {
            "audio": state.audio,
            "image": state.image,
            "video_prompt": state.video_prompt,
            "raw_video": state.raw_video,
            "copy_final": state.copy_final,
        }
        for key in expected:
            if expected[key] != actual[key]:
                differences.append({
                    "case_id": row["case_id"],
                    "field": key,
                    "expected": expected[key],
                    "actual": actual[key],
                    "slots": row["slots"],
                })

    for row in rows["video_only_prompt"]:
        slots = parse_slots(row["slots"])
        state = derive_video_only_plan_state(slots, video_prompt_exists=row["prompt_exists"] == "存在")
        expected = {
            "video_prompt": row["video_prompt"],
            "raw_video": row["raw_video"],
            "copy_final": row["copy_final"],
        }
        actual = {
            "video_prompt": state.video_prompt,
            "raw_video": state.raw_video,
            "copy_final": state.copy_final,
        }
        for key in expected:
            if expected[key] != actual[key]:
                differences.append({
                    "case_id": row["case_id"],
                    "field": key,
                    "expected": expected[key],
                    "actual": actual[key],
                    "slots": row["slots"],
                })
    return differences


def print_state(
    slots: tuple[int, int, int, int, int],
    state: SlotState,
    image_plan_state: ImagePlanState,
    video_only_plan_state: VideoOnlyPlanState,
    *,
    content_type: str = "口播",
    color_mode: str = COLOR_ALWAYS,
) -> None:
    final_label = video_plan_final_step_label(content_type)
    color_enabled = should_colorize(color_mode)
    print(f"slots={list(slots)}")
    print(f"content_type={normalize_content_type(content_type)}")
    print(f"Video Plan Result: 音频{state_token(state.audio, color_enabled=color_enabled)} -> 新图{state_token(state.image, color_enabled=color_enabled)} -> 新视频{state_token(state.raw_video, color_enabled=color_enabled)} -> {final_label}{state_token(state.final_video, color_enabled=color_enabled)}")
    print(f"Video Only Plan Result: 音频{state_token(video_only_plan_state.audio, color_enabled=color_enabled)} -> 新图{state_token(video_only_plan_state.image, color_enabled=color_enabled)} -> 提示词{state_token(video_only_plan_state.video_prompt, color_enabled=color_enabled)} -> 新视频{state_token(video_only_plan_state.raw_video, color_enabled=color_enabled)} -> 拷贝{state_token(video_only_plan_state.copy_final, color_enabled=color_enabled)}")
    print("Video Plan:")
    print(f"音频={colorize_state(state.audio, color_enabled=color_enabled)}")
    print(f"Image Prompt={colorize_state(state.image_prompt, color_enabled=color_enabled)}")
    print(f"新图={colorize_state(state.image, color_enabled=color_enabled)}")
    print(f"Video Prompt={colorize_state(state.video_prompt, color_enabled=color_enabled)}")
    print(f"新视频={colorize_state(state.raw_video, color_enabled=color_enabled)}")
    print(f"{final_label}={colorize_state(state.final_video, color_enabled=color_enabled)}")
    print("Image Plan:")
    print(f"提示词={colorize_state(image_plan_state.image_prompt, color_enabled=color_enabled)}")
    print(f"新图={colorize_state(image_plan_state.image, color_enabled=color_enabled)}")
    print("Video Only Plan:")
    print(f"音频={colorize_state(video_only_plan_state.audio, color_enabled=color_enabled)}")
    print(f"新图={colorize_state(video_only_plan_state.image, color_enabled=color_enabled)}")
    print(f"提示词={colorize_state(video_only_plan_state.video_prompt, color_enabled=color_enabled)}")
    print(f"新视频={colorize_state(video_only_plan_state.raw_video, color_enabled=color_enabled)}")
    print(f"拷贝={colorize_state(video_only_plan_state.copy_final, color_enabled=color_enabled)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Koubo Video/Image Plan slot color state checker.")
    parser.add_argument("--slots", default="", help="Slot vector, e.g. '[0, 0, 0, 0, 0]'.")
    parser.add_argument("--image-prompt", default="0", help="Whether Image Prompt file exists.")
    parser.add_argument("--video-prompt", default="0", help="Whether Video Prompt file exists.")
    parser.add_argument("--content-type", default="口播", help="Video Plan content type: 口播/talking/lipsync or 空镜/cutaway/audio_compose.")
    parser.add_argument("--color", default=COLOR_ALWAYS, choices=[COLOR_AUTO, COLOR_ALWAYS, COLOR_NEVER], help="Colorize output state words.")
    parser.add_argument("--case-doc", default=str(DEFAULT_CASE_DOC), help="Markdown test case document to compare.")
    parser.add_argument("--compare", action="store_true", help="Compare derived states against the Markdown test case table.")
    args = parser.parse_args()

    if args.slots:
        slots = parse_slots(args.slots)
        state = derive_state(
            slots,
            image_prompt_exists=normalize_bool(args.image_prompt),
            video_prompt_exists=normalize_bool(args.video_prompt),
        )
        image_plan_state = derive_image_plan_state(
            slots,
            image_prompt_exists=normalize_bool(args.image_prompt),
        )
        video_only_plan_state = derive_video_only_plan_state(
            slots,
            video_prompt_exists=normalize_bool(args.video_prompt),
        )
        print_state(slots, state, image_plan_state, video_only_plan_state, content_type=args.content_type, color_mode=args.color)

    if args.compare or not args.slots:
        path = Path(args.case_doc)
        differences = compare_doc(path)
        print(f"case_doc={path}")
        print(f"differences={len(differences)}")
        for item in differences:
            print(
                f"{item['case_id']} {item['field']}: "
                f"expected={item['expected']} actual={item['actual']} input={item['slots']}"
            )
        return 1 if differences else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
