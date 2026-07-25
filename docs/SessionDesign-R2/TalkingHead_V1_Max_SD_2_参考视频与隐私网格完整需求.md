# TalkingHead_V1 Max SD 2 参考视频与隐私网格完整需求

> 状态：已实现并验证
> 日期：2026-07-15
> 适用工作流：`person_talking_head_v1`
> 模型显示名：`Max SD 2`

## 1. 目标

在人物口播创建流程中新增 `Max SD 2`，使用 OpenRouter 的
`bytedance/seedance-2.0` 和 `input_references` 完成参考视频生视频。

参考视频提供以下视觉参考：

- 参考人物的表情及表情变化；
- 动作；
- 姿态；
- 节奏；
- 手势；
- 镜头运动。

参考视频不得成为身份来源。人物身份、脸型、发型、服装、体型和主体外观以用户上传的人物形象照片为准。

## 2. 模型与人物口播参数

新增标准模型：

| 字段 | 值 |
|---|---|
| `model_key` | `max_sd_2` |
| `provider` | `openrouter` |
| `model` | `bytedance/seedance-2.0` |
| `model_alias` | `Max SD 2` |
| `reference_mode` | `input_references` |
| 单段最大时长 | 15 秒 |
| 专用提示词 | `Video_SDR2V_TalkingHead.md` |

其余表单参数、默认值和人物口播分段行为与 `Max 2.7 W` 一致，包括容忍度、首帧覆盖视频个数、人物照片、参考视频、声音和语速；单段时长上限按 Max SD 2 模型能力独立设置为 15 秒。

## 3. 参考隐私设置

选择 `Max SD 2` 时，隐私模式在系统内部固定为：

```text
隐私网格（身份可见）
```

内部值固定为 `red_grid_guide`。页面不显示“参考隐私设置”下拉框，用户无需也不能修改该固定模式。

选择后显示两个独立复选框：

1. 参考视频：应用隐私网格；
2. 目标人物图：应用隐私网格。

“应用目标图”默认勾选。“应用视频”的初始值由参考视频来源决定：使用系统默认参考视频时默认取消，切换为用户上传参考视频时默认勾选。

系统默认参考视频是已准备的系统资产，`use_default_reference_video=true` 时不得再次运行参考视频隐私网格覆盖逻辑。网格预设下拉框仍保持可选，用于人工比较、密度测试、目标图处理，以及后续切换为上传视频时复用；该选择不得触发系统默认参考视频的重复覆盖。

用户上传参考视频时，参考视频是否执行网格处理读取“应用视频”的最终布尔值；目标图片是否执行网格处理始终只读取“应用目标图”的最终布尔值。用户上传视频且两项均取消时允许保存和运行，但界面必须提示原始视觉输入会直接发送给模型。

### 3.1 隐私网格预设下拉框

选择 `Max SD 2` 时显示组合预设下拉框，字段固定为 `privacy_grid_preset`。“单个视频长度 / 容忍度 / 首帧覆盖视频个数 / 网格密度与线宽 / 应用视频与应用目标图”必须保持一行布局，并位于参考视频素材区之前。界面必须按以下顺序和名称显示 8 个选项：

| 顺序 | 下拉显示名称 | 枚举值 | `cell_size_reference` | `line_width_reference` |
|---:|---|---|---:|---:|
| 1 | `密集 12×1（默认）` | `dense_12_1` | 12 | 1 |
| 2 | `密集细线 12×0.5` | `dense_12_0_5` | 12 | 0.5 |
| 3 | `较密 24×1` | `medium_dense_24_1` | 24 | 1 |
| 4 | `较密细线 24×0.5` | `medium_dense_24_0_5` | 24 | 0.5 |
| 5 | `稀疏 36×1` | `sparse_36_1` | 36 | 1 |
| 6 | `稀疏细线 36×0.5` | `sparse_36_0_5` | 36 | 0.5 |
| 7 | `极疏 48×1` | `very_sparse_48_1` | 48 | 1 |
| 8 | `极疏细线 48×0.5` | `very_sparse_48_0_5` | 48 | 0.5 |

