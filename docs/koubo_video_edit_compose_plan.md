# Koubo 视频剪辑与合成能力方案（Agent 驱动 · 待审核）

> 状态：草案 **v0.2**（已纳入多轮评审反馈，见 §0；正文已重写对齐，不再保留冲突旧口径）。
> 范围：在 Asset Library 既有"Audio / Video 工作区 + 媒体 Agent"基础上，引入**剪辑（编辑已有素材）**与**合成（生成/拼装成片）**能力，统一由 Agent 驱动、本地执行、版本化交付。
> 关联文档：`docs/koubo_asset_library_agent_chat_implementation_plan.md`、`docs/koubo_storyboard_agents_implementation_plan.md`。
> 👉 想快速了解"用户能看到什么、怎么用、解决什么问题"，直接看 **§6 用户视角**（非技术读者友好）。

---

## 0. 评审修订（v0.2）—— 集成成本与真相源

> 本节为决策记录。团队对 v0.1 的 5 项质疑已**逐条对照代码核实，全部成立**；**正文（§2、§4.2、§5、§6、§7、§9）已按以下决策重写对齐，不再保留冲突旧口径**。结论：方向成立，但 v0.1 低估了与 VideoPlan/Composer、HyperFrame 默认行为、Agent 执行闭环、Asset Library 版本语义的集成成本，已按本节细化。第 2 轮评审（Edit Timeline 实现边界、首期承诺收窄、异步 job、Agent 分期、动效不含转场）亦已并入正文。

### 0.1【阻塞】真相源：不把 storyboard 扩成唯一 Timeline，VideoPlan 保持权威
- 核实：composer 候选 / scope warning / 执行均以 `video_generation_plan.json` 的 `plan_hash` + `target` 为活跃依据。见 `composer_services.py:520`（`composer_candidates_payload` 读 `video_plan_with_hash`）、`composer_routes.py:54`（execute 读 `VIDEO_PLAN_REL` 并 `composer_target` 做 scope 校验，scene 未覆盖时 409）。
- 决策：**撤回 v0.1“storyboard 单一 Timeline 并吸收 composer”的提法**，改为双层：
  - **Generation Timeline = storyboard + VideoPlan（既有，权威）**：生成/合成管线的源，不动。
  - **Edit Timeline = 新增、下游派生层**：只引用“已渲染输出”（composer 结果、上传素材），在其上叠加 edit/compose 算子，**不回写 VideoPlan/storyboard**。
- 需先定义的四点，给出答复：
  - **谁是源**：VideoPlan 仍是合成管线的源；Edit Timeline 是派生物。
  - **hash/target 失效**：Edit 节点记录上游来源（composer 结果路径 + `plan_hash`）；上游 `plan_hash` 变化或 composer 重跑 → 该节点标记 **stale**，复用现有 stale-edit 范式让用户重应用。
  - **局部 scope 映射**：Edit 算子的 scope 是“渲染输出片段”，与 VideoPlan 的 scene/shot scope **解耦，不做映射**。
  - **执行 state/result 迁移**：不迁移 composer 的 state/result；Edit 层维护**独立**的 job state/result。

### 0.2【高】HyperFrame 现状定位修正：已是默认依赖，不是“按需新增”
- 核实：composer 默认 `subtitle_mode=hyperframe`（`composer_services.py:45`、`kouboStoryboardComposer.js:3`），且该模式实际调用 HyperFrame 渲字幕（`06_01_VideoPlanComposer.py:937` `render_hyperframe_subtitles`）。
- 决策：**撤回 v0.1“基础合成纯 ffmpeg、HyperFrames 仅高级按需”的提法**：
  - 现状定性：**带动态字幕的当前 Composer 已属“高级合成”，HyperFrame 是生产中的既有依赖**（利好：已在 Mac mini 验证可跑）。
  - **不改 Composer 默认**（避免回归）。
  - 若要“低负载纯 ffmpeg 字幕”作为可选项，则**新增 `subtitle_mode` option（如 `burn`/`ffmpeg`）**，而非改默认。
  - 选型表中 HyperFrames 由“✅ 用（按需）”修正为“✅ 已在用（composer 字幕）”。

