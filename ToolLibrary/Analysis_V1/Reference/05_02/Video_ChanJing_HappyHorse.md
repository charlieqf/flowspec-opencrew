# 05_02 Video Chanjing HappyHorse Prompt Template / ChanJing HappyHorse视频提示词模板

适用范围：蝉镜 HappyHorse 视频生成，重点用于首帧图片生成竖屏 9:16 口播人物视频；也支持被标记为 cutaway / product closeup 的无口播空镜段。

中文用于维护；`OPENCREW:*` 块名、字段名和变量名保持英文，块内提示词正文使用中文。

## 中文维护版

ChanJing HappyHorse 口播任务：基于上传的首帧图片生成真实竖屏 9:16 口播人物视频。首帧是唯一视觉来源，提供人物身份、脸型、五官、发型、服装、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系和手机视频质感。

提示词只控制首帧之后的自然运动：人物如何口播、嘴型如何收敛、镜头如何稳定、表情和手势如何克制、哪些元素绝对不能变化。不要重新设计人物、服装、背景、道具或场景。

HappyHorse 版本重点：优先保持参考身份、首帧构图和主体一致性。它覆盖 i2v / r2v / t2v / video-edit 等模型码，但当前 Analysis V1 口播链路主要使用首帧生成；提示词必须强调“不要把参考当成新角色设计、不要改身份、不要生成多主体、不要扩大动作范围”。

统一强化方向：人物表情必须松弛、面带自然笑意、亲和力强，口播节奏略快于一般人，表现力比普通口播更强，但不能变成夸张演讲、咧嘴大笑、露齿笑或紧绷用力的表演。

如果当前段落是 cutaway / product closeup / no visible face，则不要求人物口播，只生成稳定空镜、产品特写或过渡镜头；台词只作为节奏和语义参考，不要生成字幕或屏幕文字。

### 中文踩坑记录（只追加）

- 2026-06-21 baseline：首帧是唯一视觉来源，保持人物、产品、背景、光线、构图和手机视频质感。
- 2026-06-21 block prefix：此模板必须使用 `VIDEO_CHANJING_HAPPYHORSE_*` 块名，不能复用 `VIDEO_KLING_*`。
- 2026-06-21 standard dialogue：标准口播段需要人物看向镜头自然口播中文台词。
- 2026-06-21 cutaway：cutaway 段不要强制口播；保持产品、环境、背景、光线稳定。
- 2026-06-21 clean frame：口播内容只允许成为声音或口型，不允许出现在字幕、标题、屏幕文字、浮动文字、水印或界面中。
- 2026-06-21 restrained mouth：口播嘴型准确但收敛，不能大幅张嘴、下巴变形、牙齿异常或嘴部漂移。
- 2026-06-21 HappyHorse reference lock：HappyHorse 参考模型容易扩大动作或生成新主体；必须强调只继承首帧主体，不新增角色、不改身份、不多主体。
- 2026-06-21 expressive relaxed smile：所有口播模型统一强化松弛表情、自然笑意、略快语速和更强表现力，同时禁止大笑、露齿笑、抬头纹、皱眉和用力表演。
- 新增踩坑时只追加，不要改写历史记录；同时在 `VIDEO_CHANJING_HAPPYHORSE_PITFALLS_APPEND_ONLY` 中追加对应模型调用禁令。

## 模型调用版

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_POSITIVE_BASE_START -->
ChanJing HappyHorse 图生视频任务：基于提供的首帧图片，生成真实竖屏 9:16 视频。

首帧是唯一视觉来源。严格继承首帧中的人物身份或主体身份、脸型、五官、发型、肤色、年龄感、服装、妆容、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系、主体比例和手机视频质感。

不要重新设计人物或主体，不要换脸，不要换衣服，不要改变发型，不要改变背景，不要添加新主持人、新路人、新手、新产品、新道具或新场景。

HappyHorse 运动策略：把首帧作为唯一主体参考，只做安全、稳定、表情松弛、面带笑意、表现力更强的口播运动。不要让模型生成第二个人、替换主体、扩展身体动作、重设姿势、改成全身表演或视频编辑式重绘。

目标时长：约 {{duration_seconds}} 秒。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_DIALOGUE_STANDARD_START -->
首帧中的人物正对镜头，自然口播以下中文台词：
"{{dialogue_text}}"

