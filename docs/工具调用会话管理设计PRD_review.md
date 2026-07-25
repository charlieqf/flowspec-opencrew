# 《工具调用会话管理设计 PRD》评审报告

评审对象：`docs/工具调用会话管理设计PRD.md`（原 v0.1，Tool Use Session；已根据本评审和团队讨论更新到 v0.4）

日期：2026-05-28

评审方式：对照现有架构文档（`opencrew_workflow_data_storage_implementation_prd.md`、`opencrew_repo_improvement_plan.md`、`opencrew_workflow_data_storage_prd_review.md`、`opencrew_llm_gateway_billing_design.md`）与实际代码（`ToolLibrary/Analysis/*`）逐项核对。

更新记录：2026-05-28 二次审核补充了 Tool Use Session 运行目录隔离、Variables 合并治理、状态机持久化、文件可见性、安全审计和 Phase 0 本地密钥库边界，并已同步回 PRD v0.4。团队讨论进一步修正了 v0.9 gateway 设计中的本地密钥库存储方式、模型调用 broker、registry crosswalk、自由文本依赖 token 人工归一化、GC 和 schema_version 规则。

---

## 0. 结论

这是一份**动机扎实、核心机制设计良好**的好文档。它有两条主线：

- **(a) 沙盒权限收敛**（§2.1/§2.2/§5）：把所有会触发 Codex 沙盒授权的操作（DB、OpenCode、外部文件）集中到**第 0 步**一次性完成，后续工具只在 workspace 内读写——这是它的根本驱动力。
- **(b) 单工具执行标准化**（§9–21）：`prepare→run→finalize` 生命周期、目录结构、状态机、依赖自检、Prompt 透明化、上下游交接合同。

`prepare→run→finalize` + 输入隔离、Prompt 外置 + ModelCall 审计是其中最有价值的部分，直接命中了现有 review 指出的 stale 文件、工具乱接、提示词不透明问题。

**原 v0.1 落地前需先闭合三件架构对齐问题（Must-fix），并放宽一处过严约束、规划好迁移路径。** v0.4 已闭合第一批合同，但仍需要迁移计划和首批工具验证。

## 1. 已核实事实（评审依据）

对照 `ToolLibrary/Analysis/` 实际代码：

1. **工具确实直接访问 DB / 直连 provider**：`03_semantic_llm_structure_builder`、`06_scene_transition_llm_judge`、`14_segment_descriptor_subtitle_builder`、`17_detail_scheme_recomposer`、`02_audio_asr_pipeline` 中出现 `psycopg / DATABASE_URL / dashscope / api_key`。→ §2.1/§2.3 的痛点真实存在。
2. **已有 `*_result.json` 约定且存在跨工具共享读取**：`16_semantic_first_quality_checker` 读取 `meta_dir` 下 `13/14/15` 的 `*_result.json`。→ §2.4 的「通过约定目录寻找上游产物」真实存在。
3. **输出大量使用子目录 + 多文件**：`schemes/<scheme>/segment_*.{mp4,srt,json}`（`16` 用 `scheme_dir.glob("segment_*.mp4")`）。→ 与 §6/§14「不得再建子目录、全平铺」存在冲突（见 H3）。
4. **编号工具约 21 个**（01–17 + `02_0/05_1/05_2/13_01` 等子工具）。→ 迁移量大（见 M2）。

## 2. 优点（应予肯定）

- **第 0 步集中权限访问**：一次授权而非链路中途反复打断，是对 Codex 沙盒问题的正确解法。
- **prepare→run→finalize + 输入隔离**（§15）：上游 Output 在 prepare 阶段拷入本工具 Working、run 只读自己 Working——使强制 Rerun 只需清理本目录、断点续跑依赖稳定、彻底规避 stale 跨读。这是全文最强设计。
- **Prompt 透明化 + ModelCall 审计**（§17）：提示词不硬编码、模板/变量/渲染/请求响应全部落盘——与基础设施 PRD「Final Prompt 不能只在 OpenCode 消息里」一致。
- **registry 声明**（§16：reads/writes_session_context、consumes/produces_outputs）：让工具链 DAG 机器可读，是 Plan Runner 编排的基础。
- **状态机 + 依赖自检可操作错误**（§10/§12）：利于断点续跑与「卡在哪一步」的可诊断性。

## 3. 评审发现（按严重度）

### Must-fix（落地前必须先定清楚）

