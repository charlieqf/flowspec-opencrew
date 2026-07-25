# Short Drama OCR-ASR Quality Arbitration Skill

## Video Type

This skill is for short drama, micro drama, web drama, or skit-style videos where story, dialogue subtitles, visual title cards, character relationships, and dramatic conflict carry the main meaning.

Use this skill when the video is best understood as:

```text
短剧/网剧剧情推进 + 屏幕字幕 + 角色冲突 + 专家/产品/知识点嵌入 + 片尾导流/背书
```

Typical characteristics:

- Vertical short-form video, usually fast-cut and subtitle-heavy.
- Background music, sound effects, or dramatic mixing may make ASR unreliable.
- On-screen subtitles often preserve the actual dialogue better than raw ASR.
- OCR may contain watermarks, brand fragments, serial numbers, or partial subtitle reveals.
- Plot logic depends on characters, conflict, status relationship, reversal, explanation, evidence, and final value/CTA.
- ASR and OCR must be compared before deciding which source is trusted.

This skill does not require or describe VAD. It focuses only on ASR quality, OCR quality, and evidence arbitration.

## Core Formula

Primary formula:

```text
短剧设定引入 + 冲突钩子 + 人物关系/风险升级 + 专业解释/产品嵌入 + 证据展示 + 方案/效果预期 + 价值收束 + 片尾导流/合规背书
```

Common structure slots:

- `短剧设定引入`: title card, series name, world setup, character setup, episode title.
- `冲突钩子`: first emotional clash, accusation, complaint, misunderstanding, threat, challenge, or reveal.
- `人物关系建立`: character identity, power relationship, social status, helper/opponent roles.
- `风险升级`: punishment, loss, public pressure, failure consequence, deadline, or moral pressure.
- `专业解释`: expert explanation, medical/scientific/business logic, cause-and-effect reasoning.
- `证据展示`: data, diagnosis, product proof, screenshot, document, chart, reference, guideline.
- `方案/效果预期`: treatment, product, method, plan, result, benefit, conflict resolution.
- `价值收束`: slogan, belief, principle, corrected misconception, final takeaway.
- `片尾导流/背书`: follow prompt, next episode teaser, reference list, compliance source, brand card.

## Required Evidence Strategy

Always treat ASR and OCR as competing evidence sources, not fixed-priority sources.

ASR role:

- ASR is a candidate source for spoken dialogue and narration.
- ASR can be primary only when quality is good enough and it agrees with subtitle OCR.
- ASR must be downgraded when quality metadata or OCR comparison indicates hallucination, drift, overlap, or background-music interference.

OCR role:

- OCR is a candidate source for on-screen subtitle dialogue, title cards, data cards, expert name straps, product/brand cards, reference pages, and end cards.
- OCR can become primary when subtitles are stable and ASR is likely wrong.
- OCR must be cleaned before use; raw OCR may contain watermark fragments or serial codes.

Primary rule:

```text
Do not assume ASR is correct. Compare ASR and OCR by time, quality, stability, and semantic plausibility. Use the more reliable source as preferred_text.
```

## OCR Weak Dependency Rule

OCR is a weak dependency:

- If `visual_ocr_timeline.json` is missing, proceed with ASR-only segmentation and report OCR as unavailable.
- If OCR exists but contains only watermarks/noise, ignore it and use ASR where reliable.
- If OCR exists and is more reliable than ASR for a time range, OCR must become the standard for that range.
- If ASR is weak and OCR is strong, do not force ASR wording into dialogue text.

## Evidence Arbitration Policies

Every OCR-ASR overlap should be assigned one policy.

### `suppress_as_duplicate`

Use when OCR and ASR are highly similar.

- ASR remains primary for dialogue text.
- OCR is kept as subtitle/visual confirmation.
- Do not create a separate OCR semantic unit.

### `ocr_fill_gap`

Use when ASR is absent or empty and OCR carries meaning.

