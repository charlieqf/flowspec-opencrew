# DanceMimic_V1 后续执行 Seedance SDR2V DanceMimic 适配需求

版本：v0.1

> v0.6 implementation note：本文是早期执行路线草案。实施以 `DanceMimic_V1_实施收敛设计.md` v0.6 §8 为准：不新增 `video_sdr2v_dancemimic.py` provider 模块，DanceMimic reference-video 场景复用 `video_openrouter.py` + `Video_SDR2V_DanceMimic.md`，并通过 05_01/05_05 plan 字段与 05_02/05_06 路由/传参改造命中 `input_references`。

状态：需求草案。本文记录 DanceMimic_V1 在 03 生成标准 StoryBoard 后，继续运行 `05_02` / `05_06` 生成视频时的专用 Seedance 参考视频 + 首帧执行需求。

## 1. 需求结论

DanceMimic_V1 完成 00-03 后，用户进入现有 StoryBoard 页面继续运行 Video Plan 或 Video Only Plan。

当运行 `05_02_VideoPlanExecutor` 或 `05_06_VideoOnlyPlanExecutor` 生成视频时，需要新增一条 DanceMimic 专用视频生成路线：

```text
每个 Dialogue / Segment：
  首帧图片 = 当前 StoryBoard 图片槽位的新图 / 原图 / 物化首帧
  参考视频 = 02 拆分并遮脸后的对应片段视频
  视频模型 = Seedance SDR2V / reference-video + first-frame 能力
  执行模块 = DanceMimic 专用 SDR2V 模块
  提示词模板 = DanceMimic 专用 SDR2V 模板
```

核心规则：

1. 每个分段都只能使用自己对应的参考视频片段，不得使用全量源视频或其它 Dialogue 的参考视频。
2. 首帧图片仍按现有 StoryBoard 槽位规则选择，优先使用用户确认的新图或物化后的首帧。
3. 参考视频使用 02 产出的 `face_masked_reference_video_path`，也就是遮脸后的分段参考视频。
4. `05_02` / `05_06` 继续复用现有执行与回写合同，只在 provider 模块、prompt 模板和 plan 输入层增加 DanceMimic 专用适配。
5. Raw / Final 语义不改变：`05_06` 先产 Raw，Confirm Final 后才绑定 Final；`05_02` 完整链路仍负责 Final 和 TailFrame。

## 2. 命名约定

用户草案中提到的文件名：

```text
Video_SDR2V_DanceMinimc.py
```

正式命名建议统一修正为：

```text
Video_SDR2V_DanceMimic.py
```

原因：

1. 工具集名称是 `DanceMimic_V1`。
2. `DanceMinimc` 应视为草案拼写错误。
3. 文档、代码、模板、plan 字段应统一使用 `DanceMimic`，避免后续路由、配置和审计文件出现两个拼写。

结合当前 Analysis_V1 代码风格，实际落地建议为：

```text
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/video_sdr2v_dancemimic.py
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md
```

如果执行器注册或外部配置必须使用用户草案大小写名称，可在 registry / adapter 层暴露：

```text
Video_SDR2V_DanceMimic
```

但 Python 模块文件建议保持现有小写风格。

## 3. 与 03 StoryBoard 输出的关系

03 必须把 02 的分段参考视频绑定到 StoryBoard 可消费位置。

推荐 03 写入：

```text
SessionOutput/storyboard/assets/videos/{dialogue_asset_key}_Reference_FaceMasked.mp4
SessionOutput/storyboard/storyboard_seed.json
```

`storyboard_seed.json` 中每个 segment 必须保留：

```json
{
  "dialogue_asset_key": "dak_0001",
  "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
  "reference_video_source_path": "SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4",
  "sdr2v_provider_module": "video_sdr2v_dancemimic",
  "sdr2v_prompt_template": "Video_SDR2V_DanceMimic.md",
  "start": 0.0,
  "end": 8.0,
  "duration": 8.0
}
```

03 不生成 Raw / Final 视频，只准备后续计划和执行器能找到每段参考视频。

## 4. Plan 字段需求

后续 `05_01` / `05_05` 生成 Video Plan / Video Only Plan 时，DanceMimic 任务需要在 segment video task 中携带：

