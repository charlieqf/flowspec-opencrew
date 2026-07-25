# 05_02 Video Chanjing Kling Prompt Template / ChanJing 可灵视频提示词模板

适用范围：蝉镜 Kling 视频生成，重点用于首帧图片生成竖屏 9:16 口播人物视频；也支持被标记为 cutaway / product closeup 的无口播空镜段。

中文用于维护；`OPENCREW:*` 块名、字段名和变量名保持英文，块内提示词正文使用中文。

## 中文维护版

ChanJing Kling 口播任务：基于上传的首帧图片生成真实竖屏 9:16 口播人物视频。首帧是唯一视觉来源，提供人物身份、脸型、五官、发型、服装、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系和手机视频质感。

提示词只控制首帧之后的自然运动：人物如何口播、嘴型如何收敛、镜头如何稳定、表情如何更丰富但不夸张、肢体语言如何更主动但不失真、哪些元素绝对不能变化。不要重新设计人物、服装、背景、道具或场景。

如果当前段落是 cutaway / product closeup / no visible face，则不要求人物口播，只生成稳定空镜、产品特写或过渡镜头；台词只作为节奏和语义参考，不要生成字幕或屏幕文字。

### 中文踩坑记录（只追加）

- 2026-06-21 baseline：首帧是唯一视觉来源，保持人物、产品、背景、光线、构图和手机视频质感。
- 2026-06-21 block prefix：此模板必须使用 `VIDEO_CHANJING_KLING_*` 块名，不能复用 `VIDEO_KLING_*`。
- 2026-06-21 standard dialogue：标准口播段需要人物看向镜头自然口播中文台词。
- 2026-06-21 cutaway：cutaway 段不要强制口播；保持产品、环境、背景、光线稳定。
- 2026-06-21 clean frame：口播内容只允许成为声音或口型，不允许出现在字幕、标题、屏幕文字、浮动文字、水印或界面中。
- 2026-06-21 restrained mouth：口播嘴型准确但收敛，不能大幅张嘴、下巴变形、牙齿异常或嘴部漂移。
- 2026-06-21 expressive friendly performance：人物需要面带微笑并更有感染力；表情丰富主要来自眼神、眉眼、头部节奏和手势，不要依赖大幅张嘴或夸张笑容。
- 新增踩坑时只追加，不要改写历史记录；同时在 `VIDEO_CHANJING_KLING_PITFALLS_APPEND_ONLY` 中追加对应模型调用禁令。

## 模型调用版

<!-- OPENCREW:VIDEO_CHANJING_KLING_POSITIVE_BASE_START -->
ChanJing Kling 图生视频任务：基于提供的首帧图片，生成真实竖屏 9:16 视频。

首帧是唯一视觉来源。严格继承首帧中的人物身份、脸型五官、发型肤色、年龄感、服装妆容、产品或手持道具、背景光线、镜头角度、构图景别、主体比例和手机视频质感。

不要重新设计人物、服装、发型、背景、道具或场景，不要添加新人物、新手、新产品或新物体。

目标时长：约 {{duration_seconds}} 秒。
<!-- OPENCREW:VIDEO_CHANJING_KLING_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_DIALOGUE_STANDARD_START -->
首帧中的人物正对镜头，自然口播以下中文台词：
"{{dialogue_text}}"

必须逐字说出，不要翻译、改写、总结或增加台词。对白只作为声音或口型出现。画面必须干净，不要出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。

语速比一般人口播更快，节奏紧凑、干脆、有推进感；同时吐字清楚、语气松弛自然，不抢话、不含糊，不因为语速快而嘴型夸张或表情紧绷。
<!-- OPENCREW:VIDEO_CHANJING_KLING_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_DIALOGUE_CUTAWAY_START -->
当前段落是 cutaway / product closeup / no visible face，不要求人物口播。

台词仅作为画面节奏和语义参考：
"{{dialogue_text}}"

画面以首帧中的产品、环境、人物局部、道具或场景为主，保持稳定、真实、干净。不要生成字幕、屏幕文字、水印、Logo、二维码或 UI。
<!-- OPENCREW:VIDEO_CHANJING_KLING_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_CAMERA_LOCK_START -->
镜头锁定，固定机位，保持首帧原始景别、视线高度、主体位置、主体比例、背景、画面边缘和构图不变。

全程不要推拉摇移、旋转、变焦、自动重构图、画面漂移、跳切、切镜、手持漂移或场景变化。
<!-- OPENCREW:VIDEO_CHANJING_KLING_CAMERA_LOCK_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_PERFORMANCE_START -->
表演风格：真实、松弛、自然、亲和，像带着表达欲和镜头前的人轻松聊天。人物眼神稳定看向镜头，眼神温暖，眉眼有自然变化，脸上始终带轻微笑意。

人物嘴角轻轻上扬，神态放松、不端着、不用力表演。表情丰富但不过度，主要来自眼神、眉眼、头部节奏和手势，不靠大幅张嘴或夸张笑容。避免咧嘴笑、露齿大笑、酒窝、苹果肌、抬头纹、额头横纹、紧绷脸或皱眉。

嘴部是自然中文口播嘴型，清楚但收敛，开口幅度适中。语速偏快时仍保持稳定自然，避免抢话、含糊、夸张张嘴、异常牙齿、嘴部漂移、下巴变形或面部畸变。