默认预设固定为 `dense_12_1`，界面显示 `密集 12×1（默认）`。名称中不显示 `px`；`×` 左侧数字是 1080 画面短边下的网格基础间距，右侧数字是红线线宽。

下拉框只在 `reference_privacy_mode=red_grid_guide` 时显示。任一预设变更后必须立即进入表单状态，保存后可完整恢复；变更预设不得自动改动“应用视频”或“应用目标图”两个开关。

“隐私网格”是身份仍可见的输入侧红色细线标记，不得描述为匿名化、身份去除或强隐私遮挡。

## 4. 独立工具边界

人物口播必须在 `ToolLibrary/TalkingHead_V1` 内拥有独立实现，运行时不得导入 `DanceMimic_V1` 或 `Analysis_V1` 的隐私网格实现。

需要在人物口播工具目录内拥有：

- 独立的 OpenRouter provider 模块 `video_plan_executor_modules/video_openrouter.py`；
- 独立提示词 `Reference/05_02/Video_SDR2V_TalkingHead.md`；
- 独立系统兜底视频 `Reference/05_02/Video_SDR2V_TalkingHead.mp4`，文件名禁止改动，时长必须小于等于 `15.0s`；
- 独立人脸检测、红网格绘制、视频逐帧处理、图片处理和 QA 函数；
- 独立隐私网格 manifest；
- 独立的新图和尾帧连续性处理；
- 独立合同测试。

`video_openrouter.py` 的请求方式和文件名保持不变；人物口播副本只将专用模板映射到 `Video_SDR2V_TalkingHead.md`，并读取人物口播参考字段。

## 5. 参考视频处理合同

用户上传参考视频且参考视频网格开关开启时：

1. 物化用户上传的参考视频；
2. 检测参考人物在视频中的人脸活动范围；同一帧内多个 Haar cascade 对同一张脸产生的重叠框必须先聚类，并选择置信度最高且接近簇中位面积的代表框，禁止再次用“面积最大框”放大误检；
3. 跨帧按空间重叠关系建立稳定人脸轨迹；同一人物的重复检测只能形成一个稳定区域，短暂出现且达不到最小采样支持的背景、衣服、手部和纹理误检必须丢弃；
4. 多张有效人脸以覆盖区域的空间并集进入固定网格；新的人脸只能增加尚未覆盖的面积，已经覆盖的像素不得再次累计；
5. 对视频逐帧执行一次全局对齐的透明底红色细线网格写入；无论一个像素被多少检测框命中，最大绘制次数必须为 1，禁止增加线宽、网格密度或重复边框；
6. 执行红线存在率 QA，并在 manifest 写入 `render_mode=unique_region_union_once`、人脸轨迹数、输入重叠深度和 `maximum_render_count_per_pixel=1`；
7. 输出独立 provider 视频，不覆盖原视频；
8. 将源文件、provider 文件、hash、唯一覆盖区域和 QA 写入 manifest；
9. 使用处理后的参考视频进入 OpenRouter `input_references`。

用户上传参考视频且网格开关关闭时，使用标准化的原始参考视频，不绘制网格。

使用系统默认参考视频时，必须固定读取 `Video_SDR2V_TalkingHead.mp4`，不得错误回退到 Analysis_V1 文件，也不得指向不存在的 `Video_SDR2V.mp4`。该系统资产直接进入后续参考链路，不调用视频人脸检测、逐帧网格绘制或网格视频编码逻辑。当前 `privacy_grid_preset` 仍作为任务配置保存，但只对已勾选的目标图和后续切换的用户上传参考视频生效。

### 5.1 尾帧连续性隐私合同

