# DanceMimic_V1 设计评审结果

版本：v1

状态：评审结论。本文汇总对 `docs/DanceMimic_V1/` 下全部 6 篇设计文档的评审发现，已逐条对照仓库现状代码核验。结论分为：阻塞性（动工前必须解决）、High、Medium、次要。每条给出证据定位与修订建议。

评审范围文档：

```text
DanceMimic_V1_工具目标与实现需求.md
DanceMimic_V1_01_ReferenceMediaDemux_工具实现需求.md
DanceMimic_V1_02_ReferenceFaceMaskedVideoBuild_工具研究与设计.md
DanceMimic_V1_标准Session管理与工具实现规范.md
DanceMimic_V1_StoryBoard标准Task适配问题集.md
DanceMimic_V1_后续执行_Seedance_SDR2V_DanceMimic_适配需求.md
```

核验基线代码：

```text
ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py
ToolLibrary/Analysis_V1/video_plan_executor_modules/
ToolLibrary/Analysis_V1/Reference/05_02/
ToolLibrary/Analysis_V1/04_02_StoryBoard.py
ToolLibrary/Analysis/media_binaries.py
ToolLibrary/Analysis/02_0_source_separation.py
ToolLibrary/PromptKnowledge/registry/models.json
backend/opcrew_backend/koubo/koubo_storyboard/storyboard_plan_services.py
```

## 0. 总体评价

设计整体扎实，Session 规范、安全姿态（key 不落盘）、Resume/Force 语义、blocked/warning/failed 纪律都做得到位，绝大多数集成点（05_02 模块分发、Reference 模板、media_binaries、demucs、MaxSR2 别名、tool_registry）确认存在。

核心风险集中在一句被低估的假设：**“最高复用、最小开发量地复用现有 StoryBoard / 05_02 链路”**。代码核验显示这条链路的三处关键契约与设计假设不符，且 03（标准 StoryBoard 构建）缺独立实现需求文档。这些必须在动工前收敛。

下表为快速索引：

| 编号 | 严重度 | 标题 | 来源 |
| --- | --- | --- | --- |
| B1 | 阻塞 | Provider 路由靠 provider/model，不读 `provider_module` 字段 | 代码核验 |
| B2 | 阻塞 | MaxSR2/OpenRouter 已有参考视频能力，但 05_xx 执行链尚未接入 DanceMimic 每段参考视频 | 代码核验 + 最新文档复核 |
| B3 | 阻塞/High | `dialogue_asset_key` 源 schema 不写、由后端自动 UUID 化，03 需保证稳定 key | 代码核验 + 同事 H3 |
| B4 | 阻塞/High | Task 注册（openclip_tasks）owner 未指定 | 代码核验 + 同事 H3 |
| B5 | 阻塞/High | 产品入口/Runner 独立，ToolLibrary/DanceMimic_V1 采用薄封装 | 用户澄清 + 代码核验 |
| H1 | High | Variables.json 输入路径字段三套入口不统一 | 同事 |
| H2 | High | 01 manifest 命名与必需性矛盾 | 同事 |
| H3 | High | 缺独立的 03 实现需求文档 | 同事（并入 B3/B4） |
| H4 | High | 分段算法缺可行性校验 | 同事 |
| M1 | Medium | `--force` 清理规则与规范冲突，且缺级联失效策略 | 同事 |
| M2 | Medium | 状态枚举未统一 | 同事 |
| M3 | Medium/High | Plan 生成职责未落地：05_01/05_05 既不读 seed 也不产 DanceMimic 字段 | 同事 + 代码核验 |
| M4 | Medium | 02 默认检测引擎前后不一致 | 同事 + 代码核验 |
| S1 | 次要 | 抽取音频的下游用途未定义 | 代码核验 |
| S2 | 次要 | demucs 作为硬依赖、缺失即 blocked 过严 | 代码核验 |
| S3 | 次要（降级） | `koubo_storyboard_edit.json` 可懒生成，是否预生成按打开路径/性能定，非必须 | 代码核验 |

---