肢体语言更主动但松弛自然：可以有 2-3 次胸口级开放手掌、向内强调、轻微前推或双手配合表达动作。动作和台词重点同步，像自然聊天时顺手发生；手不要靠近镜头、遮挡脸、夸张挥手或变形。
<!-- OPENCREW:VIDEO_CHANJING_KLING_PERFORMANCE_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_STATIC_OBJECTS_START -->
如果首帧中有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素，形状、颜色、位置、尺寸、透视和文字布局稳定。

不要让产品漂浮、融化、复制、消失、旋转、改字或自行运动。包装文字、屏幕文字、Logo 和标签不要闪烁、漂移、重排或变成字幕；印刷人脸不能眨眼、张嘴或说话。
<!-- OPENCREW:VIDEO_CHANJING_KLING_STATIC_OBJECTS_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_BASE_START -->
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、水印、Logo、二维码、UI、贴纸、新人物、新手、新产品、新道具、新场景、换脸、身份漂移、脸部变形、五官漂移、发型变化、服装变化、背景变化、光线变化、构图变化、低质量、模糊、闪烁、卡通感
<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_CAMERA_START -->
镜头运动、手持晃动、画面漂移、自动重构图、推拉摇移、旋转、变焦、视角变化、跳切、切镜、场景变化
<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_CAMERA_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_EXPRESSION_START -->
夸张表情、过度表演、冻结表情、冷漠表情、凶表情、紧绷脸、皱眉、咧嘴笑、大笑、露齿笑、夸张假笑、酒窝、苹果肌、抬头纹、额头横纹、深法令纹、深笑纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指
<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_EXPRESSION_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_OBJECTS_START -->
产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装融化、包装改字、屏幕内容变化、Logo 变形、平面人脸眨眼、平面人脸张嘴、平面人脸说话
<!-- OPENCREW:VIDEO_CHANJING_KLING_NEGATIVE_OBJECTS_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_PITFALLS_APPEND_ONLY_START -->
模板说明文字、日期记录、调试记录、block marker、代码标记、维护注释、研发备注

照读提示词、把提示词显示在画面中、字幕化台词、屏幕文字、浮动文字、UI 提示

强制空镜口播、无脸画面嘴动、产品或印刷人脸说话

表情僵硬、表情紧绷、表情过度、假笑、咧嘴笑、露齿大笑、酒窝、苹果肌、抬头纹

语速拖沓、语速过慢、抢话、含糊、嘴型夸张、下巴变形、口型漂移
<!-- OPENCREW:VIDEO_CHANJING_KLING_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_CHANJING_KLING_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_CHANJING_KLING_PROMPT_END -->

## 中文直接使用版

```text
基于提供的首帧图片，生成一段约 {{duration_seconds}} 秒的竖屏 9:16 真实视频。首帧是唯一视觉来源，严格继承首帧中的人物身份、脸型五官、发型肤色、年龄感、服装妆容、产品或手持道具、背景光线、镜头角度、构图景别、主体比例和手机视频质感。

如果是标准口播段：人物正对镜头，自然口播以下中文台词：“{{dialogue_text}}”。必须逐字说出，不要翻译、改写、总结或增加台词。语速比一般人口播更快，节奏紧凑、干脆、有推进感；同时吐字清楚、语气松弛自然，不抢话、不含糊。对白只作为声音或口型出现，画面中不要出现字幕、屏幕文字、水印、Logo、二维码或 UI。

如果是 cutaway / product closeup / no visible face 段：不要求人物口播，台词只作为画面节奏和语义参考；保持首帧中的产品、环境、人物局部、道具或场景稳定真实。

镜头锁定，固定机位，保持首帧原始景别、视线高度、主体位置、主体比例、背景、画面边缘和构图不变。全程不要推镜、拉镜、摇镜、移镜、旋转、变焦、自动重构图、画面漂移、跳切、切镜或场景变化。

人物口播时，表情真实、松弛、自然、亲和，像带着表达欲和镜头前的人轻松聊天。眼神稳定温暖，眉眼自然变化，脸上保持轻微笑意。表情丰富主要来自眼神、眉眼、头部节奏和手势，不靠大幅张嘴或夸张笑容；不要酒窝、苹果肌、抬头纹或额头横纹。嘴部像自然中文口播，清楚但收敛，开口幅度适中。肢体语言更主动但松弛自然，可以有 2-3 次胸口级开放手掌、向内强调、轻微前推或双手配合表达动作，动作和台词重点同步，不紧绷、不刻意、不用力。

如有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素，不漂移、不变形、不改字、不闪烁、不重排、不说话。
```

## 中文负向提示词

```text
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、水印、Logo、二维码、UI、贴纸、新人物、新手、新产品、新道具、新场景、换脸、身份漂移、脸部变形、五官漂移、发型变化、服装变化、背景变化、光线变化、构图变化、镜头运动、手持晃动、画面漂移、自动重构图、推拉摇移、旋转、变焦、跳切、切镜、夸张表情、过度表演、冷漠表情、凶表情、紧绷脸、皱眉、咧嘴笑、大笑、露齿笑、酒窝、苹果肌、抬头纹、额头横纹、深法令纹、深笑纹、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指、产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装改字、Logo 变形、平面人脸眨眼、平面人脸张嘴、低质量、模糊、闪烁、卡通感
```
