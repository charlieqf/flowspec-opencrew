# 05_02 Video Kling Prompt Template / 可灵非 Omni 视频提示词模板

适用范围：Kling 普通图生视频 / `/v1/videos/image2video`，重点适配 Kling 2.5 Turbo、Kling Turbo 及同类非 Omni 首帧口播人物视频。

中文用于维护；`OPENCREW:*` 块名、字段名和变量名保持英文，块内提示词正文使用中文。非 Omni 模板不使用 `<<<image_1>>>`、`<<<video_1>>>`、`<<<element_1>>>` 引用语法。

## 中文维护版

Kling 非 Omni 口播任务：基于上传的首帧图片生成真实竖屏 9:16 口播人物视频。首帧是唯一视觉来源，提供人物身份、脸型、五官、发型、服装、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系和手机视频质感。

提示词只控制首帧之后的自然运动：人物如何口播、嘴型如何收敛、镜头如何稳定、表情和手势如何克制、哪些元素绝对不能变化。不要重新设计人物、服装、背景、道具或场景。

目标画面必须镜头锁定、人物稳定、背景稳定、脸部轮廓稳定、口型自然收敛、表情亲和但克制。避免字幕、水印、生成文字、身份漂移、镜头移动、自动重构图、明显笑纹、酒窝、夸张嘴型和手部畸形。

### 中文踩坑记录（只追加）

- 2026-06-21 non-Omni baseline：首帧是唯一视觉来源，不使用 Omni `<<<...>>>` 引用语法。
- 2026-06-21 image2video：Kling 2.5 Turbo 使用 `image + prompt + negative_prompt` 的普通图生视频结构。
- 2026-06-21 locked camera：镜头默认锁定，不要推拉、摇移、旋转、变焦、自动重构图、跳切或手持漂移。
- 2026-06-21 clean frame：口播内容只允许成为声音或口型，不允许出现在字幕、标题、屏幕文字、浮动文字、水印或界面中。
- 2026-06-21 sound default：非 Omni Kling 默认请求有声视频；只有 Omni 带 video_list 视频参考时才按官方限制关闭 sound。
- 2026-06-21 restrained mouth：口播嘴型准确但收敛，不能大幅张嘴、下巴变形、牙齿异常或嘴部漂移。
- 2026-06-21 restrained expression：表情只保留轻微克制笑意，不要明显微笑、咧嘴笑、大笑、露齿笑、酒窝、深法令纹或深笑纹。
- 新增踩坑时只追加，不要改写历史记录；同时在 `VIDEO_KLING_PITFALLS_APPEND_ONLY` 中追加对应模型调用禁令。

## 模型调用版

<!-- OPENCREW:VIDEO_KLING_POSITIVE_BASE_START -->
Kling 图生视频任务：基于提供的首帧图片，生成真实竖屏 9:16 口播人物视频。

首帧是唯一视觉来源。严格继承首帧中的人物身份、脸型、五官、发型、肤色、年龄感、服装、妆容、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系、人物比例和手机视频质感。

不要重新设计人物，不要换脸，不要换衣服，不要改变发型，不要改变背景，不要添加新主持人、新路人、新手、新产品、新道具或新场景。

目标时长：约 {{duration_seconds}} 秒。
<!-- OPENCREW:VIDEO_KLING_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_KLING_DIALOGUE_TALKING_HEAD_START -->
首帧中的人物正对镜头，自然口播以下中文台词：
"{{dialogue_text}}"

必须逐字说出，不要翻译、改写、总结或增加台词。对白只作为声音或口型出现。画面必须干净，不要出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。
<!-- OPENCREW:VIDEO_KLING_DIALOGUE_TALKING_HEAD_END -->

<!-- OPENCREW:VIDEO_KLING_CAMERA_LOCK_START -->
镜头锁定，固定机位，保持首帧原始景别、视线高度、人物位置、人物比例、背景、画面边缘和构图不变。

全程不要推镜、拉镜、摇镜、移镜、平移、旋转、变焦、自动重构图、画面漂移、视角变化、跳切、切镜、手持漂移或场景变化。
<!-- OPENCREW:VIDEO_KLING_CAMERA_LOCK_END -->