## 1. 阻塞性问题（动工前必须解决）

### B1. Provider 路由机制与设计假设不符

**现状证据**：`05_02_VideoPlanExecutor.py:636-657` 的 `video_module_for(provider, model)` 纯粹按 **provider/model 字符串** 分发：

```python
if is_wan_rtv_model(provider_value, model_value):
    return import_executor_module("video_wan_rtv")
...
if provider_value in {"bytedance","seedance",...} or "seedance" in model_value:
    return import_executor_module("video_seedance")
```

`is_wan_rtv_model`（`:660-663`，`WAN_RTV_MODEL_IDS = {"wan2.7-r2v"}`）是“按 model id 路由到专用模块”的已有先例。

**问题**：设计在 4 篇文档反复要求执行器读取 plan 里的 `provider_module = video_sdr2v_dancemimic` / `video_generation_mode = seedance_sdr2v_dancemimic`（见 工具目标 `:507/:521`、问题集 `:300-307`、后续执行 `:98-110`）。**但 05_02 不读这两个字段**，因此 plan 里写了也不会生效。这与问题集 Q33–Q36=A（“直接复用现有 05_02、不改执行器”）自相矛盾。

**更正与补强（同事意见，已核验）**：仅“加 model-id dispatch”不足以让 DanceMimic 跑通，因为 05_02 当前根本没有“每段不同参考视频”的传参通路：

1. `generate_video_with_provider(config, prompt_path, output_path, reference_images, duration, ...)`（`:1114`）只接 `reference_images`（即首帧图），**没有 per-segment reference video 形参**。
2. WAN RTV / Kling Omni 的参考视频是**全任务固定的打包文件**（`Video_Wan_R2V.mp4` / `Video_Kling_Omni.mp4`，`:85/:103`），由 `copy_wan_rtv_reference_video_to_working()`（`:1475-1490`）复制到 Working，再经 `context["reference_videos"] = [固定文件]`（`:1125-1127`）注入。**所有 segment 共用同一段参考视频**，与 DanceMimic“每段绑定自己的 `Segment_NNNN_Reference_FaceMasked.mp4`”的模型根本不同。

**建议（修订）**：B1 的完整改造范围是四件事，缺一不可：

1. **dispatch**：照 `is_wan_rtv_model` 先例加 `is_seedance_sdr2v_model()` 分支，路由到 `video_sdr2v_dancemimic`。
2. **plan 字段生成**：让 plan 在 segment 级携带 `reference_video_path`（见 M3，05_01/05_05 需新增）。
3. **per-segment 参考视频复制 + 传参**：05_02 需新增“按当前 segment 的 `dialogue_asset_key` 复制对应遮脸视频到 Working、并把该路径注入 `context["reference_videos"]`”的逻辑；不能复用 WAN/Kling 的“固定单一参考视频”路径。
4. **05_06 同步改造**：VideoOnly 链路同样要走 per-segment 参考视频传参。

文档应把“靠 `provider_module` 字段、直接复用 05_02 不改执行器”改写为“靠 model id 路由 + 05_02/05_06 per-segment 参考视频传参改造”（Q33–Q36 实际取 B）。

### B2. MaxSR2/OpenRouter 已有参考视频能力，但 05_xx 执行链尚未接入 DanceMimic 每段参考视频

**现状证据**：

1. `video_seedance.py:346-349` 的火山 / Ark payload 仍只支持 **first_frame 参考图片**（`image_url` + `role:"first_frame"`），这一路不是 DanceMimic 要依赖的 MaxSR2 多模态参考视频落点。
2. `video_openrouter.py:618-653` 已支持 OpenRouter 视频 `input_references`，可发送 `image_url`、`audio_url`、`video_url`。
3. `koubo_model_aware_video_settings_plan.md:150/:155/:558` 明确 `Max SR2 = OpenRouter / bytedance/seedance-2.0`，支持 image/audio/video references，并使用 `input_references`。
4. `backend/tests/contracts/test_analysis_v1_openrouter_video_contract.py:233-260` 已有合同测试覆盖 SR2 multimodal `input_references`，断言 payload 中包含 image/audio/video 三类引用。