### 0.3【高】Agent 可执行候选 = 一个后端子系统，不是改 prompt + 标签
- 核实：现 asset_video prompt 明确禁止保存 / 合成 / 执行（`agent_chat_services.py:398`），前端仅把 `next_actions` 渲染成文本标签（`UploadAssetLibraryOverlay.jsx:635`）。
- 决策：advice → 可执行**拆为独立里程碑**，需新增：
  - 执行 API（异步 ffmpeg 作业）；
  - 每算子的操作 schema 校验 + 算子白名单；
  - 路径安全（限 workspace 内，复用 `safe_workspace_rel`）；
  - 作业状态机（queued/running/done/failed + 进度）+ 取消 / 重试；
  - 确认 UI（diff / 预览 + 确认按钮，取代纯文本标签）；
  - 可参考现有 `composer_lock` / job 模式。
  - 分期：**Stage A 保留只读 advice；Stage B 才建执行子系统，两者不混。**

### 0.4【高】新增真正的 “rendered asset version” manifest，不复用 history backup
- 核实：现 history 是 move / overwrite-backup（`asset_history_services.py:69` `move_working_file_to_history`、`06_01_VideoPlanComposer.py:833` `publish_file`/`backup_before_overwrite`），非叠加式版本。
- 决策：**撤回“复用 history/版本机制”的提法**，新增独立 manifest 记录：
  - 新 asset id、source asset id(s)、applied ops、render 参数；
  - **计费元数据（大小 / 时长，对接第二条产出物计费线）**；
  - provenance / 可恢复关系；
  - UI 展示规则（呈现为 source 的**派生版本**，而非覆盖）。
  - 不复用、不重载现有 overwrite-backup 路径。

### 0.5【中】词级转写不存在：按转写依赖给算子分级
- 核实：现字幕是 dialogue 级 `start/end/text`（`06_01_VideoPlanComposer.py:349` `subtitles_for_scene`，按 segment cursor 累加），**非 word-level**。
- 决策：按转写需求把算子分层：
  - **Tier-1（无需词级，现在可建）**：`trim / split / concat / replace_clip / reorder / set_audio / mute / speed / crop_aspect / burn_subtitle(dialogue 级)`。
  - **Tier-2（需词级，暂缓）**：`remove_silence / auto_cut_to_beat / 词级精准字幕 + 字幕越界自检`。
  - **词级来源**：暂缓直到引入**本地词级 ASR**（faster-whisper / WhisperX 对齐，产出 `word_timestamps.json`，**保持 LAN 本地、不用 ElevenLabs**）；缺失时 Tier-2 算子置灰降级。
  - 自检回环相应降级为 dialogue 级检查（无词级时）。

---

## 1. 需求背景

### 1.1 业务出发点
视频类工作与图片有本质区别：图片基本是"终态产物"，而视频天然是**时间维度上的半成品**。系统 pipeline 产出的内容——分镜片段、口播画面、composer 合成结果——几乎没有一个能保证"拿来即发"，用户普遍需要：

- **剪辑**：裁剪、拼接、替换、调序、换音轨、去静音、烧字幕等，对**已有素材/中间片段**做修改。
- **合成**：把片段、音轨、字幕、图形层拼装成成片；进阶需求还包括动态字幕、片头片尾、数据可视化等"图形层生成"。（注：动画转场是调研常见项，但本方案**明确不做**，见 §2.3。）

客户明确这两类需求**都要**，并希望通过 **Agent 驱动 + 本地交付** 的方式实现。

### 1.2 现状盘点（已有可复用资产）
- **storyboard-as-timeline**：StoryBoard 的 shot/dialogue 序列本身就是一条时间轴，video_plan 决定每段时长与切分，composer 负责拼装。**已有时间线模型，不必另造。**
- **Asset Library**：已具备 Images / Videos / Audio 三个工作区、上传、重命名、删除、move-to-history 的**版本/历史机制**。
- **Agent 范式**：已落地多 surface 的 agent chat（`storyboard_edit / image_plan / video_plan / composer / asset_audio / asset_video`）。其中 `storyboard_edit` 已支持**可执行候选**（`STORYBOARD_EDIT_CANDIDATE` 带 `operations`，提议→确认→执行），而 `asset_video / asset_audio` 当前仅为**只读建议**（`ASSET_VIDEO_ADVICE / ASSET_AUDIO_ADVICE`）。
- **本地媒体链路**：已有 TTS / SRT（⚠️ 经核实为 **dialogue 级** start/end/text，**非词级**，见 §0.5），可作为"结构化转写"喂给剪辑 Agent，无需引入云 ASR；词级能力需另引本地 ASR。

### 1.3 约束（来自交付与商业模型）
- **交付形态**：转售的 Mac mini 单机 appliance、单租户、**LAN-only**。
- **商业模型**：利润来自计费加价；产出物按文件大小计费（第二条计费线）。→ **剪辑/合成产出新视频文件天然命中计费**。
- **硬约束**：单机渲染/转码负载有限；不得引入云依赖；不得引入按量/按公司规模收费的第三方授权（与转售模型冲突）。

