# Animated Title Interview OCR-ASR Skill

## Video Type

This skill is for videos that interleave animated title cards with person interview or expert explanation segments.

The defining feature is that many animated title cards have no ASR because nobody is speaking during those moments. Their meaning exists only as on-screen text. Therefore, OCR must be used to fill gaps in the ASR timeline and recover the full narrative logic.

Typical characteristics:

- Animated title cards, motion graphics, chapter cards, or stylized text screens appear before, between, or after interview/explanation clips.
- Title-card sections often have background music, sound effects, or silence, but no spoken narration.
- Interview or talking-head sections contain ASR, but ASR alone does not describe the title-card information.
- The video logic depends on the alternation between visual text and spoken explanation.
- OCR text may introduce topic, chapter, question, conflict, transition, data point, quote, date, or final teaser.
- ASR may start only after the animated title has already established the topic.

Use this skill when the video is best understood as:

```text
动图标题/文字卡点 + 人物访谈/讲解 + 动图标题转场 + 人物继续讲解
```

This is not a pure interview video and not a pure slideshow. It is a hybrid structure where title animation and interview narration jointly carry meaning.

## Core Formula

Primary formula:

```text
动图标题引入 + 人物访谈/讲解 + 动图标题转场/补充 + 人物继续解释 + 片尾标题/预告
```

Common structure slots:

- `动图标题引入`: animated visual text introduces topic, hook, question, chapter, date, or conflict.
- `人物访谈/讲解`: interviewee, narrator, expert, or host explains the topic in spoken language.
- `动图标题转场`: visual text bridges from one topic to another without ASR.
- `视觉补充信息`: on-screen text provides names, terms, claims, numbers, study names, warnings, or conclusions not fully spoken.
- `片尾标题/预告`: visual-only end card, teaser, date, CTA, or summary phrase.

## Required Evidence Strategy

Always combine ASR and OCR.

ASR role:

- ASR is the primary evidence for spoken interview or narration.
- Keep ASR as the source of dialogue text.
- Do not rewrite spoken lines using OCR.

OCR role:

- OCR is the primary evidence for animated title cards and visual-only text.
- OCR must fill ASR gaps when the screen shows meaningful text but no one is speaking.
- OCR should be used as context when title cards and interview speech overlap but provide different information.

Priority rules:

- If OCR overlaps ASR and repeats the same spoken content, treat OCR as subtitle duplication. Use ASR as primary text and suppress duplicate OCR as a separate semantic unit.
- If OCR overlaps ASR but is different, treat OCR as visual context. The semantic unit should be `mixed`: ASR provides spoken explanation, OCR provides topic/title/context.
- If OCR appears during ASR silence or ASR gaps, create `ocr` semantic units so the title card is not lost.
- If an animated title card introduces a topic and the interview explains it immediately after, keep the title and speech connected in the same business stage, but preserve the title-card boundary if it is visually and semantically important.

## Recommended Tool Flow

Use this flow for this video type:

```text
01 VideoMetadataExtractor
02 AudioASRPipeline
04 PySceneDetectRunner
05 VisualEvidenceExtractor
05_1 VisualOCRTimelineBuilder
07 SilentVisualSegmentDetector
03 SemanticLLMStructureBuilder
08 BoundaryAligner
10 EvidenceCollector
13 FineTimelineBuilder
14 SegmentDescriptorSubtitleBuilder
15 SchemeExportValidator
16 SemanticFirstQualityChecker
```

Default choices:

- Run `05_1` after `05`. It is required for this video type because animated title sections often have no ASR.
- Run `07` because silent visual intervals may contain meaningful animated title cards.
- Do not run `06` unless the user specifically needs strict physical scene transition judgement.
- Do not run `09`, `11`, or `12` unless intermediate results show under-segmentation, over-coarse segments, or over-fragmentation.

## OCR Requirements

For `05_1 VisualOCRTimelineBuilder`:

- Use `rapidocr` or `paddleocr` for Chinese/English mixed screen text.
- Enable progressive text merging because animated titles often reveal a sentence across several frames.
- Preserve source frame times, text candidates, representative frame, and merge reasons.
- Use `visual_ocr_timeline.json` as the OCR input for `03`, not raw frame OCR.