必须逐字说出，不要翻译、改写、总结或增加台词。对白只作为声音或口型出现。画面必须干净，不要出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。

口播节奏略快于一般人，像经验丰富的短视频口播者，语流更紧凑但吐字清楚。嘴型跟随偏快语速自然变化，不要拖沓、不要慢吞吞，也不要快到嘴部失真。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_DIALOGUE_CUTAWAY_START -->
当前段落是 cutaway / product closeup / no visible face，不要求人物口播。

台词仅作为画面节奏和语义参考：
"{{dialogue_text}}"

画面以首帧中的产品、环境、人物局部、道具或场景为主，保持稳定、真实、干净。不要生成字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_CAMERA_LOCK_START -->
镜头锁定，固定机位，保持首帧原始景别、视线高度、主体位置、主体比例、背景、画面边缘和构图不变。

全程不要推镜、拉镜、摇镜、移镜、平移、旋转、变焦、自动重构图、画面漂移、视角变化、跳切、切镜、手持漂移或场景变化。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_CAMERA_LOCK_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_PERFORMANCE_START -->
表演风格：真实、自然、放松、亲和、可信，同时表现力强。人物眼神稳定看向镜头，自然眨眼，有轻微呼吸和小幅头部稳定运动。整体像熟练口播者，状态松弛但有感染力。

表情保持松弛、温暖、亲和、面带自然笑意。笑意可以清楚可见，但必须自然、轻松、不过度用力。不要咧嘴笑、大笑、露齿笑、僵硬假笑、挤出酒窝、明显苹果肌、深法令纹、深笑纹、紧绷脸、皱眉、抬眉导致抬头纹、凶表情或严厉表情。

嘴部动作为略快于一般人的自然中文口播嘴型，清楚、有节奏、有表现力，开口幅度适中。不要夸张张嘴、异常牙齿、嘴部漂移、下巴变形、面部畸变或口型慢半拍。

如有手势，允许小到中等幅度的胸口级自然强调，表现力比普通口播更强，但动作必须协调、有节奏、有停顿。手不要靠近镜头、不要遮挡脸、不要夸张挥手、不要多指少指或手部变形。

如果首帧中手部不可见或只露出一部分，不要凭空生成大幅手势。手部可见时只做小幅自然动作，保持手、袖口、道具和身体位置关系稳定。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_PERFORMANCE_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_STATIC_OBJECTS_START -->
如果首帧中有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素。保持形状、颜色、位置、尺寸、透视、标签布局和文字位置稳定。

不要让产品漂浮、融化、复制、消失、变形、旋转、改字或自行运动。不要让包装文字、屏幕文字、Logo 或标签闪烁、漂移、重排或变成字幕。包装上印刷的人脸必须保持平面静态图形，不能眨眼、张嘴或说话。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_STATIC_OBJECTS_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_BASE_START -->
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、中文字符、英文单词、水印、Logo、二维码、UI、界面元素、贴纸、新人物、新主持人、新路人、新手、新产品、新道具、新场景、换脸、身份漂移、脸部变形、五官漂移、主体变形、发型变化、服装变化、背景变化、光线变化、构图变化、画面边缘漂移、背景物体移动、低质量、低清晰度、模糊、闪烁、颜色突变、卡通感
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_CAMERA_START -->
镜头运动、手持晃动、轻微晃动、画面漂移、自动重构图、推镜、拉镜、摇镜、移镜、平移、旋转、变焦、视角变化、构图变化、跳切、切镜、场景变化、快速运动
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_CAMERA_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_EXPRESSION_START -->
夸张表情、过度用力表演、冻结表情、冷漠表情、凶表情、严厉表情、紧绷脸、皱眉、僵硬假笑、咧嘴笑、大笑、露齿笑、酒窝、明显酒窝、苹果肌过重、脸颊凹陷、深法令纹、深笑纹、抬头纹、额头横纹、深额纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_EXPRESSION_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_OBJECTS_START -->
产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装融化、包装改字、屏幕内容变化、Logo 变形、平面人脸眨眼、平面人脸张嘴、平面人脸说话
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_NEGATIVE_OBJECTS_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_PITFALLS_APPEND_ONLY_START -->
- 2026-06-21 block prefix：模板块名必须是 `VIDEO_CHANJING_HAPPYHORSE_*`。
- 2026-06-21 baseline：首帧是唯一视觉来源，保持身份、主体、背景、光线、构图和手机视频质感。
- 2026-06-21 standard dialogue：标准口播段需要人物逐字口播中文台词，不要生成字幕或屏幕文字。
- 2026-06-21 cutaway：cutaway / product closeup / no visible face 段不要强制口播。
- 2026-06-21 clean frame：不要字幕、标题、屏幕文字、浮动文字、水印、Logo、二维码或 UI。
- 2026-06-21 restrained performance：嘴型准确但收敛，表情亲和但克制，手势只做胸口级小幅自然强调。
- 2026-06-21 HappyHorse reference lock：不要新增人物、多主体、全身表演、重设姿势或把首帧参考重绘成另一个角色。
- 2026-06-21 expressive relaxed smile：表情松弛，面带自然笑意，语速略快于一般人，表现力强但不过度用力。
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_CHANJING_HAPPYHORSE_PROMPT_END -->