---

## 2. 目标与范围

### 2.1 目标
1. 用户与 AI 都能对系统产出的视频/中间片段进行**剪辑**。
2. 支持把片段/音轨/字幕/图形层**合成**为成片。
3. 全流程 **Agent 驱动**（提议→用户确认→执行→版本化），复用现有 agent 范式。
4. 全本地执行、零授权成本、合 LAN-only、产出物可计费。

### 2.2 范围内
- **新增独立 `edit_timeline.json`（下游派生层）**：引用"已渲染输出"（Composer 成片、上传素材），**不扩 storyboard、不回写 VideoPlan/storyboard**。
- **剪辑算子最小集**（ffmpeg 执行，**异步 job + state 轮询**）：Tier-1 = trim/split/concat/replace_clip/reorder/set_audio/mute/speed/crop_aspect/burn_subtitle(dialogue 级)。
- **基础拼装合成**（片段+音轨+字幕 → 成片，ffmpeg）：**Composer 仅作为上游产物来源，不被"纳入"统一模型**。
- **动效合成**（HyperFrames，**异步 job**）：3 类（片头片尾/Logo、信息条/lower-third、数据可视化），偶尔点缀；**不做转场**。
- **独立 rendered-version manifest**：记录派生版本（新 asset id、source、ops、参数、计费元数据），**不复用现有 history backup**。
- **Agent 分期**：Stage A 仅只读建议（现有 `ASSET_VIDEO_ADVICE`）；Stage B 待手动执行 API 稳定后再接可执行候选。

### 2.3 范围外（明确不做）
- **不自建通用 NLE**（帧级自由时间线、多轨关键帧、实时预览渲染）——单机 appliance 上是无底洞。
- **不做镜头间转场动画**（保持硬切）；不做自由拖拽时间线。
- **Tier-2 算子暂不做**（remove_silence / auto_cut_to_beat / 词级精准字幕）——依赖词级时间戳，留待本地词级 ASR 引入后（§0.5）。
- 重度手动精修走**导出外部编辑器**（FCPXML/EDL），不在产品内复刻剪映/Premiere。

---

## 3. 关键判断：三个范式，别混为一谈

客户调研时点名的工具分属**三个完全不同的范式**，并列评估会带偏选型：

| 范式 | 做什么 | 代表 |
|---|---|---|
| ① 生成/渲染引擎（video-as-code） | 从声明式 spec **造新视频/动效** | Remotion、HyperFrames、Revideo |
| ② 交互式 NLE | 人手拖时间线**剪辑** | OpenCut、CapCut |
| ③ Agent 剪辑范式 | Agent 把"剪辑已有片段"表达成结构化操作，本地 ffmpeg 执行 | video-use |

**结论：客户的"剪辑"诉求属于 ②/③，"合成"诉求属于 ①（高级）或纯 ffmpeg 拼装（基础）。** 真正的执行主干是 **ffmpeg**，而非客户清单里的任一 GUI/框架。

---

## 4. 技术选型

### 4.1 逐项判定

| 工具 | 判定 | 角色 | 理由 |
|---|---|---|---|
| **ffmpeg** | ✅ 用（核心） | 剪辑 + 基础合成执行引擎 | 本地、无头、LAN 安全、产出物可计费；两条线主干 |
| **HyperFrames**（HeyGen，Apache-2.0） | ✅ **已在用**（composer 字幕，§0.2） | 高级合成（动效/字幕/图形层） | 既有生产依赖（composer 默认 `subtitle_mode=hyperframe`），非新增；零授权费、为 agent 设计、直接写 HTML |
| **video-use**（browser-use，MIT） | 🟡 只抄架构，不引入包 | 剪辑线设计蓝本 | 思路对（EDL JSON + 结构化转写 + ffmpeg + 自检），但为 Claude-Code shell skill 且硬依赖 ElevenLabs 云转写，**违反 LAN-only**；抄模式、转写换本地 SRT |
| **MoviePy**（MIT） | 🟢 可选 | ffmpeg 之上的 Python 封装层 | 让后端用 Python 表达算子而非裸命令；契合 Python 后端，非必须 |
| **Revideo**（MIT） | 🟢 备选 | HyperFrames 替代 | "模板 + JSON 参数"模式；与 HyperFrames 二选一，默认 HyperFrames |
| **Remotion** | ❌ 不用 | — | 4 人以上公司收费 / 按 render 计费，与转售 appliance 利润模型直接冲突 |
| **OpenCut**（MIT） | ❌ 现在不用（留观） | — | 能用旧版已归档停维，重写版编辑器未完成；待其 MCP/headless 落地再评估 |
| **CapCut** | ❌ 不用 | — | 闭源、必须桌面 App 出片（无 headless）、ToS 对商用素材有授权风险、不可自托管 |