```json
{
  "asset_key": "dak_0001",
  "dialogue_asset_key": "dak_0001",
  "video_generation_mode": "seedance_sdr2v_dancemimic",
  "provider_module": "video_sdr2v_dancemimic",
  "prompt_template": "Video_SDR2V_DanceMimic.md",
  "first_frame_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png",
  "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
  "reference_video_role": "dance_mimic_segment_motion_reference",
  "duration": 8.0,
  "model_alias": "MaxSR2"
}
```

字段规则：

1. `provider_module` 必须指向 DanceMimic 专用 SDR2V 模块。
2. `reference_video_path` 必须来自当前 `dialogue_asset_key` 对应的分段参考视频。
3. `first_frame_path` 必须按现有槽位矩阵解析，不得从参考视频临时抽帧替代用户确认的新图。
4. 如果该 Dialogue 没有可用首帧，当前 segment 应 blocked，不得偷用参考视频第一帧冒充首帧。
5. 如果该 Dialogue 没有对应分段参考视频，当前 segment 应 blocked。

## 5. 首帧图片选择规则

首帧图片用于告诉 Seedance 当前要生成的主体、画面身份、构图、背景和风格。

推荐优先级：

```text
1. SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*
2. SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_Source.*
3. 已落位上传图片物化后的标准首帧
4. 上一段已确认 Final 的 TailFrame，仅在现有 TailFrame 规则允许时使用
```

禁止：

1. 不得直接从遮脸参考视频抽首帧作为默认首帧。
2. 不得使用其它 Dialogue 的图片槽位。
3. 不得使用未落位上传素材作为首帧。
4. 不得在缺首帧时静默降级到纯文生视频。

## 6. 参考视频选择规则

参考视频用于提供动作、姿态、节奏、镜头内运动和分段长度参考。

DanceMimic 的参考视频必须来自 02 对应分段：

```text
SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4
```

03 发布到 StoryBoard assets 后，执行器读取：

```text
SessionOutput/storyboard/assets/videos/{dialogue_asset_key}_Reference_FaceMasked.mp4
```

要求：

1. 每个 `dialogue_asset_key` 只绑定自己的参考视频。
2. 参考视频应为遮脸版本，避免把原人脸身份传给模型。
3. 参考视频时长应与当前 Dialogue duration 一致或在容差内。
4. 参考视频不得作为 Final / Raw 业务视频展示。
5. 参考视频缺失时，当前 segment blocked。

## 7. 专用执行模块需求

新增执行模块：

```text
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/video_sdr2v_dancemimic.py
```

模块职责：

1. 读取当前 segment 的 `first_frame_path`。
2. 读取当前 segment 的 `reference_video_path`。
3. 渲染 `Video_SDR2V_DanceMimic.md` 专用提示词。
4. 调用 Seedance 的 reference-video + first-frame 视频生成能力。
5. 下载 provider 输出视频。
6. 写入本工具执行目录 `Output/`。
7. 由 `05_02` / `05_06` 按现有规则发布到 `SessionOutput/storyboard/Working/`。
8. 记录请求摘要、输入文件 sha256、模型别名、provider task id、输出路径。
9. 不把 API key、Authorization header、signed URL 写入 Prompt、Result、Output 或 stdout。

模块接口应尽量对齐现有 `video_seedance.py` / `video_openrouter.py` 风格，便于 `05_02` 和 `05_06` 复用。

## 8. 专用提示词模板需求

新增模板：

```text
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md
```

模板目标：

1. 明确图 1 / first frame 是主体、画面身份、构图和背景锚点。
2. 明确视频 1 / reference video 只作为动作、姿态、节奏和镜头内运动参考。
3. 明确不要迁移参考视频中的原人脸身份。
4. 明确保持首帧人物、服装、背景、构图和比例稳定。
5. 明确不要字幕、水印、屏幕文字、额外人物、场景跳变。
6. 明确时长约等于当前 segment duration。

模板至少包含以下块：

```text
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_POSITIVE_BASE
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_DIALOGUE_ACTION
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_REFERENCE_VIDEO_RULES
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_CAMERA_LOCK
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_NEGATIVE_BASE
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_PITFALLS_APPEND_ONLY
OPENCREW:VIDEO_SDR2V_DANCEMIMIC_PROMPT
```

提示词语义建议：

```text
图1是唯一画面身份来源，保持图1的人物、服装、背景、构图、光线和人物比例。
视频1只参考身体动作、手势节奏、姿态变化、镜头内运动和分段节奏。
不要迁移视频1中的原人物身份、五官、服装、背景或遮脸网格。
生成与当前片段时长一致的真实自然视频。
```

负向提示词建议：

