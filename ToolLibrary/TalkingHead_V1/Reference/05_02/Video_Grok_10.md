# 05_02 Video Grok 1.0 Prompt Template / Flush X 提示词模板

Scope: xAI `grok-imagine-video` / Flush X only.

中文用于维护；`video_grok_10.py` 按模式只读取对应的英文模型调用块，`PITFALLS` 块仅作历史记录。

## 中文维护版

Grok 视频任务默认使用首帧图生视频。Talking Head 与 Cutaway 是两套独立提示词：`Speech` 控制声音，`Storyboard` 控制画面，`Context` 标记单一模式，`Negative` 只放对应模式的禁止项。

产品特写 / cutaway 必须只做产品安全的微运动或稳定持镜，不要人脸、人物、主持人、说话嘴、嘴唇、牙齿、眼睛、身体或新手。包装上印刷的人脸必须保持平面静态图形。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：cutaway 不要出现 talking product package 或 mouth movement。
- 2026-06-02 baseline：不要切镜、快速变焦、镜头跳变、字幕、水印或生成文字。
- 2026-06-20 talking-head：人物额头保持自然平滑，不要生成抬头纹、额头横纹、明显额头皱纹或深额纹。
- 2026-06-20 expressive talking-head：人物更有感染力、表情更丰富时，主要通过眼神、眉眼、停顿、点头、语气和胸口级手势增强；不要通过加大嘴型幅度实现，嘴型必须收敛、自然、保持下半脸稳定。
- 2026-07-12 relaxed talking-head：表情保持自然松弛和轻微笑意，不皱眉、不挤压脸颊、不生成酒窝；持物时必须保持真实手指支撑、稳定接触和合理重力关系。
- 新增踩坑时只追加，不要改写历史记录；英文历史追加到对应 `PITFALLS` 块，当前生效禁令放入对应模式的 `NEGATIVE` 块。

## English Model-Call Version

<!-- OPENCREW:VIDEO_GROK_SPEECH_TALKING_HEAD_START -->
Speech / 口播:
Use this exact Mandarin Chinese dialogue as the sole spoken content:
"{{dialogue_text}}"

Deliver clean presenter audio with precise lip sync, small relaxed articulation, subtle controlled jaw and lower-face movement, warm friendly pacing, and brief key-phrase pauses.

Duration: about {{duration_seconds}} seconds.
<!-- OPENCREW:VIDEO_GROK_SPEECH_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_STORYBOARD_TALKING_HEAD_START -->
Storyboard / 分镜:
Create a realistic {{aspect_ratio}} talking-head continuation from the first frame.

Preserve identity, face, hair, outfit, body and prop position, background, lighting, framing, and phone-video texture.

Locked eye-level medium shot; keep composition and presenter scale fixed.

Expression: relaxed and approachable. Convey faint friendly warmth mainly through soft smiling eyes and a calm gaze. Keep eyebrows in a neutral resting position, forehead smooth and still, cheeks relaxed, and mouth corners only slightly softened. Use subtle eye and head emphasis while speaking.

Use small relaxed waist-to-chest gestures. Keep any prop naturally supported by the same hand with stable contact, grip, orientation, and gravity; gesture mainly with the free hand.
<!-- OPENCREW:VIDEO_GROK_STORYBOARD_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_SPEECH_CUTAWAY_START -->
Speech / 口播:
Soundtrack: natural room tone. Use the dialogue meaning only as silent visual context for the product:
"{{dialogue_text}}"
<!-- OPENCREW:VIDEO_GROK_SPEECH_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_STORYBOARD_CUTAWAY_START -->
Storyboard / 分镜:
Create a realistic {{aspect_ratio}} product cutaway from the first frame.

Center the existing product package. Preserve its identity, printed artwork, background, camera angle, lighting, scale, perspective, and phone-video texture.

Duration: about {{duration_seconds}} seconds. Use subtle product-safe micro-movement with steady framing.
<!-- OPENCREW:VIDEO_GROK_STORYBOARD_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_CONTEXT_TALKING_HEAD_START -->
Mode: talking_head
<!-- OPENCREW:VIDEO_GROK_CONTEXT_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_CONTEXT_CUTAWAY_START -->
Mode: product_only_cutaway
<!-- OPENCREW:VIDEO_GROK_CONTEXT_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_TALKING_HEAD_START -->
subtitles, captions, title cards, generated overlay text, new Chinese or English text, added watermark or logo, UI overlay, music, narration, extra voices, face morphing, frozen or distorted face, frown, raised eyebrows, inner-brow lifting, eyebrow arching, brow knitting, brow furrows, glabellar lines, forehead tension, forehead wrinkles, horizontal forehead lines, deep forehead creases, stern or angry gaze, broad, toothy or exaggerated smile, forced grin, dimples, deep nasolabial folds, cheek compression, overacting, lip-sync errors, pursed, protruding, sustained O-shaped or over-wide mouth, jaw or lower-face distortion, floating prop, changing grip, broken or implausible hand-object contact, object-finger intersection, independent prop motion, extra, missing or deformed fingers
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_CUTAWAY_START -->
subtitles, captions, title cards, generated overlay text, new Chinese or English text, added watermark or logo, UI overlay, music, spoken dialogue, narration, live human face, real person, host, presenter, talking head, new hands, speaking lips, lip-sync, mouth movement, blinking, animated or talking package artwork, package morphing, label drift
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_PITFALLS_TALKING_HEAD_START -->
- 2026-06-20 talking-head: No subtitles/text/camera moves. Expressive gaze, not wide mouth or overacting.
- 2026-07-12 talking-head: Relaxed face with a gentle smile; no frowning, dimples, facial compression, exaggerated mouth shapes, or implausible hand-object contact.
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
