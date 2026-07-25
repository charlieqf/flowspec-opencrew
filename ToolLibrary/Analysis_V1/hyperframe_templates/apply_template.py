#!/usr/bin/env python3
"""把一套 HyperFrame 模板叠加到底层视频上渲染成片（直播带货标题/字幕模板）。

也可用于无视频的预览渲染（不传 --video，模板自带渐变背景）。

用法:
  apply_template.py --template <id> --output <out.mp4> \
      [--video <base.mp4>] [--params <json-string-or-@file>]

设计要点:
- 参数替换: 模板 index.html 中的 {{token}} 由 params 覆盖；缺失的 token 用 templates.json 里该模板的 params 默认值兜底。
- 视频底层: 传 --video 时，把视频探测真实宽高/时长后作为 <video> 底层注入，叠层抬到其上，
  并把合成/叠层时长拉到视频时长（动画播完保持）。
- 全本地: hyperframes(无头 Chrome) + ffmpeg，均走本机已装件，不依赖 CDN。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # hyperframe_templates/
ANALYSIS = ROOT.parent                            # Analysis_V1/
HF_BIN = ANALYSIS / "node_modules" / ".bin" / "hyperframes"
MAX_VIDEO_SECONDS = 180.0                          # 单机无头 Chrome 渲染长视频易超时/超内存,套用上限


def _tool(env_name: str, exe: str) -> str:
    val = os.environ.get(env_name)
    if val and Path(val).exists():
        return val
    found = shutil.which(exe)
    if found:
        return found
    candidate = ANALYSIS.parent / ".bin" / exe   # ToolLibrary/.bin/<exe>
    if candidate.exists():
        return str(candidate)
    return exe


FFMPEG = _tool("OPENCREW_FFMPEG_PATH", "ffmpeg")
FFPROBE = _tool("OPENCREW_FFPROBE_PATH", "ffprobe")


def load_manifest() -> dict:
    return json.loads((ROOT / "templates.json").read_text(encoding="utf-8"))


def template_defaults(manifest: dict, template_id: str) -> dict:
    for tpl in manifest.get("templates", []):
        if str(tpl.get("id")) == template_id:
            return dict(tpl.get("params") or {})
    return {}


def flatten_params(params: dict) -> dict:
    """把嵌套 params 摊平成 token 字典。
    例: {"points":[{"text":"a","highlight":"b"}]} -> point1_text=a, point1_hl=b。
    """
    flat: dict[str, str] = {}
    for key, value in (params or {}).items():
        if isinstance(value, list):
            for idx, item in enumerate(value, start=1):
                if isinstance(item, dict):
                    for sub, sval in item.items():
                        alias = "hl" if sub == "highlight" else sub
                        flat[f"{key[:-1] if key.endswith('s') else key}{idx}_{alias}"] = "" if sval is None else str(sval)
                else:
                    flat[f"{key[:-1] if key.endswith('s') else key}{idx}"] = "" if item is None else str(item)
        elif isinstance(value, dict):
            for sub, sval in value.items():
                flat[f"{key}_{sub}"] = "" if sval is None else str(sval)
        else:
            flat[key] = "" if value is None else str(value)
    return flat


def substitute(html: str, flat: dict) -> str:
    def repl(match: re.Match) -> str:
        token = match.group(1).strip()
        return flat.get(token, "")
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", repl, html)


def probe(video: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])


def inject_video(html: str, clip_name: str, duration: float) -> str:
    # 叠层抬到视频之上
    html = html.replace("position:absolute;", "position:absolute;z-index:2;")
    # 视频底层 css（z-index 0）
    html = html.replace(
        "</style>",
        "  video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;z-index:0;}\n</style>",
    )
    # 所有合成/叠层时长拉到视频时长，动画播完保持
    html = re.sub(r'data-duration="[\d.]+"', f'data-duration="{duration:.3f}"', html)
    # 在 #root 开标签后插入 <video> 底层
    html = re.sub(
        r'(<div id="root"[^>]*>)',
        r'\1\n    <video id="base" data-start="0" data-duration="%.3f" data-track-index="0" src="media/%s" muted playsinline></video>' % (duration, clip_name),
        html, count=1,
    )
    return html


def render(template_id: str, output: Path, video: Path | None, params: dict) -> dict:
    tpl_dir = ROOT / template_id
    if not (tpl_dir / "index.html").exists():
        raise SystemExit(f"unknown template: {template_id}")
    manifest = load_manifest()
    merged = template_defaults(manifest, template_id)
    merged.update(params or {})
    flat = flatten_params(merged)

    html = (tpl_dir / "index.html").read_text(encoding="utf-8")
    html = substitute(html, flat)

    workdir = Path(tempfile.mkdtemp(prefix=f"hf_apply_{template_id}_"))
    try:
        for fname in ("hyperframes.json", "meta.json", "gsap.min.js"):
            src = tpl_dir / fname
            if src.exists():
                shutil.copy2(src, workdir / fname)

        duration = None
        if video is not None:
            w, h, duration = probe(video)
            if duration > MAX_VIDEO_SECONDS:
                raise SystemExit(
                    f"视频时长 {duration:.0f}s 超过模板套用上限 {MAX_VIDEO_SECONDS}s；"
                    f"请对场景级/较短片段套用模板（单机无头 Chrome 渲染长视频易超时/超内存）。"
                )
            media = workdir / "media"
            media.mkdir(exist_ok=True)
            clip = media / "clip.mp4"
            # 归一 30fps（HyperFrame 取帧更稳），保留源宽高
            subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                 "-vf", "fps=30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 str(clip)],
                check=True,
            )
            html = inject_video(html, "clip.mp4", duration)

        (workdir / "index.html").write_text(html, encoding="utf-8")

        if not HF_BIN.exists():
            raise SystemExit(f"hyperframes not installed at {HF_BIN}")
        rendered = workdir / "_render.mp4"
        env = os.environ.copy()
        env["PATH"] = f"{Path(FFMPEG).parent}{os.pathsep}{env.get('PATH','')}"
        completed = subprocess.run(
            [str(HF_BIN), "render", "--output", str(rendered)],
            cwd=str(workdir), env=env, capture_output=True, text=True,
        )
        if completed.returncode != 0 or not rendered.exists() or rendered.stat().st_size <= 0:
            raise SystemExit(f"hyperframes render failed: {completed.stderr[-1200:] or completed.stdout[-1200:]}")

        output.parent.mkdir(parents=True, exist_ok=True)
        # 若有底视频，按其时长收尾裁剪（HyperFrame 可能略超）
        if duration is not None:
            subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(rendered),
                 "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-movflags", "+faststart", str(output)],
                check=True,
            )
        else:
            shutil.move(str(rendered), str(output))
        return {"output": str(output), "template": template_id, "duration": duration}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--video", default="")
    ap.add_argument("--params", default="")
    args = ap.parse_args()

    params = {}
    if args.params:
        raw = args.params
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        params = json.loads(raw)

    video = Path(args.video) if args.video else None
    result = render(args.template, Path(args.output), video, params)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