**更正结论**：初版“Seedance reference-video 能力当前不存在”的判断不准确。仓库里已经存在 MaxSR2 / OpenRouter Seedance SR2 的多模态 image/audio/video reference 能力；`DanceMimic_V1_工具目标与实现需求.md:146` 对 `MaxSR2` 的描述基本成立。

**真正的阻塞点**：DanceMimic 的后续执行不是走 Asset Library 视频生成服务，而是计划复用 `05_02 / 05_06`。当前 `05_02.generate_video_with_provider()` 只接 `reference_images`，只在 WAN / Kling 分支注入固定打包参考视频，不会从当前 segment 读取 `reference_video_path` 并传给 `video_openrouter.py` 的 `context["reference_videos"]`。因此能力存在，但 DanceMimic 执行链还没有把“每个 Dialogue 对应的遮脸参考视频”送进 MaxSR2/OpenRouter。

**建议（修订）**：

1. B2 不再要求先验证“provider 是否存在参考视频能力”，而是要求确认 DanceMimic 走 **OpenRouter MaxSR2 / `input_references`** 这一路，而不是火山 `video_seedance.py` 的 first-frame-only 路径。
2. 将原本的 `video_sdr2v_dancemimic.py` 设计定位为 `video_openrouter.py` 风格的专用封装或 adapter：核心是渲染 DanceMimic 专用 prompt，并把首帧图 + 当前分段遮脸参考视频作为 `input_references` 传入。
3. 结合 B1/M3 补 05_01/05_05 plan 字段与 05_02/05_06 per-segment `reference_video_path` 传参，确保 `context["reference_videos"]` 能到达 OpenRouter SR2 模块。
4. 付费 smoke 可作为验收项保留，但不再作为“是否能动工”的前置阻塞；代码合同与文档已能证明集成路径存在。

### B3. 源 schema 不写 `dialogue_asset_key`，由后端自动 UUID 化；03 必须保证稳定 key 不被改写

**更正说明（同事意见，已核验）**：初版把本条写成“现有 schema 不支持 `dialogue_asset_key`”，证据不准确，结论过强。实际情况是：

1. `04_02_StoryBoard.py` 产出的源 `srt_storyboard.json`（schema `analysis_v1_srt_storyboard_0.2`）确实不在 `dialogue_items[]` 上写 `dialogue_asset_key`；该字段是 **edit plan（`koubo_storyboard_edit_0.1`）层** 的概念。
2. 后端 `storyboard_plan_services.py` 会**自动补齐**该字段：`normalize_source_plan()`（`:187`）把源 plan 规范化为 edit plan 时为每个 dialogue 生成 key；`recalculate()`（`:340-387`）补齐/去重；05_02 `flatten_dialogues()`（`:1521-1538`）还会用 srt_id/dialogue_id 回填缺失 key。
3. **但生成器是随机 UUID**：`asset_core_services.py:129-133` 的 `new_dialogue_asset_key()` 返回 `f"dak_{uuid.uuid4().hex[:12]}"`；`derive_dialogue_asset_key()`（`:136/:161`）**优先采用 dialogue 上已写明的显式 `dialogue_asset_key`**，没有才回退到 UUID。

**真正的风险**：DanceMimic 的 `storyboard_seed.json`、`assets/videos/{dak}_Reference_FaceMasked.mp4` 以及后续 plan 都以 `dak_0001…` 这种**稳定 key** 为锚点。如果 03 产出源 schema 且不写显式 key，后端会把它们重排成随机 UUID，导致 seed/参考视频/plan 的绑定全部对不上。

**建议（修订）**：03 实现文档必须明确两点 ——

1. **选定输出 schema**：03 是直接产出 edit schema（`koubo_storyboard_edit_0.1`），还是产出源 schema 由后端 normalize；二选一并固定。
2. **写显式稳定 key**：无论哪种 schema，03 都要在每个 dialogue 上写明 `dialogue_asset_key = dak_NNNN`，使其命中 `derive_dialogue_asset_key()` 的“显式优先”分支、不被 UUID 化；并对照 `storyboard_plan_services.py` 验证 normalize/recalculate 不会重排这些 key。该项纳入 B4 的 03 独立实现文档。