- OCR is primary.
- Create `source_type=ocr` semantic units.
- Use for title cards, silent visual cards, end cards, references, and subtitles in ASR gaps.

### `ocr_primary`

Use when OCR is clearly more reliable than ASR.

Typical triggers:

- ASR contains suspicious words, broken phrases, hallucinated fragments, or obvious homophone errors.
- ASR and OCR have low similarity, but OCR subtitle repeats across several keyframes.
- ASR quality reports timestamp coverage suspicion, audio activity gaps, or overlapping segments.
- OCR text is a complete subtitle sentence and ASR is semantically implausible.

Output requirements:

- `semantic_units.text` uses `preferred_text` from OCR.
- `dialogue_text` may use OCR subtitle text when OCR represents the on-screen dialogue.
- `source_type` should be `ocr` or `mixed`.
- `evidence_policy` must be `ocr_primary`.
- Reason must explain why OCR corrected ASR.

### `mixed_reconcile`

Use when ASR and OCR both contain useful information and should be fused.

Examples:

- ASR contains spoken reasoning; OCR contains expert name, institution, reference number, data label, or chart text.
- ASR gives a long explanation; OCR slices it into subtitle beats and visual evidence.

Output requirements:

- `semantic_units.text` uses fused meaning.
- `dialogue_text` preserves ASR or corrected spoken subtitle.
- `visual_text` preserves cleaned OCR.
- `source_type=mixed`.
- `evidence_policy=mixed_reconcile`.

### `ocr_context`

Use when OCR is useful visual context but not reliable enough to override ASR.

- Use OCR for title, identity, label, chart, product, CTA, or visual context.
- Keep ASR wording if ASR is more reliable.

### `asr_primary`

Use when ASR is reliable and OCR is noisy, decorative, or only a partial visual duplicate.

### `ignored_ocr_noise`

Use when cleaned OCR contains no meaningful text.

- Ignore OCR for semantic segmentation.
- Common examples: watermark only, brand fragment only, serial number only.

## Quality Scoring Heuristics

ASR should be downgraded when:

- `asr_quality.timestamp_coverage_suspect=true`.
- `asr_gap_with_audio_activity_count > 0`.
- ASR segments overlap each other.
- One ASR segment covers many changing OCR subtitles.
- ASR contains isolated suspicious short words.
- ASR has broken phrases that do not match story logic.
- ASR contradicts stable OCR subtitle text.

OCR should be upgraded when:

- Same subtitle appears across multiple keyframes.
- OCR has high confidence and repeated candidates.
- OCR contains complete Chinese dialogue, data, expert identity, reference, or CTA.
- OCR explains story logic missing from ASR.
- OCR corrects ASR homophone or hallucination.

OCR should be downgraded when:

- It only contains watermark, serial code, or brand fragment.
- It is a partial progressive reveal and not the final complete subtitle.
- It contains too few meaningful characters after cleaning.
- It conflicts with both ASR and visible story logic.

## OCR Cleaning Rules

Keep raw OCR for audit, but use cleaned OCR for semantic decisions.

Remove or downgrade common noise:

- `CMAT-*` serial codes.
- Brand/OCR fragments such as `Lee`, `Leey`, `Leley`, `Liley`, `Pieey`, `Llly`, `MED`, `MID` when they are not meaningful content.
- Repeated watermark-like strings.

Normalize obvious OCR errors when safe:

- `主任区师` -> `主任医师`
- `电任医师` -> `主任医师`
- `自天` -> `白天`
- `二匹院` -> `第二医院`
- `中心型肥胖` and `中心性肥胖` should be treated as equivalent for semantic matching.

Do not over-normalize names, institutions, medical terms, or references when uncertain. Mark them for human review instead.

## Recommended Tool Flow

Use this flow for this video type:

```text
01 VideoMetadataExtractor
02 AudioASRPipeline
04 PySceneDetectRunner
05 VisualEvidenceExtractor
05_1 VisualOCRTimelineBuilder
03 SemanticLLMStructureBuilder
08 BoundaryAligner
10 EvidenceCollector
13 FineTimelineBuilder
14 SegmentDescriptorSubtitleBuilder
15 SchemeExportValidator
16 SemanticFirstQualityChecker
```

Default choices:

- Run `05_1` whenever visible subtitles, title cards, data cards, or reference pages exist.
- Run `03` after both ASR and OCR are available, so evidence arbitration can compare them.
- Do not run `06` unless strict physical-location transition judgement is requested.
- Do not introduce VAD-specific assumptions in this skill.

## Semantic Segmentation Rules

In `03 SemanticLLMStructureBuilder`, use `semantic_evidence_timeline` as the evidence arbitration layer.

Semantic unit source types:

- `asr`: ASR is reliable and OCR is duplicate/noise.
- `ocr`: OCR subtitle/title/card is primary or fills an ASR gap.
- `mixed`: ASR and OCR must be fused to understand the unit.

Boundary triggers:

- New story setup or title card.
- Character identity or relationship changes.
- Conflict appears or escalates.
- A threat, accusation, misunderstanding, or reversal appears.
- The video shifts from drama to expert explanation.
- Data/evidence card appears.
- Solution, product, or treatment logic begins.
- Value slogan or CTA appears.
- Reference/end card begins.

Do not split only because the camera cuts if the story beat is unchanged.

Do not merge across major formula slots just because the duration is short.

## Detail/Balanced/Summary Scheme Rules

Detail scheme:

- Preserve every meaningful dialogue beat corrected by OCR.
- Preserve title cards, evidence cards, expert identity cards, and references when they change semantic function.
- Short OCR-primary beats may remain separate if they change story logic.

Balanced scheme:

- Merge adjacent detail beats into complete short-drama business units.
- Typical balanced segments: setup, conflict, relationship/risk, explanation, evidence, solution/effect, value close, end card.
- Each balanced segment should be usable as one retake instruction block.

Summary scheme:

- Aggregate by formula slot and story stage.
- Preserve the final CTA/reference stage if it carries compliance or conversion value.

## Retake Description Focus

For `14 SegmentDescriptorSubtitleBuilder`, retake JSON should record:

- Corrected dialogue source: ASR, OCR, or mixed.
- Which OCR subtitles corrected ASR.
- Character relationship and emotional trigger.
- Main action and reaction in each story beat.
- Required on-screen subtitles, title cards, expert name straps, data cards, references, and CTA.
- Any OCR terms that need human verification before production.

If OCR is primary for dialogue, `spoken_script` should use the OCR-corrected dialogue, not the raw ASR hallucination.

## Quality Risks

Common risks for this video type:

- Strong background music causes ASR hallucinations.
- ASR may merge multiple subtitle beats into one long segment.
- OCR may include watermarks and brand fragments that should not become semantic text.
- OCR may miss fast-moving subtitle reveals or stylized fonts.
- LLM may over-trust ASR unless evidence policy explicitly says `ocr_primary`.
- End cards and references may be lost if OCR is ignored.

Quality checks should confirm:

- `semantic_evidence_timeline.json` exists when OCR exists.
- Policy counts include OCR decision types.
- `ocr_primary` items have non-empty `preferred_text`.
- Known ASR errors are not used as final dialogue when OCR corrected them.
- `semantic_segment_candidates.dialogue_text` uses corrected dialogue for OCR-primary subtitle sections.
- Detail/balanced/summary schemes cover the full video without gaps or overlaps.

## Recommended Final Report Summary

When reporting this video type, include:

- ASR quality level and major ASR warnings.
- OCR engine and OCR timeline count.
- Number of `ocr_primary`, `ocr_fill_gap`, `ocr_context`, and `suppress_as_duplicate` items.
- Key ASR corrections made by OCR.
- Final detail/balanced/summary segment counts.
- Terms requiring human verification, especially names, institutions, medical terms, and references.
