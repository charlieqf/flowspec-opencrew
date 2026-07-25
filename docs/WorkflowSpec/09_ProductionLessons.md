# 09 · 从生产中长出来的教训（Battle-tested Lessons）

> 前面 00–08 主要回答“应该怎么设计”。本章把 OpenCrew 真实业务中长出的防御代码、contract test、事故审计和后续需求放在一起复盘。**并非每一条都已经实现或都能追溯到一份正式事故单**；证据标签明确区分“代码已经保证”“测试已经锁定”“事故材料观察到”和“设计上要求”。它的价值是说明哪些规则已有生产证据、哪些仍只是从问题中提炼出的目标不变量。
>
> 每条格式：**规则** → **挡的什么生产事故** → **通用化教训** → **repo 证据（file:line）**。

> ### ⚑ 证据类型标签（v0.2 起，团队审核要求）
> 每条证据标来源类型，**防止需求文档被误读成现有保证**：
> `[impl]` 实现代码 · `[contract-test]` 契约测试 · `[requirement]` 需求/设计文档（=想做，未必已做）· `[incident-audit]` 事故审计（描述症状，非实现）。
>
> ### ⚠️ 两条重要范围限定
> 1. **L2/L3 的多数恢复/完整性教训来自口播 execution runner（`koubo_storyboard`）的已实现经验，不自动代表所有后台 runner**。尤其**通用 runner 并不满足**其中若干条（如原子写——通用 `runner.py:_write_json` 仍是直接 `write_text`）。逐条已注明范围。
> 2. **L6 级联/投影一致性多为 `[requirement]` 设计文档，不是已上线代码或 contract**。是"应当如此"，非"已然如此"。

---

## L0 · 三条贯穿一切的元原则

刚从代码里反复出现的模式收敛成三句话，先记住这三条，后面全是它们的展开：