### B4. Task 注册 owner 未指定

**现状证据**：task_list_router 以 `openclip_tasks` DB 记录 + `SessionOutput/storyboard/srt_storyboard.json` 文件存在判定一个 workspace 是否可作为 StoryBoard Task 打开；不存在“DanceMimic task 类型”。

**问题**：问题集 Q1–Q4=A 假设结果自动进 Task List 并能打开 StoryBoard，但**没有任何一步写明谁 insert/update `openclip_tasks` 记录**。00 号称 DB-aware 却被描述为只读；03 只写文件。注册 owner 缺失。

**建议**：在 03 独立实现文档中指定 Task 注册 owner（00 或新增 finalize 步骤），写明 DB 写入字段、与文件落盘的先后、失败回滚。

### B5. 产品入口与 Runner 目标独立，但 ToolLibrary/DanceMimic_V1 应是薄封装

**用户目标澄清**：DanceMimic 应在任务创建入口中与“视频分析”并列，例如新增 “DanceMimic” 按钮；它不是现有 “视频分析 → Prompt Builder → 运行设置 → 进入任务” 的 7 步任务。同时，`ToolLibrary/DanceMimic_V1` 目录不应复制一整套 Analysis_V1 工具脚本，而应只放很少几个 DanceMimic 专属文件；大部分脚本能力应复用 `ToolLibrary/Analysis_V1`，或把 Analysis_V1 中的通用脚本扩充为支持 DanceMimic workflow。

**现状证据**：

1. 设计目标文档已推荐独立目录 `OpenCrew/ToolLibrary/DanceMimic_V1/`，且定义的逻辑链是 `00_PrepareSessionVariables`、`01_ReferenceMediaDemux`、`02_ReferenceFaceMaskedVideoBuild`、`03_StoryBoardStandardTaskBuild` 四步。
2. 当前任务创建菜单只有 “视频分析” 和 “脚本生成”：`frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateMenu.jsx:4-5`。
3. 当前 “视频分析 / Prompt Builder / 运行设置 / 进入任务” 绑定的是 Analysis_V1：前端调用 `/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/plan`，后端 `analysis_v1_run_step_specs()` 默认产出 7 步 `00, 01, 02_01, 02_02, 03_02, 04_01, 04_03`。

**问题**：如果 DanceMimic 接入时复用这个 Analysis_V1 弹窗和 plan endpoint，用户看到的任务会错误地变成口播分析 7 步（ASR、字幕帧对齐、TTS、SRT 改写、StoryBoard 分组），而不是 DanceMimic 的参考视频预处理与标准 StoryBoard 构建链。即使前两步概念相似，Analysis_V1 的 `01_VideoProbeMetadata` 也不是 DanceMimic 的 `01_ReferenceMediaDemux`。

**建议（新增集成边界）**：

1. 建立独立后端 surface / runner target，例如 `dance_mimic_v1`，返回 DanceMimic 自己的 run plan，不复用 `analysis-v1/run-to-storyboard/plan`。
2. `ToolLibrary/DanceMimic_V1/` 采用薄目录：只放 `tool_registry.json`、DanceMimic 专属配置/Reference 文档、必要 wrapper/adapters，以及无法自然泛化进 Analysis_V1 的少量专属脚本。
3. 可复用能力优先落在 `ToolLibrary/Analysis_V1`：例如 Session 变量准备、视频元数据探测、StoryBoard 标准化/注册、05_01/05_05 plan 生成、05_02/05_06 执行链改造。若要支持 DanceMimic，优先把这些脚本扩展为按 `workflow_id=dance_mimic_v1` 或显式参数分支，而不是在 DanceMimic_V1 下复制一份。
4. 在 Task List 创建入口新增 “DanceMimic” 按钮与创建弹窗，创建/打开 DanceMimic task 后进入 DanceMimic 自己的运行设置与进度弹窗。
5. DanceMimic 运行完成后再注册或关联标准 StoryBoard Task，并进入现有 StoryBoard 结果页；StoryBoard 后续 05_xx 执行链的改造仍按 B1/B2/M3 处理。
6. 03 独立实现文档应新增“复用矩阵”：逐步列明每个逻辑步骤由 `Analysis_V1` 哪个脚本承担、需要新增哪些参数/字段、DanceMimic_V1 下仅保留哪些薄 wrapper，避免形成第二套重复工具链。

