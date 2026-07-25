# OpenCrew 无声素材视觉检索与派生片段全局复用——需求变更与开发实施设计

版本：v0.3.1

日期：2026-07-22

状态：Implementation Ready；R0A–R4 已于 2026-07-22 按本文合同完成本地实现与验收。v0.3.1 进一步按最简发布决定让 R1/R2 在未配置环境变量时默认启用，同时保留原有两个开关作为显式关闭和回退手段，不新增变量。

文档导航：[OpenCrew 素材库文档索引](./OpenCrew_素材库文档索引.md)

变更记录：

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v0.2 | 2026-07-21 | 吸收状态语义、Agent 兼容、raw 文件防护、SQLite 重建和 clip 可发现性审核意见。 |
| v0.2.1 | 2026-07-21 | 将“无声原视频可按画面检索”提升为客户上线 P0，拆分 R1/R2 的迁移、合同和发布门禁；R1 复用现有原视频 Candidate 先形成可独立发布的最短价值闭环，R2 再增加派生片段全局复用。 |
| v0.2.2 | 2026-07-22 | 固定 R1 原视频候选身份 `candidate_kind=original_video` 并写入新搜索快照，避免 R2 再引入身份字段；明确 StoryBoard 复用现有逐片段命中与带范围打开剪辑机制，Agent/editor 只补当前缺口。 |
| v0.2.3 | 2026-07-22 | 将每个视觉结构片段四帧采样提升为 R1 前置质量门禁：固定 `scene_uniform_4_v1`、四个稳定采样位置、单次多图 VLM、逐帧证据/缓存/计量和不支持多图时结构化阻断。现有单中点语义不得直接作为 R1 视觉索引完成证据。 |
| v0.3 | 2026-07-22 | 按最小可用原则完成实施收口：R2 首版只允许剪辑完成后显式加入检索，不修改 clip 创建合同；R1/R2 复用 Query Plan v1，不引入 Query Plan v2；接受当前部署级全局可见边界；历史素材只按需重分析，不自动全库调用模型。同步锁定 API、DTO、标签限制、feature flags、评分和发布任务顺序。 |
| v0.3 implementation record | 2026-07-22 | 完成 R0A–R4、0023/0024、真实 DSCF0157 四帧模型与三入口检索、派生片段跨 Task 复用、PostgreSQL 500/2,000 性能、全量回归、浏览器 E2E、截图与离线用户手册验收。 |
| v0.3.1 | 2026-07-22 | 简化发布配置：现有 R1/R2 两个开关在环境变量缺失时默认启用；仍可用严格布尔值显式关闭或回退，无效值继续 fail closed，不新增环境变量。派生片段自身仍默认 `search_eligible=false`，必须由用户显式加入全局检索。 |

实施状态：仓库已交付 `0023_media_library_visual_search` 与 `0024_media_library_clip_search`；R1/R2 功能默认启用，中心索引支持合格四帧 `visual_semantic`。每个派生片段仍保持默认 `search_eligible=false`，可由用户按人工名称/标签显式加入或移除全局检索。真实 DSCF0157 已产生 current/ready 的 `scene_uniform_4_v1` structure/semantic v2；StoryBoard、Agent 和 editor 三入口及真实剪切/导入、跨 Task clip 复用均已通过。历史 `scene_midpoint_v1` 继续只读保留且不获得视觉检索资格。

上游基线：

1. [OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_需求评审.md](./OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_需求评审.md) v0.9.3。
2. [OpenCrew_素材库综合能力_开发实施设计_v1.md](./OpenCrew_素材库综合能力_开发实施设计_v1.md) v1.1.4。

本文是一次明确的产品范围变更，不是对既有实现的文字澄清。它锁定修改上游基线中的两个旧边界：

1. 无 active dialogue fragment 的无音轨素材不参加跨页面召回。
2. `media_library_clip_derivatives` 永远不参加全局召回。

上游需求评审 v0.9.5、开发实施设计 v1.1.6 已同步本文 R1/R2 增量合同、默认启用决定与 2026-07-22 实施验收状态；历史 M0–M4 段落只描述原对白优先基线，不再构成当前相反合同。

v0.3 在 v0.2.3 四帧质量决定基础上完成实施收口，并锁定以下方向：

1. 无音轨 Dialogue 不得覆盖素材聚合业务状态；“无音轨”和“可按画面检索”使用独立能力语义。
2. `derived_clip` 必须在 StoryBoard、Agent - Asset Library 和 editor 三个共享检索入口正确渲染、预览和分派动作。
3. clip 预览不得使用未登记、不可下载的 Session raw 文件；候选归属和失效状态由搜索/replay/import 权威复核，文件服务继续负责鉴权与下载策略。
4. SQLite 表重建升级提升为发布阻塞级高风险门禁。
5. 首版 clip 强召回主要依赖名称和人工标签，产品必须明确管理可发现性预期。
6. 对“上传素材以无声画面为主”的目标客户，R1 不是增强项，而是上线 P0；M0–M4 的对白场景验收不能替代 R1 的真实无声视觉召回验收。
7. R1 不等待派生片段 Candidate v2、clip metadata 迁移或三入口 clip 导入；R2 在 R1 稳定可用后紧随交付。
8. R1 虽然只有原视频候选，也必须在 DTO 和新搜索快照中显式输出 `candidate_kind=original_video`；R2 只增量增加 `derived_clip`，不得再改变原视频身份语义。
9. R1 的 StoryBoard 逐片段命中展示复用现有实现，不重做结果卡；Agent 和 editor 按各自现有交互补齐范围展示与动作。
10. R1 先把每个视觉 fragment 升级为四帧单次多图分析；单帧历史结果不直接发布，四帧仍不宣称连续动作理解。

---

## 1. 评审结论

### 1.1 用户真正要解决的问题

客户上传的素材以无声画面为主。当前系统能够对无声原视频完成画面结构和视觉语义分析，也能够手动剪出派生片段，但：

1. 跨页面检索只查询 active dialogue fragment，无声原视频即使视觉语义已经 ready 也无法被 StoryBoard 检索。
2. 派生片段被数据库约束和搜索服务同时排除，用户只能回到来源原视频的剪辑页，重新选择目标 StoryBoard 后逐次导入。
3. 同一个常用片段跨多个视频或 Task 重复使用时，用户每次都要回到来源素材、找到派生片段并重新选择目标，已经完成的剪辑劳动无法转化为全局可复用素材。

用户要求的目标工作流是：

```text
无声原视频
  -> 画面结构与视觉语义分析
  -> 按中文画面描述在 StoryBoard 中检索
  -> 剪出高价值片段并命名/标记
  -> 一次性加入全局素材检索
  -> 在任意后续 StoryBoard 中检索、预览并直接加入当前 Task
```

### 1.2 已锁定的产品结论

产品行为锁定为：

1. 只有 current/ready/active、`scene_uniform_4_v1`、schema v2 且四帧完整的视觉语义 fragment 可以独立赋予原视频跨页面检索资格；对白分析不再是唯一资格来源，历史单帧 ready 不获得该资格。
2. 派生片段可以由用户显式设置为“全局可检索”，但默认不自动公开，避免临时剪切和测试产物污染部署级共享素材库。
3. StoryBoard 搜索结果同时支持“原视频”和“可复用片段”两种候选；可复用片段可以直接预览和导入当前 Task，不要求再次剪切。
4. 派生片段继续保存在 `media_library_clip_derivatives`，不得伪装成上传原视频写入 `media_library_assets`。
5. 首次交付是“基于每片段四帧 VLM 已生成中文视觉描述文本的确定性检索”，不是图像 embedding、视频 embedding 或向量相似度检索。
6. 每个 StoryBoard Task 仍保留独立 Asset Pool 和 manifest。全局检索消除的是重复定位与剪切，不取消目标 Task 的受控复制和 provenance 登记。
7. 素材列表将聚合业务状态、无音轨事实和检索能力分开表达；视觉索引可用后不得继续把整条素材表现为不可处理的“无音轨”死胡同。
8. 同一 Candidate 合同服务 StoryBoard、Agent - Asset Library 和 editor；首版只补齐 `derived_clip` 的必要渲染和动作分派，不为三个入口分别建设平行检索系统。

### 1.3 业务价值优先级与可独立发布闭环

本需求包含两个相关但不同的用户价值，不能继续把它们绑成一次“大版本全部完成后才可使用”的交付：

| 优先级 | 用户价值 | 最短闭环 | 发布判定 |
| --- | --- | --- | --- |
| P0 / R1 | 从大量无声素材中按画面内容找到需要的原视频和精确时间范围 | 无声视频按每片段四帧完成视觉语义 -> StoryBoard/Agent/editor 输入中文物体或场景词 -> 返回原视频及命中画面片段 -> 预览命中范围或带范围打开剪辑页 -> 剪切并导入当前 Task | 目标客户上线阻塞项；必须独立先发布 |
| P1 / R2 | 把已经剪好的高价值片段跨 Task 反复复用 | clip 命名/打标 -> 显式加入素材检索 -> 任意后续入口按名称/标签找到精确 clip -> 直接导入当前 Task | R1 后紧随交付；不反向阻塞 R1 |

业务声明必须与交付阶段一致：

1. 只完成 M0–M4、画面分析或素材库名称筛选时，只能说“无声素材可分析、可剪辑”，不能说“无声素材可按画面找到”。
2. R1 完成后可以说“无声原视频可按已发布中文画面描述检索，并可定位到命中时间范围”；此时不得说“已剪片段可全局复用”。
3. R2 完成后才可以说“已剪片段可加入全局检索并在其他 Task 直接复用”。
4. 对当前以无声画面为主的客户，R1 是产品产生实际价值的最低门槛。原 M0–M4 验收仍是必要技术基线，但不再构成该客户场景的上线充分条件。

R1 必须复用现有 `original_video` Candidate、搜索运行、预览、打开剪辑和原视频导入合同，同时把视觉语义从单中点升级为四帧，再增加 `visual_semantic` 发布、资格、召回、命中片段呈现和状态能力。R1 不引入 `derived_clip` Candidate、不修改 clip eligibility、不等待 Agent 卡片重构，也不引入新的搜索平台。

R1 的用户可见结果不能只是返回整条原视频主卡。每个 visual semantic 命中必须保留真实 `start_ms/end_ms`、中文命中摘要和 `analysis_scheme=visual_semantic`；支持的页面应允许预览命中范围，或把命中范围带入 editor 作为默认选区。否则用户仍需在整条视频里人工重找画面，不构成上述最短闭环。

### 1.4 首版最小可用边界

本变更以尽快交付用户可用闭环为优先，分两次小步发布：

R1 首发只完成：

1. 无声原视频以 `scene_uniform_4_v1` 四帧视觉语义重新分析后，可以按已发布中文视觉描述检索；现有 `scene_midpoint_v1` 单帧结果不直接获得 R1 检索资格。
2. 三个共享入口能显示原视频的视觉命中片段、时间范围和中文命中理由。
3. 用户可以预览命中范围，或带建议范围打开剪辑页并完成已有剪切/导入流程。
4. 素材列表真实显示聚合分析状态以及“无音轨 / 可按画面检索”两个独立事实。

R2 紧随完成：

1. 用户可以给 clip 命名、添加少量标签并显式加入/移出全局素材检索。
2. StoryBoard、Agent - Asset Library 和 editor 能正确显示、预览和导入同一个 `derived_clip` 候选。
3. 复用现有 `media_library_clip_derivatives`、共享搜索服务、Session 文件策略和 `MediaLibraryStoryBoardImportService.import_clip`。

为控制范围，首版明确不做：

