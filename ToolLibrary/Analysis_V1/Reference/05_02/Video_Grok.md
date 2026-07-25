# 05_02 Video Grok Prompt Template / 视频 Grok 提示词模板

Scope: xAI / Grok video modules.

中文用于维护；英文 `OPENCREW:*` 块由 `video_grok.py` 读取并进入模型调用。

## 中文维护版

Grok 视频任务默认使用首帧图生视频。普通口播段落拆成两个部分维护：`Speech / 口播` 只控制逐字中文对白、嘴型、无字幕；`Storyboard / 分镜` 只控制首帧继承、镜头、人物动作、产品稳定性和画面连续性。

产品特写 / cutaway 必须只做产品安全的微运动或稳定持镜，不要人脸、人物、主持人、说话嘴、嘴唇、牙齿、眼睛、身体或新手。包装上印刷的人脸必须保持平面静态图形。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：cutaway 不要出现 talking product package 或 mouth movement。
- 2026-06-02 baseline：不要切镜、快速变焦、镜头跳变、字幕、水印或生成文字。
- 2026-06-20 talking-head：人物额头保持自然平滑，不要生成抬头纹、额头横纹、明显额头皱纹或深额纹。
- 2026-06-20 expressive talking-head：人物更有感染力、表情更丰富时，主要通过眼神、眉眼、停顿、点头、语气和胸口级手势增强；不要通过加大嘴型幅度实现，嘴型必须收敛、自然、保持下半脸稳定。
- 2026-07-12 relaxed eyes：笑意只通过温和的眼神表达；眉毛保持自然静止，额头平滑，不抬眉、不挤压脸颊、不生成抬头纹或明显笑容。
- 新增踩坑时只追加，不要改写历史记录；同时在对应英文 `VIDEO_GROK_PITFALLS_TALKING_HEAD` 或 `VIDEO_GROK_PITFALLS_CUTAWAY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:VIDEO_GROK_SPEECH_TALKING_HEAD_START -->
Speech / 口播:
The presenter speaks exactly this Mandarin Chinese dialogue:
"{{dialogue_text}}"
Do not translate, paraphrase, summarize, or add words. Spoken audio only.

Clean video frame with speech only. The dialogue is audio only and must never appear as subtitles, captions, title text, on-screen text, generated text, Chinese characters, English words, watermark, logo, or UI.

Mouth movement: precise Mandarin lip sync, restrained natural mouth movement, clear articulation, moderate identity-preserving mouth opening, not wide or exaggerated.

Delivery: warm relaxed Mandarin with natural pacing, gentle confidence, and brief pauses. No narration, music, or extra voices.

Target duration: about {{duration_seconds}} seconds. Prioritize clear Mandarin and natural pacing, not rushing.
<!-- OPENCREW:VIDEO_GROK_SPEECH_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_STORYBOARD_TALKING_HEAD_START -->
Storyboard / 分镜:
Grok video task: generate a vertical 9:16 realistic talking-head continuation from the provided first frame.

Use the provided first frame as the exact visual source. Preserve presenter identity, face, hairstyle, outfit, product or handheld prop, background, lighting, camera angle, framing, and mobile-video texture.

Camera locked, static, fixed medium shot, eye-level. The camera remains completely still for the entire video. The presenter stays in the same position and scale. Preserve the first-frame composition exactly. No pan, tilt, zoom, push-in, pull-out, rotation, reframing, handheld drift, cuts, or scene changes.

Performance style: relaxed and approachable, with warmth carried by the eyes, calm voice rhythm, and restrained gestures.

Facial expression: convey faint friendly warmth mainly through soft smiling eyes and a calm gaze. Keep eyebrows in a neutral resting position, forehead smooth and still, cheeks relaxed, and mouth corners only slightly softened. Use subtle eye and head emphasis while speaking.

Keep mouth and lower-face movement subtle and controlled.

Hand movement: controlled natural presenter gestures, small to medium range, hands stay between waist and chest. Use brief open-palm or inward-facing emphasis on key phrases, then relaxed pauses. Stable fingers and stable product or handheld prop if present.

The product or handheld prop, if present, remains stable in shape, color, label layout, position, and scale. It does not float, melt, change, duplicate, disappear, or move independently.
<!-- OPENCREW:VIDEO_GROK_STORYBOARD_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_SPEECH_CUTAWAY_START -->
Speech / 口播:
No spoken presenter dialogue, no lip sync, and no mouth motion in product-only cutaway mode.

Dialogue meaning is context only, not a mouth-motion driver:
"{{dialogue_text}}"
<!-- OPENCREW:VIDEO_GROK_SPEECH_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_STORYBOARD_CUTAWAY_START -->
Storyboard / 分镜:
Grok video task: generate a vertical 9:16 product-only cutaway from the provided first frame.

The first frame is the only visual source. Preserve product package identity, background, camera angle, lighting, scale, perspective, and phone-video texture.

Target duration: about {{duration_seconds}} seconds. Use only subtle product-safe micro-movement or steady hold.

No human face, person, presenter, talking mouth, lips, teeth, eyes, body, or new hands. Printed faces on packaging must remain flat static graphics.
<!-- OPENCREW:VIDEO_GROK_STORYBOARD_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_CONTEXT_START -->
Mode: {{cutaway_mode}}
<!-- OPENCREW:VIDEO_GROK_CONTEXT_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_BASE_START -->
subtitles, captions, title text, on-screen text, generated text, floating text, burned-in captions, Chinese characters, English words, watermark, logo, UI, stickers, camera movement, camera jump, cuts, fast zoom, low quality
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_TALKING_HEAD_START -->
identity drift, face morphing, frozen or distorted face, frown, raised eyebrows, inner-brow lifting, eyebrow arching, brow knitting, brow furrows, glabellar lines, forehead tension, forehead wrinkles, forehead lines, horizontal forehead lines, deep forehead creases, stern or angry gaze, broad, toothy or exaggerated smile, forced grin, dimples, deep nasolabial folds, cheek compression, overacting, lip-sync errors, pursed, protruding, sustained O-shaped or over-wide mouth, jaw or lower-face distortion, product drift, label changes, floating product, moving background objects, new props, camera movement, cuts, extra, missing or deformed fingers, hands moving toward camera, exaggerated waving
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_CUTAWAY_START -->
human face, person, host, presenter, talking head, speaking lips, lip-sync, mouth movement, blinking package face, animated package face, talking product package
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_PITFALLS_TALKING_HEAD_START -->
- 2026-06-20 talking-head: No subtitles/text/camera moves. Expressive gaze, not wide mouth or overacting.
- 2026-07-12 talking-head: Friendly warmth comes from the eyes only; neutral resting brows and a smooth still forehead; no raised brows, forehead lines, broad smile, cheek compression, or dimples.
<!-- OPENCREW:VIDEO_GROK_PITFALLS_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_PITFALLS_CUTAWAY_START -->
- 2026-06-20 cutaway: No talking product packages, mouth movement, lip-sync, talking heads, blinking package faces, subtitles, captions, camera jumps, cuts, fast zooms, watermarks, or generated text.
<!-- OPENCREW:VIDEO_GROK_PITFALLS_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_PROMPT_START -->
{{speech_prompt}}

{{storyboard_prompt}}

{{context_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_GROK_PROMPT_END -->