**MF1. Tool Use Session 与 Attempt / Plan Runner 的关系未定。**
- 现状：文档自己在 §8 第 8 条 defer 了这一关系；但基础设施 PRD 的运行记录是 **Attempt**，改进计划 P3 要做 **Plan Runner** 来编排工具链。本文又引入第三个「会话」概念。
- 影响：若不对齐，会与已有 Attempt / Plan Runner 长出两套并行模型，后续无法统一恢复、审计、计费。
- 建议：明确「一次 Tool Use Session ≈ 一个 Attempt」（或给出明确映射），并指定**编排者 = Plan Runner**（由它驱动 S0→S1→…、写状态、调度断点续跑/强制 rerun）。

**MF2. 只收敛了 DB「读」，未定义「写回」；需申明 workspace 仅为派生快照。**
- 现状：§5 把 DB 读集中到第 0 步，但**运行结果如何写回 DB（attempt 状态、`session_files` 索引、`session_events`）全文未写**。
- 影响：基础设施 PRD 与 review 反复强调「DB 是主状态、workspace 是产物」，review 问题 3 专门批评过「拿 workspace JSON 当主状态」。当前设计若止于 workspace 文件，运行对 DB-权威模型与 Debug Console「不可见」。
- 建议：(a) 明确 `0_SessionContext` / `Variables.json` 是**本次运行的派生输入快照，DB 仍是唯一真相**；(b) 定义 **DB 写回路径**（finalize / 编排层把 SessionOutput + 状态同步进 DB 的 attempt / result_index / files / events）。

### High

**H1. 第 0 步与编排在沙盒内还是沙盒外，需明确；并联动「5 个直连 DB/key 的工具」的改造。**
- 最干净形态：**受信任后端（非沙盒）跑第 0 步 + DB 写回，沙盒只跑「纯 workspace 进/出」的工具**——沙盒工具永不碰 DB。
- 联动事实①：`03/06/14/17/02_audio_asr` 今天直连 DB、直接持 provider key。迁移时 **DB 访问 → 第 0 步；provider key 不进入工具输入 / stdout / 审计文件**。
- 阶段性修正：按 `opencrew_llm_gateway_billing_design.md` v0.9 最新设计，Phase 0 是 Local Box Trial，真实 key 在盒子本地密钥库：`0600` 加密文件 + 设备密钥 / Secure Enclave 包裹的加密密钥，launchd 启动解包；本机 resolver / broker 注入凭据，本机直连或按配置走代理。Phase 1+ Managed Gateway 才迁到运营方网关。因此这里不能简单写成「provider key → 网关」，也不能继续写成旧的 macOS 登录态密钥串方案，应写成「Phase 0 → 本地密钥库 + resolver/broker，Phase 1+ → Gateway」。
- 建议：文档补一节「执行位置与信任边界」，并把这 5 个工具列为首批改造对象。

**H2. 与现有 Tool Registry / Plan Runner / result index 合同重复，需收敛为一套。**
- §16 registry 与改进计划 **P3.3 Tool Registry** 重叠；§21 `SessionOutput/manifests` 与 **P2.1/P2.2 result index + scheme manifest** 及 attempt 的 `result_index_json` 重叠 → 页面绑定可能出现三处来源。
- 建议：定一条单链 **DB attempt → result_index → `SessionOutput/manifests/*`**；registry 字段与 P3.3 合并为同一份 schema，不要并行两套。

**H3.「不得再建子目录、全平铺」（§6/§14）对媒体工具过严，且与 §20/§21 自相矛盾。**
- 事实③：当前产物是 `schemes/<scheme>/segment_*.{mp4,srt,json}`，单方案几十上百个分段文件；强行平铺成数百个 `segment_001_*` 文件难管理。
- 矛盾：§20 SessionReport / §21 SessionOutput **允许**子目录，单工具目录却**禁止**，规则不一致。
- 建议：对「多 item 产物」放宽一层有界子目录约定（如 `Output/schemes/<scheme>/segment_*`），或明确「全平铺」仅适用于 `0_SessionContext`。

### Medium

**M1. 每个模型工具运行时生成中文 `Prompt说明.html`（§17.3）是表现层耦合。**
- 让工具代码产 HTML = 把展示逻辑写进工具，难维护、易不一致。
- 建议：工具只产**结构化 Prompt 元数据（JSON）**，HTML 由 Debug Console / SessionReport **按需渲染**。