1. 上一段 `TailFrame` 原图必须保持干净并作为可追溯源文件，不允许原地覆盖。
2. 当尾帧物化为下一段 `Image_New` 时，必须先读取 StoryBoard 根部 `talking_head_config.max_sd_2_reference`；不能只依赖可能为空的逐段 `talking_head_reference`。
3. 前端“尾帧作为新图”接口与 Analysis_V1 `05_06` 的现有图、复制图、新生成图三条路径，都必须调用 TalkingHead 本地 `prepare_continuity_frame()`，再将红网格派生图发布为下一段 `Image_New`。
4. 连续帧必须对全部有效人脸绘制网格；检测框先按重叠关系聚类去重，小型背景、衣服、手部和纹理误检必须过滤，不得因为存在多张有效人脸而阻断。
5. 已通过红线存在率 QA 的连续帧再次进入该路径时必须识别为 `already_gridded`，禁止重复叠加网格。

### 5.2 Analysis_V1 05_02 / 05_06 严格门禁

新图和尾帧的连续帧隐私网格只允许在 Analysis_V1 `05_02_VideoPlanExecutor.py`、`05_06_VideoOnlyPlanExecutor.py` 的 Max SD 2 可见人物口播分支执行。以下条件必须同时满足：

1. `Variables.default_video_config.provider=openrouter`；
2. `Variables.default_video_config.model=bytedance/seedance-2.0`；
3. 当前 Segment 明确为口播：`tasks.need_lipsync=true` 且 `tasks.sync_mode=lipsync`；
4. 当前 Segment 不是空镜、产品特写、用户标记切片或 DanceMimic；
5. `Variables.talking_head.reference_privacy.enabled=true`，模式为 `red_grid_guide`；
6. `apply_privacy_grid_to_target_identity_image=true`；
7. StoryBoard 当前 `max_sd_2_reference` 已确认 `privacy_grid_mode=true` 且 `target_identity_grid_applied=true`。

任一条件不满足都必须 fail closed：不检测人脸、不生成网格派生图、不改变现有图片，也不得因为 StoryBoard 根部残留 `max_sd_2_reference` 就误处理其它模型或空镜。页面“尾帧作为新图”接口必须复用同一门禁，不能形成比 `05_02 / 05_06` 更宽的旁路。

该门禁只约束 Analysis_V1 执行期的新图/尾帧连续性处理，不改变 TalkingHead 配置阶段对上传人物图和参考视频生成独立 provider 隐私资产的职责。

### 5.3 TalkingHead_V1 05_02 同步门禁

TalkingHead_V1 自有 `05_02_VideoPlanExecutor.py` 必须执行与 Analysis_V1 相同的 Max SD 2 + 明确口播门禁，但门禁和网格函数保留在 `TalkingHead_V1` 本地，不得导入 Analysis_V1 的隐私实现。

1. `Variables.default_video_config` 当前不是 `openrouter / bytedance/seedance-2.0` 时，即使 Segment 仍残留旧 `talking_head_reference`，也不得切换到 Max SD 2 模板、参考视频或隐私函数；
2. Segment 缺少 `need_lipsync=true` 或 `sync_mode=lipsync` 时，不得执行连续帧网格；
3. cutaway、产品镜头、无可见人脸和 DanceMimic 必须原样通过；
4. 目标图隐私开关关闭或隐私资产状态不完整时，不得调用 `prepare_continuity_frame()`；
5. 新生成图、尾帧复制图、已有首帧和正式 provider 请求前的兜底检查必须使用同一门禁。

TalkingHead_V1 05_02 的模型路由也必须先验证 Variables 当前默认模型，禁止“历史 reference 配置覆盖当前模型选择”。

### 尾帧踩坑记录

- `video-only-plan/segments/{asset_key}/materialize-tail-frame` 旧实现直接复制 PNG，完全绕过隐私函数；只修复 05_02 不足以覆盖 StoryBoard 页面上的“尾帧作为新图”。
- VideoOnly Plan 的逐段 `talking_head_reference` 可能为空，真实 Max SD 2 隐私配置仍保存在 StoryBoard 根部；若不做根配置兜底，尾帧会以干净图片进入下一段。
- 只检查 StoryBoard 根部是否存在 `max_sd_2_reference` 会把历史隐私状态错误扩散到其它模型、空镜或产品镜头；恢复根配置之前必须先通过 Max SD 2 + 明确口播 + 目标图隐私开关的统一门禁。
- TalkingHead_V1 05_02 旧逻辑只检查 Segment 是否带有 `talking_head_reference`，会在用户已经切换其它默认视频模型后仍强制选择 Max SD 2 并执行网格；必须同时校验 Variables 当前模型和 canonical 口播标记。