1. **内容哈希 > 时间戳**。并发控制、缓存失效、陈旧检测，一律用"内容的 sha256"而非"改动时间"——时间戳会因时钟漂移、投影写、并发而骗你。
2. **高价值派生边界 = 作用域内稳定 binding + Runtime 输入摘要**。对需要跨产物绑定、跨 Run 复用或正式发布的 canonical Artifact，业务只声明必要 binding key；Runtime 给 producer Attempt 计算“用了哪些声明输入与冻结契约”的摘要，并在 finalize 自动计算输出 hash。它**不适用于每个下游临时文件**，也不是只凭哈希相等就自动复用：副作用、Schema、内容完整性与按需 binding 仍要共同校验。**当前实现部分兑现**——视频 Plan 的绑定与执行确实按 `plan_hash` 门控（L1.2），但步骤复用仍由用户边界决定、`plan_hash_match` 只是信息字段（见 [L4.6](#l46-复用边界与哈希对账-已更正)）。
3. **持久化的"运行中"状态不可信**。worker 会死。把"worker 死了但活可能没事"用**独立的中间态**表达，而不是塌缩成 `completed`/`failed`；每次进程重启都是 **DB 权威 ↔ 文件工作区**的对账点。

---

## L1 · 身份与哈希：派生产物的不变量（最强一条）

### L1.1 跨产物绑定只用一个作用域内稳定 key，不把它泛化成全局 ID
- **规则**：在确实需要跨媒体/跨版本绑定的逻辑单元内，只用**一个在该业务对象与产物角色范围内稳定**的身份键（如 `dialogue_asset_key`）；`srt_id`/`dialogue_id`/数组下标/`_{index}` 只能用于溯源，不能作为该 binding domain 的替代键。Working 文件可使用安全的 Attempt-local 名称，不要求继承业务 binding。
- **挡的事故**：绑定键与真实审计过——TTS 语音/生成视频绑到了错误的对白行，页面显示"割裂"的完成状态（一半绿一半不绿）。
- **通用化**：只为需要 join/re-bind/跨 Run 对齐的 canonical Artifact 声明 binding，并明确作用域；同一范围内禁止多个“等价 key”。一次性正式产物可以只有 Runtime 生成的版本 `artifact_id`，临时文件、日志、Prompt、staging 与投影不需要业务身份键。
- **证据 `[requirement]`+`[incident-audit]`**：需求文档 `docs/SessionDesign-R2/Koubo_Storyboard_DialogueKey_统一资源绑定需求与测试案例.md:22,40,80-83`；审计症状 `docs/SessionDesign-R2/Koubo_Storyboard_Plan执行与页面绑定一致性审计.md:157`。**注意：这是设计要求与事故审计，不是实现代码佐证**——是"规则应如此 + 违反会出的事故"，落地程度以代码为准。

### L1.2 高价值 Plan 记录它从哪个输入算出，执行/绑定硬门控在哈希相等
- **规则**：每个 Plan 生成时记录 `source_plan_hash`（由输入内容摘要算出）；执行状态与结果都盖同一个哈希戳；`当前 plan_hash ≠ 戳` ⇒ 该 Plan/结果为 **stale**，**不得执行、不得绑定、不得给当前状态上色**，只能看/重生成/迁移。
- **挡的事故**：用户改了分镜后，旧的执行结果（针对旧 Plan 生成）被拿来对当前 Plan 上色/绑定——展示或使用了错配的媒体。
- **通用化**：对会被异步复用、绑定或发布的结果，在 producer Attempt 保存由 Runtime 根据**声明输入**和冻结 Step/Tool/Profile 契约计算的摘要；用前核验，不等就当历史。不要把整个 session 的无关字段都折入，也不要要求每个输出文件重复保存同一摘要。
- **证据 `[impl]`**：**执行期 409 硬门控**在 `video_plan_routes.py:388,418,467`（`requested_hash != current_hash` → 409 / `video_plan_stale` → 409）；`binding_status` 比对 `video_plan_execution_state_services.py:150-170`；`--source-plan-hash` `tool_runner_services.py:370-371`；`analysis_v1_plan_hash` `router.py:4127-4154`。（去掉 `[contract-test]`——`test_koubo_video_plan_tts_consistency_contract.py` 未测该 hash 门控。）

### L1.3 内容哈希做乐观并发，而非时间戳
- **规则**：可人工编辑的产物（storyboard edit）保存时，重算磁盘上"上次所见版本"的 sha256，与客户端携带的 `*_revision_sha256` 比对，任一漂移 ⇒ HTTP 409 拒绝，不静默覆盖。
- **挡的事故**：两个浏览器标签互相覆盖（丢失更新）；重生成后的新文案被旧手改覆盖，用户悄悄发了错稿。
- **通用化**：写操作按"上次所见内容的哈希"门控，陈旧写拒绝而非合并——乐观并发用内容哈希，不用时间戳。
- **证据 `[impl]`+`[contract-test]`**：`storyboard_plan_services.py:636-687`（409 `storyboard_edit_changed`/`storyboard_source_changed`）；contract `test_koubo_storyboard_stale_edit_contract.py:309,358`。

### L1.4 自投影豁免：别把自己的派生写当成外部改动
- **规则**：当一次保存**顺带**把内容投影成下游用的派生副本（edit → source projection），派生副本物理哈希必然不同；用嵌入的 provenance 签名判断"这是我自己刚写的投影"，不要用物理哈希，否则系统会把自己上一秒的保存判成"被外部改动"。
- **挡的事故**：系统自己的投影写触发陈旧检测器，把用户的下一次编辑误判为冲突。
- **通用化**：当保存有"写派生副本"的副作用，用独立的 provenance 签名追踪来源，别让系统把自己的派生物当成外部变更。
- **证据 `[impl]`**：`storyboard_plan_services.py:132-150`（注释明确解释）。

### L1.5 每个**声明的**执行相关字段都折进派生摘要
- **规则**：凡是会影响执行结果的字段（如 `voice_id`），都要折进 Plan 的签名哈希；改了它，哈希就变，旧的昂贵产物（音频）自动失效不复用。
- **挡的事故**：用户换了音色，但 Plan 哈希没算进音色，于是复用了旧音色的音频。
- **通用化**：缓存/复用签名必须覆盖每一个**已声明**的执行相关输入和冻结执行契约；平台从 `reads/consumes` 自动计算，Tool 不手写。未声明却影响结果是契约缺陷；无关字段不应造成全量失效。
- **证据 `[impl]`+`[contract-test]`**：`voice_id` 折入输入快照见 `video_plan_signature_services.py:305`（在 `video_plan_input_snapshot` :248-311 内），签名计算 `video_plan_signature` :313；contract `test_koubo_video_plan_tts_consistency_contract.py:486-499`。（`:57` 只是通用哈希函数，不构成证明。）

---

## L2 · 状态可信度：worker 死亡与恢复

### L2.1 持久"running"不可信：双存活信号 + grace period
- **规则**：判断一个"running"状态是否还活着，同时看**内存 job handle**（进程还活着时权威）和**时间戳/mtime 年龄**（重启后的 fallback）；两者都不在且超过宽限期（如 30s）才判 orphan。刚入队的作业给宽限期，避免误杀。
- **挡的事故**：worker 崩溃/重启/部署后，状态文件永远停在"running"，UI 无限转圈等一个已不存在的作业。
- **通用化**：进程重启后，持久的"进行中"状态不可信；用"临时进程内 handle + 持久心跳/mtime"两个信号判活，并给新作业宽限期。
- **证据 `[impl · 仅口播 video-plan execution runner]`**：`video_plan_execution_state_services.py:66-110`（`_job_is_active` + `_state_age_seconds` + `ORPHAN_GRACE_SECONDS=30`）；legacy 无时间戳时退到文件 mtime（`:99-109` 注释）。**通用 runner 走的是心跳超时 → `stale_running`（见 L2.2），非此 orphan 检测。**

### L2.2 中间态优于塌缩：stale_running / orphaned / completed_with_sync_error
- **规则**：给"我不知道 worker 出了什么事"的情况保留**独立的中间状态**，而非塌缩进 `completed`/`failed`：
  - `stale_running`（心跳超时 = 未知，不是失败——磁盘产物可能还好）
  - `orphaned`（worker 死亡）
  - `completed_with_sync_error`（活干成了但写 DB/索引失败 = DB↔文件漂移）
- **挡的事故**：把"心跳没了"直接判 `failed` 会丢弃可恢复的昂贵产物；把"活成了但同步失败"塞进 `completed` 会掩盖漂移。
- **通用化**：为"未知/部分成功"保留专门的中间态，让对账器能只重试同步或检查产物，而不是重跑昂贵工作。
- **证据 `[impl]`**：`stale_running`、`completed_with_sync_error` 是通用 runner 的真实状态（`runner.py:594-616,618-625,663-690`）。**但 `orphaned` 不是通用状态**——口播里它是 `failed` + `orphaned:true` **标志位**（`video_plan_execution_state_services.py:113-139`），把它当统一状态机取值属 `[proposed]`。

### L2.3 写终态失败前先重读权威状态（幂等失败写）
- **规则**：任何在 wrapper/`finally` 里写"失败"的路径，**先重读磁盘上的权威状态**，只在它**仍然像 in-progress** 时才覆盖为 failed——绝不覆盖一个已经成功或已被更新 Run 接管的终态。
- **挡的事故**：工具实际已成功，但外层包装事后抛异常，盲写 `failed` 会把好结果改坏。
- **通用化**：写终态失败是有条件的——先确认它还没结束，再失败它。
- **证据 `[impl · 口播后台 runner]`**：`tool_runner_services.py:444,465,309`（口播各后台 runner 先 `video_plan_execution_is_running(state)` 再写 failed）。范围限于口播执行 runner，非全平台通用保证。

### L2.4 `finally` 一定清理 job handle
- **规则**：每个后台协程在 `finally` 里 `pop` 自己的 job id。否则存活登记表永远返回"活着"，永久掩盖作业为 running，堵死重跑与 orphan 检测。
- **通用化**：存活登记表必须在 `finally` 清理，否则你自己的 orphan 检测器永远不会触发。
- **证据 `[impl · 口播后台 runner]`**：`tool_runner_services.py:477,326,1020-1032`。

### L2.5 进程重启即对账
- **规则**：把"进程重启"当作对账触发点——启动时扫出所有由崩溃 worker 遗留的 in-flight run，终结为 `failed`（带 `error_code`），再重建投影。
- **挡的事故**：后端重启后分析 run 永远"running"，UI 无限转、槽位永不释放。
- **通用化**：每个非持久的"running"记录必须在启动时被扫成终态。
- **证据 `[impl · 仅 media-analysis]`**：`context.py:105-106,186-224`（`reconcile_media_analysis_runs` → `failed` + `analysis_worker_lost` + `reconcile_projections`）。**当前只覆盖 media-analysis 的 active runs**，不是"所有持久 running"的通用对账；推广到全部为 `[proposed]`。

### L2.6 孤儿失败要级联写进嵌套子状态树
- **规则**：失败一个被中断的作业时，不只置顶层 `status=failed`，还要把**当前 segment、当前 step、嵌套子项**全部标失败并置 `orphaned/interrupted_at`，写用户可读的"请重跑"信息。
- **挡的事故**：父状态说 failed 但子 segment 还显示 running，UI 与恢复逻辑读到矛盾状态。
- **通用化**：失败一个中断作业时，把失败传播到每个在飞的子单元，别让任何后代还宣称"在推进"。
- **证据 `[impl]`**：`video_plan_execution_state_services.py:113-139`。

---

## L3 · 写入与读取的完整性

### L3.1 原子写：temp-then-rename ⚠️ 不是"一律"
- **规则（目标）**：关键状态**应**先写 `path + ".tmp"` 再 `replace(path)` 原子改名。
- **实际范围 `[impl · 仅口播 io_utils]`**：**只有** `koubo_storyboard/io_utils.py:20-24`（`write_json`）这么做。**通用 runner 并不原子写**——`tool_sessions/runner.py:38` 的 `_write_json` 仍是直接 `path.write_text(...)`。所以"一律原子写"是 `[proposed]`，当前部分实现。
- **通用化**：关键状态应写临时文件再原子改名——但要落实到**每个** runner，当前尚未。

### L3.2 防御性读：坏文件返回空默认 ⚠️ 不是"每个读"
- **规则（目标）**：磁盘状态读应把解析/OS 错误降级为安全默认。
- **实际范围 `[impl · 口播若干读取路径]`**：`io_utils.py:10-17`、`tts_selection_recovery.py:65-70`、`storyboard_plan_services.py:65-72` 会吞错返回 `{}`。**但并非所有读都如此**——通用 runner 与 TTS quick-adv 的部分读取会**直接抛解析异常**。故"每个读都返回空"是 `[proposed]`。

### L3.3 非零字节才算完成产物 `[impl · 仅 storyboard video-plan]`
- **规则**：产物存在性检查要求 `exists AND is_file AND st_size > 0`。
- **挡的事故**：被杀进程留下的 0 字节产物被当成"已完成"，下游状态机于是跳过重生成。
- **通用化**：文件存在 ≠ 文件完成；信任产物前要求非零大小。
- **证据 `[impl]`**：`video_plan_load_services.py:45-67`（**仅 storyboard video-plan 的完成检查**，非全平台）。

---

## L4 · 幂等是数据库不变量（这是"钱"的问题）

### L4.1 幂等交给 DB 唯一约束，不靠调用方自觉
- **规则**：每个昂贵/计费操作带确定性幂等键，落库用 `ON CONFLICT (idempotency_key) DO NOTHING`；由 DB 而非调用方决定"首次 vs 重复"，并暴露 `recorded` vs `deduped`。
- **挡的事故**：重跑/重试重复计费、重复烧 LLM/GPU/搜索；用量计量与现实发散。
- **通用化**：让幂等成为数据库不变量（唯一键 + 冲突忽略），并区分"真干了 vs 重放"。
- **证据 `[impl · 本地用量/计费去重]`**：`services/local_usage.py:89`（`ON CONFLICT DO NOTHING`）；`koubo/analysis_v1_artifact_billing.py:135,151`（recorded/deduped）；步骤幂等键 `runner.py:363-364`。**范围是本地用量与计费去重**，不等于"每个昂贵操作"都已有 DB 级幂等——推广到所有昂贵边界为 `[proposed]`。

### L4.2 付费非幂等边界禁止盲重试，从持久状态对账
- **规则**：调用付费且非幂等的外部边界（如创建视频 Interaction）时 `attempts=1`，**丢响应不重发**，改为从持久状态对账已发生的结果。
- **挡的事故**：provider 已扣费/已生成后网络超时，第二次 POST 触发二次 GPU 生成——双花 + 重复产物。
- **通用化**：非幂等付费调用禁用盲重试；模糊结果从持久状态对账，而非重发请求。
- **证据 `[impl]`**：`gemini_omni_video_services.py:472-474`（注释明说）。

### L4.3 同 key 不同 payload → 409；同 key 同 payload → replay
- **规则**：幂等键绑定到输入的 scope 摘要。同键不同载荷 = 冲突（409），同键同载荷 = 安全重放。
- **通用化**：幂等键必须绑输入摘要；同键异载荷是错误，同键同载荷才是重放。
- **证据 `[impl]`**：`video_interaction_repository.py:300-306`（`video_stateful_idempotency_conflict`）。

### L4.4 并发发布竞争由 DB 唯一插入裁决，输家删自己孤儿文件
- **规则**：多个生产者争相写同一输出时，用一次 DB 唯一插入裁决胜者；`inserted=False` 的输家必须删掉自己刚写的、无 DB 行指向的文件。
- **挡的事故**：并发 clip 请求留下无主的 `.mp4`，堆积工作区、混淆后续清理。
- **通用化**：用单一 DB 唯一性裁决并发生产者，并要求输家回滚它已写的副作用（文件）。
- **证据 `[impl]`**：`media_library_clips/processor.py:113-122`。

### L4.5 attempt 要可区分，但业务 operation key 在技术重试间应稳定
- **实际行为 `[impl]`**：OpenCrew 当前步骤键 = `{session}:{step}:{retry_count}`；force-rerun 自增 `retry_count`，子进程带 `--force-rerun`，并先 `_reset_tool_dir` 清空本步目录。这个键实际是 **attempt 级身份**。
- **挡的事故**：force-rerun 静默复用上次失败的中毒/半成品产物。
- **目标规则 `[proposed]`**：保留独立 `attempt_no` 区分每次尝试，但同一逻辑操作的自动技术重试必须沿用稳定 `operation_idempotency_key`；只有输入修订或用户明确要求重新计算才换 operation key。否则把 attempt 号直接交给外部付费接口，会把重试误当新业务操作。
- **通用化**：尝试身份和业务幂等身份是两件事；工作区按 attempt 隔离/重置，外部副作用按稳定 operation key 去重。
- **证据 `[impl]`**：`runner.py:363-364,243-244,455-456,771-779`（证明当前 attempt 级键与目录重置，不证明目标 operation key 已实现）。

### L4.6 复用边界与哈希对账 ⚠️ 已更正
> **更正说明（团队审核）**：本条早期版本称"只有 `prev.plan_hash == plan_hash` 才标 `reused`"——**与代码相反**，特此更正。

- **实际行为 [impl]**：run 边界前的步骤（`index < start_index`）**按用户选择的起始步骤位置无条件**标 `reused`；`plan_hash_match` 只是**附加的信息字段**（记录 `previous_state.plan.plan_hash == plan_hash`），**不参与**复用决策。也就是说：当前实现里"跳过哪些步骤"由用户选的重跑边界决定，而非由输入哈希是否相等自动决定。
- **证据 [impl]**：`router.py:4326-4333`——第 4329 行无条件 `status="reused"`，第 4333 行 `plan_hash_match` 仅为布尔标注。
- **目标规则 [proposed]**：复用**应当**门控在输入哈希相等——不变的活跳过，任何输入变更确定性失效。当前 `plan_hash_match` 已经把判断所需的信号算了出来，但尚未据此自动作废/强制重跑；把它从"信息字段"升级为"复用门控"是建议补齐项。
- **通用化教训**：区分"用户选择的重跑边界"与"输入哈希决定的自动失效"——目前口播实现是前者，规范目标是后者，二者不可混为一谈。

### L4.7 昂贵外部资源按 (provider + 输入哈希 + 目标模型) 去重
- **规则**：克隆音色绑定其创建时的 `target_model`，用 **(provider + `reference_audio_sha256` + target_model)** 复用既有 `voice_id`，不跨模型、不重复克隆。去重维度**含 provider**（不止哈希+模型）。
- **通用化**：昂贵外部资源按 (provider + 输入内容哈希 + 目标模型) 建键去重。
- **证据 `[impl]`**：`ToolLibrary/Analysis_V1/tts_quick_adv/voice_cloning.py:520-528`——按 `reference_audio_sha256` + `target_model` + `provider` 匹配既有 `voice_id`（520 单行只算哈希，匹配在 521-528）。

---

## L5 · 失败关闭 + 信任边界

### L5.1 缺必需可信输入 → fail closed，绝不回退默认/便宜路径
- **规则**：所选 TTS 音频缺失/不匹配/不可校验时，执行 **fail closed（409）**，绝不回退到默认/便宜音色。
- **挡的事故**：昂贵的视频生成用了**错误音色**（默认音而非客户克隆音）——高成本、错交付。
- **通用化**：必需的可信输入缺失时失败关闭，绝不悄悄降级到默认/更便宜的路径。
- **证据 `[contract-test]`+`[impl]`**：`test_koubo_video_plan_tts_consistency_contract.py:179-189`（`selected_tts_audio_missing`）；`tool_runner_services.py:372`（`--no-execute-audio`）、`tool_runner_services.py:428`（`prepare_video_plan_selected_tts_audio`）。

### L5.2 客户端回传的执行关键字段一律服务端重新推导
- **规则**：provider/model/voice 这类执行关键字段**从不信任浏览器**——服务端从可信存储（`tts_builder_candidates.json` / `Variables.json`）重新 rehydrate，剥离任何客户端携带的运行时字段，无法解析就 fail closed（409）。
- **挡的事故**：浏览器注入任意/伪造的 provider+model+voice，走未计量/未授权的 provider；或保存了一个执行期才会更晚失败的无效选择。
- **通用化**：绝不让客户端提供安全/执行关键字段；剥离并从可信服务端状态重新推导，保存时若无法解析就失败关闭。
- **证据 `[impl]`+`[contract-test]`**：`tts_selection_recovery.py:236,254-297`（注释："those fields must never be required from the browser"）；`test_koubo_video_plan_tts_consistency_contract.py:261-290`。

### L5.3 歧义不猜、非运营记录排除
- **规则**：从多源重建状态时，跳过 disabled/inactive/无 API key 的记录；只有唯一候选匹配才接受映射，多个匹配 ⇒ 拒绝（不猜）。
- **通用化**：重建状态时排除非运营条目，遇歧义拒绝而非任选。
- **证据 `[impl]`**：`tts_selection_recovery.py:35-42,194-200`。

### L5.4 不泄露内部路径/状态到用户面
- **规则**：`SessionOutput/...` 内部路径与下游状态**绝不**出现在面向客户的聊天/UI；用户可见的 prompt 文本是唯一"要说什么"的来源。
- **通用化**：内部文件路径/状态不得外泄到用户面。
- **证据 `[contract-test · 仅 TTS agent 用户面]`**：contract `test_koubo_tts_agent_real_generation_contract.py:173-188,340-353`（路径/状态脱敏断言）。**只覆盖 TTS agent 的用户面**，不足以推出全平台不泄露——推广为 `[proposed]`。

### L5.5 per-run 配置权威于全局默认，冲突 fail loud
- **规则**：后端**不得**把全局 DB 的 `--video-provider/--video-model` 注入 `05_06`；非空 CLI 参数若与 session Variables 不一致，**阻断执行**。
- **挡的事故**：全局默认悄悄覆盖每会话所选 provider/model，用错（可能更贵/不合规）的引擎生成。
- **通用化**：每会话配置权威于全局默认；注入参数与本次声明冲突时必须大声失败，不静默解析。
- **证据 `[impl]`**：`ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py:688-694`（`provider_selection` 里的运行时 provider/model 冲突检查——非空 override 与 Variables 默认不一致即 raise）。

### L5.6 子进程 env scrub + 路径 containment 断言
- **规则**：起工具子进程前按 allow-list 注入环境，屏蔽含 `SECRET/TOKEN/API_KEY/PASSWORD/DATABASE_URL` 的键，显式传敏感变量报错；任何递归删除/路径派生写前重解析并断言在 root 下。
- **挡的事故**：后端密钥泄进任意业务子进程（可能打日志）；构造的相对路径造成沙箱外删除/读写（路径穿越）。
- **通用化**：子进程只继承显式 allow-list；递归删除/路径写前断言 containment，宁可大声失败也不越界。
- **证据 `[impl]`**：`runner.py:182-204,117-133`（通用子进程 env scrub）、`:771-779`（`_reset_tool_dir` 目录 containment）；`io_utils.py:27-36`（`safe_workspace_rel`）。**已证实这几处**；但"全仓库所有递归删除/派生写都受控"尚未证明，属 `[proposed]`。

---

## L6 · 级联与投影一致性

> **范围警示**：L6.2–L6.5 的证据**主要是需求/设计文档 `[requirement]`，不是已上线代码或 contract**。它们描述"应当如此"，落地程度需以代码核实。L6.3 所说的"同步更新多个投影"当前**不是事务原子**。

### L6.1 重跑一个阶段要完整、协调地清整条下游产物集
- **规则**：重跑某阶段时，把该阶段的**整套下游产物**一起清：plan 目录 + plan 文件 + UI 缓存 + 执行 tool 目录 + 执行 **state** + 执行 **result**。
- **挡的事故**：新 plan 配上仍说"completed"的旧执行状态，binding-status 逻辑误报。
- **通用化**：重跑一个阶段要**完整、协调地**清空整条下游产物集，别留任何陈旧兄弟毒化新 run。（注：实现是**连续清理多个路径**，**不具备事务意义上的"原子"**——措辞已从"原子"改为"完整协调"。）
- **证据 `[impl]`**：`tool_runner_services.py:480-498,525-542,694-711`。

### L6.2 authoritative 上游重建 → cascade 作废所有派生完成标志
- **规则**：全量权威重建（`04_02_StoryBoard`）必须归档所有旧 Working 产物、并把下游 Plan/生成状态全部作废为 stale；新源**不得**从旧 Working 残留里"恢复"完成标志。
- **挡的事故**：重新生成分镜后，页面把旧对白错显为"已生成"，用户跳过真正的工作、发了陈旧媒体。
- **通用化**：权威上游产物的重生成必须级联作废所有派生状态；完成标志绝不能挺过一次源重建。
- **证据 `[requirement]`**：`docs/SessionDesign-R2/STORYBOARD_ASSET_HISTORY_REQUIREMENTS.md:18,78,126,155`（设计要求，非实现佐证）。

### L6.3 publish/delete 要同步所有镜像该事实的投影 + 派生帧
- **规则**：确认 Final 视频时，必须同步把 Final 路径写进**两份** storyboard JSON 并按同一 key 重抽 `TailFrame.png`；删除时清掉两处绑定并把状态"去绿"。
- **挡的事故**：两份 storyboard 文件与执行状态文件对"绑的是哪个视频"各执一词；尾帧抽自旧视频，下一段续帧就错了。
- **通用化**：publish/delete 必须**协调**更新每一个镜像该事实的投影（所有索引文件 + 派生帧），否则消费者读到互相矛盾的状态。（当前多文件+派生帧写入**非事务原子**。）
- **证据 `[requirement]`**：`docs/SessionDesign-R2/Analysis_V1_05_05_05_06_VideoOnlyPlan_工具需求整理.md:266-267,400,730`（设计要求）。

### L6.4 区分"内容变更"与"重组"
- **规则**：覆盖生成资产 = archive-then-replace（旧的进 `assets/history/` 带 manifest，留可回滚历史）；纯 Shot/Scene 重组 = 身份保持、**不**备份、**不**清 Working、**不**搬数据。
- **挡的事故**：覆盖永久毁掉旧媒体（无回滚）；或一次无害重组错误地抹掉仍有效的对白资产。
- **通用化**：区分"权威内容变更"（归档后替换、留历史）与"重组"（身份不变、不搬数据），绝不让改名/重组删除活内容。
- **证据 `[requirement]`**：`docs/SessionDesign-R2/STORYBOARD_ASSET_HISTORY_REQUIREMENTS.md:77-126`（设计要求）。

### L6.5 别让异步刷新覆盖未保存的用户意图
- **规则**：编辑页**禁止**后台定时轮询或静默刷新覆盖当前未保存的本地编辑；只在显式保存/返回权威结果的边界上对账。
- **挡的事故**：后台刷新冲掉用户正在进行的分镜编辑，丢工作。
- **通用化**：绝不让异步状态刷新覆盖未保存的用户意图；只在显式保存边界对账。
- **证据 `[requirement]`**：`docs/SessionDesign-R2/STORYBOARD_ASSET_HISTORY_REQUIREMENTS.md:344-353`（设计要求）。

---

## L7 · 这些教训回填到规范哪一章

本章的教训不是孤立的——它们应该被读进前面各章，很多本来就是那些章立场的"血证"：

| 教训簇 | 强化规范哪章 |
|---|---|
| L1 按需 binding + producer-input staleness | **高价值边界的一等不变量**，01 概念与 05 Artifact 引用；由 Runtime 自动计算，不泛化到所有文件 |
| L2 worker 死亡/中间态/重启对账 | [04 状态机](./04_VariablesAndState.md)（stale/orphan/sync-error）、[06 恢复](./06_Runtime_Observability.md) |
| L3 原子写/防御读/非零字节 | [05 工作区](./05_Workspace.md) 的产物完整性 |
| L4 幂等=DB不变量 / 付费边界 / 复用门控 | [03 工具契约](./03_ToolContract.md) 的幂等与成本、[06] 重试取向 |
| L5 fail-closed / 信任边界 / env scrub | [03 适配器](./03_ToolContract.md)、[06 门控求值] |
| L6 级联失效 / 投影一致性 | [02 计划确认](./02_ProcessDefinition.md)、[05 产物生命周期](./05_Workspace.md) |

> **一句话**：把 L0 的三条元原则用在真正高价值的边界——**关键并发不用时间戳、canonical Artifact 可追到 producer 输入摘要、持久“运行中”不可信且重启即对账**。边界之外让 Working/日志/Prompt/staging 保持轻量，才是可持续的工程纪律。