- clip 独立抽帧/VLM、embedding、pgvector 或相似视频检索。
- 新的通用素材实体表、平行搜索索引平台或跨 Task 物理文件共享层。
- 新租户/用户权限系统、复杂审批流、自动公开规则或自动标签生成。
- 为 Agent 重做一套候选卡；只在现有通用卡上补 candidate kind、用户文案和 `import_clip` 分派。
- 为 clip 另建媒体网关；优先复用现有受鉴权的 Session raw 服务并补齐“必须已登记且允许下载”的校验。只有兼容性测试证明不能安全收紧 raw 服务时，才另行评审专用 preview route。

### 1.5 必须避免的错误实现

以下做法不能视为完成：

- 只删除 `search_eligible = FALSE` 约束，但搜索服务、快照和导入合同仍只认识原视频。
- 只把派生片段名称加入搜索，却继续让无声原视频的视觉分析结果不参加检索。
- 把派生片段插入 `media_library_assets`，造成上传素材、派生素材和 Session 身份混用。
- 把 StoryBoard Task 中已复制的局部文件反向当作全局素材来源。
- 无条件复制父视频的视觉描述并宣称是派生片段自身的精确视觉事实。
- 把文本关键词召回描述成图像向量检索或视频相似度搜索。
- 自动把全部既有剪切测试产物开放给全局用户。
- 前端显示可检索或已导入，但后端没有真实搜索快照、文件复制、manifest 和审计记录。
- 继续让 `video_has_no_audio` 无条件覆盖已经可按画面检索的聚合业务状态。
- 让共享服务向 Agent/editor 返回 clip，却仍按原视频渲染或执行 `import_original`。
- 仅凭可猜测的 Session ID 和路径提供未登记 clip 文件，或绕过现有内部产物下载策略。
- 以安全或未来扩展为名新增首版不需要的模型、索引平台、权限系统或平行数据模型。
- 为等待 R2 的双 Candidate、clip schema 或跨入口导入而延后已经可独立交付的 R1。
- R1 搜索只返回整条原视频而丢掉视觉命中的时间范围，迫使用户在原视频中重新定位画面。
- 对模型发送四次单图请求代替一次四图请求，造成调用次数、会话上下文和成本成倍放大。
- 模型或 provider 不支持一次输入四张图片时静默退回单帧，或缺帧后仍把结果标记为 `scene_uniform_4_v1`。
- 把四张稀疏静帧包装成连续动作、因果关系或人物意图识别。

---

## 2. 实施前已核实的基线实现与真实数据

本节保留 2026-07-22 开始 R0A–R4 实施前的事实快照，用于说明迁移和兼容修复为什么必要；它不是文档顶部“实施状态”的当前值。完成后的权威状态与验收证据见第 18 节及文档索引。

### 2.1 数据库与检索资格

实施前数据库最后一个迁移为：

```text
0022_media_library_storyboard_imports
```

`media_library_fragment_index` 已具备 `summary`、`keywords_json`、`visual_labels_json` 和 `search_text` 字段，但 `analysis_scheme` 的 check constraint 只允许：

```text
dialogue
composite
```

搜索仓储又进一步硬编码：

```text
media_library_tasks.dialogue_status == "ready"
media_library_fragment_index.analysis_scheme == "dialogue"
```

因此视觉语义结果即使存在，也不会进入当前召回。

`media_library_clip_derivatives.search_eligible` 当前被以下约束强制为 false：

```text
CHECK (search_eligible = FALSE)
```

StoryBoard 搜索响应、前端卡片和通用搜索导入请求也只接受 `media_library_original`。

### 2.2 `DSCF0157` 真实样本

实施前测试环境中：

```text
原视频：DSCF0157.MOV
asset_id：mla_1784601908573_70c828790521
时长：26,000 ms
音轨：无
visual_semantic：ready/current
派生片段：mlc_1784605289217_6a71573ce61e
片段名称：化橘红倒入玻璃碗中
片段范围：1,752–5,974 ms
片段时长：4,240 ms（媒体实际时长，允许编码帧级容差）
```

实施前 M0–M4 历史视觉语义来自原视频 13,000 ms 的 Scene 中点 Keyframe，内容包括：

```text
玻璃碗中有深色液体，放在绿色包装盒上，背景为浅蓝色墙面。
objects: 透明玻璃碗、深色液体、绿色包装盒、浅色细线或管状物
keywords: 玻璃碗、深色液体、绿色包装、浅蓝背景、近景
action: null
```

13,000 ms 不在派生片段的 1,752–5,974 ms 范围内。因此：

1. 该单帧结果证明已有可用中文视觉事实，但在 v0.3 R1 中必须先按四帧合同重新分析，才可以按“玻璃碗”“深色液体”等视觉描述召回。
2. 派生片段可以按人工名称“化橘红倒入玻璃碗中”召回。
3. 首版不把原视频 Scene 描述继承给派生片段，也不做来源场景弱召回；派生片段只按人工名称和标签检索，避免把父场景误当作片段精确事实。
4. “倒入”只能由片段名称、人工标签或未来真正的视频动作分析支持；R1 的四张稀疏 Keyframe 仍不能声称识别了连续倒入动作。

### 2.3 实施前测试数据的污染风险

实施前测试环境存在 9 个派生片段，其中多数名称包含 `E2E`、`UI验收留存` 或测试时间戳，且 0 个可检索。若迁移后无差别把全部既有记录设为 true，会立即污染全局结果。

因此既有和新建派生片段固定默认 `search_eligible=false`，由用户在剪辑成功后的派生片段卡上显式加入。首版不在创建弹窗增加“创建后加入素材检索”，不修改现有 clip job 请求和幂等身份。

### 2.4 实施前已确认的兼容缺口

本次范围不能假设现有调用方会自动兼容新候选，已核实：

1. `analysisStatusMeta(status, reason)` 实施前只要 reason 为 `video_has_no_audio` 就无条件返回“无音轨 · 可画面分析”，会覆盖视觉索引已可用后的聚合业务状态；后端聚合规则也会让任一 `blocked` 子状态优先成为整体 blocked。
2. Agent - Asset Library 的通用卡可以播放视频，但全局素材动作固定为 `preview/open_editor/import_original`，导入又要求 `candidate_id == asset_id` 并固定调用 `import_original`，不能正确处理 `derived_clip`。
3. editor 的共享搜索动作同样需要显式识别 `candidate_kind`，不能把 clip 走到原视频或外部 provider 分支。
4. Session raw 路由已经经过统一登录中间件和 `SessionFileService.resolve_download()`，没有证据表明它绕过了提交 `9c732fc` 的内部产物防护；但实施前实现允许在 `session_files` 查询不到行时退回默认路径分类，与“必须是已登记文件”的本文合同不一致。

以上都是首版闭环中的兼容修复，不扩展为新的状态平台、Agent UI 或媒体网关。

---

## 3. 用户故事与验收语义

### 3.1 无声原视频检索

作为 StoryBoard 用户，我希望无声原视频完成视觉语义分析后，可以根据画面中的人物通用称谓、物体、场景和关键词找到它，而不需要不存在的对白。

验收语义：

- visual structure ready 但 visual semantic 未 ready：仍不具备视觉描述召回资格。
- visual semantic current/ready/active，且为完整通过校验的 `scene_uniform_4_v1` / schema v2 四帧结果：具备视觉描述召回资格。
- visual semantic current/ready/active，但仍为历史 `scene_midpoint_v1` 单帧结果：详情只读可见，提示需要重新分析，不具备 R1 视觉描述召回资格。
- visual semantic stale/blocked/failed：不具备视觉描述召回资格。
- 有对白和视觉两种索引时，同一原视频聚合成一个候选，不能重复两张主卡。

### 3.2 派生片段全局复用

作为素材整理用户，我希望把一个已经剪好的片段显式加入全局素材检索，以后在其他 StoryBoard 中直接使用，而不再回到来源视频重做范围选择和 FFmpeg 剪切。

验收语义：

- 创建 clip 不等于自动全局公开。
- “加入素材检索”只改变检索资格和检索元数据，不复制文件、不创建新 Session，也不修改已导入的 StoryBoard。
- “从素材检索移除”不删除物理 clip，不撤销已完成的 StoryBoard 导入。
- 删除 clip 时检索资格和检索结果同步消失；已被 StoryBoard 引用的 clip 继续执行现有删除保护。

### 3.3 StoryBoard 直接导入

作为 StoryBoard 用户，我希望搜索结果清楚区分原视频和可复用片段；对于可复用片段，可以直接播放精确片段并加入当前 Task。

验收语义：

- 原视频允许“预览原视频”“打开剪辑”“加入当前 Task”。
- 派生片段允许“预览片段”“加入当前 Task”，首版不提供递归打开片段剪辑器。
- 派生片段导入调用现有 `media_library_clip` 原子复制分支，不经过原视频导入或外部 provider 下载。
- 每个目标 Task 仍产生独立 manifest 记录、受控目标文件和 `media_library_storyboard_imports` provenance。

### 3.4 共享入口一致性

作为使用 Agent - Asset Library 或 editor 素材检索的用户，我希望同一个全局候选在不同入口保持相同身份、预览内容和导入结果。

验收语义：

- 三个入口复用同一后端搜索运行和 Candidate DTO，不分别复制检索逻辑。
- `original_video` 继续执行 `import_original`；`derived_clip` 只执行 `import_clip`。
- 现有 Agent 通用候选卡只做必要字段和文案兼容，不重做交互结构。
- 任一入口暂不支持某动作时必须从 `allowed_actions` 移除并明确禁用，不能静默退回原视频导入。

### 3.5 明确非目标

本文不包含：

- 图像、视频或文本 embedding 存储。
- pgvector 或其他向量数据库。
- 对每个派生片段自动重新执行 VLM。
- 连续视频动作识别，或仅凭四张稀疏静帧证明“倒入、走动、挥手”等连续动作。
- 跨 Task 共享同一个可写物理文件或取消 StoryBoard Asset Pool 隔离。
- 把派生片段作为新原视频再次进入剪辑器。
- 自动把历史 clip 全部开放。
- 全局素材的用户/租户权限模型；部署级共享边界保持不变。

---

## 4. 术语与身份合同

| 术语 | 稳定身份 | 权威表 | 含义 |
| --- | --- | --- | --- |
| 原视频 | `asset_id` | `media_library_assets` | 用户上传、不可原地替换的源视频 |
| 派生片段 | `clip_id` | `media_library_clip_derivatives` | 从一个原视频精确剪切并持久化的物理结果 |
| 检索候选 | `candidate_kind + candidate_id` | 搜索运行快照 | 一次检索可展示、预览或导入的对象 |
| 来源素材 | `source_asset_id` | `media_library_assets` | 派生片段所属的原视频 |
| 内容版本 | `content_sha256` | 原视频或 clip 权威记录 | 当前候选实际文件的内容身份 |
| 来源版本 | `source_version` | 原视频 SHA-256 | 分析、时间范围和派生 provenance 的源身份 |

候选身份不得继续隐式等于 `asset_id`。统一响应至少返回：

```text
candidate_kind: original_video | derived_clip
candidate_id
asset_id: string | null
source_asset_id
source_clip_id: string | null
source_version
content_sha256
```

具体规则：

```text
original_video:
  candidate_id    = asset_id
  asset_id        = asset_id
  source_asset_id = asset_id
  source_clip_id  = null
  content_sha256  = source_version

derived_clip:
  candidate_id    = clip_id
  asset_id        = null
  source_asset_id = clip.source_asset_id
  source_clip_id  = clip_id
  content_sha256  = clip.content_sha256
```

前端不得把 `clip_id` 填入 `asset_id` 冒充原视频；后端不得根据前端提交的 `source_asset_id` 猜测 clip 归属。

---

## 5. 目标架构

