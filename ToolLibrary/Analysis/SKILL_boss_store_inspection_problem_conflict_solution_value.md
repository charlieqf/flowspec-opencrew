# Boss Store Inspection Problem-Conflict-Solution-Value Skill

## Video Type

This skill is for owner-led store inspection videos where a boss, founder, manager, or expert enters a real business scene, identifies an operational problem, triggers or reveals a conflict, gives a practical solution, and finally converts the solution into business value.

Typical Chinese content examples:

- 餐饮老板巡店、探店、查后厨、查宴会厅、查前厅。
- 门店老板现场发现问题，质问负责人或员工。
- 通过真实空间、人物对话、现场证据证明经营痛点。
- 结尾给出解决办法、管理建议、服务价值或客户转化理由。

Use this skill when the video is best understood as:

```text
老板巡店/探店 + 发现问题 + 现场冲突 + 解决方案 + 价值提升/转化
```

The core value of this video type is not simply showing a store environment. It is using the boss perspective to build authority, expose a real operational contradiction, and make the audience believe the solution has practical business value.

## Core Formula

Primary formula:

```text
问题钩子 + 巡店过程 + 冲突爆发/矛盾人 + 解决方案 + 价值提升/转化
```

Common structure slots:

- `问题钩子`: quickly introduces the business pain point, customer complaint, operational risk, or abnormal judgment.
- `巡店过程`: boss enters the scene, walks through key spaces, observes details, asks questions, and establishes why the problem needs verification.
- `冲突爆发`: conflict appears through dialogue, staff explanation, customer expectation, back-of-house pressure, or contradiction between efficiency and quality.
- `解决方案`: boss gives a practical operating method, management action, process adjustment, communication rule, or service standard.
- `价值提升`: solution is translated into customer experience, store trust, product quality, efficiency, reputation, conversion, or management capability.

For restaurant inspection videos, common scene labels include:

- 大门及前厅
- 宴会厅
- 厨房
- 后厨操作区
- 顾客用餐区
- 收银/接待区
- 门店外立面

Only treat a scene change as a real main-scene transition when the physical shooting space changes. Do not split solely because of camera angle, subtitle change, person change, topic change, or action change within the same physical space.

## Recommended Tool Flow

Use this flow for the first full analysis:

```text
01 VideoMetadataExtractor
02 AudioASRPipeline
04 PySceneDetectRunner
05 VisualEvidenceExtractor
05_1 VisualOCRTimelineBuilder
03 SemanticLLMStructureBuilder
06 SceneTransitionLLMJudge
07 SilentVisualSegmentDetector
08 BoundaryAligner
10 EvidenceCollector
13 FineTimelineBuilder
14 SegmentDescriptorSubtitleBuilder
15 SchemeExportValidator
16 SemanticFirstQualityChecker
```

Default choices:

- Run `05_1` when the video contains large subtitles, store labels, menu boards, operation signs, or title cards that affect meaning.
- Run `06` when physical scene accuracy matters, especially when distinguishing 大门及前厅、宴会厅、厨房, or other real store spaces.
- Run `07` when silent walking shots, B-roll, inspection actions, or visual-only evidence may carry business meaning.
- Do not run `09`, `11`, or `12` by default unless the user asks for visual boundary promotion or segment refinement.
- Run `14 --description-mode vlm` for production retake JSON because scene, people coordination, props, and actions are central to this type.
- Run `15` and `16` when full deliverable packages are needed.

## Closed-Loop Recomposition Flow

Use `17 DetailSchemeRecomposer` when the detail scheme is acceptable but the user is dissatisfied with the balanced or summary scheme.

This tool is designed for post-analysis scheme editing:

```text
17 DetailSchemeRecomposer
```

It does not rerun ASR, OCR, VLM scene analysis, semantic segmentation, or detail retake descriptions. It reads existing detail segments and detail retake JSON, uses the Task run model to regroup detail segments, then exports only the target scheme package.

For `--target balanced`:

- Rewrites `meta/scheme_balanced_segments.json`.
- Rewrites `meta/scheme_balanced_segment_descriptions.json`.
- Rewrites `meta/segment_descriptions/scheme_balanced/segment_*.json`.
- Rewrites `transcripts/scheme_balanced_subtitles/segment_*.srt`.
- Exports only `schemes/scheme_2/segment_*.mp4|srt|json`.

For `--target summary`:

- Rewrites `meta/scheme_summary_segments.json`.
- Rewrites `meta/scheme_summary_segment_descriptions.json`.
- Rewrites `meta/segment_descriptions/scheme_summary/segment_*.json`.
- Rewrites `transcripts/scheme_summary_subtitles/segment_*.srt`.
- Exports only `schemes/scheme_3/segment_*.mp4|srt|json`.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/17_detail_scheme_recomposer.py \
  --workspace /path/to/workspace \
  --task-id 19 \
  --target balanced \
  --instruction "均衡分镜按照五个连续业务逻辑从detail分镜合并：1.总结性的钩子快速介绍全篇的冲突；2.从大门走向前厅边走边介绍问题；3.在厨房里找到矛盾人；4.在宴会厅进行对话矛盾冲突；5.从宴会厅走向大门交谈解决办法。" \
  --fresh-session \
  --overwrite \
  --print-json