### 4.2 最终要落地的栈
- **剪辑 + 基础拼装合成**：ffmpeg（可选 MoviePy 做 Python 封装），**异步 job**。
- **动效合成**：HyperFrames（既有依赖，扩展用途，3 类、异步 job）。
- **video-use**：只借鉴架构，不引入代码。
- **Remotion / OpenCut / CapCut**：均不用。

> 客户点名的 5 个里，**HyperFrames 已在栈**（现有 Composer 字幕默认依赖）；本方案对它做**条件性/分批的动效扩展**。video-use 贡献思路不贡献代码；其余 3 个排除。主力是清单外的 **ffmpeg**。

---

## 5. 设计方案

### 5.1 核心架构：双层时间线 —— 上游生成（不动）+ 下游编辑（新增）

```
  ┌─ 上游(既有,不动) ───────────────────────────────────────┐
  │  storyboard + VideoPlan(video_generation_plan.json)        │
  │  Composer(后台 job + state 轮询)                            │
  │  权威源:plan_hash / target / scope —— 维持现状             │
  └──────────────────────────┬───────────────────────────────┘
                             │ 产出"已渲染输出"(成片/片段)
                             ▼ (单向引用,只读)
  ┌─ 下游(新增) edit_timeline.json ──────────────────────────┐
  │  节点引用 已渲染输出 / 上传素材,记录上游来源 + plan_hash    │
  │    • edit    节点 → 剪辑算子(Tier-1)                       │
  │    • compose 节点 → 拼装参数 / 动效模板入参                 │
  │  上游重跑或 plan_hash 变化 → 相关节点标 stale,提示重应用    │
  └──────────────────────────┬───────────────────────────────┘
                Agent 只读建议(Stage A) / 用户手动操作
                             │ 用户确认
                             ▼
  ┌─ 独立 Edit Renderer(异步 job + state 轮询,镜像 Composer)─┐
  │  edit 节点  → ffmpeg(trim/concat/换音/烧字幕…)             │
  │  compose 节点→ ffmpeg 拼装 / HyperFrames 动效图层           │
  │  最终 ffmpeg 拼成片                                         │
  └──────────────────────────┬───────────────────────────────┘
                             ▼
        独立 rendered-version manifest(派生版本,不碰 history backup)
```

**要点**：
- **不存在"统一/扩展 storyboard 的单一时间线"**。上游生成管线（storyboard+VideoPlan+Composer）维持现状、不被改动；新增的 `edit_timeline.json` 是**下游派生层**，单向只读引用上游产物，**不回写**。
- Composer **只作为上游产物来源**，不消费 `edit_timeline.json`、不被"纳入"——其候选/执行仍读 `video_generation_plan.json`（`composer_services.py:520`、`composer_routes.py:54`）。
- 编辑/动效执行是**独立的异步 job（写 state、前端轮询）**，镜像现有 Composer 的 `read/write_composer_execution_state` 模式，**不是同步请求**——避免长视频/Chrome 渲染卡住请求与 UI。
- 该分层与 video-use 实践一致（ffmpeg 主干 + 生成引擎做图层 + agent 发结构化操作）。

### 5.2 数据模型：新增 `edit_timeline.json`（独立文档，非扩展 storyboard）
- 独立文档，**不修改 storyboard schema**。
- 节点 `type: "edit" | "compose"`，clip 引用**已渲染输出或上传素材的路径**，并记录上游来源：`{ source_render_path, source_plan_hash }`。
- `edit` 节点：`{ source_asset, in/out, ops: [Tier-1 算子] }`。
- `compose` 节点：`{ compose_kind: "assemble" | "motion", spec }`（assemble=拼装参数；motion=动效模板入参）。
- **失效契约**：上游 `plan_hash` 变化或 Composer 重跑 → 引用该产物的节点标 `stale`，前端提示用户重新应用（复用现有 stale-edit 范式），不静默失效、不冲突。

### 5.3 操作动词表（按转写依赖分级，见 §0.5）