```text
原视频 visual_semantic ready/current
  -> 可信结果校验
  -> visual_semantic fragments 原子发布到中心索引
  -> 原视频获得视觉描述检索资格

派生片段创建成功
  -> search_eligible=false
  -> 用户编辑名称/标签并显式“加入素材检索”
  -> clip search_text 原子更新
  -> 派生片段获得全局检索资格

StoryBoard Dialogue + 用户补充关键词
  -> 现有 Query Plan v1 + 确定性 visual/clip 字段召回
  -> 原视频 dialogue/visual fragment 召回
  -> 派生片段名称/标签召回
  -> 按 candidate 聚合与稳定排序
  -> 搜索快照记录 candidate kind/identity
  -> 原视频或 clip 权威导入分派
  -> 目标 Task Asset Pool + provenance
```

分层原则：

1. `media_library_fragment_index` 继续保存分析 run 产生的、带时间范围的原视频 fragment。
2. `media_library_clip_derivatives` 保存 clip 的物理身份、人工元数据和显式检索资格。
3. 搜索仓储用统一候选 DTO 聚合两类权威来源，不新增第二套 StoryBoard 搜索服务。
4. 搜索运行快照必须足以在导入时重新验证候选身份，但不能保存源绝对路径或未脱敏结果正文。
5. clip 导入复用既有 `MediaLibraryStoryBoardImportService.import_clip`，不复制事务实现。

---

## 6. 数据库迁移设计

### 6.1 迁移编号

为避免 clip 范围拖延 P0 无声视觉召回，迁移按价值阶段拆分：

```text
R1: 0023_media_library_visual_search
R2: 0024_media_library_clip_search
```

`0023` 只放开 `visual_semantic` fragment scheme，不修改 clip 表；它随 R1 独立发布。`0024` 再放开 clip eligibility 并增加 clip 检索元数据，随 R2 发布。不得为了少一个迁移编号把两个发布风险重新绑定。

`0023` 必须覆盖：

1. 空数据库完整创建 `0001–0023`。
2. 已有 `0018` 数据库连续升级到 `0023`。
3. 已有 `0022` 数据库原地升级到 `0023`。
4. PostgreSQL 正式数据库。
5. SQLite 合同测试数据库。
6. 重复执行 migrations 不改变数据、不重复索引。

`0024` 必须另外覆盖空库、`0018 -> 0024`、`0022 -> 0024` 和 `0023 -> 0024`；R2 开发开始前先以已经通过 R1 的 `0023` 数据库作为主要升级路径。

### 6.2 `media_library_fragment_index`

将 scheme check 从：

```sql
CHECK (analysis_scheme IN ('dialogue', 'composite'))
```

改为：

```sql
CHECK (analysis_scheme IN ('dialogue', 'visual_semantic', 'composite'))
```

PostgreSQL 使用显式 drop/add constraint；SQLite 合同环境需要以临时表重建方式迁移并逐列复制，保留主键、唯一约束、外键和所有既有行。不得在 SQLite 中简单跳过新约束，否则空库 schema 和升级 schema 会不一致。

现有索引继续保留：

```text
(is_active, analysis_scheme, asset_id)
(asset_id, analysis_scheme, is_active)
(analysis_run_id)
```

### 6.3 `media_library_clip_derivatives`

以下变更全部属于 `0024_media_library_clip_search`，不进入 R1 的 `0023`。

保留已有 `search_eligible BOOLEAN NOT NULL DEFAULT FALSE`，删除：

```sql
CHECK (search_eligible = FALSE)
```

新增：

```text
tags_json                    JSON NOT NULL DEFAULT []
search_text                  TEXT NOT NULL DEFAULT ''
search_normalization_version TEXT NOT NULL DEFAULT 'nfkc_casefold_ws_v1'
search_enabled_at            BIGINT NULL
search_updated_at            BIGINT NULL
```

新增索引：

```text
ix_media_library_clip_search_eligible_source
  (search_eligible, source_asset_id, created_at)
```

迁移只回填规范化 `display_name` 到 `search_text`，所有既有 clip 保持：

```text
search_eligible = false
search_enabled_at = null
```

不能在 schema migration 中读取 Session 文件、调用 VLM 或自动打开历史 clip。

### 6.4 Schema 一致性

必须同步修改：

```text
backend/opcrew_backend/db/schema.py
backend/opcrew_backend/db/migrations.py
```

迁移测试要分别比较 `0023` 和 `0024` 的空库与对应升级库的列、默认值、nullable、check constraints、unique constraints、foreign keys、indexes 和 triggers；不能只断言列存在。

SQLite 修改 CHECK 约束需要重建表，是各阶段迁移的最高风险步骤，但不因此另建平行索引表。`0023` 只重建 `media_library_fragment_index`；`0024` 只重建 `media_library_clip_derivatives`。发布时设置显式阻塞门禁：

1. R1 先在数据库副本上分别验证 `0018 -> 0023` 和 `0022 -> 0023`；R2 再验证 `0018/0022/0023 -> 0024`，通过后才允许升级对应真实环境。
2. 重建必须使用单一迁移事务；失败注入后旧表、行、约束和引用仍完整，不留下临时表。
3. 升级前后比较行数、主键集合和关键列内容 hash，并验证既有 fragment、clip、StoryBoard import、session file 关系。
4. 执行 SQLite `PRAGMA foreign_key_check` 与 `PRAGMA integrity_check`；PostgreSQL 运行等价约束和引用断言。
5. 在数据库备份完成且两个新增 feature flags 均关闭时才执行升级；未通过不得进入 backfill。

---

## 7. 四帧视觉语义升级与索引发布

### 7.1 四帧采样合同

R1 不改变 Scene Detect 的真实切点和“不超过 15 秒的连续分析窗口”规则，只升级每个 `visual_structure` fragment 的图像采样：

```text
sampling_strategy = scene_uniform_4_v1
sampling_ratios    = [0.125, 0.375, 0.625, 0.875]
keyframe_count     = 4
```

对范围 `[start_ms, end_ms)` 的片段，四个目标时间按 `start + duration * ratio` 计算并限制在片段内部，时间和文件均按顺序保存。稳定身份固定为：

```text
{fragment_id}-sample-01
{fragment_id}-sample-02
{fragment_id}-sample-03
{fragment_id}-sample-04
```

硬性规则：

1. 每个 fragment 必须得到四个采样槽和四个已登记图像文件；极短片段因编解码帧率得到相同图片 hash 时仍保留四个稳定槽，不得悄悄减成一帧。
2. 单个目标点取帧失败时，只能在所属四分之一区间内按确定性近邻顺序重试，并记录实际 `keyframe_time_ms`；不得借用另一个采样槽的文件。该槽全部失败则本次 visual structure run 失败。
3. structure 输出升级为 `media_library_visual_structure_v2`，每帧记录 `keyframe_id/keyframe_time_ms/image_path/image_sha256`；任一缺失、越界、重复 ID、路径不安全或 hash 不符都禁止发布。
4. UI 默认代表画面选择离片段中点最近且时间更早的 `sample-02`，但详情和证据面板可以显示全部四帧。
5. `scene_midpoint_v1` 与 `media_library_visual_structure_v1` 只读兼容，不自动改写成 v2，也不能作为 R1 的 `visual_search_ready` 证据。

structure v2 不引入新的业务字段，只把 v1 的单 `keyframes` 项固定扩为四项；权威输出形状锁定为：

```json
{
  "schema_version": "media_library_visual_structure_v2",
  "asset_id": "mla_...",
  "source_version": "64-char-sha256",
  "analysis_run_id": "mlar_...",
  "sampling_strategy": "scene_uniform_4_v1",
  "items": [{
    "fragment_id": "scene_0001",
    "start_ms": 0,
    "end_ms": 15000,
    "duration_ms": 15000,
    "sampling_strategy": "scene_uniform_4_v1",
    "keyframes": [
      {"keyframe_id": "scene_0001-sample-01", "keyframe_time_ms": 1875,  "image_path": "SessionOutput/visual/keyframes/...01.jpg", "image_sha256": "64-char-sha256"},
      {"keyframe_id": "scene_0001-sample-02", "keyframe_time_ms": 5625,  "image_path": "SessionOutput/visual/keyframes/...02.jpg", "image_sha256": "64-char-sha256"},
      {"keyframe_id": "scene_0001-sample-03", "keyframe_time_ms": 9375,  "image_path": "SessionOutput/visual/keyframes/...03.jpg", "image_sha256": "64-char-sha256"},
      {"keyframe_id": "scene_0001-sample-04", "keyframe_time_ms": 13125, "image_path": "SessionOutput/visual/keyframes/...04.jpg", "image_sha256": "64-char-sha256"}
    ]
  }]
}
```

`keyframes` 必须按同一对象结构恰好出现四项 `sample-01..04`；顶层和 item 均 `additionalProperties=false`。除 schema/version、sampling strategy 和四帧数组长度外，v1 的时间、路径、hash、身份和 fragment 顺序校验全部原样复用。

### 7.2 单次多图 VLM 合同

`03_03` 继续不读取源视频，只读取当前四帧 structure 快照。prepare 必须把四张图及有序 hash 冻结到该 run 的独立 `0_SessionContext/visual_inputs/`。

每个 fragment 只发起一次基础模型请求：一个文本 instruction 加四个按 `sample-01..04` 排序的 image parts。禁止拆成四次单图请求后在应用层拼接描述。模型策略必须在运行前证明支持同一次请求至少四张图片；不支持时返回结构化：

```text
business status: blocked
code: visual_model_multi_image_unsupported
```

不得静默退回 `scene_midpoint_v1`。云端授权文案必须明确“每个画面片段发送四张采样截图”，但仍不得上传整段视频。每张图片继续受单文件大小和格式限制，新增每 fragment 四图解码前总字节上限，默认 `32 MiB`；超过时以 `visual_semantic_keyframe_payload_too_large` 阻断。

模型 prompt 升级为 `visual_semantic_prompt_v3`，输出 schema 升级为 `media_library_visual_semantic_v2`。顶层 `keyframe_refs` 按时间包含全部四个稳定 ID；`claim_evidence.people/objects/scene` 只引用实际支持对应字段的一个或多个已知采样帧。`visual_summary/keywords` 只能归纳四帧中直接可见的事实，不能补充帧外事实。

v2 直接复制现有 `visual_semantic_candidate.schema.json` 和 `visual_semantic_segments.schema.json` 的 v1 字段、required、长度和敏感信息约束，只作以下确定变更：

```text
candidate schema const = media_library_visual_semantic_candidate_v2
result schema const    = media_library_visual_semantic_v2
sampling_strategy      = scene_uniform_4_v1
keyframe_refs          = uniqueItems=true, minItems=4, maxItems=4
claim_evidence people/objects/scene = uniqueItems=true, maxItems=4
claim_evidence action  = maxItems=0
action                 = null
```

两个 v2 JSON Schema 文件必须作为仓库源文件提交并由运行时 validator 实际加载；不能只在 prompt 中描述四帧而继续用 v1 单帧 schema。

四帧提高物体、人物和场景覆盖率，但仍是稀疏静帧，因此 R1 继续强制：

```text
action = null
claim_evidence.action = []
```

可以在摘要中客观描述“不同采样画面分别出现空杯和盛有液体的杯子”，但不能写成“有人把液体倒入杯中”，也不能推断因果、意图或中间未采样动作。

### 7.3 缓存、调用与成本合同

缓存键必须包含：

```text
ordered_image_sha256[4]
sampling_strategy=scene_uniform_4_v1
model provider/model/version
visual_semantic_prompt_v3
output schema version
```

顺序变化、任一图片变化、模型或 prompt 版本变化都必须 cache miss。一次基础请求计为 `model_call_count=1`、`image_count=4`；唯一一次结构化修复仍发送相同四帧，累计再增加一次调用和四张图片。usage、审计和质量报告至少记录 `fragment_count/image_count/model_call_count/cache_hit_count/structured_repair_count/estimated_cost`。

