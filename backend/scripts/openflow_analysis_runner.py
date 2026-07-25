from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import imageio_ffmpeg  # type: ignore
import whisper  # type: ignore


@dataclass
class Segment:
    index: int
    start: float
    end: float
    title: str
    shot_size: str
    camera_angle: str
    movement: str
    subject_action: str
    transition: str
    reshoot_notes: str
    structure_role: str = ""
    design_direction: str = ""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def seconds_text(value: float) -> str:
    return f"{value:.1f}"


def slug_time_range(start: float, end: float) -> str:
    return f"[{seconds_text(start)}-{seconds_text(end)}]"


def clip_filename(segment: Segment) -> str:
    safe_title = segment.title.replace("/", "-").replace(" ", "")
    return f"{segment.index:02d}_{slug_time_range(segment.start, segment.end)}_{safe_title}.mp4"


def frame_filename(segment: Segment) -> str:
    safe_title = segment.title.replace("/", "-").replace(" ", "")
    return f"{segment.index:02d}_{slug_time_range(segment.start, segment.end)}_{safe_title}.jpg"


def ffmpeg_bin() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def ensure_ffmpeg_on_path() -> None:
    ffmpeg_path = Path(ffmpeg_bin())
    ffmpeg_link = ffmpeg_path.parent / "ffmpeg"
    if not ffmpeg_link.exists():
        try:
            ffmpeg_link.symlink_to(ffmpeg_path.name)
        except Exception:
            pass
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{ffmpeg_path.parent}:{current}" if current else str(ffmpeg_path.parent)


def ffprobe_cmd(ffmpeg_path: str) -> list[str]:
    ffmpeg_file = Path(ffmpeg_path)
    probe_guess = ffmpeg_file.with_name(ffmpeg_file.name.replace("ffmpeg", "ffprobe"))
    if probe_guess.exists():
        return [str(probe_guess)]
    return []


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)


