# 05_02 Video Kling Omni Prompt Template / 可灵 3.0 Omni 视频提示词模板

适用范围：Kling 3.0 Omni / `/v1/videos/omni-video`，用于“首帧人物 + 参考视频节奏”的真实竖屏 9:16 口播视频。

中文用于维护；`OPENCREW:*` 块名、字段名和变量名保持英文，块内提示词正文使用中文。

## 中文维护版

Kling 3.0 Omni 口播任务：使用 `<<<image_1>>>` 作为第一帧和唯一人物身份来源，生成真实竖屏 9:16 口播人物视频。`<<<image_1>>>` 提供人物五官、发型、服装、背景、光线、构图、景别、镜头角度和手机视频质感。

如有 `<<<video_1>>>`，它只作为特征参考视频使用：参考口播节奏、自然眨眼、面部微表情、轻微头部动作、说话状态和亲和气质。不要参考或迁移 `<<<video_1>>>` 的人物身份、脸型、服装、背景、场景、镜头运动或构图。

最高优先级是首帧和镜头稳定。固定镜头要高于参考视频运镜。目标视频必须人物稳定、背景稳定、画面边缘稳定、脸部轮廓稳定、口型自然收敛、表情亲和但克制。避免字幕、水印、生成文字、身份漂移、镜头移动、自动重构图、明显笑纹、酒窝、夸张嘴型和手部畸形。

### 中文踩坑记录（只追加）

- 2026-06-21 Omni baseline：`<<<image_1>>>` 是身份、首帧和构图来源；`<<<video_1>>>` 只参考口播节奏和微表情。
- 2026-06-21 first frame lock：需要在图片参数中设 `type: "first_frame"`，并在提示词中写清楚首帧必须等于 `<<<image_1>>>`。
- 2026-06-21 feature video：口播参考视频应使用 `refer_type: "feature"`；不要让参考视频迁移人物身份、服装、背景、构图或镜头运动。
- 2026-06-21 video input sound limit：可灵 Omni 有 `video_list` 时不支持 `sound: on`；Raw 静音，声音交给后续 TTS / 对嘴型 / 合成流程。
- 2026-06-21 locked camera：固定镜头优先于参考视频运镜；不要推拉、摇移、旋转、变焦、重构图、画面漂移或切镜。
- 2026-06-21 clean frame：对白不要变成字幕、标题、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。
- 新增踩坑时只追加，不要改写历史记录；同时在 `VIDEO_KLING_OMNI_PITFALLS_APPEND_ONLY` 中追加对应模型调用禁令。

## 模型调用版

<!-- OPENCREW:VIDEO_KLING_OMNI_POSITIVE_BASE_START -->
Kling 3.0 Omni 任务：以 `<<<image_1>>>` 作为视频第一帧和首帧画面，生成真实竖屏 9:16 口播人物视频。

`<<<image_1>>>` 是唯一人物身份和画面基础来源。严格保持 `<<<image_1>>>` 中的人物身份、脸型、五官、发型、肤色、年龄感、服装、妆容、背景、光线、镜头角度、构图、景别、人物比例和真实手机竖屏质感。

目标时长：约 {{duration_seconds}} 秒。
<!-- OPENCREW:VIDEO_KLING_OMNI_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_REFERENCE_VIDEO_START -->
如果提供 `<<<video_1>>>`，它只作为特征参考视频。只参考 `<<<video_1>>>` 的口播节奏、自然眨眼、轻微头部运动、面部微表情、说话状态和亲和自然的口播气质。

不要复制或迁移 `<<<video_1>>>` 中的人物身份、脸型、五官、发型、服装、背景、场景、光线、镜头运动、手持晃动或构图。固定镜头优先于参考视频运镜。
<!-- OPENCREW:VIDEO_KLING_OMNI_REFERENCE_VIDEO_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_DIALOGUE_OR_MOUTHING_START -->
`<<<image_1>>>` 中的人物正对镜头，像正在自然口播以下中文台词：
"{{dialogue_text}}"

台词作为口型节奏和后续对口型参考，不要翻译、改写、总结或增加台词。当前 `video_list` 参考视频链路使用 `sound: off`，不要要求模型生成清晰对白音频；声音由后续 TTS / 对嘴型 / 合成流程处理。