## 6. 上传人物图片处理合同

目标人物图网格开关开启时：

1. 读取上传的人物形象照片；
2. 检测目标人物图中的全部人脸，并对回退 Haar 检测使用更严格的稳定阈值；
3. 对高度重叠的候选框聚类去重，过滤面积过小或位于明显非人物区域的背景、衣服、手部和纹理误检；
4. 扩展每一个有效人脸区域后先计算空间并集，再用全局对齐网格一次性写入；重叠人脸只能增加并集面积，不得重复绘制而增加线宽或网格密度；
5. 执行红线存在率 QA；
6. 生成独立 `PrivacyGrid` 图片；
7. 将该图片作为 `target_identity` 发送给模型。

检测到多张有效人脸时不得报错，必须全部绘制网格。只有过滤后没有任何有效人脸时才允许 fail closed，禁止发送未处理的目标人物图。

已踩坑：OpenCV Haar 在单人照片中把背景区域误报为比真实人脸略大的框，同时又由多个 cascade 重复检测真实人脸。旧逻辑先选择面积最大框，再把其它大小接近的框视为“竞争人脸”，因此错误报告“检测到 4 张人脸”。目标人物图处理不得再使用“只允许一个主要人物”的阻断逻辑。

### 6.1 可选网格密度与线宽

实验曾将参考格距设为约 `44px`，部分视频处理失败；后续以 `12` 作为稳定默认基准。本次在保留 `12` 默认值的同时，开放 `12/24/36/48` 四档 1080 短边基础间距，并与 `1/0.5` 两档线宽组合成第 3.1 节定义的 8 个预设。`44` 不是可选值，历史任务无有效预设时必须回退为 `12×1`。

任一预设的实际格距均按画面短边同比缩放：

```text
实际间距 = max(基础间距, round(基础间距 × 画面短边 / 1080))
```

例如选择 `密集 12×1（默认）` 时，短边不超过 `1080` 的实际间距为 `12`，短边 `1440` 为 `16`，短边 `2160` 为 `24`。选择 `较密 24×1` 时，上述三种短边的实际间距分别为 `24/32/48`。因此选项中的数字是 1080 基准，不是所有分辨率素材的固定输出像素。

`line_width_reference` 只能为 `1` 或 `0.5`，不随画面分辨率放大。`0.5` 必须以细线视觉效果输出，不得在参数解析、绘制或编码阶段被静默取整为 `1`。格距和线宽变更不得改变人脸覆盖区域、多人区域并集或“每个像素最多绘制一次”的合同。单人图中，面积低于主要人脸五分之一的孤立 Haar 弱候选应作为背景误检过滤；接近尺寸的多张有效人脸仍必须全部保留。

不得覆盖用户上传的原图。开关关闭时直接使用原图。

## 7. 新图与尾帧连续性

目标人物图网格开关开启时：

- 人物照片重置产生的 `Image_New` 必须使用网格派生图；
- 新目标图在作为 provider 首帧前必须重新检测并生成网格派生图；
- 使用上一段或上一场尾帧时，必须保留干净尾帧原文件，重新检测尾帧中的全部人脸并生成独立网格图；
- 下一段 `Image_New`、`continuity_first_frame` 和 provider 请求必须使用同一张尾帧网格派生图；
- 尾帧无人脸或红线 QA 不通过时必须阻断，不得发送干净尾帧。

尾帧派生文件命名：

```text
{asset_key}_ContinuityFirstFrame_PrivacyGrid.png
```

目标人物图网格开关关闭时，新图和尾帧不添加红网格。