```text
身份漂移、参考视频人物身份迁移、脸部变形、服装变化、背景变化、构图变化、镜头跳切、场景重置、字幕、水印、屏幕文字、Logo、额外人物、遮脸网格残留、低质量、模糊、动作夸张
```

## 9. `05_02` 适配需求

`05_02_VideoPlanExecutor` 执行 DanceMimic segment 时：

1. 从 plan 中识别 `provider_module = video_sdr2v_dancemimic`。
2. 准备 Dialogue 音频、首帧图片和对应参考视频。
3. 先调用 SDR2V 生成 Raw video。
4. 如当前完整链路需要音频同步 / lipsync，继续按现有 05_02 合同处理。
5. 最终发布 `{asset_key}_Video_Final.*`。
6. 抽取 `{asset_key}_TailFrame.*`。
7. 回写 StoryBoard JSON 和执行状态。

不改变：

1. `dialogue_asset_key` 资源锚点。
2. Working 标准文件命名。
3. Final / TailFrame 语义。
4. 失败、blocked、warning 的 Result 结构。

## 10. `05_06` 适配需求

`05_06_VideoOnlyPlanExecutor` 执行 DanceMimic segment 时：

1. 从 video-only plan 中识别 `provider_module = video_sdr2v_dancemimic`。
2. 准备首帧图片和对应参考视频。
3. 调用 SDR2V 生成 Raw video。
4. 发布 `{asset_key}_Video_Raw.*`。
5. 等待用户 Confirm Final。
6. Confirm 后复制 / 绑定为 `{asset_key}_Video_Final.*`。
7. Final 后才解锁 TailFrame 延续。

不改变：

1. Raw 不等于 Final。
2. Confirm Final 是绑定 Final 的唯一入口。
3. VideoOnly 生成 Raw 后不自动冒充完整 Final。

## 11. 审计与 QA

每次 SDR2V 调用必须记录：

```json
{
  "provider_module": "video_sdr2v_dancemimic",
  "prompt_template": "Video_SDR2V_DanceMimic.md",
  "dialogue_asset_key": "dak_0001",
  "first_frame_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png",
  "first_frame_sha256": "",
  "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
  "reference_video_sha256": "",
  "reference_video_source": "dance_mimic_segment_0001",
  "duration": 8.0,
  "model_alias": "MaxSR2",
  "provider_task_id": "",
  "output_video_path": ""
}
```

验收要求：

1. 每个 Dialogue 的 reference video 路径与 02 manifest 一一对应。
2. 执行器不会使用全量参考视频。
3. 执行器不会使用其它 Dialogue 的参考视频。
4. 缺首帧时 blocked。
5. 缺参考视频时 blocked。
6. Prompt 审计文件写明模板来源和关键输入。
7. Result 不泄漏密钥、Authorization、signed URL。

## 12. 与现有通用 Seedance 的关系

现有通用 Seedance 模块仍可保留：

```text
video_seedance.py
Video_Seedance.md
```

DanceMimic 不直接复用通用模板作为默认原因：

1. DanceMimic 每段必须带“对应分段参考视频”。
2. DanceMimic 的参考视频是遮脸后的动作参考，不是身份来源。
3. DanceMimic 需要在提示词中明确“首帧是身份锚点，参考视频只提供动作/节奏”。
4. DanceMimic 需要避免模型迁移原参考视频中的人脸身份或遮脸网格。

因此建议新增专用模块与专用模板，而不是把通用 `video_seedance.py` 直接改成带 DanceMimic 分支。

## 13. 错误码建议

```text
dancemimic_reference_video_missing
dancemimic_reference_video_not_segment_matched
dancemimic_first_frame_missing
dancemimic_prompt_template_missing
dancemimic_sdr2v_provider_missing
dancemimic_sdr2v_request_failed
dancemimic_sdr2v_output_missing
dancemimic_sdr2v_output_download_failed
```

## 14. 后续落地顺序

1. 03 在 `storyboard_seed.json` 写入每段 `reference_video_path` 和 SDR2V 默认模块信息。
2. 05_01 / 05_05 生成计划时带上 `provider_module = video_sdr2v_dancemimic`。
3. 新增 `Video_SDR2V_DanceMimic.md`。
4. 新增 `video_sdr2v_dancemimic.py`。
5. 05_02 / 05_06 路由到该模块。
6. 增加回归测试：首帧选择、参考视频一一对应、缺输入 blocked、Raw/Final 回写一致。