画面中绝对不要出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。
<!-- OPENCREW:VIDEO_KLING_OMNI_DIALOGUE_OR_MOUTHING_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_CAMERA_LOCK_START -->
镜头完全锁定，像手机固定在三脚架上一样稳定。保持 `<<<image_1>>>` 的中近景或半身口播构图，人物位置、人物比例、背景、画面边缘、脸部轮廓和视线高度都保持不变。

全程不要推镜、拉镜、摇镜、移镜、平移、旋转、变焦、自动重构图、画面漂移、视角变化、跳切、切镜或场景变化。
<!-- OPENCREW:VIDEO_KLING_OMNI_CAMERA_LOCK_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_PERFORMANCE_START -->
表演风格：真实、自然、放松、亲和、可信。人物眼神稳定看向镜头，自然眨眼，有轻微呼吸和小幅头部稳定运动。

表情保持温暖亲和但克制。嘴角只能轻微上扬，不要明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显苹果肌、深法令纹、深笑纹、紧绷脸、皱眉或凶表情。

嘴部动作为自然中文口播嘴型，清楚但收敛，开口幅度适中。不要夸张张嘴、异常牙齿、嘴部漂移、下巴变形或面部畸变。

如有手势，只允许短促、小到中等幅度的胸口级自然强调。手不要靠近镜头、不要遮挡脸、不要夸张挥手、不要多指少指或手部变形。
<!-- OPENCREW:VIDEO_KLING_OMNI_PERFORMANCE_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_STATIC_OBJECTS_START -->
如果 `<<<image_1>>>` 中有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素。保持形状、颜色、位置、尺寸、透视、标签布局和文字位置稳定。

不要让产品漂浮、融化、复制、消失、变形、旋转、改字或自行运动。不要让包装文字、屏幕文字、Logo 或标签闪烁、漂移、重排或变成字幕。包装上印刷的人脸必须保持平面静态图形，不能眨眼、张嘴或说话。
<!-- OPENCREW:VIDEO_KLING_OMNI_STATIC_OBJECTS_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_AUDIO_CONTROL_START -->
有 `video_list` 参考视频时，可灵 Omni 不支持 `sound: on`，必须使用 `sound: off`。此时 Raw 新视频不会生成音频，最终对白声音应由下游 TTS / 对嘴型 / 合成流程处理。
<!-- OPENCREW:VIDEO_KLING_OMNI_AUDIO_CONTROL_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_BASE_START -->
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、中文字符、英文单词、水印、Logo、二维码、UI、界面元素、新人物、新主持人、新路人、新手、新产品、新道具、换脸、身份漂移、参考视频人物身份迁移、脸部变形、五官漂移、发型变化、服装变化、背景变化、光线变化、构图变化、画面边缘漂移、背景物体移动、低质量、低清晰度、模糊、闪烁、颜色突变、卡通感
<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_CAMERA_START -->
镜头运动、手持晃动、轻微晃动、画面漂移、自动重构图、推镜、拉镜、摇镜、移镜、平移、旋转、变焦、视角变化、构图变化、跳切、切镜、场景变化、快速运动
<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_CAMERA_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_EXPRESSION_START -->
夸张表情、过度表演、冻结表情、紧绷表情、凶、不亲和、皱眉、明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显酒窝、苹果肌过重、脸颊凹陷、深法令纹、深笑纹、抬头纹、额头横纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指
<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_EXPRESSION_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_OBJECTS_START -->
产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装融化、包装改字、屏幕内容变化、Logo 变形、平面人脸眨眼、平面人脸张嘴、平面人脸说话
<!-- OPENCREW:VIDEO_KLING_OMNI_NEGATIVE_OBJECTS_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_PITFALLS_APPEND_ONLY_START -->
- 2026-06-21 Omni baseline：`<<<image_1>>>` 提供身份、首帧、画面和构图；`<<<video_1>>>` 只提供口播节奏、微表情和说话状态。
- 2026-06-21 first frame lock：首帧必须等于 `<<<image_1>>>`，调用侧应设置 `image_list[0].type = "first_frame"`。
- 2026-06-21 feature video：参考视频只用 `refer_type: "feature"` 做特征参考；不要迁移参考视频人物、服装、背景、镜头或构图。
- 2026-06-21 video input sound limit：可灵返回 `sound on is not supported with video input`；有 `video_list` 时必须 `sound: off`，否则接口 400。
- 2026-06-21 locked camera：固定镜头优先于参考视频运镜；不要镜头移动、画面漂移、自动重构图、跳切、字幕、水印或生成文字。
<!-- OPENCREW:VIDEO_KLING_OMNI_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_KLING_OMNI_PROMPT_START -->
{{positive_prompt}}