```

## Segmentation Rules

Detail scheme:

- Preserve every meaningful business beat.
- Split when the boss changes action, target, evidence, location, judgment, or solution point.
- Keep short but important conflict triggers as independent detail segments.
- Keep real scene evidence separate when it proves a different part of the problem.
- Preserve dialogue turns when they expose different stakeholder positions.

Balanced scheme:

- Merge consecutive detail segments into practical retake units.
- A balanced segment should represent one continuous business function, not a fixed duration.
- Common balanced structure for this type:
  - summary hook introducing the whole conflict;
  - boss walking into the store and introducing the problem;
  - finding the contradiction person or contradiction action;
  - dialogue conflict in the core business scene;
  - solution and value closure while moving out or returning to the main entrance.
- Do not merge unrelated physical spaces unless the user explicitly wants a montage-style segment.
- Do not let balanced segments become too fragmented if they serve one same conflict or solution chain.

Summary scheme:

- Aggregate by business stage and formula slot.
- Usually 3 to 5 segments are enough:
  - problem;
  - inspection/process;
  - conflict;
  - solution;
  - value/CTA.
- If the video formula is clearly `问题-过程-方案`, summary may use three segments.

## Evidence Strategy

ASR role:

- ASR is the primary source for dialogue, conflict, explanation, and solution language.
- Preserve direct lines that show the boss's judgment, staff pressure, customer complaint, or solution statement.
- Do not rewrite spoken dialogue into generic marketing language.

Visual role:

- Visual evidence identifies real spaces, props, actions, and relationships.
- Key visual evidence includes boss movement, employee response, kitchen operations, customer scene, store layout, product status, signage, table setting, equipment, and menu or subtitle text.
- OCR is useful when screen text names the problem, labels the scene, shows platform UI, or adds business framing not fully spoken.

Scene transition role:

- Use `06` to identify physical scene transitions when scene labels are central to the formula.
- Distinguish true physical movement from camera-angle changes in the same space.
- Treat title cards, black screens, screenshots, and platform pages as special visual types, not main shooting spaces.

## Retake Description Focus

For `14 SegmentDescriptorSubtitleBuilder`, every retake JSON should capture:

- Boss identity and authority posture.
- Who the boss is speaking to or challenging.
- The physical scene and why it matters.
- The concrete problem discovered.
- The contradiction or conflict between stakeholders.
- The evidence that proves the judgment.
- The solution action or process standard.
- The value outcome for customer experience, quality, reputation, efficiency, trust, or conversion.
- Props and visual must-haves, such as kitchen equipment, dishes, tables, reception area, uniforms, store signs, phones, menus, or customer scene.
- Emotional tone, such as questioning, pressure, firmness, professional judgment, reassurance, or final authority.

Avoid generic descriptions. The retake JSON must remain tied to the actual scene and business logic.

## Prompting `17` For This Type

When using `17`, the instruction should define the desired business logic directly.

Good instruction pattern:

```text
请把 detail 分镜合并为 balanced，按照以下连续业务逻辑：
1. <钩子/冲突总览>
2. <巡店进入/问题铺垫>
3. <矛盾人/矛盾动作>
4. <核心对话冲突/证据推演>
5. <解决办法/价值提升/转化>
要求只合并连续 detail 段，不打乱顺序，不遗漏，不臆造不存在的信息。
```

Useful grouping dimensions:

- Physical scene: 大门及前厅、宴会厅、厨房。
- Business function: 问题、过程、冲突、方案、价值。
- Character relation: 老板与店长、老板与厨师、老板与顾客场景、老板与前厅后厨协同。
- Evidence chain: 客户反馈、现场观察、员工解释、老板反驳、解决建议。
- Retake purpose: opening hook, walking inspection, contradiction discovery, dialogue conflict, closing solution.

## Quality Risks

Common risks for this video type:

- Treating camera-angle changes as real scene transitions.
- Over-splitting dialogue into fragments that are not useful for retake planning.
- Under-splitting the conflict so the contradiction person or contradiction action is lost.
- Losing the boss's authority role and turning the video into a generic store tour.
- Confusing evidence with solution: evidence proves the problem; solution tells what to do next.
- Generating balanced or summary segments from semantic labels only, ignoring detail retake JSON.
- Re-exporting all schemes when only one target scheme needs refreshing.

Quality checks should confirm:

- Detail segments preserve all key business beats.
- Balanced segments match the user's intended formula.
- Each target scheme segment has `mp4`, `srt`, and `json` after `17` export.
- Coverage starts at `0.0` and ends at video duration.
- Retake JSON records scene, people coordination, props, emotion, spoken script, solution, and value.
- The final scheme package does not contain stale files from an older grouping.

## Recommended Final Report Summary

When reporting this video type, include:

- Source video duration.
- Detail segment count.
- Balanced and summary segment counts.
- Whether `06` detected real physical scene transitions.
- Whether `14` used VLM retake descriptions.
- Whether `17` was used to recombine target schemes.
- Final exported scheme folders and segment counts.
- Any known risks, such as uncertain scene labels, ASR overlap, OCR errors, or intentionally skipped tools.