**Tier-1 剪辑算子（现可建）→ ffmpeg 异步 job**
`trim / split / concat / replace_clip / reorder / set_audio / mute / speed / crop_aspect / burn_subtitle(dialogue 级)`
无状态 ffmpeg 算子，本地、按产出物计费。

**Tier-2（暂不做，待本地词级 ASR）**
`remove_silence / auto_cut_to_beat / 词级精准字幕 + 字幕越界自检` —— 现有字幕为 dialogue 级（`06_01_VideoPlanComposer.py:349`），缺词级时间戳；未具备时界面置灰说明。

**compose 节点 → ffmpeg / HyperFrames**
- `assemble`（基础拼装）：片段+音轨+字幕拼成片，ffmpeg。
- `motion`（动效）：3 类（片头片尾/Logo、信息条、数据可视化），HyperFrames 渲染，`compose 节点 → 模板参数 → HTML`；**不做转场**。详见 §5.6。

### 5.4 Agent 层（分期：先只读，后可执行）
- **Stage A（先做）**：Agent 仅输出**只读建议**（沿用现有 `ASSET_VIDEO_ADVICE`，prompt 维持禁止执行）。建议内容可对应 Tier-1 算子，但不直接落地。
- **Stage B（后做，待手动执行 API 稳定）**：再引入可执行候选 `<VIDEO_EDIT_OPS>` / `<VIDEO_COMPOSE_OPS>`，复用已建成的执行 API + 确认 UI + manifest。**不在 Stage A 改前端标签或放开 prompt 执行**。
- 上下文层：喂结构化时间线 + 现有 dialogue 级字幕（**不用 ElevenLabs**）；词级能力随 Tier-2 一起延后。
- 共享风险：`agent_chat_services.py` / `routes.py` 为所有 surface 共用，Stage B 须**新增 handler，不改共享逻辑**。

### 5.5 与现有系统/约束的契合
- **数据模型**：**新增独立 `edit_timeline.json`**，不改 storyboard、不动 VideoPlan/Composer 真相源。
- **版本化**：**新增独立 rendered-version manifest**，不复用 history backup（§0.4）。
- **执行**：独立异步 job，镜像 Composer 的 state 轮询模式。
- **Agent**：复用 surface / 候选解析 / drawer；Stage B 加 handler 不改共享逻辑。
- **计费**：剪辑/合成产出新文件 → 产出物计费；动效/Agent 方案 → 模型计费。
- **LAN-only**：全本地执行，转写用本地能力。
- **负载**：剪辑+基础拼装走 ffmpeg（轻）；HyperFrames 无头 Chrome 单独计负载，偶尔点缀、异步 job。

---

### 5.6 动效合成（HyperFrames 扩展用途）—— 客户已反馈"可能需要"

> 客户反馈：**可能需要动效**。鉴于 HyperFrame 已是 composer 的既有字幕渲染依赖（§0.2，已在 Mac mini 验证可跑），**扩展其用途做动效是风险最低的路径**——复用同一引擎，不引入新依赖/新授权。主要新增工作量在"模板库 + 负载治理"，不在引擎本身。

- **用途范围（已拍板）**：✅ 片头/片尾标题卡 + 品牌/Logo动画/动态大标题、✅ lower-third / 人名条 / 信息条 / 字幕强调、✅ 数据可视化 / 数字动画；❌ **转场动画暂不做**（镜头间仍用硬切，降低模板量与渲染次数）。
- **频率定性（已拍板）**：**偶尔点缀**（非每条成片必用）→ **负载治理从简**：动效仍走**按需异步 job**（写 state、前端轮询，镜像 Composer），首期**不做复杂队列/缓存**，但保留 **running guard / 单任务互斥 / 状态轮询**；**渲染排队 / 并发上限 / 结果缓存暂缓**，留到实际频率上来后再加。
- **引擎与边界**：仍是 `compose 节点 → HTML（带 data-* 时间属性）→ HyperFrame 无头渲染 → 作为图层叠加 / 拼入成片`。HyperFrames 自带 50+ 现成 block，可作为动效模板起点。
- **Agent 驱动方式**：agent 输出"用哪个动效模板 + 参数（文案 / 时长 / 位置 / 配色）"，**而非手写 HTML**；由模板库把参数渲成 HTML。降低 agent 出错面、便于 schema 校验。
- **配套工程项**：
  - **动效 block 模板库**：按已拍板的 3 类（片头片尾/Logo、信息条、数据可视化）建可复用模板——**这是主要新增工作量**。
  - **渲染负载治理（已降级为后续）**：因定性"偶尔点缀"，首期只做**按需异步 job**（running guard + 单任务互斥 + 状态轮询）；作业排队、并发上限、结果缓存留待频率上来后再加。