---

## 2. High

### H1. Variables.json 输入路径字段三套入口不统一

**证据**：

- 工具目标 `:172`：00 写入 `source_reference_video_path`；
- 01 细文档 `:132` + `:200`：01 读 `reference_video_path`；
- 标准规范 `:182`：另有 `input_files[]` 入口。

00 写的键与 01 读的键字面不同，01 会读到空，直接导致 01 拿不到输入视频路径。

**建议（修订，采纳同事意见）**：优先对齐 Analysis_V1 基线键名 **`source_video_path`** —— `Analysis_V1/00_PrepareSessionVariables.py:748` 正是把源视频相对路径写为 `source_video_path`（输入取自 DB `task.reference_video_path`，见 `:842/:852`）。DanceMimic 既然以对齐 Analysis_V1 为目标，就不该新造 `source_reference_video_path`/`reference_video_path` 两个变体；除非有明确理由要 DanceMimic 专用字段，否则统一为 `source_video_path`，00 写它、01 读它，并明确 `input_files[]` 与该键的关系（冗余登记还是唯一真源）。三处一并对齐。

### H2. 01 manifest 命名与必需性矛盾

**证据**：

- 工具目录 manifest：总文档 `:234` 叫 `media_demux_manifest.json`，01 细文档 `:178/:299` 叫 `reference_media_demux_manifest.json` —— 文件名对不上（硬错）。
- SessionOutput 入口 `reference_media_manifest.json`：01 `:167` 标“可选”、`:349`“建议复制”，但 02 `:150` 与 01 `:514` 又当跨工具读取入口。

**判断**：工具目录 manifest 文件名不一致是硬错，必须统一。“上游成功、下游 blocked”目前是潜在陷阱（02 把该 manifest 标为可选、硬 preflight 是无声视频），但需明确这份 SessionOutput manifest 到底是不是 02/03 的必需入口，避免实现时一边写可选一边按必需读。

### H3. 缺独立的 03_StoryBoardStandardTaskBuild 实现需求文档

**证据**：工具目标 `:383` 的 §7 只给定位/结构/字段原则；问题集 `:438` §7 自列 11 项“正式需求待补齐”。仓库中 01、02 均有专门实现需求文档，唯独 03 没有。

**判断**：缺的正是最难部分 —— `srt_storyboard.json` / `koubo_storyboard_edit.json` 精确 schema、DB 更新方式、失败回滚、plan 生成/同步合同。本条与 B3、B4 同源，应合并为一份 03 独立实现文档统一交付，**列为下一步第一优先**。

### H4. 分段算法缺可行性校验

**证据**：02 `:232-249` 仅校验 `target<minimum`、`D<minimum`；问题集 `:168` 同。

**问题**：缺存在性校验。反例 `D=11, target=8, minimum=6`：`ceil(11/8)=2` 段、均分 5.5/5.5 均 <minimum，无解，但现算法会落到“近似均分”产出违规分段，无人拦截。

**数学结论**：存在满足 `minimum ≤ len ≤ target` 的 k 段划分，当且仅当存在整数 k 使 `k·minimum ≤ D ≤ k·target`，即：

```text
ceil(D / target) <= floor(D / minimum)
```

**建议**：在 blocked 前显式做此校验，不满足时返回 blocked + 新错误码（如 `segment_constraints_infeasible`），并在示例与验收中覆盖。

---

## 3. Medium

### M1. `--force` 清理规则与标准规范冲突，且缺级联失效策略

**证据**：