**M2. 约 21 个工具的迁移量大（事实④），勿大爆改。**
- 建议：参考改进计划 **P2.4「保留旧 runner + 包一层 adapter」**，分批迁移；先用 1–2 个工具（建议含一个直连 DB/key 的，如 `06` 或 `14`）打通整套合同再铺开。

**M3. ModelCall 审计应按 provider mode 对账。**
- Phase 0 没有网关权威账本，应记录 `request_id` / `local_usage_id` 并与 gateway 设计 §7.9 的 `local_usage_log` 对账。
- Phase 1+ Managed Gateway 下，应记录 `gateway_request_id`，与网关 `usage_ledger` / `usage_line_items` 对账，避免本地审计与计费账本脱节。

**M4. 机器消费的文件名建议 ASCII。**
- 中文文件名（`Ref_提示词撰写指南.md`、`Prompt说明.html`）会撞上 file API 路径编码问题（基础设施 PRD 坑点 §15.14）。人读的 Report/HTML 用中文可以，但被 raw/zip 下载或被下游按路径引用的文件名尽量 ASCII。

### 文档卫生

**D1. §5 状态与 §8「后续待设计」已过期。**
- 原 v0.1 头部写「当前仅固化需求 1-6 点」，但 §9–21 已详细设计工具标准、状态机、Prompt 透明化；§8 把「提示词透明化规则」等列为「待设计」，而 §17 等其实已写。v0.4 已同步状态说明和 §8 待设计列表。

## 4. 二次审核补充

以下是本次二次审核和团队讨论新增或强化的问题，已同步进 PRD v0.4：

**S1. Tool Use Session 不能固定为 `1:1 OpenCode Session 1:1 Workspace`。**
- 原 v0.1 会把多次 Run / Rerun 和同一 Task 的持续 OpenCode Session 搅在一起。
- 修正方向：`Tool Use Session` 绑定 Attempt / Plan Run，复用 Task 的 `opencode_session_id`，并在 workspace 下有独立运行根目录 `tool_use_sessions/<tool_use_session_id>/`。

**S2. `Variables.json` 不能让多个工具直接抢写。**
- 原 v0.1 只要求保留字段，但没有 atomic write、锁、schema、ownership 或并发策略。
- 修正方向：Tool 输出 `SessionContextPatch_*.json` 或在 tool result 中声明 patch，由 Plan Runner 校验 ownership 后合并；兼容旧工具直接写时必须 atomic rename + 文件锁。

**S3. 状态机需要落到文件和 DB step 状态。**
- 原 v0.1 有状态枚举，但没有 `State.json`、heartbeat、retry count、idempotency key、stuck running 恢复策略。
- 修正方向：每个 Tool 目录有 `State.json`，Plan Runner 仍是 DB step 状态权威；`completed` 必须同时满足 State、tool result 和 `OutputManifest.json`。

**S4. Prompt / ModelCall 审计要纳入文件可见性和敏感性。**
- 原 v0.1 只禁止 secret，但完整 prompt、客户素材、模型输入和响应正文同样可能包含 PII / 商业敏感内容。
- 修正方向：Prompt / ModelCall / SessionOutput 文件带 `visibility`、`sensitivity`、`downloadable`，并同步到 `session_files`；HTML 展示由 Debug Console / SessionReport 按结构化 JSON 渲染。

**S5. 媒体输入快照需要 checksum 和 workspace 边界。**
- 原 v0.1 要复制全局媒体到 `0_SessionContext`，但没有说明大文件膨胀、去重、hash 校验、symlink / hardlink 安全。
- 修正方向：新增 `InputManifest.json`，记录 path、source_ref、sha256、size、visibility、sensitivity；允许 workspace 内 canonical 引用和去重，但必须 realpath 校验，不得指向 workspace 外。

**S6. 单工具目录平铺要求应只约束 `0_SessionContext`。**
- 原 review H3 已指出单工具 Output 全平铺过严；二次审核确认应放宽为受控子目录 + manifest 索引。
- 修正方向：`Working/Output/Report/Prompt` 可有 registry 声明的受控子目录；下游读取 manifest，不扫描目录。

**S7. 沙盒工具不持 key 时，需要模型调用 broker/resolver。**
- 仅说「key 不进入工具输入 / 审计」不够，因为 `03/06/14/17/02_audio_asr` 本身要发 LLM / VLM / ASR 调用。
- 修正方向：PRD §5.1 明确 Tool 经本机 broker/resolver 调用模型；Phase 0 broker 从盒子本地密钥库解析凭据并写 `local_usage_log`，Phase 1+ 转发 Managed Gateway；Tool 只拿 `model_id`、`request_id` 和非 secret 参数。