<!-- OPENCREW:VIDEO_KLING_PERFORMANCE_START -->
表演风格：真实、自然、放松、亲和、可信。人物眼神稳定看向镜头，自然眨眼，有轻微呼吸和小幅头部稳定运动。

表情保持温暖亲和但克制。嘴角只能轻微上扬，不要明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显苹果肌、深法令纹、深笑纹、紧绷脸、皱眉或凶表情。

嘴部动作为自然中文口播嘴型，清楚但收敛，开口幅度适中。不要夸张张嘴、异常牙齿、嘴部漂移、下巴变形或面部畸变。

如有手势，只允许短促、小到中等幅度的胸口级自然强调。手不要靠近镜头、不要遮挡脸、不要夸张挥手、不要多指少指或手部变形。
<!-- OPENCREW:VIDEO_KLING_PERFORMANCE_END -->

<!-- OPENCREW:VIDEO_KLING_STATIC_OBJECTS_START -->
如果首帧中有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素。保持形状、颜色、位置、尺寸、透视、标签布局和文字位置稳定。

不要让产品漂浮、融化、复制、消失、变形、旋转、改字或自行运动。不要让包装文字、屏幕文字、Logo 或标签闪烁、漂移、重排或变成字幕。包装上印刷的人脸必须保持平面静态图形，不能眨眼、张嘴或说话。
<!-- OPENCREW:VIDEO_KLING_STATIC_OBJECTS_END -->

<!-- OPENCREW:VIDEO_KLING_AUDIO_CONTROL_START -->
默认请求可灵生成有声口播。声音只包含首帧人物的中文口播，不要背景音乐、额外声效、环境噪声或第二个说话人。说话风格为温暖松弛的中文口播，自然语调，节奏舒适。
<!-- OPENCREW:VIDEO_KLING_AUDIO_CONTROL_END -->

<!-- OPENCREW:VIDEO_KLING_NEGATIVE_BASE_START -->
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、中文字符、英文单词、水印、Logo、二维码、UI、界面元素、贴纸、新人物、新主持人、新路人、新手、新产品、新道具、新场景、换脸、身份漂移、脸部变形、五官漂移、发型变化、服装变化、背景变化、光线变化、构图变化、画面边缘漂移、背景物体移动、低质量、低清晰度、模糊、闪烁、颜色突变、卡通感
<!-- OPENCREW:VIDEO_KLING_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_KLING_NEGATIVE_CAMERA_START -->
镜头运动、手持晃动、轻微晃动、画面漂移、自动重构图、推镜、拉镜、摇镜、移镜、平移、旋转、变焦、视角变化、构图变化、跳切、切镜、场景变化、快速运动
<!-- OPENCREW:VIDEO_KLING_NEGATIVE_CAMERA_END -->

<!-- OPENCREW:VIDEO_KLING_NEGATIVE_EXPRESSION_START -->
夸张表情、过度表演、冻结表情、冷漠表情、凶表情、严厉表情、紧绷脸、皱眉、明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显酒窝、苹果肌过重、脸颊凹陷、深法令纹、深笑纹、抬头纹、额头横纹、深额纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指
<!-- OPENCREW:VIDEO_KLING_NEGATIVE_EXPRESSION_END -->

<!-- OPENCREW:VIDEO_KLING_NEGATIVE_OBJECTS_START -->
产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装融化、包装改字、屏幕内容变化、Logo 变形、平面人脸眨眼、平面人脸张嘴、平面人脸说话
<!-- OPENCREW:VIDEO_KLING_NEGATIVE_OBJECTS_END -->

<!-- OPENCREW:VIDEO_KLING_PITFALLS_APPEND_ONLY_START -->
- 2026-06-21 non-Omni baseline：首帧是唯一视觉来源；不要使用 Omni `<<<...>>>` 引用语法。
- 2026-06-21 image2video：普通图生视频使用 `image + prompt + negative_prompt`，负向提示词应放在独立 `negative_prompt` 字段。
- 2026-06-21 locked camera：镜头锁定，人物位置、比例、背景、画面边缘和构图保持首帧一致。
- 2026-06-21 clean frame：不要字幕、标题、屏幕文字、浮动文字、水印、Logo、二维码或 UI。
- 2026-06-21 restrained performance：嘴型准确但收敛，表情亲和但克制，手势只做胸口级小幅自然强调。
- 2026-06-21 sound default：非 Omni Kling 默认 sound=on；不要把普通 Kling 图生视频写成静音链路。
<!-- OPENCREW:VIDEO_KLING_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_KLING_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_KLING_PROMPT_END -->

