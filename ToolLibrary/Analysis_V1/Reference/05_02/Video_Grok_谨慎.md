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
- 新增踩坑时只追加，不要改写历史记录；同时在对应英文 `VIDEO_GROK_PITFALLS_TALKING_HEAD` 或 `VIDEO_GROK_PITFALLS_CUTAWAY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:VIDEO_GROK_SPEECH_TALKING_HEAD_START -->
Speech / 口播:
The presenter speaks exactly this Mandarin Chinese dialogue:
"{{dialogue_text}}"
Do not translate, paraphrase, summarize, or add words. Spoken audio only.

Clean video frame with speech only. The Mandarin dialogue must never appear as subtitles or visible text.

Mouth movement: clear natural mouth movement, precise Mandarin lip sync, each word articulated clearly, not exaggerated and not too subtle.

Target duration: about {{duration_seconds}} seconds. If the dialogue is long, prioritize clear Mandarin articulation and natural pacing instead of rushing.
<!-- OPENCREW:VIDEO_GROK_SPEECH_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_STORYBOARD_TALKING_HEAD_START -->
Storyboard / 分镜:
Grok video task: generate a vertical 9:16 realistic product talking-head continuation from the provided first frame.

Use the provided first frame as the exact visual source. Preserve the presenter identity, face, hairstyle, outfit, product, background, lighting, camera angle, framing, and mobile-video texture from frame 1 to final frame.

Camera locked, static, fixed medium shot, eye-level. No pan, tilt, zoom, push-in, pull-out, rotation, reframing, cuts, or scene changes.

Facial expression: professional, sincere, calm and confident, natural blinking, slight nod on key product points.

The presenter has a smooth natural forehead with no forehead wrinkles or forehead lines.

Hand movement: controlled natural presenter gestures, small to medium range, hands stay between waist and chest, one brief two-hand emphasis only on the key product point, relaxed pauses between gestures.

The product remains stable in shape, color, label layout, position, and scale. The product does not float, melt, change, duplicate, disappear, or move independently.
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
Scene context: {{scene_summary}}
Shot context: {{shot_summary}}
Cutaway mode: {{cutaway_mode}}
<!-- OPENCREW:VIDEO_GROK_CONTEXT_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_BASE_START -->
subtitles, captions, title text, on-screen text, generated text, floating text, burned-in captions, Chinese characters, English words, watermark, logo, UI, stickers, camera movement, camera jump, cuts, fast zoom, low quality
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_TALKING_HEAD_START -->
identity drift, face morphing, distorted mouth, frozen face, lip-sync errors, forehead wrinkles, forehead lines, horizontal forehead lines, deep forehead creases, visible forehead creases, product drift, product changes, label changes, floating product, moving background objects, new props, pan, tilt, zoom, push-in, pull-out, rotation, reframing, scene changes, extra fingers, missing fingers, deformed hands, hands moving toward camera, exaggerated waving
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_NEGATIVE_CUTAWAY_START -->
human face, person, host, presenter, talking head, speaking lips, lip-sync, mouth movement, blinking package face, talking product package
<!-- OPENCREW:VIDEO_GROK_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_PITFALLS_TALKING_HEAD_START -->
- 2026-06-02 baseline: Do not create camera jumps, cuts, fast zooms, subtitles, captions, watermarks, generated text, distorted mouth, frozen face, or extra limbs.
- 2026-06-20 product talking-head: The presenter must speak the Mandarin dialogue exactly as written; do not translate, paraphrase, summarize, add words, or show the dialogue as visible text.
- 2026-06-20 product talking-head: Keep clear natural Mandarin lip sync, locked camera, controlled presenter gestures, and stable product shape, color, label layout, position, and scale.
- 2026-06-20 talking-head forehead: Keep the presenter's forehead smooth and natural; do not create forehead wrinkles, forehead lines, horizontal forehead lines, deep forehead creases, or visible forehead creases.
<!-- OPENCREW:VIDEO_GROK_PITFALLS_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_GROK_PITFALLS_CUTAWAY_START -->
- 2026-06-02 baseline: Do not create talking product packages, mouth movement, lip-sync, talking heads, blinking package faces, or animated package faces in product-only cutaways.
- 2026-06-02 baseline: Do not create camera jumps, cuts, fast zooms, subtitles, captions, watermarks, generated text, distorted mouth, frozen face, or extra limbs.
<!-- OPENCREW:VIDEO_GROK_PITFALLS_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GROK_PROMPT_START -->
{{speech_prompt}}

{{storyboard_prompt}}

{{context_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_GROK_PROMPT_END -->