成本门禁按四图请求估算，不得继续沿用单图调用单价。完全无切点时：74 秒为 5 个 fragment、20 张基础输入图片、5 次基础调用；10 分钟为 40 个 fragment、160 张图片、40 次基础调用；30 分钟 synthetic 为 120 个 fragment、480 张图片、120 次基础调用。结构化修复会使对应片段的图片数和调用数再增加一倍，但 30 分钟仍是非阻塞压力测试。

### 7.4 发布映射

当前 visual semantic item 映射为中心 fragment：

```text
analysis_scheme = visual_semantic
fragment_id     = item.fragment_id
start_ms        = item.start_ms
end_ms          = item.end_ms
dialogue_text   = null
title           = null
summary         = item.visual_summary
keywords        = item.keywords
visual_labels   = people + objects + [scene if non-null]
keyframe_refs   = item.keyframe_refs
quality_status  = review if needs_review else ready
confidence      = item.confidence
```

`search_text` 继续由服务端统一 normalization 生成，输入包括 summary、keywords 和 visual labels。不得使用前端提交的拼接文本。

只有 `sampling_strategy=scene_uniform_4_v1`、schema v2、四帧完整且结果校验通过的 visual semantic item 才能在 R1 获得视觉检索资格。单帧历史结果可以继续在详情页只读显示，但 publisher/reconcile 必须以 `sampling_strategy_ineligible` 跳过，不能仅凭 `status=ready` 发布到 R1 index。

### 7.5 原子发布

`MediaLibraryFragmentPublisher` 扩展支持 `visual_semantic`，必须在一个数据库事务中：

1. 锁定 asset、目标 run 和当前 upstream。
2. 校验 `asset_id/source_version/run_id/result_hash`。
3. 插入新 visual fragments，初始 `is_active=false`。
4. 将同 asset 的旧 visual semantic fragments 设为 inactive。
5. 激活新 fragments。
6. 将目标 visual semantic run 设为 ready/current。
7. 更新 `media_library_tasks.visual_semantic_*` 投影。
8. 将依赖旧 visual semantic 的 current composite 标记 stale，并停用其 index。
9. 提交后再发送 event/metric。

任一阶段失败，旧 active visual index 保持可用；不能出现 run 已显示 ready 但 fragment index 尚未发布的可见半状态。

### 7.6 存量重分析与 index backfill

新增可 dry-run、可 write、可重复运行的 index backfill/reconcile 命令。它不调用模型，只处理：

```text
scheme = visual_semantic
status = ready
is_current = true
result_index_path 非空
sampling_strategy = scene_uniform_4_v1
keyframe_count = 4
尚无对应 active fragment index
```

每个 run 必须：

1. 通过 Session workspace 安全解析相对路径。
2. 确认结果路径已经登记到 `session_files`。
3. 重新计算并比对权威 `result_hash`。
4. 校验结果中的 asset、source version、analysis run 和时间范围。
5. 以专用的 current-ready backfill 事务发布，不伪造新的 Tool Session 或模型调用。

现有 `scene_midpoint_v1` ready/current run 必须在 dry-run 中计入 `reanalysis_required_count`，不能由 DB backfill 冒充四帧结果。重新分析属于真实 visual structure + visual semantic run，必须重新确认适用的云图像授权、配额和四图模型能力；首版不自动全库触发模型调用，也不新增批量调度平台。R1 发布前只把目标客户明确选择及本次验收使用的无声素材通过现有逐素材分析入口显式重跑为四帧结果，其他历史素材在 UI 标记“需重新进行画面语义分析后可按画面检索”。

### 7.7 Stale 行为

- visual structure 新版本激活：旧 visual semantic run stale，其 active index 立即停用。
- visual semantic 新版本激活：旧 visual index 停用，依赖它的 composite stale。
- stale visual context 不能继续给原视频或 clip 提供视觉召回理由。
- clip 的人工名称/标签检索资格不因视觉上游 stale 自动关闭；只移除失效的视觉上下文理由。

---

## 8. 派生片段检索元数据

### 8.1 显式启用

新增 clip 元数据更新服务，首版接口固定为：

```http
PATCH /api/media-library/{asset_id}/clips/{clip_id}
```

请求：

```json
{
  "display_name": "化橘红倒入玻璃碗中",
  "tags": ["化橘红", "玻璃碗", "产品演示"],
  "search_eligible": true
}
```

所有字段可选，但请求必须至少包含一个字段。Pydantic `extra=forbid`。字段限制固定为：

```text
display_name: 清理后 1..120 个字符，复用现有 clean_display_name
tags:         最多 10 项
单个 tag:    NFKC 清理后 1..32 个字符
```

标签保持首次出现顺序并按清理后的值去重；空标签、超过数量或长度分别返回 `media_clip_tag_invalid`、`media_clip_tags_too_many`，启用检索但名称与标签均无有效检索词时返回 `media_clip_search_terms_required`。不增加标签层级、颜色、自动补全或独立标签表。

后端必须：

1. 按 path 中的 `asset_id + clip_id` 读取权威 clip。
2. 拒绝归档、删除中或来源版本不一致的 source asset。
3. 复用 clip 名称清理规则，标签执行上述 NFKC、去控制字符、去路径字符、去重和固定长度限制。
4. 服务端重新计算 `search_text = normalized(display_name + tags)`。
5. 在一个事务中更新元数据、资格和时间戳。
6. 写 session event，但 event 失败不回滚权威更新。

当请求把 `search_eligible` 从 false 改为 true 时，规范化后的 `display_name + tags` 必须至少产生一个非空检索词；否则返回结构化 422。首版不建设自动标签或复杂命名质量评分，但 UI 必须提示：

```text
名称和标签将用于以后检索，请使用能描述片段内容的名称。
```

“DSCF0157 片段”一类泛化名称可以保存，但应显示“可发现性较低”的非阻塞提示；不能为赶进度伪造模型标签。

响应不得返回源绝对路径：

```json
{
  "clip": {
    "clip_id": "mlc_...",
    "display_name": "化橘红倒入玻璃碗中",
    "tags": ["化橘红", "玻璃碗", "产品演示"],
    "search_eligible": true,
    "search_enabled_at": 1784607000000
  }
}
```

### 8.2 创建合同保持不变

首版不向 clip job 请求增加 `publish_to_search` 或 `tags`，也不修改 FFmpeg 创建幂等身份。clip 必须先按现有原子流程创建成功，再由用户在派生片段卡调用第 8.1 节接口显式加入检索。创建失败时没有可检索记录；创建成功但用户尚未执行加入动作时始终保持 `search_eligible=false`。

创建时 opt-in、记住用户偏好和自动继承上次选择均属于后续可选增强，不进入 R2 完成判定。

### 8.3 移除与删除

- 设置 `search_eligible=false`：立即停止新检索，保留文件和已完成导入。
- 删除 clip：现有文件事务继续负责清理文件、`session_files` 和数据库记录；搜索结果因权威记录消失而失效。
- clip 已被 StoryBoard 引用：继续执行现有 `media_clip_in_use` 删除保护，但允许从新搜索中移除。

---

## 9. 共享检索服务增量扩展

交付边界说明：R1/R2 均复用现有 Query Plan v1、搜索运行和候选聚合，不新增 Query Plan v2。R1 在现有原视频 Candidate 中扩展 `visual_semantic` 资格、召回、命中片段和评分，并立即固定候选身份字段 `candidate_kind=original_video`；新建的 search snapshot 同步保存该字段。R2 只增量增加 `candidate_kind=derived_clip`、确定性名称/标签召回及其动作，不再改变原视频 DTO/快照身份语义。

### 9.1 请求上下文

StoryBoard 公共请求仍使用：

```json
{
  "user_text": "玻璃碗 化橘红",
  "orientation": "portrait",
  "limit": 12
}
```

后端继续按 `task_id + dialogue_asset_key` 重读权威 Dialogue。内部不能再把 Dialogue 和 `user_text` 作为一个不可区分的长字符串处理，而应保留：

```text
dialogue_query
user_query
```

用户明确输入的 `user_query` 是高优先级条件；即使当前 Dialogue 很长或不相关，完整片段名称命中仍应稳定排在前面。

### 9.2 复用 Query Plan v1

公共请求、`media_library_query_plan_v1`、planner 降级和现有 `dialogue_query/user_query` 内部分离保持不变。R1 只让既有查询词参加 `visual_semantic` 字段召回；R2 再让同一查询词参加 clip 完整名称和人工标签召回。不得为双候选另建规划器 schema、模型调用或搜索服务。

规划器可以从 Dialogue 生成保守、通用、非敏感的可见对象/场景词，但：

- 不得虚构品牌、人物身份、疾病或敏感属性。
- 不得生成并声称源视频已经具备的视觉事实。
- 规划器失败、关闭、超时或配额耗尽时，`user_query`、Dialogue 原文和确定性 normalization 仍可检索。
- 规划器产生的对象/场景词只是查询词，不代表 embedding。

### 9.3 原视频召回

原视频资格改为以下 OR 条件：

```text
upload ready
not archived
content_sha256 valid
AND (
  active/current/ready dialogue fragment exists
  OR active/current/ready R1-eligible visual_semantic fragment exists
)
```

其中 `R1-eligible` 固定要求 `scene_uniform_4_v1`、schema v2、四帧完整且 source version 匹配；不能把历史单帧 ready 状态简化成资格。

召回字段：

- dialogue：`dialogue_text`、title、summary、keywords。
- visual semantic：visual summary、objects、people、scene、keywords。
- asset：display name、original filename、人工 tags。

同一 asset 多 scheme 命中后聚合为一个 `original_video` 候选；matched fragments 保留真实 scheme 和时间范围。

R1 的业务闭环要求：visual 命中不得只转成 asset 级分数。响应中的 matched fragment 至少保留 `fragment_id`、`analysis_scheme=visual_semantic`、`start_ms/end_ms`、中文摘要和命中理由，供 StoryBoard/Agent/editor 展示、片段预览或打开 editor 时初始化建议范围。现有“剪切这个片段”动作继续使用原视频身份和上述时间范围，不引入 derived candidate。

实现不能只放宽数据库 CHECK。至少要同步更新 fragment publisher 的 scheme allowlist、SQLAlchemy schema、capacity 统计、retrieve 资格/筛选、recheck eligibility，以及当前所有把 `dialogue_status == ready` 与 `analysis_scheme == dialogue` 写死的合同测试。资格应按第 9.3 节的 OR 条件集中表达，避免不同入口各自遗漏。

### 9.4 派生片段召回

派生片段的强召回来源：

1. `clip.search_text` 完整用户查询命中。
2. display name 精确短语命中。
3. 人工标签命中。

首版 clip 只按人工名称和标签召回，不继承父视频的视觉描述，不计算来源 Scene 重叠，也不返回任何 `clip_visual_analysis/source_scene_context/clip_contains_keyframe` 证据。这样可以直接复用 clip 表上的确定性文本，不增加跨表时间重叠查询或容易误解的弱事实。未来若确实新增针对 clip 文件的独立抽帧、VLM run、结果 hash 和证据链，再单独设计视觉召回。

### 9.5 时间坐标

原视频 matched fragment 的 `start_ms/end_ms` 使用原视频坐标。

派生片段需要同时返回：

```text
candidate_start_ms/candidate_end_ms  # clip 本地坐标，0..clip.duration
source_start_ms/source_end_ms        # 原视频坐标
time_basis = candidate
```

前端预览使用 candidate 本地坐标，provenance 使用 source 坐标；不得把原视频 13 秒时间直接显示为 4 秒 clip 内的 13 秒。

### 9.6 评分与稳定排序

首版基础权重固定为：

| 命中 | 基础权重 |
| --- | ---: |
| clip 完整名称命中 user query | 140 |
| clip 人工标签命中 | 110 |
| dialogue 完整原始查询命中 | 100 |
| visual summary 完整短语命中 | 90 |
| visual objects/scene/keywords 命中 | 70 |
| 方向完全匹配 | 5 |