## 8. Provider 输入角色

Max SD 2 请求至少包含：

- `continuity_first_frame`：当前段首帧或上一段尾帧派生图；
- `target_identity`：上传人物照片或其网格派生图；
- `talking_head_motion_expression_reference`：参考视频或其网格派生视频。

参考视频只用于参考人物的表情及表情变化、动作、姿态、节奏、手势和镜头运动。不得复制参考人物身份、脸型、服装、身体、背景、水印或网格痕迹。

## 9. 提示词合同

`Video_SDR2V_TalkingHead.md` 必须明确：

- 参考人物的表情和表情变化需要被参考；
- 表情参考只迁移情绪、强弱、时序和面部动作规律，不迁移参考人物身份；
- 目标人物身份及外观以 `target_identity` 为准；
- 参考视频同时提供动作、姿态、节奏、手势和镜头运动；
- 红网格只是输入标记，最终视频不得出现红线、边框、网格、扫描线或跟踪标记；
- 不生成字幕、水印或文字叠加；
- 人物口播声音继续使用现有 TTS、克隆声音、语速及后续唇形/音视频合成链路。

### 9.1 首次生成与重新加载的路由边界

两个入口必须明确区分：

1. Video Only Plan 顶部首次执行“提示词”保持现有 Analysis_V1 `05_06_VideoOnlyPlanExecutor.py` 路线，根据 `Variables.default_video_config` 选择 Analysis_V1 provider 模块；Max SD 2 使用 Analysis_V1 `video_openrouter.py + Video_OpenRouter.md`。
2. Video Prompt 编辑弹窗中的“重新加载”必须先识别 workflow。普通 Analysis 工作流仍走 Analysis_V1 `05_06`；`person_talking_head_v1` 必须走 TalkingHead_V1 `05_02_VideoPlanExecutor.py` 和人物口播本地 `video_openrouter.py`，强制复制并渲染 `Video_SDR2V_TalkingHead.md`。
3. 两个入口的 provider/model 都只读取 `Variables.default_video_config`，不得读取数据库全局 active 模型，也不得读取 `talking_head.video_model`。
4. TalkingHead 重新加载不能依赖旧 plan 是否已写入 `prompt_template`；后端必须显式传入 `Video_SDR2V_TalkingHead.md`，防止回退到通用 `Video_OpenRouter.md`。
5. 重新加载后的业务 Prompt 必须记录 `provider_profile=video_openrouter`、`template_source=Ref_05_02_Video_SDR2V_TalkingHead.md`。

### 9.2 StoryBoard 与人物口播一键成片的执行器边界

1. StoryBoard 编辑页面只有“刷新 Session Variables”和“重新加载提示词”是 workflow-aware 特例；其余按钮和工具全部保持 `Analysis_V1`。
2. StoryBoard 的 Video Plan 执行固定调用 `Analysis_V1/05_02_VideoPlanExecutor.py`，即使 `Variables.workflow_id=person_talking_head_v1` 也不得切换。
3. 人物口播“一键成片”中的 05_02 才调用 `TalkingHead_V1/05_02_VideoPlanExecutor.py`。
4. StoryBoard 的“重新加载提示词”可以局部加载 TalkingHead 05_02 的 `video_openrouter.py` 和 `Video_SDR2V_TalkingHead.md`，但只生成 Prompt，不得借此替换整个 StoryBoard 执行器。
5. 禁止根据 `workflow_id` 将 Analysis 05_01 的计划交给 TalkingHead 05_02。两套执行器的 lipsync 默认值、计划字段和 Prompt fallback 不同，混用会产生 `Lip-sync is required but disabled` 或模板 block marker 缺失。

## 10. 严格校验与传输