**S8. Tool Registry 需要字段 crosswalk。**
- 现有 `ToolLibrary/Analysis/tool_registry.json` 已有 `id/name/script/stage/hard_dependencies/soft_dependencies/main_outputs/uses_llm/supports_resume/cost_level` 等字段。
- 修正方向：PRD §16.1 增加映射表，例如 `main_outputs -> produces_outputs`，数字型 `hard_dependencies -> consumes_outputs`，`source_video/task_id/opencode_image_model -> reads_session_context`，`ffmpeg/demucs -> runtime_dependencies`。迁移期由 adapter 生成 normalized registry，不能并行维护两套。

**S9. Tool Use Session 历史目录需要 retention / GC。**
- `Attempt 1:1 Tool Use Session` 意味着每次 Rerun 都会新增 `tool_use_sessions/<id>/`，长期会膨胀。
- 修正方向：PRD §22 补充 GC 规则：latest attempt 的可下载交付物和 result index 不删；被 DB attempt / session_files / events 引用的文件不得直接删；GC 必须 DB-first 并写 cleanup event。

**S10. `schema_version` 统一为字符串语义版本。**
- 现有 `tool_registry.json` 使用 `"1.0"`，PRD v0.2 中 Variables / InputManifest / State 示例用了整数 `1`。
- 修正方向：PRD v0.4 保持统一为 `"1.0"`，并规定机器可读 JSON 不混用整数和字符串。

**S11. 旧 registry 依赖数组包含自由文本 token，不能完全靠 adapter 自动推导。**
- 实测 `hard_dependencies` / `soft_dependencies` 中存在 `13 detail scheme`、`14 detail segment descriptions`、`target balanced_or_summary`、`task_opencode_session_or_fallback`、`human_scene_transition_overrides`、`user_recomposition_instruction` 等非规范 token。
- 这会放大 §16.1 的风险：`main_outputs -> produces_outputs` 等映射可以机械处理，但自由文本依赖需要人工归一化为 tool output、Session Context、plan parameter、runtime dependency、user input 或 manual override。
- 修正方向：PRD §16.1.1 增加依赖 token 归一化规则；adapter 只允许自动推导规范 token，未归一化 token 必须导致 registry validation 失败，不能让 Plan Runner 猜。

## 5. 概念与现有文档对照

| 本 PRD 概念 | 现有对应 | 需对齐点 |
| --- | --- | --- |
| Tool Use Session | Attempt（基础设施 PRD §3.7）/ Plan Runner（改进计划 P3.1） | 是否 1:1？谁编排？（MF1） |
| `0_SessionContext` / `Variables.json` | DB 主状态 + 派生快照（基础设施 PRD §4） | 申明为派生快照，定义写回（MF2） |
| §16 tool registry | Tool Library Registry（改进计划 P3.3） | 合并为一份 schema（H2） |
| §21 SessionOutput/manifests | result index / scheme manifest（改进计划 P2.1/P2.2、attempt.result_index_json） | 单链绑定（H2） |
| §17 ModelCall 审计 | Phase 0 `local_usage_log` / Phase 1+ 网关 `usage_ledger` | 按 provider mode 用 request_id 对账（M3） |
| 工具直连 DB/key | 第 0 步集中 DB + Phase 0 本地密钥库 / resolver / broker，Phase 1+ Gateway | 5 个工具改造（H1/S7） |

## 6. 建议的下一步

1. 在本 PRD 补一节「**与现有架构的对齐与待决**」，先把 MF1/MF2/H1/H2 写清（关系、写回、执行位置、合同收敛）。
2. 放宽 H3 的平铺约束；按 M1 把 HTML 改为按需渲染。
3. 写「**迁移实施计划**」（§8 第 9 条）：采用 adapter + 分批，首批含 1–2 个直连 DB/key 的工具，验证整套合同（含 DB 写回、key 不进工具输入，并经本地密钥库 + broker/resolver 或 Gateway）后再推广。
4. 同步 §5/§8 文档状态（D1）。

## 7. 一句话

核心机制（权限收敛 + prepare/run/finalize 隔离 + Prompt 透明化）扎实可取；落地的关键不在推翻设计，而在**把 Tool Use Session 接进既有的 Attempt / Plan Runner / Registry / result index / provider mode 合同**，并补上「DB 写回」与「workspace 只是派生快照」的边界。