本轮实现不得自行修改这些数值；未来调优必须升级 retrieval version 并补排序快照。排序规则同时必须满足：

1. 用户完整输入可复用片段名称时，该 clip 高于同源原视频。
2. 相同 raw score 使用 `updated_at desc + candidate_kind + candidate_id` 稳定排序。
3. 同一原视频的 dialogue/visual 多 fragment 仍只形成一张主候选卡。

### 9.7 能力声明

前后端公开文案统一为：

```text
支持对白、关键词和已发布的视觉描述检索；当前优先精确率。
暂不包含图像或视频向量相似度检索。
```

不能继续显示“只有完成对白分析的原始视频可检索”。

---

## 10. 搜索响应与快照合同

### 10.1 Candidate DTO

R1 的公共候选 DTO 必须已经输出：

```json
{
  "source": "media_library",
  "candidate_kind": "original_video",
  "candidate_id": "mla_...",
  "asset_id": "mla_...",
  "source_asset_id": "mla_...",
  "source_clip_id": null,
  "source_version": "source-sha256",
  "content_sha256": "source-sha256",
  "matched_fragments": [],
  "allowed_actions": ["preview", "open_editor", "import_original"]
}
```

R1 中 `candidate_kind` 是必填、固定为 `original_video`，不能只依赖 `candidate_id == asset_id` 或既有 `search_eligible_source` 推断。现有内部构造器可以用默认值减少调用点改动，但 API 序列化结果和新合同测试必须显式含有该字段。R2 将允许值扩展为 `original_video | derived_clip`，原视频字段和值保持不变。

R2 新增派生片段候选示例：

```json
{
  "source": "media_library",
  "candidate_kind": "derived_clip",
  "candidate_id": "mlc_...",
  "asset_id": null,
  "source_asset_id": "mla_...",
  "source_clip_id": "mlc_...",
  "source_version": "parent-sha256",
  "content_sha256": "clip-sha256",
  "display_name": "化橘红倒入玻璃碗中",
  "preview_url": "/api/session-tasks/.../raw/...mp4",
  "thumbnail_url": null,
  "duration_ms": 4240,
  "orientation": "portrait",
  "score": 0.91,
  "score_reasons": ["片段完整名称命中"],
  "matched_fragments": [],
  "allowed_actions": ["preview", "import_clip"]
}
```

原视频：

```text
candidate_kind = original_video
allowed_actions = preview, open_editor, import_original
```

派生片段：

```text
candidate_kind = derived_clip
allowed_actions = preview, import_clip
```

外部 provider 合同保持不变，不获得 `import_clip` 或 `open_editor`。

### 10.2 Search snapshot

`media_library_search_runs.top_candidates_json` 对每个候选保存隐私安全快照：

```text
rank
source
candidate_kind
candidate_id
source_asset_id
source_clip_id
source_version
content_sha256
score
matched_fragment_ids
```

快照兼容规则：

1. R1 上线后创建的所有 `source=media_library` 快照必须写入 `candidate_kind=original_video`。
2. R1 replay 读取升级前已经存在、缺少 `candidate_kind` 的 media library 快照时，按 `original_video` 兼容；这是唯一允许的缺字段默认。
3. 非空但不在当前允许集合内的 `candidate_kind` 必须安全排除或返回结构化不可用状态，不能根据 ID 前缀猜测。
4. R2 新快照可以写入 `derived_clip`；原视频快照格式不再变化。
5. 外部 provider 的既有快照合同保持不变，不强行伪造 media library candidate kind。

不得保存：

- 绝对路径。
- provider 密钥。
- 完整 Dialogue 正文。
- 未脱敏模型响应。

replay 时重新读取当前权威记录和公开 URL。若 clip 已删除、被移出检索、父素材已归档或版本不符，replay 返回候选不可用状态或安全排除，不能复活旧快照。

---

## 11. StoryBoard 导入合同

### 11.1 公共请求

扩展：

```http
POST /api/koubo-storyboard/tasks/{task_id}/media-library-search/import
```

```json
{
  "source_kind": "media_library_clip",
  "source_id": "mlc_...",
  "target_task_id": 308,
  "requested_name": "化橘红倒入玻璃碗中",
  "search_id": "mls_...",
  "dialogue_asset_key": "dialogue_...",
  "idempotency_key": "mlui_..."
}
```

`source_kind` 只允许：

```text
media_library_original
media_library_clip
```

### 11.2 权威分派

原视频：

```text
source_id -> asset_id -> import_original
```

派生片段：

```text
source_id -> clip_id
  -> 后端读取 clip.source_asset_id
  -> 校验 search snapshot 中的 candidate_kind/candidate_id/content_sha256
  -> import_clip(source_asset_id, clip_id)
```

前端不提交或后端不信任 clip 的绝对路径、来源 Session 和父 asset 归属。

### 11.3 导入前复核

必须复核：

1. search run 属于当前 target Task 和 Dialogue。
2. run 已 completed。
3. snapshot 中存在相同 `candidate_kind + candidate_id`。
4. clip 仍 `search_eligible=true`。
5. clip content hash、source asset 和 source version 与 snapshot 一致。
6. source asset ready 且未归档。
7. target Task 仍有效、可写并能唯一解析 Session。
8. 幂等键没有用于不同源或不同目标。

复制、manifest 替换、数据库 finalize、`session_files` 登记和失败回滚继续复用现有原子实现。

### 11.4 Provenance

目标 StoryBoard manifest 至少保留：

```text
source = media_library_clip
source_asset_id
source_clip_id
source_version
content_sha256
source_start_ms
source_end_ms
source_search_id
source_dialogue_asset_key
import_id
```

---

## 12. 前端交互设计

### 12.1 无音轨素材的业务状态

素材列表和预览抽屉不得继续让 Dialogue 的 `video_has_no_audio` reason 无条件覆盖聚合业务状态。后端聚合时把该 reason 视为 Dialogue“不适用”而不是整条素材 blocked；其他授权、运行或模型错误的 blocked 语义保持不变。首版不新增通用状态机，只增加后端权威布尔能力 `visual_search_ready`，其值严格等于：feature flag 已开，且素材存在 active/current/ready、source version 匹配、`sampling_strategy=scene_uniform_4_v1`、schema v2、四帧完整的 visual semantic index。

显示规则：

| 条件 | 聚合状态 | 独立提示 |
| --- | --- | --- |
| 无音轨，尚无视觉语义 | 未分析或部分可用 | 无音轨 · 可进行画面分析 |
| 无音轨，只有历史单帧视觉语义 | 部分可用或已完成 | 无音轨 · 需重新分析后可按画面检索 |
| 无音轨，视觉语义 ready 但索引不可用 | 部分可用 | 无音轨 · 画面检索尚未就绪 |
| 无音轨，`visual_search_ready=true` | 部分可用或已完成 | 无音轨；可按画面检索 |
| 视觉索引 stale/blocked/failed | 按聚合规则 | 不显示“可按画面检索” |

不得根据 `visual_fragment_count > 0` 或旧 error reason 在前端猜测检索资格。“可按画面检索”表示当前能力，不写成容易被理解为已执行搜索的“已按画面检索”。

### 12.2 剪辑页派生片段卡

当前“预览 / 下载 / 导入 StoryBoard / 删除”扩展为：

```text
预览
下载
加入素材检索 / 已可全局检索
导入 StoryBoard
删除
```

卡片状态固定为：

- 未加入：灰色说明“仅在当前原视频剪辑页可见”。
- 已加入：绿色状态“已可在 StoryBoard 素材检索中复用”。
- 父素材归档：提示“来源素材已归档，当前不参与检索”。
- 保存失败：展示结构化中文错误，不乐观更新状态。

名称与标签编辑固定复用现有轻量对话框样式，不在窄卡片中展开复杂行内表单。普通用户不看到 `search_eligible`、hash 或 run ID。

### 12.3 创建剪切任务

创建剪切任务 UI 保持不变，不增加“创建完成后加入素材检索”或检索标签。只有剪切成功并出现派生片段卡后，用户才能显式加入检索。未来即使增加创建时选项，也不得默认勾选或自动继承上一次选择。

### 12.4 StoryBoard 搜索结果

结果头部从：

```text
原始视频结果
```

改为：

```text
素材结果
```

候选卡明确显示：

```text
全局素材库 · 原视频
全局素材库 · 可复用片段
```

R1 的原视频候选卡必须兼容画面语义命中：

- 使用“画面描述命中 N 项”，不能误写成“对白命中”。
- 每个命中项显示中文画面摘要和格式化时间范围，并保留“预览该范围 / 剪切这个片段”中的已有可用动作。
- “剪切这个片段”打开原视频 editor，并把命中 `start_ms/end_ms` 设为建议选区；不得丢失为整条视频默认范围。
- 整条原视频导入动作可以保留，但必须与命中片段动作清楚区分，不能让用户误以为点击后只导入了命中区间。
- Agent 和 editor 使用同一 matched fragment 数据；若某入口不支持范围预览，至少显示范围并提供其现有可支持动作，不能静默隐藏视觉命中。

现有实现复用边界：

- StoryBoard 已经逐条渲染 `matched_fragments`，显示 `start_ms/end_ms` 和 `dialogue_text || summary`，并由“剪切这个片段”把 `start_ms/end_ms/search_id/matched_fragment_id` 带入 editor。R1 直接复用该数据模型、卡片和路由，不新建视觉专用结果卡。
- StoryBoard 剩余工作只包括允许 `scheme=visual_semantic`、按 scheme 显示“画面描述命中”、更新能力/零结果文案，以及增加 visual fragment 的 model/route/browser 回归。
- editor 已能规范化 matched fragments，并用首个命中范围打开目标素材剪辑页；仍需把当前“命中 N 个片段”补为用户可读的逐片段摘要和时间范围，或明确保留首个命中动作。
- Agent 适配层在实施前已透传 `matched_fragments` 并可用首个 fragment 生成 description；R1 已按 §12.5 在 Agent UI 补齐逐片段范围与动作，验收时不得只凭后端透传判定前端完成。
- 不要求把三个页面强行抽成一个新的共享 UI 组件；复用统一 DTO、normalization、格式化时间和打开 editor 的参数合同即可，避免为代码形式统一扩大 R1。

派生片段卡：

- 播放精确 clip URL。
- 显示 clip 时长，不显示原视频总时长。
- 操作为“预览片段”“加入当前 Task”。
- 不显示“打开剪辑”。
- 首版派生片段只显示名称/标签命中，不显示视觉命中或来源场景命中。

### 12.5 Agent - Asset Library 与 editor

两个入口继续复用现有候选卡和共享搜索 API，只做首版必需兼容：

- 候选 key 和动作判断使用 `candidate_kind + candidate_id`，不得继续假定 candidate ID 就是 asset ID。
- `derived_clip` 显示“全局素材库 · 可复用片段”、clip 时长和精确 clip 预览。
- `derived_clip.allowed_actions = preview, import_clip`；不得获得 `open_editor/import_original/import_whole`。
- Agent 导入按 candidate kind 调用权威 `import_clip(source_asset_id, clip_id)`；不得经过外部 URL 下载分支。
- editor 如果已有目标 Task，允许执行同一 `import_clip`；没有目标 Task 时只预览并明确提示先选择目标，不静默丢弃候选。
- 不重做 Agent 布局、筛选器或详情弹窗；原视频和外部 provider 行为保持回归兼容。

### 12.6 查询输入

保留当前 Dialogue 上下文，补充输入的标签改为：

```text
补充画面或关键词（可选）
```

placeholder：

```text
例如：玻璃碗、产品近景、竖屏；留空则按当前对白检索
```

用户补充内容必须在后端作为高优先级 query 保存，不仅是拼到 Dialogue 尾部。