- **计费**：动效渲染产出物 → 产出物计费；agent 生成动效方案 → 模型计费。
- **分级**：基础动态字幕 **【已在用】**；上述 3 类动效 **【计划，分批】**；转场动画 **【不做】**。

---

## 6. 用户视角：界面 × 功能 × 操作 × 价值（面向非技术读者）

> 本节用日常语言描述"用户会在哪些界面看到什么新功能、怎么操作、得到什么结果、解决什么实际问题"。标注 **【首期】**（先做、能较快上线）与 **【后续】**（依赖更重能力，排在后面），避免过度承诺。

### 6.1 一句话概述
> 你在系统里产出的视频，往往不是"拿来即发"的成片——可能要剪掉开头的废话、换掉某个不满意的镜头、把几段拼到一起、加上字幕和片头。**本方案让你直接在素材库和分镜页面里完成这些剪辑与合成，还能让 AI 助手帮你看、帮你改，改完一键生成新版本，原文件不丢。**

### 6.2 界面一：素材库 · Videos / Audio 工作区 —— 单个片段的剪辑

- **新增功能**：在每个视频/音频卡片上，新增"剪辑"入口，可对**单个片段**做：裁剪掐头去尾、分割、变速、调整画面比例、替换音轨、静音、烧录字幕。【首期】
- **怎么操作**：
  1. 进入素材库的 **Videos**（或 **Audio**）工作区，找到要改的片段。
  2. 点卡片上的"剪辑"，在弹出的轻量编辑面板里拖动起止点、选操作。
  3. 点"生成"，系统在本地处理后，把结果**作为这个片段的新版本**存回素材库。
- **预期产出**：一个剪好的新视频/音频文件，挂在原素材下方（标明"由 XX 派生"），原始文件保留。
- **解决的实际问题**：
  - "AI 生成的口播片段前面有半秒空镜/停顿" → 一步掐掉。
  - "这段画面我想换成素材库里另一条" → 直接替换，不用回到生成流程重跑。
  - "想快速试听/试看不同版本" → 每次剪辑都留一个版本，随时比较、回退。

### 6.3 界面二：分镜（StoryBoard）· 合成器与时间线视图 —— 整片的拼装与编排

- **新增功能（首期收窄）**：在分镜页面新增一个 **"时间线 / 预览"视图**，把整条片子的片段顺序、音轨、字幕铺开。**【首期】片段级拼装、调序、删除/替换、换音轨、静态/现有动态字幕、生成预览文件**；**【后续】片头/片尾/Logo、信息条、数据可视化动效**；**【不做】镜头间转场动画、帧级自由拖拽时间线**（守住"不自建通用 NLE"边界，§2.3）。
  - 说明：当前合成器**已经在用动态字幕**（现成能力），本视图是把它和片段编排放到一个界面里；不引入自由 NLE 那套多轨/关键帧。
- **怎么操作**：
  1. 在分镜页切到 **"时间线 / 预览"** 视图，看到按分镜顺序排好的片段。
  2. 调序、删除/替换某段、换音轨、在某段上加字幕（不含转场）。
  3. 点"生成预览"在后台渲染（异步，带进度），完成后查看预览文件。
- **预期产出**：一条拼装好的完整成片（带字幕，后续可加片头尾），以及可反复调整的编排草稿。
- **解决的实际问题**：
  - "片段都生成好了，但要手动拼成一条完整视频" → 在一个界面里拖拽完成。
  - "想调整镜头先后顺序、删掉一段" → 调序、删除，提交"生成预览"后查看效果（后台异步生成预览文件，非实时编辑）。
  - "成片要统一加字幕样式、片头片尾" → 一次设置、整片应用。

> ⚠️ 重要边界（对应 §0.1）：这个"时间线 / 预览"视图是**在已生成内容之上做剪辑编排**，不会改写你原来的分镜脚本和生成计划；如果你回头重跑了生成，相关编辑会被标记为"需重新应用"，提示你确认，而不会悄悄失效或冲突。

### 6.4 界面三：素材库 / 分镜 · AI 助手（Agent）—— 先看得懂、提得出（执行为后续 Stage B）

分两期，**首期(Stage A)只读、不执行；执行能力在后续(Stage B)**。