## 中文直接使用版

```text
基于提供的首帧图片，生成一段约 {{duration_seconds}} 秒的竖屏 9:16 真实视频。首帧是唯一视觉来源，严格继承首帧中的人物身份或主体身份、脸型、五官、发型、肤色、年龄感、服装、妆容、产品或手持道具、背景、光线、镜头角度、构图、景别、透视关系、主体比例和手机视频质感。

如果是标准口播段：人物正对镜头，以略快于一般人的口播节奏自然说出以下中文台词：“{{dialogue_text}}”。必须逐字说出，不要翻译、改写、总结或增加台词。对白只作为声音或口型出现，画面中不要出现字幕、标题、说明文字、屏幕文字、浮动文字、中文字符、英文单词、水印、Logo、二维码或 UI。

如果是 cutaway / product closeup / no visible face 段：不要求人物口播，台词只作为画面节奏和语义参考；保持首帧中的产品、环境、人物局部、道具或场景稳定真实。

镜头锁定，固定机位，保持首帧原始景别、视线高度、主体位置、主体比例、背景、画面边缘和构图不变。全程不要推镜、拉镜、摇镜、移镜、旋转、变焦、自动重构图、画面漂移、跳切、切镜或场景变化。

人物口播时，表情真实、亲和、放松、可信，眼神稳定看向镜头，自然眨眼，面带自然笑意，表现力强。嘴部像略快于一般人的中文口播，清楚、有节奏、开口幅度适中，不夸张张嘴，不生成异常牙齿，不出现嘴部漂移、下巴变形或面部畸变。

HappyHorse 必须锁定首帧主体和参考身份：不要新增第二个人、不要改变人物姿势范围、不要改成全身表演，不要因为 r2v / video-edit 模型码而重绘角色或场景。

如有产品、包装、屏幕、标识、标签、文字、平面图形或印刷人脸，它们必须保持静态视觉元素，不漂移、不变形、不改字、不闪烁、不重排、不说话。
```

## 中文负向提示词

```text
字幕、标题、说明文字、屏幕文字、浮动文字、生成文字、中文字符、英文单词、水印、Logo、二维码、UI、界面元素、贴纸、新人物、新主持人、新路人、新手、新产品、新道具、新场景、换脸、身份漂移、脸部变形、五官漂移、主体变形、发型变化、服装变化、背景变化、光线变化、构图变化、画面边缘漂移、背景物体移动、镜头运动、手持晃动、轻微晃动、画面漂移、自动重构图、推镜、拉镜、摇镜、移镜、旋转、变焦、视角变化、跳切、切镜、场景变化、快速运动、夸张表情、过度用力表演、冻结表情、冷漠表情、凶表情、严厉表情、紧绷脸、皱眉、僵硬假笑、咧嘴笑、大笑、露齿笑、酒窝、明显酒窝、苹果肌过重、深法令纹、深笑纹、抬头纹、额头横纹、深额纹、嘴部变形、嘴巴张太大、夸张嘴型、口型不同步、异常牙齿、下巴变形、面部畸变、手靠近镜头、遮挡脸、夸张挥手、手部畸形、多余手指、缺失手指、产品漂移、产品变形、产品融化、产品复制、产品消失、产品旋转、标签变化、文字变形、包装融化、包装改字、屏幕内容变化、Logo 变形、平面人脸眨眼、平面人脸张嘴、平面人脸说话、低质量、低清晰度、模糊、闪烁、颜色突变、卡通感
```