### 12.7 零结果

零结果提示改为：

1. 缩短关键词或输入片段完整名称。
2. 确认无声原视频已经完成 R1 四帧“视觉语义”，而非只有画面结构或历史单帧结果。
3. 确认派生片段已经“加入素材检索”。
4. 移除可选画幅限制。
5. 规划器降级时尝试输入更明确的物体、场景或片段名称。

不得继续提示“所有候选必须完成对白分析”。

---

## 13. 生命周期、安全与一致性

### 13.1 Archive 与删除

生命周期规则锁定为：

- 父原视频 archived：原视频和它的可检索 clip 都停止新召回。
- restore 父原视频：原视频按当前 active index 恢复；clip 按自己的 `search_eligible` 恢复。
- 父原视频存在任何 derivative 时，继续禁止直接删除。
- clip 删除后搜索结果立即失效。
- clip 已导入 StoryBoard 时继续禁止物理删除，但允许关闭后续检索。

这一规则优先保护用户对“归档会从全局使用面隐藏”的直觉，并避免派生文件绕过父素材治理。

### 13.2 版本与 stale

- 原视频不可原地替换，现有 `source_version` 规则不变。
- R1 visual index 必须来自 current/ready、`scene_uniform_4_v1` / schema v2、四帧完整的 run，且 source version 等于 asset hash。
- clip candidate 的文件身份使用 clip `content_sha256`，来源分析身份使用父 `source_version`。
- 搜索之后、导入之前任一身份变化都返回 409，不使用旧路径继续复制。

### 13.3 路径安全

- 首版 clip preview/download 继续复用受统一登录中间件保护的 `/api/session-tasks/{session_id}/raw/{relative_path}`，不另建媒体网关。
- raw 服务必须要求目标存在对应 `session_files` 记录，且 `visibility/sensitivity/downloadable` 通过 `SessionFileService.resolve_download()`；查询不到登记行时不得回退为默认公开。
- clip 候选 URL 只能由后端根据权威 `source_session_id + output_path` 生成；前端提交的 Session、路径或 URL 不可信。
- commit `9c732fc` 已覆盖的 SessionContext、工具工作目录、执行 state/result、provider sidecar 和敏感路径继续返回 403；clip 媒体文件只能是明确登记为可下载的 `SessionOutput/clips/...` 产物。
- 搜索快照不保存绝对路径。
- 前端不提交文件 URL 作为导入权威输入。
- 所有 path traversal、协议 URL、跨 workspace 解析和符号链接逃逸继续拒绝。

`search_eligible` 是全局发现资格，不是新的文件 ACL。关闭资格或归档父素材必须停止新召回和 replay，但不要求撤销素材拥有者在剪辑页已有的本地预览/下载能力；首版不为此引入 token 化 ACL。

### 13.4 全局可见性

当前素材库是部署级共享，不执行租户隔离。启用一个 clip 的全局检索意味着该部署中的所有可访问 StoryBoard 都能看到它。

UI 文案应使用“加入全局素材检索”，不能只写含糊的“公开”或“加入库”。未来若引入租户/用户隔离，需要新增权限模型，不能把 `session_id` 当权限边界。

### 13.5 Telemetry

新增：

```text
media_library_visual_search_candidate_total
media_library_clip_search_enabled_total
media_library_clip_search_disabled_total
media_library_search_candidate_total{kind=original_video|derived_clip}
media_library_search_match_total{scheme=dialogue|visual_semantic|clip_metadata}
media_library_clip_search_import_total{status=completed|failed}
```

容量采样从只记录 dialogue fragments 扩展为：

```text
ready original assets
active dialogue fragments
active visual semantic fragments
search-eligible clips
P50/P95/P99 retrieval latency
P50/P95/P99 total latency
zero-result rate by query source
```

---

## 14. 性能边界

开发实施设计既有的 500 条原视频边界仍然有效，但新增 clip 后，仅统计原视频数量已经不能代表真实搜索规模。

性能门禁按两个价值阶段执行，R1 不等待构造 2,000 个 clip：

R1 / 四帧无声原视频视觉检索：

```text
不超过 500 条原视频
使用代表分布的 dialogue + visual semantic fragments
PostgreSQL 正常搜索端到端 P95 <= 3 秒
分别记录数据库召回和总延迟
```

R1 还必须独立记录四帧视觉分析容量，不能把模型分析耗时混入搜索 P95：代表无声视频分别记录 `fragment_count/image_count/model_call_count/cache_hit_count/repair_count/model_latency/estimated_cost`。74 秒固定机位基线必须为 5 个 fragment、20 张基础输入图片和 5 次基础请求；一次请求包含四图，不能出现 20 次基础请求。

R2 / 加入派生片段后：

```text
不超过 500 条原视频
不超过 2,000 个 search-eligible 派生片段
使用代表分布的 dialogue + visual semantic fragments
PostgreSQL 正常搜索端到端 P95 <= 3 秒
分别记录数据库召回和总延迟
```

`2,000` 已锁定为 R2 首版性能边界，不是磁盘容量限制。达到任一已启用阶段边界的 80% 时发出容量告警；真实使用超过该规模后依据指标扩容，不在首版预建分布式索引。

代表数据集至少包含：

- 有对白、无视觉语义的原视频。
- 无对白、有视觉语义的原视频。
- 同时有对白和视觉语义的原视频。
- 未启用和已启用的 clip。
- 多个 clip 来自同一原视频。
- clip 名称精确命中和标签命中。
- 中文短语、同义词、零结果和负向词。

若 R1 在 500 原视频边界、或 R2 在 500/2,000 边界内 P95 已超过 3 秒，必须先优化查询或交付既有设计中的 PostgreSQL FTS 增强，不能只提高超时。

---

## 15. 兼容、回填与发布顺序

### 15.1 Feature flags

新增独立能力开关并扩展 `media_library_features.py` 的封闭枚举、环境变量映射和 capabilities 响应：

```text
能力名                                    环境变量
media_library_visual_search                OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1
media_library_clip_search                  OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1
```

两个新增开关在环境变量缺失时均默认为启用，不需要为常规部署增加环境变量；已有环境变量只作为显式关闭和回退手段。环境变量继续使用现有严格布尔解析，显式 `false/off/0/no` 关闭，无效值按 `feature_flag_invalid` 安全关闭。capabilities schema 保持 v1，仅在 `features` 字典保留上述两个键。

依赖：

```text
media_library_clip_search -> library_search
media_library_visual_search -> analysis_runs + library_search
```

关闭时：

- 旧 Dialogue 搜索和原视频导入继续工作。
- 数据库新增列和视觉 index 可以存在但不参加召回。
- 前端不显示 clip 全局检索入口。

### 15.2 发布顺序

R1 / P0 无声视觉检索：

1. `media_library_visual_search` 保持关闭，完成数据库备份，并用实际配置模型通过单请求四图 capability smoke；不支持时停止发布。
2. 部署 `scene_uniform_4_v1` structure、`visual_semantic_prompt_v3` 和 schema v2，但仍不开放视觉搜索；通过四帧快照、单次多图、证据、缓存、计量和配额合同测试。
3. 对目标客户验收范围内的无声素材显式重新运行 visual structure 和 visual semantic；确认云授权、四帧文件登记、current/stale 和 composite 级联正确。
4. 在副本上通过 SQLite `0018/0022 -> 0023` 的 fragment index 重建门禁和 PostgreSQL 对等门禁。
5. 部署 `0023`，立即运行 schema 等价、完整性、迁移幂等和后端回归；任一失败停止发布，不运行 index backfill。
6. dry-run visual index backfill，分别审计四帧可发布、`reanalysis_required`、其他跳过和失败数量；任何单帧结果进入可发布集合都必须停止。
7. write backfill 并验证 active/current/hash/sampling strategy/四帧引用一致性。
8. 打开 `media_library_visual_search`，验证三个共享入口能返回无声原视频、真实视觉命中片段和时间范围，且素材列表业务状态一致。
9. 完成 R1 真实浏览器 E2E、四帧视觉容量记录、500 原视频 PostgreSQL 性能门禁和用户手册更新后，即可独立宣告“无声原视频可按画面检索”。不得等待 R2，也不得提前宣告 clip 全局复用。

R2 / 派生片段全局复用：

10. `media_library_clip_search` 保持关闭，在已通过 R1 的数据库副本上完成 `0023 -> 0024`，并补跑 `0018/0022 -> 0024`。
11. 部署 `0024`、derived Candidate 增量合同和 Query Plan v1 确定性 clip 召回，打开 `media_library_clip_search`，允许显式加入并验证三入口搜索/replay。
12. 在开关仍关闭时验证 StoryBoard、Agent 和 editor 的 `import_clip` 分派合同；打开 `media_library_clip_search` 后，搜索、预览和导入作为一个用户闭环同时可用。
13. 完成 R2 真实浏览器 E2E、500/2,000 PostgreSQL 性能门禁和用户手册更新后，才可宣告“派生片段全局复用”。

### 15.3 回滚

业务回滚优先关闭 feature flags，不删除已发布 visual index，不重置用户显式的 clip eligibility。

数据库迁移为加法和 constraint 放宽，正常回滚不做 destructive down migration。重新上线后应恢复用户原选择。若发现错误视觉 index，通过 reconcile 停用对应 scheme 行，不修改原始 Tool Session 结果。

---

## 16. 测试矩阵

### 16.1 Migration 与 schema

- 空库 `0001–0023`。
- 现有 `0018 -> 0023`。
- 现有 `0022 -> 0023`。
- R2 增量覆盖空库 `0001–0024`、`0018/0022/0023 -> 0024`。
- PostgreSQL 与 SQLite 在 `0023`、`0024` 两个阶段分别 schema 等价。
- SQLite 重建前后行数、主键集合、关键列 hash、foreign keys、indexes、triggers 和引用关系等价。
- SQLite `foreign_key_check`、`integrity_check` 通过，迁移失败注入可完整回滚且不留临时表。
- 既有 clip 保持不可检索。
- 既有 fragment、run、import 和 session file 不丢失。
- 重复 migrations 幂等。

### 16.2 四帧 visual semantic 与 publisher

- 采样时间严格为每片段 12.5%/37.5%/62.5%/87.5%，稳定 ID、实际时间、范围和顺序正确。
- 每个 structure fragment 恰好四个已登记图片文件和 hash；缺帧、越界、重复 ID、路径逃逸、hash 改变均拒绝。
- 单目标取帧失败只在其四分之一区间确定性重试；整个槽失败则 run 失败，不生成三帧或单帧伪 v2。
- 模型请求恰好一个 text part + 四个有序 image parts；基础分析每 fragment `model_call_count=1/image_count=4`。
- 不支持多图、总负载超过上限和云授权缺失分别产生结构化 blocked；均不静默降级。
- cache key 对四个有序 image hash、sampling、prompt、schema 和模型版本敏感；任一变化 cache miss。
- 输出 `keyframe_refs` 包含四帧，字段证据只能引用已知帧；未知、跨 fragment 和模型篡改引用均拒绝。
- `action` 和 `claim_evidence.action` 仍分别为 `null/[]`；四帧状态变化不得包装成连续动作或因果。
- `scene_midpoint_v1` 保持只读显示，但 publisher/backfill 以 `sampling_strategy_ineligible` 跳过并计入 `reanalysis_required_count`。
- 74 秒固定机位真实视频产生 5 个 fragment、20 张基础输入图片、5 次基础请求；有 cache/repair 时计量分别准确。
- visual semantic 原子发布成功。
- 结果 hash、asset、run、source version 不匹配拒绝。
- 插入、旧停用、新激活、projection 任一步失败回滚。
- visual structure 更新使 visual semantic index inactive。
- visual semantic 更新使旧 visual 和 composite inactive/stale。
- current-ready backfill dry-run/write/重复执行。
- 未登记路径、路径逃逸、损坏 JSON、hash 不符拒绝。