- 规范 `:441-442`：强制 Rerun 只清本工具目录，不得删上游 Output / 其它工具目录；
- 01 `:429-435`：force 删 `SessionOutput/reference/*`；
- 02 `:753-755`：force 删 `SessionOutput/reference/segments/`，但不删 `SessionOutput/storyboard/`。

**根因与建议**：规范“只清自己目录”的前提是产物落在工具自己的 `Output/`，但 DanceMimic 的 01/02 把产物写到**共享的 `SessionOutput/reference/`**，规则从根上不适配。需：(1) 在规范开明确例外——产物在 SessionOutput 的工具，force 可清自己声明写入的 SessionOutput 路径；(2) 补级联失效策略——02 force 删 segments 后，03 已生成的 `storyboard/assets/videos/*` 与 `storyboard_seed.json` 会指向不存在的 segment，必须标记 stale 或强制 03 重跑。

### M2. 状态枚举未统一

**证据**：规范 `:269-272` 仅 completed/blocked/failed；02 `:653-658` 增 completed_with_warnings；问题集 `:334`(Q8) 引入 partial、`:406`(Q45) 引入 completed_with_sync_error。

**建议**：要么把扩展态收敛进三态 + `warnings` 字段，要么在规范里一次枚举全集并确认 runner/UI 都识别，避免状态丢失或误判。

### M3. Plan 生成职责未落地（升级 Medium→High，采纳同事意见）

**证据**：工具目标 `:488/:519`（03 只生成 seed、plan 由现有工具生成）；问题集 `:373-375`（三类 plan“需要”生成，未点名 owner）；后续执行 `:95`（05_01/05_05 生成 plan 时带 DanceMimic 字段）。

**更正判断**：初版写成“三句可自洽、属表述歧义”过于乐观。代码核验：`ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py` 中**没有任何** `storyboard_seed` / `provider_module` / `video_generation_mode` / `reference_video_path` 字样 —— 即 05_01 既不读 `storyboard_seed.json`，生成 segment 时也不产出这些 DanceMimic 必需字段。所以“03 出 seed → 05_01 读 seed 出 plan”这条链**当前根本不存在**，是真实现缺口而非措辞问题。

**建议（修订）**：明确二选一并落到 03 实现文档与 05_01/05_05 改造范围：

1. **新增 05_01/05_05 的 seed 消费逻辑**：让它们读 `storyboard_seed.json`，为 DanceMimic segment 写入 `reference_video_path` 等字段；或
2. **由 03 直接生成 / patch plan**：03 产出携带 DanceMimic 字段的 plan，后续执行器直接消费。

无论哪条，都与 B1 第 2 项（plan 携带 per-segment `reference_video_path`）绑定，需一并设计。

### M4. 02 默认检测引擎前后不一致

**证据**：推荐默认配置 `:90` 为 `deface`；FaceTrack 示例 `:456` 与 manifest 示例 `:547` 为 `insightface_scrfd`；依赖/落地 `:791/:800/:967/:975` 把 deface 当 MVP、insightface 当正式版。

**叠加风险**：02 自承（`:299`）deface CLI 不一定能产出强制要求的逐帧 `FaceTrack.json`，可能要 fork。若 V1 默认 deface，很可能交不出合同强制产物。

**建议**：V1 默认直接定 `insightface_scrfd`（能直接拿 bbox/confidence），把配置、依赖、schema 示例、验收样例一次统一。

---

## 4. 次要

### S1. 抽取音频的下游用途未定义

`storyboard_seed.json` 带 `mixed_audio_path` / `vocal_audio_path`，但 SDR2V 执行（无声参考视频 + 首帧）并不消费音频。最终 Final 视频音轨来源（mixed / vocal / 后续 TTS / lipsync）未定死。建议在 03 / 后续执行文档明确音频回到 Final 的路径。

### S2. demucs 硬依赖、缺失即 blocked 过严

01 把 demucs 列为硬依赖、缺失即 blocked（`:291`、`:537`），但下游 vocal 用途不明（见 S1）。若 vocal 实际暂未消费，建议降级为 warning + 跳过 vocal，避免阻断主流程。