- manifest 中的开关、有效范围、provider 文件和 hash 必须与计划和实际请求一致；
- manifest 声明的引用缺失时必须阻断，不得静默过滤；
- TalkingHead Max SD 2 的所有参考视频必须使用已配置的 R2 公网素材通道，禁止 `tmpfiles` fallback；这包括已预处理的系统默认视频、开启隐私网格的上传视频，以及人工取消“应用视频”的上传视频；
- `apply_privacy_grid_to_reference_video` 只决定是否绘制隐私网格，不得用来决定是否使用 R2；`require_r2_public_assets` 在 TalkingHead Max SD 2 参考模式下必须固定为 `true`；
- TalkingHead 本地 `video_openrouter.py` 必须在构造请求前合并 `OPENCREW_PUBLIC_ASSET_R2_*` 运行时配置，不得因为 Session provider config 中 `public_asset_provider` 为空而退回临时上传地址；
- R2 配置的权威来源为 `OPENCREW_DATA_DIR/public_assets_r2.env`（默认 `~/.opencrew/public_assets_r2.env`）；Analysis_V1 05_02、05_06 与 TalkingHead_V1 05_02 必须在工具运行时通过共用 `opencrew_runtime_secrets.py` 解析该文件，不得要求用户将同一 Access Key/Secret 重复保存到 LocalSecretStore；
- 一键成片子进程会按安全策略剥离名称含 `KEY/SECRET` 的父进程环境变量，这是预期行为；运行时解析器必须从已配置的 env 文件重新取得凭据，不能因此报 `R2 access key and secret are required`；
- 踩坑记录：TalkingHead 的 OpenRouter adapter 是独立副本，从 Analysis_V1 同步时若遗漏 R2 运行时配置合并，OpenRouter 可成功接收生成任务却在轮询后返回 `Invalid video_url`；此时后续 Segment 的“尾帧文件不存在”是上游视频失败的连锁现象，不是第二个根因；
- ProviderTask 状态已是 `failed/error/blocked/cancelled` 时禁止继续轮询旧 task id；重试必须使用当前 R2 URL 重新提交 OpenRouter 任务。
- 原图、原视频和干净尾帧必须保留；
- 最终视频不得残留红网格。

## 11. 持久化与恢复

以下字段必须随任务保存、更新并恢复：

- `talking_head_video_model_key=max_sd_2`；
- `reference_privacy_mode=red_grid_guide`；
- `apply_privacy_grid_to_reference_video`；
- `apply_privacy_grid_to_target_identity_image`；
- `privacy_grid_preset`；
- `cell_size_reference`；
- `line_width_reference`；
- `use_default_reference_video`；
- 用户参考视频路径；
- manifest 路径及实际 provider 输入路径。

字段需要进入任务配置、`task_meta.json`、`SessionContext/Variables.json`、StoryBoard 和视频生成计划。

## 12. 验收标准

- 人物口播界面可选择 `Max SD 2`；
- Max SD 2 参数与 Max 2.7 W 一致；
- 隐私模式内部固定为“隐私网格（身份可见）”，页面不显示模式下拉框；
- 时长、容忍度、首帧覆盖数、网格预设和两个应用开关保持一行布局；
- 隐私网格预设下拉框按第 3.1 节的顺序和名称显示 8 个选项，默认为“密集 12×1（默认）”；
- 8 个预设均能正确保存、重新加载和传入隐私网格工具，实际间距使用选中的 1080 基础间距按短边缩放；
- `0.5` 线宽能输出可辨别的细线效果，不得被取整或回退为 `1`；
- 使用系统默认视频时“应用视频”初始为未勾选，且不运行参考视频隐私覆盖逻辑；下拉预设仍可选并对已勾选的目标图生效；
- 切换为用户上传视频时“应用视频”初始为勾选，只有此时才可对参考视频执行选中的隐私网格预设；
- 用户上传的参考视频和人物图均能按开关生成独立网格派生资产；
- 新图和尾帧按目标人物图开关正确处理；
- OpenRouter 请求携带目标身份图、连续首帧和参考视频；
- 提示词明确参考人物表情，但不复制参考人物身份；
- 现有 Flush X、Max 1.5 X、Max 2.7 W 行为不回归；
- 合同测试通过，前端生产构建通过。