### 16.3 Search repository/service

- R1 原视频 Candidate DTO 和新建隐私快照均显式含 `candidate_kind=original_video`。
- R1 replay 对升级前缺少 kind 的 media library 快照兼容为 `original_video`，对未知非空 kind 安全排除。
- 纯 Dialogue 原视频继续召回。
- 无音轨、R1 四帧 visual ready 且已发布的原视频可召回。
- 无音轨、只有历史单帧 visual ready 的原视频不召回，并计入/显示需要重新分析。
- visual blocked/stale/failed 不召回。
- 同一原视频 dialogue + visual 命中只返回一张主卡。
- clip eligibility false 不返回。
- clip 名称、标签开启后返回。
- 完整 clip 名称高于同源原视频。
- clip 不因父视频视觉描述或时间重叠获得召回。
- negative terms、orientation、duration、exclude current asset 继续生效。
- archive、删除、版本变化后 recheck 移除候选。
- planner disabled/timeout/invalid 时确定性检索可用。
- replay 不复活已失效 clip。

### 16.4 Import 与安全

- 原视频搜索导入回归。
- clip 搜索结果直接导入成功。
- 导入文件 hash 等于 clip content hash。
- manifest provenance 包含 clip/source/search/dialogue 身份。
- 同一幂等键重放复用，同键不同 clip 冲突。
- 伪造 clip ID、clip/source 归属、search snapshot、target Task 拒绝。
- 搜索后关闭 eligibility、archive 或删除时导入拒绝。
- 复制、DB finalize、manifest 替换失败时文件和记录正确回滚。
- 服务重启后已成功 clip 和 import 保持可读。
- clip raw preview 在登录、已登记、`downloadable=1` 且路径安全时可读；未登录、未登记、`downloadable=0`、内部产物、错误 Session、traversal 和 symlink escape 均拒绝。
- clip raw 防护继续通过 commit `9c732fc` 的 Session 文件策略合同；不得为搜索预览另开无策略裸文件路由。

### 16.5 Frontend contracts

- R1 三入口都保留并验证 `candidate_kind=original_video`，不得再只用 `candidate_id == asset_id` 推断身份。
- Candidate model 区分 `original_video/derived_clip`。
- clip 不获得 `open_editor/import_original`。
- original 不获得 `import_clip`。
- StoryBoard、Agent 和 editor 都以 `candidate_kind + candidate_id` 作为身份，并从 `allowed_actions` 派生按钮。
- Agent/editor 对 clip 调用 `import_clip`，不调用 `import_original` 或外部下载分支。
- 无音轨 reason 不覆盖聚合业务状态；只有后端 `visual_search_ready=true` 时显示“可按画面检索”，历史单帧 ready 显示“需重新分析后可按画面检索”。
- 视觉详情默认使用 `sample-02` 作为代表画面，并能检查同一片段的四个有序采样证据；不得把四个采样槽渲染成四个独立分析片段。
- clip 本地时间与 source 时间不混用。
- “加入素材检索”失败不乐观显示成功。
- 启用检索时提示名称/标签决定稳定可发现性；泛化名称只给非阻塞提示，不生成假标签。
- 零结果和能力文案不再错误要求对白。
- StoryBoard 既有 matched-fragment UI 对 `visual_semantic` 显示中文摘要、格式化范围和带范围打开 editor；不得另建只服务视觉结果的平行卡片。
- editor 与 Agent 对 visual matched fragments 至少显示用户可读范围和当前支持动作；不能只显示计数或首条 description 后宣称三入口完成。
- Production build 通过。

### 16.6 真实浏览器 E2E

使用真实 `DSCF0157.MOV` 和真实 StoryBoard Task：

R1 必须先独立完成并留证：

1. 对 DSCF0157 明确触发四帧重分析；打开无声素材详情，确认 `scene_uniform_4_v1` 四个有序采样证据、visual semantic ready、Dialogue 无音轨，以及“可按画面检索”。
2. 在 StoryBoard 搜索“玻璃碗”，返回 DSCF0157 原视频。
3. StoryBoard 结果展示至少一个 `visual_semantic` 命中项、中文摘要和真实时间范围，不只显示整条视频。
4. 点击该命中项预览命中范围，或打开 editor 后默认选区等于命中范围；完成一次真实剪切并导入当前 Task。
5. 在 Agent - Asset Library 和 editor 用同一关键词搜索，均能看到同一原视频及视觉命中范围，且不误标为对白命中。
6. 刷新目标 StoryBoard，Asset Pool 中仍有真实导入文件和 provenance。

R2 在 R1 通过后继续：

7. 搜索“化橘红倒入玻璃碗中”，clip 未启用时不返回 clip。
8. 在剪辑页对 `mlc_1784605289217_6a71573ce61e` 执行“加入素材检索”。
9. 回到另一个 StoryBoard，再次搜索并返回“可复用片段”。
10. 预览必须播放 4.240 秒左右的精确 clip，而不是 26 秒原视频。
11. 直接加入当前 Task。
12. 刷新 StoryBoard，Asset Pool 中仍有真实文件和 provenance。
13. 在 Agent - Asset Library 搜索同一名称，显示可复用片段并导入另一个真实 Task；验证实际导入的是 4.240 秒 clip 而非 26 秒原视频。
14. 在 editor 搜索同一名称，正确显示 clip；有目标 Task 时按 `import_clip` 导入，无目标时给出明确提示。
15. 返回剪辑页关闭检索资格，后续新搜索不再返回；已导入文件仍存在。

必须保存：

- 原视频视觉命中主卡、中文摘要、时间范围和片段动作同时可见的截图。
- R1 从视觉命中范围打开 editor、完成剪切并在目标 Asset Pool 留存的截图。
- clip 未启用零结果截图。
- clip 全局可检索状态截图。
- StoryBoard 派生片段结果卡截图。
- Agent - Asset Library 派生片段结果与导入截图。
- editor 派生片段结果与目标状态截图。
- 精确片段预览截图。
- 导入成功与 Asset Pool 留存截图。
- 移除检索后的零结果截图。
- 桌面和移动端关键页面截图。

E2E 结束后按验收要求保留测试产生的素材、clip、search run、Task/Session 和目标 Asset Pool 文件，不做自动清理。

### 16.7 PostgreSQL 性能

- 使用隔离 schema。
- 20 次 warmup，至少 200 次代表查询。
- 报告 DB retrieval、planner、total 的 P50/P95/P99。
- R1 报告 500 条原视频、Dialogue fragment 数和 visual fragment 数；不要求构造 eligible clip。
- R2 在同一方法下增加并报告 2,000 个 eligible clip。
- 正常搜索 P95 <= 3 秒。
- 保存 JSON artifact，内容包含 git working tree 标识、数据库版本、数据分布 hash 和 retrieval version。

---

## 17. 开发任务拆分与依赖

### 17.1 R0A：R1 四帧合同与视觉迁移

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-R000 | `03_02` 四帧 structure v2、`03_03` 单次多图 VLM、prompt v3、证据/缓存/计量合同 | 本文 v0.3 | `scene_uniform_4_v1` 真实模型与媒体证据 |
| ML-R002 | `0023_media_library_visual_search`、schema 与 SQLite fragment index 重建门禁 | 本文 v0.3 | visual scheme + 等价/完整性报告 |

R0A 门禁：四帧 structure/semantic 合同与真实多图模型 smoke 通过；空库、0018 和 0022 到 `0023` 全通过；SQLite foreign key/integrity/失败回滚通过；clip 表和既有 clip 数据未改变。

### 17.2 R1：无声原视频视觉检索

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-R101 | 仅发布四帧 visual semantic 的 fragment publisher | ML-R000,ML-R002 | 原子 visual index；单帧结果被拒绝 |
| ML-R102 | 四帧 current-ready index backfill/reconcile 与单帧 reanalysis 清单 | ML-R101 | 合格存量 visual index + `reanalysis_required_count` |
| ML-R103 | 在现有原视频 Candidate 中扩展多 scheme 资格、召回、评分与聚合；DTO/新快照固定 `candidate_kind=original_video` | ML-R101 | 无声原视频可检索，身份合同前向兼容但不引入 derived candidate |
| ML-R104 | stale、容量与 telemetry | ML-R101,R103 | 生命周期与指标 |
| ML-R105 | 无音轨聚合状态与 `visual_search_ready` | ML-R103 | 列表状态和真实检索能力一致 |
| ML-R106 | 复用 StoryBoard 既有 matched-fragment UI；补齐 scheme 文案、Agent/editor 范围展示与动作 | ML-R103 | 三入口用户可见最短闭环，不新建平行结果卡 |
| ML-R107 | R1 四帧真实模型/媒体容量、PostgreSQL 500 原视频性能、production build、真实浏览器 E2E 与手册 | ML-R000–ML-R106 | P0 独立发布证据 |

R1 门禁：DSCF0157 已按 `scene_uniform_4_v1` 重新分析，单次请求包含四图且输出中文多帧语义，再按“玻璃碗”在 StoryBoard、Agent 和 editor 的共享服务中真实召回；结果显示 visual semantic 中文命中摘要与真实时间范围，能够预览该范围或带范围打开剪辑并完成一次真实导入；素材列表显示“无音轨”和“可按画面检索”，不显示整体 blocked 死胡同；四帧容量记录、500 原视频 PostgreSQL P95、生产构建、浏览器 E2E 和手册均通过。达到该门禁即可独立发布 R1，不等待以下 R2。

### 17.3 R0B + R2：派生片段全局复用

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-R003 | `0024_media_library_clip_search` 与 SQLite clip 表重建门禁 | R1 已通过 | clip search metadata + 等价/完整性报告 |
| ML-R004 | derived clip Candidate、动作和 Query Plan v1 确定性召回增量合同 | R1 已通过 | 在既有 original kind 上增加 derived kind，不升级规划器 schema |
| ML-R201 | clip metadata/eligibility API | ML-R003 | 显式加入与移除 |
| ML-R202 | clip 名称/标签确定性召回 | ML-R103,R201 | derived candidate |
| ML-R203 | search snapshot/replay 扩展 | ML-R004,R202 | 可审计 clip 候选 |
| ML-R204 | 共享 clip 导入分派 | ML-R203 | StoryBoard/Agent/editor 复用 `import_clip` |
| ML-R205 | raw 预览登记与文件策略收紧 | ML-R201 | 复用现有路由且未登记文件不可读 |

R2 门禁：启用前不可搜、启用后在三入口可搜，预览精确 clip，并可从支持导入的入口直接加入另一个 Task。

### 17.4 R3：R2 前端与浏览器闭环

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-R301 | 编辑器 clip 检索资格 UI | ML-R201 | 加入/移除/标签 |
| ML-R302 | 三入口双候选兼容 | ML-R202,R204 | 复用现有卡片完成预览与正确分派 |
| ML-R303 | clip 可发现性文案和 model contracts | ML-R301,R302 | 诚实能力说明 |
| ML-R304 | R2 真实浏览器 E2E 与截图 | ML-R003-R303 | 用户工作流证据 |

### 17.5 R4：R2 完整发布门禁

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-R401 | PostgreSQL 500/2,000 代表数据性能 | ML-R202 | P95 报告 |
| ML-R402 | 全量后端合同与回归 | ML-R003-R304 | 0 failure |
| ML-R403 | 前端 production build 与 E2E | ML-R003-R304 | build/browser 证据 |
| ML-R404 | 用户手册和发布记录 | ML-R304,R401-R403 | 可审核交付物 |

---

## 18. Definition of Ready 与完成判定

### 18.1 Definition of Ready

本文 v0.3.1 已锁定以下产品和工程边界，普通代码、迁移和合同测试可以直接开始，不再等待额外产品评审：