### S3. `koubo_storyboard_edit.json` 可懒生成（降级，原判断为误报）

**更正（采纳同事意见）**：初版“必须生成、否则打开即缺数据”证据不足，已降级。代码核验：后端 `load_plan()` 在 edit 文件缺失（schema 不为 `koubo_storyboard_edit_0.1`，`storyboard_plan_services.py:126`）时，会从源 `srt_storyboard.json` 调 `normalize_source_plan()`（`:187`）**懒生成** edit plan；`task_list_router.py:203-216` 也把“只有源 storyboard”的状态记为 `storyboard_status:"generated"`、`status:"editable"`。即只有源文件也能打开并编辑。

**建议（修订）**：把表述从“必须预生成”改为“**03 是否预生成 `koubo_storyboard_edit.json` 由打开路径与性能体验决定，非硬性必须**”。若选择不预生成，仍需确保 03 写出的源 schema 带稳定 `dialogue_asset_key`（见 B3），否则懒生成时会被 UUID 化。

---

## 5. 建议的修订顺序

1. **先钉死 B5 产品/架构边界**：DanceMimic 使用独立入口、独立 run plan/runner target，不复用 Analysis_V1 7 步弹窗；但 `ToolLibrary/DanceMimic_V1` 是薄封装目录，大部分实现复用或扩充 `ToolLibrary/Analysis_V1`。
2. **修正 B2 执行路线**：明确 DanceMimic 默认走 OpenRouter MaxSR2 / `input_references`，不是火山 `video_seedance.py` 的 first-frame-only 路径；付费 smoke 作为验收，不作为动工前置。
3. **补齐 03 独立实现需求文档**（合并 H3 + B3 + B4）：钉死 dak 显式稳定 key 写法、源/edit schema 选型、Task 注册 owner、失败回滚、plan 责任边界（与 M3 绑定）。
4. **完整规划 B1 + M3 + B2 的 05_02/05_06/05_01/05_05 改造**：model-id dispatch 或 OpenRouter SR2 adapter + plan 携带 per-segment `reference_video_path` + 05_02/05_06 per-segment 参考视频复制传参（不能复用 WAN/Kling 固定单一参考视频路径）+ 05_01/05_05 seed 消费或 03 直接 patch plan。
5. **统一文档级契约**：H1（输入键统一为 `source_video_path`）、H2（manifest 命名/必需性）、M2（状态枚举）、M4（检测引擎默认值）。
6. **补 H4 可行性校验** 与 **M1 force 例外 + 级联失效**。
7. **澄清次要项** S1–S2；S3 改为“按需懒生成”。

## 6. 评审结论

- 同事第三轮关于 Seedance/MaxSR2 参考视频能力的复核成立：B2 已由“能力不存在”更正为“OpenRouter MaxSR2 / `input_references` 能力存在，但 05_xx 执行链尚未接入 DanceMimic 每段参考视频”。
- 同事第二轮提出的 5 条复核意见（针对 B1、B3、M3、S3、H1）经代码核验**全部成立**，本文已据此更正：B1 补强为四件套改造、B3 由“schema 不支持”更正为“后端 UUID 化、需写显式稳定 key”、M3 升级为真实现缺口、S3 降级为可懒生成、H1 推荐键改为 `source_video_path`。
- 同事第一轮 8 条（H1–H4、M1–M4）仍全部成立。
- 代码核验另发现 5 条链路契约问题（B1–B5），其中 B3/B4 与同事 H3 同源，B5 来自最新产品意图澄清。
- 设计可继续推进，但 00–03 与 05_xx 改造应在 B5 独立入口/Runner 边界和薄 ToolLibrary 复用矩阵、B1+B2+M3 的执行链改造方案、以及 03 独立实现文档收敛后再开工，否则存在“工具做完却挂到错误的 Analysis_V1 7 步任务 / 复制出第二套重复工具链 / 无法注册成 Task / 无法执行出视频 / 素材槽位绑定全错”的返工风险。