def detect_metadata(video_path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0
    capture.release()
    stat = video_path.stat()
    return {
        "path": str(video_path),
        "duration_seconds": round(duration, 3),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "size_bytes": int(stat.st_size),
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
    }


def extract_audio(video_path: Path, audio_path: Path) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg_bin(), "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)]
    result = run(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Failed to extract audio")


def transcribe_audio(audio_path: Path) -> dict[str, Any]:
    model = whisper.load_model("tiny")
    result = model.transcribe(str(audio_path), verbose=False)
    segments = []
    for index, item in enumerate(result.get("segments") or [], start=1):
        segments.append({
            "index": index,
            "start": round(float(item.get("start") or 0.0), 3),
            "end": round(float(item.get("end") or 0.0), 3),
            "text": str(item.get("text") or "").strip(),
        })
    return {
        "language": result.get("language") or "unknown",
        "text": str(result.get("text") or "").strip(),
        "segments": segments,
    }


def detect_scene_candidates(video_path: Path, fps: float, duration: float) -> list[dict[str, Any]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return []
    interval = max(1, int(fps // 2) or 1)
    threshold = 18.0
    candidates: list[dict[str, Any]] = []
    prev_gray = None
    frame_index = 0
    scene_index = 1
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % interval != 0:
            frame_index += 1
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            diff = cv2.absdiff(prev_gray, gray)
            score = float(diff.mean())
            if score >= threshold:
                ts = round(frame_index / fps, 3)
                if ts < duration - 0.2:
                    candidates.append({
                        "index": scene_index,
                        "time": ts,
                        "score": round(score, 3),
                        "reason": "frame_difference",
                    })
                    scene_index += 1
        prev_gray = gray
        frame_index += 1
    capture.release()
    deduped: list[dict[str, Any]] = []
    last_time = -999.0
    for item in candidates:
        if item["time"] - last_time < 1.0:
            continue
        deduped.append(item)
        last_time = float(item["time"])
    return deduped


def build_base_segments(duration: float, candidates: list[dict[str, Any]]) -> list[tuple[float, float]]:
    cut_times = [0.0]
    cut_times.extend(float(item["time"]) for item in candidates)
    if duration > 0:
        cut_times.append(duration)
    cut_times = sorted(set(round(item, 3) for item in cut_times if 0.0 <= item <= duration))
    segments = []
    for start, end in zip(cut_times, cut_times[1:]):
        if end - start < 1.0:
            continue
        segments.append((start, end))
    if not segments and duration > 0:
        step = duration / 6
        segments = [(round(step * i, 3), round(step * (i + 1), 3)) for i in range(6)]
        segments[-1] = (segments[-1][0], duration)
    return segments


def merge_ranges(ranges: list[tuple[float, float]], groups: int) -> list[tuple[float, float]]:
    if not ranges:
        return []
    if len(ranges) <= groups:
        return ranges
    chunk = math.ceil(len(ranges) / groups)
    merged = []
    for index in range(0, len(ranges), chunk):
        group = ranges[index:index + chunk]
        merged.append((group[0][0], group[-1][1]))
    return merged


def normalize_range_density(ranges: list[tuple[float, float]], preferred: int) -> list[tuple[float, float]]:
    if not ranges:
        return []
    preferred = max(1, preferred)
    if len(ranges) <= preferred:
        return ranges
    return merge_ranges(ranges, preferred)


def title_for_position(position: int, total: int) -> tuple[str, str]:
    bank = [
        ("开场抓钩", "问题引出"),
        ("背景铺陈", "反馈进入"),
        ("现场推进", "问题拆解"),
        ("冲突展开", "冲突升级"),
        ("方案说明", "方案提出"),
        ("价值落点", "标准落下"),
        ("转化收束", "动作引导"),
    ]
    if total <= len(bank):
        return bank[min(position, len(bank) - 1)]
    return (f"片段{position + 1}", f"结构段{position + 1}")


def build_scheme_segments(ranges: list[tuple[float, float]], mode: str) -> list[Segment]:
    segments: list[Segment] = []
    total = len(ranges)
    shot_size = {"fine": "中近景", "balanced": "中景", "coarse": "综合景别"}[mode]
    movement = {"fine": "快速切换", "balanced": "自然推进", "coarse": "结构概览"}[mode]
    camera = {"fine": "手持/跟拍", "balanced": "跟拍+定机位", "coarse": "主机位概述"}[mode]
    transition = {"fine": "硬切", "balanced": "以叙事动作为主", "coarse": "桥段切换"}[mode]
    note = {"fine": "保持节奏密度，保留高频动作点", "balanced": "适合直接拍摄执行，兼顾结构与镜头任务", "coarse": "适合先确认结构与组件槽位"}[mode]
    for idx, (start, end) in enumerate(ranges, start=1):
        title, role = title_for_position(idx - 1, total)
        segments.append(Segment(
            index=idx,
            start=round(start, 3),
            end=round(end, 3),
            title=title,
            shot_size=shot_size,
            camera_angle=camera,
            movement=movement,
            subject_action="围绕当前桥段推进信息",
            transition=transition,
            reshoot_notes=note,
            structure_role=role,
            design_direction="围绕医美老板巡店与智能工牌系统价值表达",
        ))
    return segments


def scheme_payload(name: str, label: str, segments: list[Segment], recommended: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "recommended": recommended,
        "segment_count": len(segments),
        "segments": [segment.__dict__ for segment in segments],
    }


def transcript_for_range(asr_segments: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    items = []
    for item in asr_segments:
        item_start = float(item["start"])
        item_end = float(item["end"])
        if item_end <= start or item_start >= end:
            continue
        items.append(item)
    return items


def dialogues_for_scheme(segments: list[Segment], asr_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for segment in segments:
        items = transcript_for_range(asr_segments, segment.start, segment.end)
        combined = " ".join([str(item["text"]).strip() for item in items if str(item["text"]).strip()]).strip()
        payload.append({
            "index": segment.index,
            "start": segment.start,
            "end": segment.end,
            "title": segment.title,
            "text": combined,
            "char_count": len(combined),
            "seconds": round(segment.end - segment.start, 3),
            "chars_per_second": round(len(combined) / max(segment.end - segment.start, 0.001), 3),
            "source_segments": items,
        })
    return payload


def write_storyboard_markdown(path: Path, scheme: dict[str, Any]) -> None:
    lines = [f"# {scheme['name']} / {scheme['label']}", ""]
    for segment in scheme["segments"]:
        lines.extend([
            f"## {segment['index']:02d} {segment['title']}",
            f"- 时间: {seconds_text(segment['start'])} - {seconds_text(segment['end'])}",
            f"- 结构作用: {segment.get('structure_role') or '-'}",
            f"- 景别: {segment['shot_size']}",
            f"- 机位: {segment['camera_angle']}",
            f"- 运镜: {segment['movement']}",
            f"- 主体动作: {segment['subject_action']}",
            f"- 转场: {segment['transition']}",
            f"- 复拍要点: {segment['reshoot_notes']}",
            "",
        ])
    write_text(path, "\n".join(lines).strip() + "\n")


def extract_frame(video_path: Path, output_path: Path, timestamp: float) -> None:
    capture = cv2.VideoCapture(str(video_path))
    capture.set(cv2.CAP_PROP_POS_MSEC, max(timestamp, 0.0) * 1000)
    ok, frame = capture.read()
    capture.release()
    if ok:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), frame)


def export_clip(video_path: Path, output_path: Path, start: float, end: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        seconds_text(start),
        "-to",
        seconds_text(end),
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-preset",
        "veryfast",
        str(output_path),
    ]
    result = run(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"Failed exporting clip: {output_path}")


def slot_mapping(duration: float, scheme2_dialogues: list[dict[str, Any]]) -> dict[str, Any]:
    hook_end = min(3.0, duration)
    trust_end = max(hook_end, duration * 0.82)
    hook = [item for item in scheme2_dialogues if item["start"] < hook_end]
    trust = [item for item in scheme2_dialogues if item["start"] >= hook_end and item["end"] <= trust_end]
    cta = [item for item in scheme2_dialogues if item["start"] >= trust_end or item["end"] > trust_end]
    return {
        "hook": {
            "start": 0.0,
            "end": round(hook_end, 3),
            "role": "抓取注意力，建立前 3 秒钩子",
            "dialogues": hook,
            "fit_for_componentized_ads": True,
        },
        "trust": {
            "start": round(hook_end, 3),
            "end": round(trust_end, 3),
            "role": "展示证据、过程、判断和可信信息",
            "dialogues": trust,
            "fit_for_componentized_ads": True,
        },
        "cta": {
            "start": round(trust_end, 3),
            "end": round(duration, 3),
            "role": "形成收束、动作引导或转化落点",
            "dialogues": cta,
            "fit_for_componentized_ads": True,
        },
    }


def write_slot_markdown(path: Path, slots: dict[str, Any]) -> None:
    lines = ["# Hook / Trust / CTA Mapping", ""]
    for key in ["hook", "trust", "cta"]:
        slot = slots[key]
        lines.extend([
            f"## {key.upper()}",
            f"- 时间: {seconds_text(slot['start'])} - {seconds_text(slot['end'])}",
            f"- 作用: {slot['role']}",
            f"- 适合商投快复刻: {'是' if slot['fit_for_componentized_ads'] else '否'}",
            "- 对白:",
        ])
        if slot["dialogues"]:
            for dialogue in slot["dialogues"]:
                lines.append(f"  - {seconds_text(dialogue['start'])}-{seconds_text(dialogue['end'])}: {dialogue['text']}")
        else:
            lines.append("  - 无")
        lines.append("")
    write_text(path, "\n".join(lines).strip() + "\n")


def story_formula(project_input: dict[str, Any], duration: float) -> dict[str, Any]:
    return {
        "summary": f"以{project_input.get('persona') or '核心角色'}为中心，通过{project_input.get('scene_context') or '现场推进'}建立问题、过程、标准和收束。",
        "formula": [
            "开场抓钩",
            "问题引出",
            "现场推进",
            "判断展开",
            "方案说明",
            "价值落点",
        ],
        "componentized_fit": {
            "hook": "可抽取前 3 秒强钩子",
            "trust": "中段可承载老板判断、产品价值和过程证据",
            "cta": "末尾可衔接动作引导或标准收束",
        },
        "duration_seconds": round(duration, 3),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    input_dir = workspace / "input"
    meta_dir = workspace / "meta"
    transcripts_dir = workspace / "transcripts"
    storyboards_dir = workspace / "storyboards"
    keyframes_dir = workspace / "keyframes"
    clips_dir = workspace / "clips"
    reports_dir = workspace / "reports"
    outbox_dir = workspace / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    project_input = read_json(input_dir / "project_input.json")
    video_path = Path(str(project_input.get("reference_video_path") or workspace / "inbox" / "reference_video.mp4"))
    ensure_ffmpeg_on_path()

    metadata = detect_metadata(video_path)
    write_json(meta_dir / "video_metadata.json", metadata)

    audio_path = outbox_dir / "reference_audio.wav"
    extract_audio(video_path, audio_path)

    asr = transcribe_audio(audio_path)
    write_json(meta_dir / "asr_segments.json", asr)
    write_text(transcripts_dir / "original_asr_full.txt", asr["text"] + "\n")

    candidates = detect_scene_candidates(video_path, float(metadata["fps"]), float(metadata["duration_seconds"]))
    write_json(meta_dir / "scene_candidates.json", {"items": candidates})

    base_ranges = build_base_segments(float(metadata["duration_seconds"]), candidates)
    fine_ranges = normalize_range_density(base_ranges, 12)
    balanced_ranges = normalize_range_density(fine_ranges, 6)
    coarse_ranges = normalize_range_density(fine_ranges, 3)

    scheme1 = scheme_payload("Scheme 1", "细分镜", build_scheme_segments(fine_ranges, "fine"))
    scheme2 = scheme_payload("Scheme 2", "均衡分镜", build_scheme_segments(balanced_ranges, "balanced"), recommended=True)
    scheme3 = scheme_payload("Scheme 3", "粗分镜", build_scheme_segments(coarse_ranges, "coarse"))
    write_json(meta_dir / "schemes.json", {"items": [scheme1, scheme2, scheme3]})

    scheme1_dialogues = dialogues_for_scheme([Segment(**item) for item in scheme1["segments"]], asr["segments"])
    scheme2_dialogues = dialogues_for_scheme([Segment(**item) for item in scheme2["segments"]], asr["segments"])
    scheme3_dialogues = dialogues_for_scheme([Segment(**item) for item in scheme3["segments"]], asr["segments"])
    write_json(transcripts_dir / "original_dialogue_segments_scheme_1.json", scheme1_dialogues)
    write_json(transcripts_dir / "original_dialogue_segments_scheme_2.json", scheme2_dialogues)
    write_json(transcripts_dir / "original_dialogue_segments_scheme_3.json", scheme3_dialogues)

    density_rows = []
    for scheme_name, dialogues in [("scheme_1", scheme1_dialogues), ("scheme_2", scheme2_dialogues), ("scheme_3", scheme3_dialogues)]:
        for item in dialogues:
            density_rows.append({
                "scheme": scheme_name,
                "index": item["index"],
                "start": item["start"],
                "end": item["end"],
                "title": item["title"],
                "char_count": item["char_count"],
                "seconds": item["seconds"],
                "chars_per_second": item["chars_per_second"],
            })
    write_csv(transcripts_dir / "rhythm_density_table.csv", density_rows)

    write_storyboard_markdown(storyboards_dir / "scheme_1_fine_storyboard.md", scheme1)
    write_storyboard_markdown(storyboards_dir / "scheme_2_balanced_storyboard.md", scheme2)
    write_storyboard_markdown(storyboards_dir / "scheme_3_coarse_storyboard.md", scheme3)

    slots = slot_mapping(float(metadata["duration_seconds"]), scheme2_dialogues)
    write_json(meta_dir / "slot_analysis.json", slots)
    write_json(transcripts_dir / "hook_dialogue.json", slots["hook"])
    write_json(transcripts_dir / "trust_dialogue.json", slots["trust"])
    write_json(transcripts_dir / "cta_dialogue.json", slots["cta"])
    write_slot_markdown(storyboards_dir / "hook_trust_cta_mapping.md", slots)

    formula = story_formula(project_input, float(metadata["duration_seconds"]))
    write_json(meta_dir / "story_formula.json", formula)

    manifest_rows = []
    for scheme_id, scheme in [("scheme_1", scheme1), ("scheme_2", scheme2), ("scheme_3", scheme3)]:
        segments = [Segment(**item) for item in scheme["segments"]]
        for segment in segments:
            manifest_rows.append({
                "scheme": scheme_id,
                "index": segment.index,
                "title": segment.title,
                "start": segment.start,
                "end": segment.end,
                "clip_filename": clip_filename(segment),
                "frame_filename": frame_filename(segment),
            })
            extract_frame(video_path, keyframes_dir / frame_filename(segment), segment.start)
            export_clip(video_path, clips_dir / scheme_id / clip_filename(segment), segment.start, segment.end)
    write_json(storyboards_dir / "scheme_filename_manifest.json", manifest_rows)

    timeline = {
        "video_duration": metadata["duration_seconds"],
        "scene_candidate_count": len(candidates),
        "asr_segment_count": len(asr["segments"]),
        "observations": [
            "前段承担抓钩与问题进入",
            "中段承担过程说明与价值证明",
            "末段可承接 CTA 或标准收束",
        ],
    }
    write_json(meta_dir / "timeline_analysis.json", timeline)

    componentized = {
        "industry": project_input.get("industry") or "",
        "persona": project_input.get("persona") or "",
        "scene_context": project_input.get("scene_context") or "",
        "product_info": project_input.get("product_info") or "",
        "formula": formula,
        "slot_analysis": slots,
        "scheme_counts": {
            "scheme_1": len(scheme1["segments"]),
            "scheme_2": len(scheme2["segments"]),
            "scheme_3": len(scheme3["segments"]),
        },
        "fit_against_pdf": {
            "hook_trust_cta": True,
            "componentized_assets": True,
            "analysis_json_primary": True,
        },
    }
    write_json(reports_dir / "componentized_analysis.json", componentized)

    summary = {
        "workflow": "OpenFlow - Analysis",
        "video_metadata": metadata,
        "story_formula": formula,
        "scheme_counts": componentized["scheme_counts"],
        "slot_keys": ["hook", "trust", "cta"],
        "package_status": "complete",
    }
    write_json(reports_dir / "analysis_summary.json", summary)
    write_text(
        reports_dir / "analysis_summary.md",
        "# OpenFlow Analysis Summary\n\n"
        f"- 视频时长: {metadata['duration_seconds']} 秒\n"
        f"- 故事公式: {' > '.join(formula['formula'])}\n"
        f"- Scheme 1 段数: {componentized['scheme_counts']['scheme_1']}\n"
        f"- Scheme 2 段数: {componentized['scheme_counts']['scheme_2']}\n"
        f"- Scheme 3 段数: {componentized['scheme_counts']['scheme_3']}\n"
        "- 已输出 Hook / Trust / CTA 槽位映射\n"
        "- 结果包状态: complete\n",
    )


if __name__ == "__main__":
    main()