负面约束：
{{negative_prompt}}
<!-- OPENCREW:VIDEO_KLING_OMNI_PROMPT_END -->

## 中文直接使用版

```text
以<<<image_1>>>作为视频第一帧和首帧画面，生成一段约 {{duration_seconds}} 秒的竖屏 9:16 真实口播人物视频。首帧必须严格等于<<<image_1>>>的画面来源，人物身份、脸型、五官、发型、肤色、年龄感、服装、妆容、背景、光线、镜头角度、构图、景别、人物比例和真实手机竖屏质感都以<<<image_1>>>为准。

如果提供<<<video_1>>>，只参考它的口播节奏、自然眨眼、面部微表情、轻微头部动作、说话状态和亲和自然的口播气质。不要复制或迁移<<<video_1>>>的人物身份、脸型、五官、服装、背景、场景、镜头运动或构图。固定镜头优先于参考视频运镜。

<<<image_1>>>中的人物正对镜头，像正在自然口播以下中文台词：“{{dialogue_text}}”。台词作为口型节奏和后续对口型参考，不要翻译、改写、总结或增加台词。当前 `video_list` 参考视频链路使用 `sound: off`，不要要求模型生成清晰对白音频；声音由后续 TTS / 对嘴型 / 合成流程处理。

镜头必须完全锁定，像手机固定在三脚架上一样稳定。全程不得出现任何镜头移动、手持晃动、轻微晃动、推镜、拉镜、摇镜、移镜、旋转、变焦、自动重构图、画面漂移、视角变化、跳切、切镜或场景变化。背景、画面边缘、人物位置、人物比例和脸部轮廓必须保持稳定。

人物表情真实、亲和、放松、可信，眼神稳定看向镜头，自然眨眼，嘴角只有轻微上扬。不要明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显苹果肌、深法令纹、深笑纹、紧绷脸、皱眉或凶表情。嘴部像自然中文口播，清楚但收敛，开口幅度适中，不夸张张嘴，不生成异常牙齿，不出现嘴部漂移或下巴变形。

只允许自然呼吸、自然眨眼、小幅点头、轻微眉眼变化、准确口播嘴型和轻微衣料自然变化。如有手势，只做短促、小到中等幅度的胸口级自然强调，手不要靠近镜头，不要遮挡脸，不要多指、少指或手部变形。

如果首帧中有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素，不漂移、不变形、不改字、不闪烁、不重排、不说话。

画面必须干净，不出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。不要新增人物、新道具、新产品或新背景。
```

## 中文负向提示词

```text
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、中文字符、英文单词、水印、Logo、二维码、UI、界面元素、新人物、新主持人、新路人、新手、新产品、新道具、换脸、身份漂移、参考视频人物身份迁移、脸部变形、五官漂移、发型变化、服装变化、背景变化、光线变化、构图变化、画面边缘漂移、背景物体移动、镜头运动、手持晃动、轻微晃动、画面漂移、自动重构图、推镜、拉镜、摇镜、移镜、旋转、变焦、视角变化、跳切、切镜、场景变化、夸张表情、过度表演、冻结表情、紧绷表情、凶、不亲和、皱眉、明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显酒窝、苹果肌过重、深法令纹、深笑纹、抬头纹、额头横纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指、产品漂移、产品变形、产品融化、产品复制、产品消失、标签变化、文字变形、包装改字、屏幕内容变化、Logo 变形、低质量、低清晰度、模糊、闪烁、颜色突变、卡通感
```

## 推荐调用参数

```json
{
  "model_name": "kling-v3-omni",
  "prompt": "{{rendered_prompt}}",
  "image_list": [
    {
      "image_url": "{{image_1_base64_or_url}}",
      "type": "first_frame"
    }
  ],
  "video_list": [
    {
      "video_url": "{{video_1_url}}",
      "refer_type": "feature",
      "keep_original_sound": "no"
    }
  ],
  "sound": "off",
  "mode": "pro",
  "aspect_ratio": "9:16",
  "duration": "{{duration_seconds}}",
  "watermark_info": {
    "enabled": false
  }
}
```