- **Stage A【首期】只读建议 + 手动套用入口**：
  - **新增功能**：AI 助手分析你的素材和成片，给出**剪辑/合成建议清单**（哪段质量不行、建议怎么剪怎么拼），并提供**跳转到对应手动剪辑界面的入口**。它**不直接动手、不执行**。
  - **怎么操作**：在素材库/分镜页打开 AI 助手抽屉提问 → 它返回**建议清单**（文字 + 跳转入口）→ 你到剪辑界面**自己手动套用**。
  - **预期产出**：一份可读的诊断/操作建议清单；实际剪辑仍由你在手动界面完成。
- **Stage B【后续】应用按钮 + 确认执行**（待手动执行 API 稳定后）：
  - 建议升级为**带"应用"按钮的可执行候选**，你逐条确认（可预览差异）后由系统执行并生成新版本。
- **解决的实际问题**：
  - "不知道这堆素材怎么用、哪段质量不行" → 【首期】AI 先帮你看一遍、点出问题、给建议。
  - "知道要改但懒得一步步点" → 【后续 Stage B】描述需求，AI 给可执行方案，你确认后它执行。
  - "怕 AI 乱改" → **AI 不会自动执行**；Stage B 的任何改动也都要你确认后才落地，且生成新版本、不动原件。

### 6.5 界面四：版本与历史 —— 改了不丢，能比、能回

- **新增功能**：剪辑/合成产出的每个结果都作为**派生版本**清晰展示：标明来自哪个原素材、做了什么操作、文件多大多长；可对比、可恢复到任一版本。【首期】
- **怎么操作**：在素材卡片或历史里展开版本列表，查看/对比/恢复。
- **预期产出**：一条清晰的"原件 → 各次剪辑版本"脉络。
- **解决的实际问题**：
  - "改完发现还是上一版好" → 一键回退。
  - "想知道这条成片是怎么来的" → 看得到完整来源和操作记录。

### 6.6 后续能力（依赖更重，排在后面，先说清不画饼）

- **去静音 / 自动卡点剪辑 / 逐字精准字幕**：需要"逐字时间戳"能力（当前字幕是按句的，做不到逐字级精度）。这些会在引入**本地逐字识别**后开放；未具备时，界面上相关按钮会**置灰并说明原因**，不会假装能用。【后续，见 §0.5】
- **动效 / 动态图形 / 数据可视化**：客户已拍板纳入。在基础动态字幕之外，扩展片头片尾/Logo、信息条、数字动画 3 类（**不含转场**），由已在用的 HyperFrame 引擎渲染（详见 §5.6）。【计划，分批】
- **导出到外部专业编辑器**：需要在剪映/Premiere/达芬奇里精修时，提供工程导出，本产品内不复刻专业剪辑软件。【后续，见 §2.3】

### 6.7 价值小结（实际问题 → 本方案如何解决）

| 用户的真实痛点 | 在哪解决 | 怎么解决 |
|---|---|---|
| 产出片段不是成品，开头有废话/停顿 | 素材库 Videos 工作区 | 单片段裁剪，一步掐掉 |
| 某个镜头不满意要换 | 素材库 / 时间线视图 | 替换片段，不重跑生成 |
| 片段要拼成完整成片 | 分镜时间线视图 | 拼装、调序、加字幕（片头尾为后续） |
| 不会用素材 / 不想逐步操作 | AI 助手 | Stage A：AI 看 + 给只读建议；Stage B（后续）：确认后执行 |
| 怕改坏、想比较版本 | 版本与历史 | 生成新版本不动原件，可比可回退 |
| 要在专业软件里精修 | 导出（后续） | 导出工程到外部编辑器 |

---

## 7. 落地顺序（先低耦合、后高耦合；执行先手动、Agent 后接）

| 阶段 | 内容 | 后端 | 耦合/风险 |
|---|---|---|---|
| 1 | `edit_timeline.json` 数据模型 + 失效契约（引用上游产物、不回写） | — | 地基，先定 |
| 2 | **手动**剪辑执行 API（Tier-1 算子）+ 异步 job/state + 路径安全 + 确认 UI | ffmpeg | 中低（加法式端点） |
| 3 | 独立 rendered-version manifest + 版本展示/对比/恢复 UI | — | 低（独立清单，不碰 history） |
| 4 | 基础拼装合成：调用 Composer 产物做下游拼装（**Composer 不被改、仅作上游来源**） | ffmpeg | 中低 |
| 5 | 动效合成：3 类模板库 + HyperFrames 异步渲染 job | HyperFrames | 中低（新增不改默认） |
| 6 | **分镜"时间线/预览"视图**（下游编辑，只读优先 + feature flag + 回归测试） | — | **高（唯一高危区，隔离对待）** |
| 7 | **Agent Stage B**：可执行候选 `<VIDEO_EDIT_OPS>`，复用已稳定的执行 API（加 handler 不改共享逻辑） | — | 中 |
| 8 | 导出兜底：FCPXML/EDL 给外部 NLE | — | 低 |
| — | Tier-2（去静音/卡点/词级字幕）：待本地词级 ASR 引入后单列 | — | 后续 |