1. 派生片段采用显式 opt-in，不自动开放。
2. 派生片段继续留在 `media_library_clip_derivatives`，不写入 `media_library_assets`。
3. 无声原视频只有以 `scene_uniform_4_v1`、四帧完整、schema v2 的已发布视觉描述文本获得检索资格；历史单中点结果保持只读但不直接进入 R1 index。
4. 首轮不新增 clip 独立 VLM，也不继承父场景描述或实现来源场景弱召回。
5. 父素材 archive 时其 clip 停止召回。
6. 每个目标 Task 仍执行受控文件复制和 manifest 登记。
7. R1 使用 `500 原视频` 性能门禁；R2 再使用 `500 原视频 + 2,000 eligible clips` 门禁。
8. 当前仍为部署级全局可见，不新增租户隔离。
9. 首版采用第 1.4 节最小可用边界，不新增 clip VLM、向量平台、权限系统、平行素材表或独立媒体网关。
10. 每个 fragment 的四帧位置固定为 12.5%/37.5%/62.5%/87.5%，一个基础请求同时携带四图；不支持多图时明确 blocked，不允许单帧降级。
11. 四帧仍属于稀疏静态证据，R1 保持 `action=null`，不宣称连续动作理解。

以下是进入真实模型、PostgreSQL 和浏览器验收前的环境前提，不阻塞先编写代码与本地合同测试：

1. 可用的 PostgreSQL 隔离测试 schema。
2. 实现四帧工具链后，把至少一个真实无声素材重新分析为 current/ready、`scene_uniform_4_v1` visual semantic；实施开始前的 DSCF0157 单帧结果尚不满足。
3. 至少一个可写 StoryBoard 目标 Task。
4. 浏览器测试可保留截图和测试数据。

本需求不直接复用已有单帧 visual semantic 作为 R1 索引。四帧重新分析属于新的真实图像处理 run，必须重新应用云图像授权、成本、Tool Session 和证据合同；随后对合格 current/ready v2 结果执行的纯 index backfill 不产生新的模型调用。未来若增加 clip 独立 VLM，仍须另行补充授权和证据合同。

### 18.2 R1 / P0 业务价值完成判定

只有同时满足以下条件，才能对以无声画面为主的目标客户宣告“无声素材可按画面检索”：

- 上游权威需求和实施设计已经同步允许合格的 current/ready 四帧 visual semantic 独立赋予原视频检索资格，同时明确排除历史单帧结果。
- `scene_uniform_4_v1` structure v2 和 visual semantic v2 已实现：每片段四个稳定采样槽、四个已登记图片/hash，一个基础请求包含四图；实际配置模型 capability smoke 通过且无静默单帧降级。
- `visual_semantic_prompt_v3`、有序四帧缓存键、每字段证据引用、总负载限制、usage 计量和结构化 blocked 合同通过；四帧仍强制 `action=null`。
- `0023_media_library_visual_search` 在空库、0018 和 0022 升级路径通过；SQLite fragment index 重建的等价、foreign key、integrity 和失败回滚门禁全部满足。
- 只有合格四帧 visual semantic 以可信、原子、可 stale 的方式发布到中心 index；单帧历史结果计入 reanalysis required，四帧 current/ready backfill dry-run/write/幂等通过。
- 无声原视频可以按真实中文视觉描述从 StoryBoard、Agent 和 editor 的同一共享服务召回；搜索资格不再要求 Dialogue ready。
- R1 Candidate DTO 和新快照显式使用 `candidate_kind=original_video`；旧快照缺字段 replay 兼容，R2 可纯增量增加 `derived_clip`。
- 每个视觉命中保留真实时间范围、中文摘要和 `visual_semantic` 证据类型；页面可预览命中范围，或带该范围打开 editor 并完成真实剪切/导入，不要求用户重新浏览整条视频寻找画面。
- 同一原视频的 dialogue/visual 多 scheme 命中聚合为一张主卡，且 visual stale/blocked/failed 不参与召回。
- 无音轨 Dialogue 不再覆盖聚合业务状态；只有权威 `visual_search_ready=true` 才显示“可按画面检索”。
- PostgreSQL 500 条代表原视频数据正常搜索 P95 <= 3 秒，并分别记录 retrieval 和 total latency。
- R1 后端合同、前端 model/route contracts、production build 和真实浏览器 E2E 通过；DSCF0157 的四帧模型输入/结果、视觉命中、范围定位、剪切导入、目标 Task 留存和截图可复核。
- R1 独立验收时，用户手册明确区分“无声原视频可按画面找到”和当时尚未交付的“派生片段全局复用”；完成 R2 后的最终手册同时标明两项能力均已交付，并离线校验全部截图可显示。

完成 R1 后，R2 未完成不阻塞 R1 发布；但不得宣告派生片段可以全局检索或跨 Task 直接复用。

### 18.3 全部变更完成判定

只有同时满足以下条件才能标记完成：

- 上游权威需求和实施设计已经同步升级，不再保留“无声永不检索、clip 永不召回”的相反合同。
- R1/P0 已满足第 18.2 节全部条件。
- `0024_media_library_clip_search` 在空库、0018、0022 和 0023 升级路径全部通过，SQLite clip 表重建的等价、foreign key、integrity 和失败回滚门禁全部满足。
- 四帧 visual semantic 以可信、原子、可 stale 的方式发布到中心 index。
- 存量四帧 current/ready visual semantic backfill dry-run/write/幂等全部通过；单帧结果没有被伪装发布。
- 无声原视频可以按真实中文视觉描述从 StoryBoard、Agent 和 editor 共享服务召回。
- 无音轨 Dialogue 不再覆盖聚合业务状态；只有权威 `visual_search_ready=true` 才显示“可按画面检索”。
- 派生片段显式加入/移除全局检索真实生效，既有 clip 未被自动开放。
- 搜索响应、快照、replay、telemetry 和导入都使用 `candidate_kind + candidate_id`，不存在 clip/asset 身份混淆。
- StoryBoard、Agent 和 editor 正确处理 clip allowed actions；支持导入的入口从真实 search run 调用 `import_clip`，并验证目标文件 hash、manifest、`session_files` 和数据库 provenance。
- clip 预览继续复用受鉴权的 Session 文件服务，未登记、不可下载和内部产物无法通过 raw 路由读取。
- 视觉命中理由严格区分原视频四帧 Keyframe 证据与派生片段名称/标签；不继承父场景描述，也不把稀疏四帧包装成连续动作。
- 父素材 archive、visual stale、clip eligibility、删除保护和服务重启行为通过合同测试。
- PostgreSQL 代表数据在 500 原视频、2,000 eligible clips 边界内正常搜索 P95 <= 3 秒，并分别记录 retrieval 和 total latency。
- 全量后端合同测试、前端 model/route contracts、production build 和真实浏览器 E2E 通过。
- DSCF0157 完整验收链路及截图保留，StoryBoard、Agent 和 editor 能看到正确候选，目标 Task 页面能看到测试留下的 clip、search run、Task/Session 和导入结果。
- 用户手册已更新并离线校验所有截图可显示。

以下内容不阻塞本需求完成，但不能被误称为已实现：

- clip 独立 VLM 与连续视频动作识别。
- embedding/pgvector/向量相似度。
- PostgreSQL 中文 FTS 与 GIN。
- 自动推荐哪些 clip 应全局公开。
- 租户和用户级权限隔离。
- 跨 Task 共享单一物理文件而不复制。

---

## 19. 已锁定决定记录

以下决定已按“避免复杂化、避免过度设计、尽快交付用户可用产品”的原则锁定。实现中不得重新引入已排除的可选分支；若未来确需改变，必须另行升级文档版本及相应合同测试：

| # | 已锁定决定 | 当前结论 |
| ---: | --- | --- |
| 1 | 无声原视频是否凭合格四帧 visual semantic ready 获得检索资格 | 接受；历史单帧 ready 不获得资格 |
| 2 | 派生片段是否进入全局检索 | 允许，但显式 opt-in |
| 3 | 是否自动开放所有新 clip | 否 |
| 4 | 是否自动开放历史 clip | 否 |
| 5 | clip 是否写入 `media_library_assets` | 否，继续使用 derivative 表 |
| 6 | StoryBoard 是否直接导入 clip | 是，复用现有原子 clip import |
| 7 | 父 Scene 视觉描述是否用于 clip 首版召回 | 否；不继承、不做来源场景弱召回 |
| 8 | 首轮是否新增 clip 独立 VLM | 否，另立增强设计 |
| 9 | 是否宣称图像/视频向量检索 | 否 |
| 10 | 父素材 archive 后 clip 是否继续召回 | 否 |
| 11 | 每个目标 Task 是否仍复制并登记文件 | 是 |
| 12 | 是否保持部署级全局可见 | 是 |
| 13 | 新性能边界是否采用 500 原视频 + 2,000 eligible clips | 是；作为 R2 首版性能边界，不是磁盘容量限制 |
| 14 | 正常搜索 P95 是否继续要求 <= 3 秒 | 是 |
| 15 | 无音轨是否与聚合业务状态、画面检索能力分开显示 | 是 |
| 16 | derived clip 是否必须兼容 StoryBoard、Agent 和 editor 三个共享入口 | 是，复用同一 Candidate/Import 合同 |
| 17 | clip 预览是否首版复用 Session raw 服务 | 是，但必须强制已登记且通过现有文件策略 |
| 18 | SQLite CHECK 重建是否为发布阻塞门禁 | 是 |
| 19 | 是否明确 clip 强召回主要依赖名称和人工标签 | 是，UI 明示且不生成假标签 |
| 20 | 是否保持第 1.4 节最小可用范围 | 是，不引入 clip VLM、向量平台、权限系统或独立媒体网关 |
| 21 | R1 是否为无声素材客户上线 P0，并允许独立先发布 | 是；M0–M4 不替代 R1，R2 不阻塞 R1 |
| 22 | R1 是否必须返回视觉命中的真实时间范围并支持范围预览/打开剪辑 | 是；只返回整条原视频不构成价值闭环 |
| 23 | 是否将迁移拆为 `0023` visual search 与 `0024` clip search | 是；隔离发布风险并缩短 R1 路径 |
| 24 | 是否升级 Query Plan v2 | 否；R1/R2 都复用 Query Plan v1，只增量增加 visual/clip 确定性召回 |
| 25 | R1 是否已在 DTO 和新快照输出 `candidate_kind=original_video` | 是；旧快照缺字段按原视频兼容，未知值不猜测 |
| 26 | R1 StoryBoard 是否复用现有逐片段命中和带范围打开剪辑机制 | 是；只补 visual scheme/文案/测试，Agent/editor 补各自缺口 |
| 27 | R1 是否必须先把原视频视觉语义升级为每片段四帧 | 是；固定 12.5%/37.5%/62.5%/87.5%，一次请求四图，单帧历史结果不得直接发布 |
| 28 | 四帧是否代表已经支持连续动作识别 | 否；仍强制 `action=null`，只能描述采样帧直接可见事实或客观状态差异 |
| 29 | 模型不支持单次四图时是否允许降级 | 否；结构化 blocked，并提示更换兼容模型配置 |
| 30 | 是否在创建剪辑时增加“创建后加入检索” | 否；首版只在剪辑成功后的派生片段卡显式加入 |
| 31 | 是否自动把历史单帧素材批量重分析为四帧 | 否；目标客户明确选择和验收素材按需重跑，其余提示需重新分析 |
| 32 | R1/R2 功能在环境变量缺失时是否默认启用 | 是；不新增变量，原有两个开关仅用于显式关闭/回退，无效值继续 fail closed；这不改变每个 clip 默认不公开的 opt-in 规则 |

任何一项未来被修改后，都必须同步升级本文对应数据合同、测试和 Definition of Done，不能只在会议纪要中留下例外。
