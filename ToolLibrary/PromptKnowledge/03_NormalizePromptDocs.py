#!/usr/bin/env python3
"""Normalize fetched raw docs into normalized/<source_id>.jsonl (stdlib only).

Design doc 6.2 (03): clean MD/HTML, chunk, dedupe, extract short rules. License
safety (11.2): we store only summaries + short excerpts (capped), never full
text, and tag trust_level/license from the registry so low-trust scraped docs
rank below curated seeds (see multi-route + trust ranking in the backend). The
hand-curated seed_rules.jsonl is never overwritten — each source writes its own
file.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
NORMALIZED_DIR = ROOT / "normalized"
REGISTRY_PATH = ROOT / "registry" / "sources.json"
EXCERPT_CAP = 600
MAX_DOCS_PER_SOURCE = 8
MIN_CHUNK = 120
MIN_WORDS = 15

NOISE_RE = re.compile(
    r"\b("
    r"announcements?|updates?|appearances?|featured|sponsored by|join now|enroll|subscribe|newsletter|discord|twitter|youtube|"
    r"we now offer|we crossed|new course|services like corporate training|use code|happy prompting|"
    r"table of contents|additional resources|running the guide locally|install node|install pnpm|pnpm|npm|pip install|"
    r"video lecture|slides|notebook with code|open a pr|always welcome feedback|cite us|bibtex|license|"
    r"wall street journal|forbes|hacker news|markettechpost|web version|translations?|star|fork"
    r")\b",
    re.IGNORECASE,
)

ACTION_RE = re.compile(
    r"\b("
    r"use|include|avoid|specify|describe|provide|keep|split|break|structure|format|ask|state|define|"
    r"separate|label|choose|prefer|test|evaluate|verify|iterate|refine|anchor|lock|remove|require|"
    r"写|包含|避免|指定|描述|拆分|保持|使用|选择|验证|迭代|固定|区分"
    r")\b",
    re.IGNORECASE,
)
CJK_ACTION_RE = re.compile(r"(写|包含|避免|指定|描述|拆分|保持|使用|选择|验证|迭代|固定|区分|标注|说明|补充|检查|提醒)")

DOMAIN_RE = re.compile(
    r"\b("
    r"prompt|instruction|context|example|format|output|response|constraint|few-shot|chain-of-thought|subtask|"
    r"image|video|reference|mask|frame|camera|shot|lens|motion|movement|scene|lighting|negative|"
    r"audio|voice|script|avatar|lipsync|lip sync|mouth|caption|subtitle|model|reasoning|json|delimiter|citation|"
    r"提示词|指令|上下文|示例|格式|输出|约束|图片|图像|视频|参考图|遮罩|首帧|尾帧|镜头|运动|负面|音频|声音|脚本|数字人|口型|字幕"
    r")\b",
    re.IGNORECASE,
)
CJK_DOMAIN_RE = re.compile(r"(提示词|指令|上下文|示例|格式|输出|约束|图片|图像|视频|参考图|遮罩|首帧|尾帧|镜头|运动|动作|负面|音频|声音|脚本|数字人|口型|字幕|水印|产品|人物)")

TAG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("reference_images", re.compile(r"\b(reference image|image reference|input image|source image|mask|first frame|last frame|start image|end image|asset reference|subject reference|参考图|参考图片|首帧|尾帧|遮罩|图生视频)\b", re.IGNORECASE)),
    ("negative_prompt", re.compile(r"\b(negative prompt|negative|avoid|undesired|unwanted|exclude|do not|don't|no |不要|避免|负面|反向)\b", re.IGNORECASE)),
    ("camera", re.compile(r"\b(camera|shot|lens|framing|composition|angle|pan|tilt|dolly|zoom|handheld|tripod|镜头|机位|构图|推拉|摇镜|变焦)\b", re.IGNORECASE)),
    ("motion", re.compile(r"\b(motion|movement|move|action|animate|animation|temporal|stabil|walk|gesture|运动|动作|移动|稳定|手势)\b", re.IGNORECASE)),
    ("lipsync", re.compile(r"\b(lipsync|lip sync|lip-sync|mouth|口型|唇形|对口型)\b", re.IGNORECASE)),
    ("audio", re.compile(r"\b(audio|sound|music|speech|dialogue|spoken|voiceover|音频|声音|音乐|对白|朗读)\b", re.IGNORECASE)),
    ("voice", re.compile(r"\b(voice|voice_id|speaker|narration|声音|音色|说话人)\b", re.IGNORECASE)),
    ("script", re.compile(r"\b(script|transcript|copy|line|caption|subtitle|脚本|台词|字幕|文案)\b", re.IGNORECASE)),
    ("avatar", re.compile(r"\b(avatar|digital human|presenter|talking head|数字人|虚拟人|主播)\b", re.IGNORECASE)),
    ("prompt_structure", re.compile(r"\b(structure|format|section|schema|json|delimiter|step-by-step|subtask|字段|结构|拆分|格式)\b", re.IGNORECASE)),
    ("examples", re.compile(r"\b(example|few-shot|sample|示例|样例)\b", re.IGNORECASE)),
    ("quality", re.compile(r"\b(quality|resolution|size|aspect ratio|lighting|high quality|清晰|分辨率|比例|质感|光线)\b", re.IGNORECASE)),
    ("text_rendering", re.compile(r"\b(text rendering|typography|lettering|subtitle|caption|文字|字体|字幕)\b", re.IGNORECASE)),
    ("safety", re.compile(r"\b(safety|policy|permission|copyright|license|watermark|安全|版权|许可|水印)\b", re.IGNORECASE)),
]
CJK_TAG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("reference_images", re.compile(r"(参考图|参考图片|输入图|源图|首帧|尾帧|遮罩|图生视频|主体参考|产品参考)")),
    ("negative_prompt", re.compile(r"(负面|反向|避免|不要|排除|不生成|水印|畸形|漂移)")),
    ("camera", re.compile(r"(镜头|机位|构图|推拉|摇镜|变焦|固定机位|画幅|景别)")),
    ("motion", re.compile(r"(运动|动作|移动|稳定|手势|走路|转身|漂移)")),
    ("lipsync", re.compile(r"(口型|唇形|对口型|唇动)")),
    ("audio", re.compile(r"(音频|声音|音乐|对白|朗读|旁白|声效)")),
    ("voice", re.compile(r"(声音|音色|说话人|配音|旁白)")),
    ("script", re.compile(r"(脚本|台词|字幕|文案|口播稿)")),
    ("avatar", re.compile(r"(数字人|虚拟人|主播|头像|形象)")),
    ("prompt_structure", re.compile(r"(字段|结构|拆分|格式|步骤|分段|标注|说明)")),
    ("examples", re.compile(r"(示例|样例|例子)")),
    ("quality", re.compile(r"(清晰|分辨率|比例|质感|光线|质量|高清)")),
    ("text_rendering", re.compile(r"(文字|字体|字幕|标题|屏幕字)")),
    ("safety", re.compile(r"(安全|版权|许可|水印|肖像|授权)")),
]


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)        # code fences
    text = re.sub(r"`[^`]*`", " ", text)                            # inline code
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)               # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)            # links -> text
    text = re.sub(r"<[^>]+>", " ", text)                            # html tags
    # Strip leading md marks + spaces/tabs per line, but NOT newlines, so blank
    # lines stay as paragraph boundaries for chunking.
    text = re.sub(r"^[ \t]*[#>*\-]+[ \t]*", "", text, flags=re.MULTILINE)
    return text


def load_registry() -> dict[str, dict]:
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid registry json: {REGISTRY_PATH}: {exc}") from exc
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise SystemExit(f"invalid registry json: {REGISTRY_PATH}: sources must be a list")
    return {str(item.get("source_id")): item for item in sources if item.get("source_id")}


def as_list(value) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def source_urls(meta: dict) -> list[str]:
    urls = [str(url) for url in as_list(meta.get("urls")) if url]
    if urls:
        return urls
    for file_meta in as_list(meta.get("files")):
        if isinstance(file_meta, dict) and file_meta.get("url"):
            urls.append(str(file_meta["url"]))
    return urls


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def too_link_heavy(raw_block: str, cleaned: str) -> bool:
    link_count = raw_block.count("](") + raw_block.count("http://") + raw_block.count("https://")
    if link_count >= 5:
        return True
    lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
    if len(lines) >= 6 and sum(1 for line in lines if re.match(r"^[-*]\s+\[", line)) >= len(lines) * 0.6:
        return True
    if link_count >= 3 and len(cleaned) < 900:
        return True
    return False


def is_actionable(block: str) -> bool:
    return bool((ACTION_RE.search(block) or CJK_ACTION_RE.search(block)) and (DOMAIN_RE.search(block) or CJK_DOMAIN_RE.search(block)))


def is_noise(raw_block: str, cleaned: str) -> bool:
    if NOISE_RE.search(raw_block) or NOISE_RE.search(cleaned):
        return True
    if too_link_heavy(raw_block, cleaned):
        return True
    lower = cleaned.lower()
    generic_intro = (
        "prompt engineering is a relatively new discipline",
        "motivated by the high interest",
        "this guide was created",
        "this is a living document",
        "large language model is a prediction engine",
        "a brief, incomplete",
        "courses are meant",
        "hands-on approach to learning",
        "learn these probabilities by training",
        "generative pre-trained models",
        "gpt-3 paper language models are few-shot learners",
        "different models will use different tokenizers",
        "there’s a lot of nuance around tokenization",
        "there's a lot of nuance around tokenization",
        "transformer networks",
    )
    if any(phrase in lower for phrase in generic_intro):
        return True
    return False


def is_prose(raw_block: str, cleaned: str) -> bool:
    # Keep substantive paragraphs; drop TOC/heading/boilerplate fragments.
    if len(cleaned) < MIN_CHUNK:
        return False
    if len(cleaned.split()) < MIN_WORDS and not has_cjk(cleaned):
        return False
    if not re.search(r"[.!?。!?]", cleaned):
        return False
    if is_noise(raw_block, cleaned):
        return False
    return is_actionable(cleaned)


def infer_tags(text: str) -> list[str]:
    tags: list[str] = []
    for tag, pattern in [*TAG_RULES, *CJK_TAG_RULES]:
        if pattern.search(text) and tag not in tags:
            tags.append(tag)
    return tags


def rule_type(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(avoid|do not|don't|negative|exclude|undesired|unwanted)\b|避免|不要|负面", lower):
        return "avoid"
    if re.search(r"\b(require|must|only|required)\b|必须|要求", lower):
        return "constraint"
    return "do"


def extract_rules(raw_block: str) -> list[dict]:
    rules = []
    for line in raw_block.splitlines():
        bullet = re.match(r"\s*[-*]\s+(.{8,200})", line)
        if bullet:
            text = re.sub(r"\s+", " ", strip_markdown(bullet.group(1))).strip()
            if text and is_actionable(text) and not is_noise(line, text):
                rules.append({"rule_id": f"rule_{len(rules)+1}", "rule_type": rule_type(text), "text": text[:220], "confidence": 0.55})
        if len(rules) >= 5:
            break
    return rules


def first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?。!?])\s", text, maxsplit=1)[0]
    return sentence[:160]


def source_title(meta: dict, source_id: str) -> str:
    return str(meta.get("title") or meta.get("source_id") or source_id)


def main() -> int:
    if not RAW_DIR.is_dir():
        print("no raw/ dir; run 02_SourceFetch.py first", file=sys.stderr)
        return 1
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    total = 0
    for source_dir in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()):
        meta_path = source_dir / "meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        source_id = str(meta.get("source_id") or source_dir.name)
        registry_meta = registry.get(source_id) or {}
        meta = {**meta, **registry_meta}
        out = NORMALIZED_DIR / f"{source_id}.jsonl"
        if meta.get("enabled", True) is False:
            if out.exists():
                out.unlink()
            print(f"{source_id}: disabled in registry")
            continue
        urls = source_urls(meta)
        docs = []
        seen = set()
        for file in sorted(source_dir.glob("*")):
            if file.name == "meta.json" or not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for raw_block in re.split(r"\n\s*\n", text):
                if len(docs) >= MAX_DOCS_PER_SOURCE:
                    break
                cleaned = re.sub(r"\s+", " ", strip_markdown(raw_block)).strip()
                if not is_prose(raw_block, cleaned):
                    continue
                tags = infer_tags(cleaned)
                if not tags and str(meta.get("trust_level") or "") in {"community_article", "experimental"}:
                    continue
                digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:10]
                if digest in seen:
                    continue
                seen.add(digest)
                docs.append({
                    "doc_id": f"{source_id}_{digest}",
                    "source_id": source_id,
                    "source_url": urls[0] if urls else "",
                    "source_title": source_title(meta, source_id),
                    "source_type": meta.get("source_type") or "article",
                    "trust_level": meta.get("trust_level") or "community_article",
                    "model_family": meta.get("model_family") or ["general"],
                    "provider": meta.get("provider") or "",
                    "model_ids": as_list(meta.get("model_ids")),
                    "language": "zh-CN" if has_cjk(cleaned) else "en",
                    "license": meta.get("license") or "unknown",
                    "chunk_id": f"chunk_{len(docs)+1:04d}",
                    "chunk_type": "excerpt",
                    "tags": tags,
                    "content": cleaned[:EXCERPT_CAP],
                    "summary": first_sentence(cleaned),
                    "rules": extract_rules(raw_block),
                    "examples": [],
                    "hash": digest,
                })
        if docs:
            out.write_text("\n".join(json.dumps(doc, ensure_ascii=False) for doc in docs) + "\n", encoding="utf-8")
            total += len(docs)
            print(f"{source_id}: {len(docs)} doc(s) -> {out.name}")
        else:
            if out.exists():
                out.unlink()
            print(f"{source_id}: 0 doc(s) after filtering")
    print(f"normalized {total} doc(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