> 排序原则：低耦合、可独立交付的先做（手动执行 / 版本 manifest / 动效）；唯一高危的"分镜时间线集成"放后并隔离（§8.3）；Agent 可执行候选**等手动执行 API 稳定后**才接（§0.3 Stage B），不提前。

---

## 8. 风险与待决

### 8.1 待客户拍板
- ✅ **已拍板（动效）**：纳入 HyperFrames 动效，类别 = 片头片尾/Logo + 信息条/lower-third + 数据可视化（不含转场）；频率 = 偶尔点缀 → 负载治理从简。详见 §5.6。

### 8.2 风险
- **单机渲染负载**：HyperFrames/任何无头 Chrome 方案每次渲染起一个 Chrome 实例，共享单机上较重；剪辑线坚持走 ffmpeg。
- **双层真相源边界**：上游（storyboard+VideoPlan+Composer）维持唯一权威；下游 `edit_timeline.json` 仅单向引用、以 stale 跟随上游 `plan_hash` 失效，**严禁回写上游**——这是避免与现有 Composer/SRT 重跑冲突的关键约束。
- **第三方授权漂移**：Remotion 已排除；后续引入任何生成引擎前先核许可证（HyperFrames Apache-2.0、Revideo MIT 为安全项）。
- **OpenCut 观望**：其重写版若落地 Editor API / MCP / headless，MIT 许可下可重评估为外部精修后端。

### 8.3 耦合与爆炸半径（破坏现有功能的概率）

风险不均匀，按功能分布；落在哪一端主要由设计选择决定（本方案的解耦决策刻意把多数功能推向低风险）。

| 新功能 | 真正风险触点（共享处） | 解耦措施 | 净风险 |
|---|---|---|---|
| 单素材剪辑（Videos/Audio） | 前端共享 `UploadAssetLibraryOverlay.jsx`；后端若写共享清单 `koubo_storyboard_assets.json` | 端点加法式；结果写**独立 manifest**；编辑 UI 独立面板 | 🟡 中低 |
| **分镜时间线/合成集成** | **活跃 VideoPlan/Composer 管线**（`plan_hash`/scope/stale） | 下游 `edit_timeline.json` 不回写；只读优先 + flag + 回归测试 | 🔴 **高（唯一高危）** |
| AI 助手·只读建议 | 独立 surface + drawer | 已隔离 | 🟢 低 |
| AI 助手·可执行（Stage B） | `agent_chat_services.py`/`routes.py` 为**所有 surface 共享** | Stage A/B 拆分；**加 handler 不改共享逻辑** | 🟡 中 |
| 版本/历史 | 若复用现有 history backup 会动到 move/composer 备份 | **新建独立 manifest，不碰 history 路径** | 🟢 低 |
| 动效 / HyperFrames | 在活跃 composer 字幕渲染链路内 | **不改默认**，只新增 option / compose 节点类型 | 🟡 中低 |

**结论**：多数功能加法式、可隔离、低破坏风险；**唯一真高危是"分镜时间线集成"对 storyboard/VideoPlan/Composer**——单独隔离对待（只读优先、feature flag、回归测试）。两个隐蔽放大器须守住：共享资产清单 `koubo_storyboard_assets.json`（版本走独立 manifest）、共享 agent 服务文件（可执行加 handler 不改共享逻辑）。

---

## 9. 附：建议下一步
落地阶段 1–3（**手动执行先行，Agent 可执行延后到 Stage B**）：产出
1. `edit_timeline.json` schema（edit/compose 节点结构 + `source_render_path`/`source_plan_hash` 失效契约）；
2. Tier-1 剪辑算子最小集定义 + **手动执行 API**（异步 job/state、路径安全、确认 UI）；
3. rendered-version manifest schema（新 asset id、source、ops、参数、计费元数据、恢复关系、UI 展示规则）。

> **明确不在本次下一步内**：Agent 可执行候选（`<VIDEO_EDIT_OPS>` 等）属 Stage B，待上述手动执行 API 稳定后再做；Agent 当前仅保留只读 `ASSET_VIDEO_ADVICE`。到可直接进入实现 plan 的程度。