## 中文直接使用版

```text
基于提供的首帧图片，生成一段约 {{duration_seconds}} 秒的竖屏 9:16 真实口播人物视频。首帧是唯一视觉来源，严格继承首帧中的人物身份、脸型、五官、发型、肤色、年龄感、服装、妆容、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系、人物比例和手机视频质感。

人物正对镜头，自然口播以下中文台词：“{{dialogue_text}}”。必须逐字说出，不要翻译、改写、总结或增加台词。对白只作为声音或口型出现，画面中不要出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。

镜头锁定，固定机位，保持首帧原始景别、视线高度、人物位置、人物比例、背景、画面边缘和构图不变。全程不要推镜、拉镜、摇镜、移镜、旋转、变焦、自动重构图、画面漂移、跳切、切镜或场景变化。

人物表情真实、亲和、放松、可信，眼神稳定看向镜头，自然眨眼，嘴角只有轻微上扬。不要明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显苹果肌、深法令纹、深笑纹、紧绷脸、皱眉或凶表情。

嘴部像自然中文口播，清楚但收敛，开口幅度适中，不夸张张嘴，不生成异常牙齿，不出现嘴部漂移、下巴变形或面部畸变。只允许自然呼吸、自然眨眼、小幅点头、轻微眉眼变化、准确口播嘴型和轻微衣料自然变化。

如有手势，只做短促、小到中等幅度的胸口级自然强调，手不要靠近镜头，不要遮挡脸，不要多指、少指或手部变形。

如果首帧中有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素，不漂移、不变形、不改字、不闪烁、不重排、不说话。

不要重新设计人物，不要换脸，不要换衣服，不要改变发型，不要改变背景，不要添加新主持人、新路人、新手、新产品、新道具或新场景。
```

## 中文负向提示词

```text
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、中文字符、英文单词、水印、Logo、二维码、UI、界面元素、贴纸、新人物、新主持人、新路人、新手、新产品、新道具、新场景、换脸、身份漂移、脸部变形、五官漂移、发型变化、服装变化、背景变化、光线变化、构图变化、画面边缘漂移、背景物体移动、镜头运动、手持晃动、轻微晃动、画面漂移、自动重构图、推镜、拉镜、摇镜、移镜、旋转、变焦、视角变化、跳切、切镜、场景变化、快速运动、夸张表情、过度表演、冻结表情、冷漠表情、凶表情、严厉表情、紧绷脸、皱眉、明显微笑、咧嘴笑、大笑、露齿笑、酒窝、明显酒窝、苹果肌过重、深法令纹、深笑纹、抬头纹、额头横纹、深额纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指、产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装融化、包装改字、屏幕内容变化、Logo 变形、平面人脸眨眼、平面人脸张嘴、平面人脸说话、低质量、低清晰度、模糊、闪烁、颜色突变、卡通感
```

## 推荐调用参数：Kling 2.5 Turbo

```json
{
  "model_name": "kling-v2-5-turbo",
  "image": "{{first_frame_base64_or_url}}",
  "prompt": "{{positive_prompt}}",
  "negative_prompt": "{{negative_prompt}}",
  "duration": "{{duration_seconds}}",
  "mode": "pro",
  "sound": "on",
  "watermark_info": {
    "enabled": false
  }
}
```

## 推荐调用参数：Kling 3.0 Turbo 本地脚本形态

```json
{
  "contents": [
    {
      "type": "prompt",
      "text": "{{positive_prompt}}\\n\\n负面提示词：\\n{{negative_prompt}}"
    },
    {
      "type": "first_frame",
      "url": "{{first_frame_base64_or_url}}"
    }
  ],
  "settings": {
    "resolution": "1080p",
    "duration": "{{duration_seconds}}"
  },
  "options": {
    "watermark_info": {
      "enabled": false
    }
  }
}
```