Important merge cases:

- `progressive_text_reveal`: a title appears gradually and should be represented by the most complete final text.
- `near_duplicate`: the same title card remains on screen across multiple frames and should become one timeline item.
- `single_keyframe`: one keyframe is enough to represent the animated title text.

## Semantic Segmentation Rules

In `03 SemanticLLMStructureBuilder`, use `semantic_evidence_timeline` rather than raw OCR when available.

Semantic unit source types:

- `asr`: spoken interview or narration only.
- `ocr`: animated title card or visual-only text without meaningful ASR coverage.
- `mixed`: spoken explanation plus different visual title/context at the same time.

Boundary triggers:

- A new animated title card appears.
- Visual text changes topic, question, chapter, date, claim, or conclusion.
- Transition from animated title card to interview speech.
- Transition from interview speech back to visual-only title card.
- ASR is silent but OCR carries a new idea.
- A visual title introduces the next interview topic.
- End card, date teaser, CTA, or final visual phrase appears.

Do not split only because of decorative animation if the text and semantic function remain the same.

## Detail/Balanced/Summary Scheme Rules

Detail scheme:

- Preserve meaningful animated title cards as independent units when they introduce, transition, or conclude a topic.
- Preserve interview explanation beats as separate units when the spoken logic changes.
- Keep OCR-only segments even if they have no ASR.
- If a title card only decorates the current interview point and does not add semantic content, attach it as visual context rather than splitting.

Balanced scheme:

- Merge an animated title card with the immediately following interview clip if together they form one complete business unit.
- Keep major title-card transitions separate when they mark a new topic or chapter.
- A balanced segment should be usable as one retake or editing instruction block.

Summary scheme:

- Aggregate by business stage, not fixed duration.
- A typical summary structure may be:
  - opening animated title/hook;
  - main interview/explanation body;
  - final animated title/end-card.

## Retake Description Focus

For `14 SegmentDescriptorSubtitleBuilder`, the retake JSON should record:

- Exact animated title text that must appear on screen.
- Whether the segment is OCR-only, ASR-only, or mixed.
- How title animation prepares the viewer for the interview speech.
- What the interviewee or narrator says and how it connects to the title card.
- Whether visual text is a topic heading, transition card, key claim, data point, CTA, or teaser.
- Which OCR text may need manual verification because of stylized fonts, motion blur, or low confidence.

For OCR-only title-card segments:

- `spoken_script` may state that there is no spoken line or no available subtitle.
- `visual_must_have` must preserve the title-card text and timing intention.
- SRT export should use a non-empty fallback subtitle such as `[画面文字] <title>` so deliverables remain complete.

## Quality Risks

Common risks for this video type:

- ASR-only segmentation loses animated title-card logic.
- OCR may misread stylized, moving, or calligraphic text.
- Animated title cards may reveal text progressively, causing duplicated partial OCR unless merged.
- OCR may duplicate subtitles in some videos; when duplicated, ASR must remain primary.
- Title-card sections can produce empty SRT if the export stage does not use fallback subtitles.
- If the prompt topic conflicts with actual OCR text, report the mismatch rather than forcing the prompt topic.

Quality checks should confirm:

- `visual_ocr_timeline.json` exists and contains meaningful items.
- `semantic_evidence_timeline.json` records OCR-ASR relation and use policy.
- OCR-only title-card content is represented in `semantic_units`.
- Mixed ASR+OCR moments are marked with `source_type=mixed` or equivalent visual context fields.
- Final scheme packages contain non-empty `mp4`, `srt`, and `json` for every segment.

## Recommended Final Report Summary

When reporting this video type, include:

- Number of keyframes processed by OCR.
- Number of OCR timeline items.
- Number of OCR-only gap-fill items.
- Number of OCR-context mixed items.
- Difference between ASR-only and ASR+OCR segmentation, if available.
- Final detail/balanced/summary segment counts.
- Known OCR or ASR terms requiring manual review.
