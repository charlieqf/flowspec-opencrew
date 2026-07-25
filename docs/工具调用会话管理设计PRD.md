# 工具调用会话管理设计 PRD

版本：v0.4

状态：架构对齐修订草案。本版补齐 Tool Use Session 与 Attempt / Plan Runner 的关系、执行信任边界、DB 写回、运行目录隔离、Variables 更新治理、工具状态持久化、文件可见性、媒体快照规则、本机模型调用 broker、Tool Registry 字段合并、依赖 token 人工归一化、历史运行保留 / GC 和 schema_version 统一规则；具体工具迁移计划仍需后续补充。

## 1. 背景

当前 OpenCrew 的 OC-Analysis 和 Tool Library 已经具备较完整的工具链，但在通过 Codex / OpenCode 自动调用时，工具执行上下文、数据库访问、OpenCode Session 绑定、全局变量、产物命名和页面绑定还没有形成统一合同。

因此，需要在现有 Task / Session / OpenCode / Workspace 边界之上，引入一套面向工具链执行的标准会话机制，让每次工具调用链都具备清晰的上下文、稳定的变量来源、可复现的文件输入输出和可审计的大模型调用记录。

## 2. 当前痛点

### 2.1 Codex 沙盒中的数据库访问不稳定

Codex 在沙盒环境中运行工具时，经常需要额外数据库访问授权。数据库访问涉及本地网络、数据库连接、环境变量、凭据和进程权限，任一环节不稳定都会阻断自动化执行。

当前问题：

1. 后续工具可能在任意步骤自行访问数据库。
2. 每个工具对数据库连接的依赖不透明。
3. Codex 沙盒授权可能在工具链中途打断运行。
4. 数据库访问失败时，无法清晰区分是业务数据缺失、数据库连接失败，还是沙盒权限问题。

### 2.2 其它沙盒授权内容会反复阻断工具链

除了数据库访问，工具链中还存在其它可能触发沙盒授权或运行阻断的内容，例如：

1. 读取 workspace 外部媒体文件。
2. 访问本地服务。
3. 调用 OpenCode Session。
4. 调用需要额外权限的本地命令。
5. 读取或写入不在当前 workspace 下的文件。
6. 长任务中途访问外部路径或外部状态。

这些授权点如果分散在多个工具中，会导致自动化运行无法稳定持续。

### 2.3 工具实现方式不标准

Tool Library 中每个工具当前对输入、变量、配置和输出的处理方式不完全一致。

典型问题：

1. 有的工具从命令行参数读取变量。
2. 有的工具从数据库读取变量。
3. 有的工具从 workspace 文件推断变量。
4. 有的工具依赖固定目录结构。
5. 有的工具输出 `*_result.json`，但字段结构不统一。
6. 有的工具把中间产物、全局状态和最终交付物混在一起。

这会导致 Codex / OpenCode 在编排工具时，需要猜测每个工具的真实输入和输出。

### 2.4 上下游工具交接混乱

当前工具之间缺少统一交接合同。

典型问题：

1. 上一步输出哪些文件、下一步必须读取哪些文件，不总是由机器可读合同描述。
2. 某些工具通过约定目录寻找上游产物。
3. 某些工具通过文件是否存在来推断状态。
4. 某些工具会读取历史残留文件，导致 attempt 与文件不匹配。
5. 缺少统一的全局变量更新机制。

结果是工具链容易出现“某一步能跑，但下一步不知道应该接什么”的问题。

### 2.5 页面绑定元素混乱

前端页面当前会同时依赖 task API、attempt、manifest、workspace 文件扫描和 legacy fallback。

典型问题：

1. 页面有时通过 `schemes/<scheme>/manifest.json` 展示卡片。
2. 页面有时 fallback 到旧的 storyboard manifest。
3. 页面有时通过目录中的 mp4 文件数量推断卡片。
4. stale 文件可能影响页面展示。
5. 页面难以确认当前显示结果属于哪个 attempt。

因此，工具产物需要形成更稳定的页面绑定合同。

### 2.6 大模型调用与提示词产生逻辑不透明

当前大模型调用存在透明度不足的问题。

典型问题：

1. 提示词可能只存在 OpenCode 消息中。
2. 最终发送给模型的 system prompt、user prompt、上下文变量和文件输入缺少稳定落盘记录。
3. 工具内部生成提示词的逻辑不总是可追踪。
4. 调用了哪个模型、使用了哪个 OpenCode Session、绑定了哪个 Task / Attempt，不总是能从产物中直接复原。
5. VLM / LLM 高成本步骤的输入输出、缓存和 resume 状态缺少统一审计格式。

这会影响后续复盘、调试、质量控制和成本治理。

## 3. 设计目标

本设计的目标是建立一个新的工具调用执行合同：

```text
第 0 步集中访问数据库和 OpenCode
  -> 生成工具调用上下文
  -> 写入本次 Tool Use Session 运行根目录
  -> 后续工具只读写 Workspace 文件
  -> 工具之间通过标准变量文件和标准产物交接
  -> 汇总 SessionOutput / result index
  -> 编排层把状态、事件、文件索引和 result index 写回 DB
```

核心目标：

1. 把数据库访问集中到第 0 步，后续工具默认不再访问数据库。
2. 把 OpenCode Session 访问、Task / Session 解析、模型配置读取、workspace 定位集中到第 0 步。
3. 每次工具集调用都有独立的工具调用会话身份。
4. 工具链中所有全局变量都有统一文件来源。
5. 全局媒体文件有统一落点和命名规范。
6. 明确 Workspace 只是派生快照和产物区，DB 仍是 Task / Session / Attempt / Plan 的主状态。
7. 为后续提示词透明化、工具标准化、页面绑定和 Plan Runner 执行闭环打基础。

## 4. 核心概念：Tool Use Session

每一次由 OpenCode / Codex 发起的工具集调用，定义为一个 `Tool Use Session`。

`Tool Use Session` 是 OpenCrew 内部的工具链执行上下文，用来表示“一次完整工具调用链”的运行边界。它和 OpenCode Session 不是同一个概念：

- `Tool Use Session`：OpenCrew / Tool Library 层的工具链执行会话。
- `OpenCode Session`：模型对话、Agent 上下文和模型调用会话。
- `OpenCrew Session`：客户状态页、事件流、文件列表和业务 Task 的通用容器。

约束：

1. 每个 `Tool Use Session` 必须绑定一个 OpenCrew Task、一个 OpenCrew Session、一个 Attempt，以及该 Task 当前绑定的 OpenCode Session。
2. 每个 `Tool Use Session` 必须使用 OpenCrew Session 的 Workspace，但不得把工具目录直接散落在 Workspace 根目录。
3. 每个 `Tool Use Session` 必须拥有独立运行根目录：

```text
<workspace>/tool_use_sessions/<tool_use_session_id>/
```

4. 当前推荐关系是：

```text
OpenCrew Task 1 : 1 OpenCode Session
OpenCrew Task 1 : N Attempt
Attempt 1 : 1 Tool Use Session
Tool Use Session N : 1 Workspace
```

5. 如果未来一个 Attempt 需要拆成多个可恢复执行批次，应引入 `workflow_plan_run_id` 或 `tool_use_session_id` 与 Attempt 形成 N:1 映射，但必须在 DB 中显式记录。
6. `Tool Use Session` 不直接替代现有 OpenCrew Session，也不替代 OpenCode Session；它是工具调用层的执行边界。

## 4.1 与 Attempt / Plan Runner 的关系

`Tool Use Session` 不是新的业务主状态表。它必须接入既有 Attempt / Plan Runner 合同：

1. 用户点击 Run / Rerun 时，后端先创建新的 Attempt。
2. Plan Runner 读取已确认的 workflow plan，创建或绑定本次 `Tool Use Session`。
3. Plan Runner 驱动 `S0 -> S1 -> ...` 的 prepare / run / finalize。
4. 每个 step 的状态、开始时间、结束时间、错误摘要、result paths 和幂等键必须由 Plan Runner 持久化。
5. Tool 输出的 result JSON 是 step 结果输入，不是 Attempt 主状态。
6. Attempt 的 `result_index_json` 或兼容字段由 Plan Runner / 最终汇总步骤生成并写回 DB。
7. Debug Console 和恢复逻辑应读取 DB 中的 attempt / plan run / step 状态，再按需读取 workspace artifact。

最低实现可以暂时把 step 状态放在 `workflow_plans.plan_json.execution_state` 中，但 schema 必须稳定，并预留迁移到 `workflow_plan_runs` / `workflow_plan_steps` 表。

## 5. 第 0 步：PrepareSessionVariables

所有工具集调用必须从第 0 步开始：

```text
00_PrepareSessionVariables
```

职责：

1. 读取 OpenCrew Task 主状态。
2. 读取 OpenCrew Session 主状态。
3. 解析并确认绑定的 OpenCode Session。
4. 确认 Workspace，并创建本次工具链使用的 `tool_session_root`。
5. 读取当前 Prompt Version、Runtime / Skill Version、Attempt、Run Model、Prompt Model 等必要指针。
6. 读取或复制全局媒体文件，例如源视频、音频、图片、PDF、Word 文档等。
7. 将后续工具所需的全局变量写入 `tool_session_root/0_SessionContext`。
8. 将后续工具所需的全局媒体文件统一写入 `tool_session_root/0_SessionContext`。
9. 写入本次 Tool Use Session 的输入快照清单、文件 checksum 和敏感性标记。
10. 向 DB 写入 attempt / plan run / step started 事件，保证 Debug Console 可见。

关键原则：

1. 数据库访问只允许在第 0 步集中完成。
2. OpenCode Session 查询和绑定只允许在第 0 步集中完成。
3. 第 0 步完成后，后续工具默认不得再访问数据库。
4. 第 0 步完成后，后续工具默认不得自行重新查找 OpenCode Session。
5. 后续工具只通过 `0_SessionContext` 获取全局变量和全局文件。
6. 第 0 步生成的是派生输入快照，不改变 DB 主状态的权威性。
7. 第 0 步失败时必须写入可诊断的 DB event，不得只在 stdout 或 workspace 文件中留下错误。

### 5.1 执行位置与信任边界

推荐执行边界：

```text
受信任后端 / Plan Runner：
  - 创建 Attempt / Plan Run
  - 运行 00_PrepareSessionVariables
  - 读取 DB / OpenCode / provider 配置
  - 写 session_events / session_files / attempt result index
  - 合并 Variables patch

沙盒工具进程：
  - 只读取 tool_session_root 下的输入快照
  - 只写本工具目录下 Working / Output / Report / Prompt
  - 默认不访问 DB、OpenCode 管理 API、provider key、workspace 外文件
```

Phase 0 中，provider key 应存放在盒子本地密钥库：`0600` 加密文件 + 设备密钥 / Secure Enclave 包裹的加密密钥，由 launchd 守护进程启动时解包读取。数据库只保存 `key_ref` / `has_key` / provider 配置，`api_key_ciphertext` 仅作为迁移输入；不得把真实 key 写入 Tool 的输入文件、stdout、Prompt 审计或 Debug payload。不得把登录态系统密钥串作为 headless LaunchDaemon 的必需解锁机制，因为守护进程无法可靠依赖用户登录态。

需要模型调用的沙盒工具不得直接持有 provider key。它们必须通过本机模型调用 broker / resolver 完成调用：

```text
Tool
  -> 本机 broker/resolver（localhost API、CLI wrapper 或 OpenCode provider 代理）
  -> Phase 0：从盒子本地密钥库解析 auth_ref 并注入凭据，写 local_usage_log
  -> Phase 1+：转发 Managed Gateway，写 gateway_request_id / usage_ledger
```

Tool 只接收 `model_id`、`provider_mode`、`billing_mode`、`request_id`、非 secret 调用参数和返回结果；不得接收 `auth_ref`、真实 key、Authorization header 或 provider cookie。`03/06/14/17/02_audio_asr` 等现有直连 DB / 直连 provider 的工具迁移时，真正要改的是：DB / provider config 读取上移到第 0 步或受信任 broker，模型调用改为走 broker/resolver。

Phase 1+ 切到 Managed Gateway 后，Tool 仍只拿到 request id、model id、provider mode 和可审计的非 secret 配置。

### 5.2 DB 写回合同

Workspace 文件不是主状态。Plan Runner 或最终汇总步骤必须把以下内容写回 DB：

1. Attempt 状态：`running`、`completed`、`failed`、`cancelled` 等。
2. Step 状态：step id、tool id、status、started_at、finished_at、retry count、idempotency key、error summary。
3. `session_events`：关键生命周期、依赖阻断、模型调用、工具调用、错误和恢复事件。
4. `session_files`：所有需要下载、展示、归档或调试索引的 workspace 文件。
5. Attempt result index：指向 `SessionOutput/manifests/*` 或等价 result index 文件。
6. 运行摘要和 metrics：耗时、模型调用数、token / 字节 / 分段数量等可索引指标。

DB 写回失败时，本次 Tool Use Session 不应被标记为完全成功；至少应进入 `completed_with_sync_error` 或等价可恢复状态，并允许后续执行 index / event / file sync 修复。

## 6. 0_SessionContext 目录规范

第 0 步必须在当前 OpenCrew Session Workspace 下创建本次运行根目录：

```text
<workspace>/tool_use_sessions/<tool_use_session_id>/
```

并在运行根目录下创建：

```text
<workspace>/tool_use_sessions/<tool_use_session_id>/0_SessionContext/
```

`0_SessionContext` 是本次 `Tool Use Session` 的全局上下文目录。

职责：

1. 保存本次工具链执行需要的全局变量。
2. 保存本次工具链执行需要的全局媒体文件。
3. 保存 OpenCrew Task / Session / OpenCode Session 的必要引用。
4. 保存当前模型、版本、attempt、workflow、tool plan 等必要指针。
5. 接收后续工具新增或更新的全局变量。

目录约束：

1. `0_SessionContext` 下不再创建子目录。
2. 所有文件平铺存放。
3. 文件类型和语义通过文件名前缀区分。
4. 后续工具不得把临时业务产物混入 `0_SessionContext`，除非该产物确实是全局变量或全局文件。

示例：

```text
tool_use_sessions/<tool_use_session_id>/0_SessionContext/
  Variables.json
  InputManifest.json
  Video_Source.mp4
  Audio_Source.wav
  Image_Reference_001.png
  Pdf_Brief.pdf
  Word_Brief.docx
  Prompt_FinalPrompt.txt
```

## 7. Variables 与全局媒体文件规范

### 7.1 Variables 文件

`0_SessionContext` 下必须包含一个变量文件：

```text
tool_use_sessions/<tool_use_session_id>/0_SessionContext/Variables.json
```

用途：

1. 保存单变量。
2. 保存数组。
3. 保存对象。
4. 保存当前工具链执行需要的全局指针。
5. 保存后续工具新增的全局状态。

建议字段：

```json
{
  "schema_version": "1.0",
  "tool_use_session_id": "",
  "workflow_id": "",
  "task_id": null,
  "opencrew_session_id": null,
  "opencode_session_id": "",
  "workspace_dir": "",
  "tool_session_root": "tool_use_sessions/<tool_use_session_id>",
  "current_attempt_id": null,
  "attempt_no": null,
  "workflow_plan_id": null,
  "workflow_plan_run_id": null,
  "current_prompt_version_id": null,
  "current_runtime_version_id": null,
  "prompt_model_provider": "",
  "prompt_model_id": "",
  "run_model_provider": "",
  "run_model_id": "",
  "provider_mode": "local_box",
  "billing_mode": "local_usage_only",
  "source_video_path": "0_SessionContext/Video_Source.mp4",
  "clip_mode": "",
  "selected_scheme": "",
  "created_at": "",
  "updated_at": ""
}
```

规则：

1. 后续工具读取全局变量时，优先读取 `Variables.json`。
2. 后续工具原则上不得直接改写 `Variables.json`；应输出 `SessionContextPatch_*.json` 或在标准 tool result JSON 中声明需要更新的字段，由 Plan Runner 校验 ownership 后合并。
3. 若短期兼容旧工具必须直接写 `Variables.json`，必须使用临时文件 + fsync + atomic rename，并持有运行级文件锁。
4. 对大型内容、媒体内容、长文本 Prompt，不应直接塞入 `Variables.json`；应写成独立文件，并在 `Variables.json` 中保存相对路径。
5. `Variables.json` 必须有 `schema_version`，Plan Runner 在每个 step 前校验必需字段和字段类型。
6. Tool 只能写入 registry 中声明 ownership 的字段，不得覆盖其它 Tool 或 Runner 拥有的字段。
7. 本 PRD 中所有机器可读 JSON 的 `schema_version` 统一使用字符串语义版本，例如 `"1.0"`，与现有 `ToolLibrary/Analysis/tool_registry.json` 保持一致；不得混用整数 `1` 和字符串 `"1.0"`。

### 7.1.1 输入快照清单

`0_SessionContext` 下必须包含输入快照清单：

```text
0_SessionContext/InputManifest.json
```

最低字段：

```json
{
  "schema_version": "1.0",
  "tool_use_session_id": "",
  "attempt_id": null,
  "files": [
    {
      "path": "0_SessionContext/Video_Source.mp4",
      "source_kind": "uploaded_file",
      "source_ref": "",
      "sha256": "",
      "size": 0,
      "visibility": "internal",
      "sensitivity": "normal"
    }
  ]
}
```

用途：

1. 证明本次运行到底使用了哪些输入文件。
2. 防止旧 attempt 或 stale 文件混入新运行。
3. 支撑大文件去重、完整性校验和后续 `session_files` 写回。
4. 明确哪些文件可以进入客户下载、哪些只能进入 Debug Console。

### 7.2 全局媒体文件

如果全局变量是媒体文件或文档文件，应复制或标准化引用到 `0_SessionContext`。

文件命名通过前缀区分：

```text
Video_*.mp4
Audio_*.wav
Image_*.png
Image_*.jpg
Pdf_*.pdf
Word_*.docx
Prompt_*.txt
ModelCall_*.json
```

示例：

```text
Video_Source.mp4
Video_Reference.mp4
Audio_Source.wav
Image_Product_001.png
Image_Keyframe_001.jpg
Pdf_ClientBrief.pdf
Word_ClientBrief.docx
Prompt_FinalPrompt.txt
Prompt_SystemPrompt.txt
```

规则：

1. 全局源视频优先命名为 `Video_Source.mp4`。
2. 全局源音频优先命名为 `Audio_Source.wav`。
3. 全局图片按用途和序号命名，例如 `Image_Reference_001.png`。
4. 全局文档按类型命名，例如 `Pdf_Brief.pdf`、`Word_Brief.docx`。
5. 文件必须能通过 Workspace 相对路径被后续工具引用。
6. 后续工具不得依赖原始绝对路径作为唯一输入。
7. 大媒体文件可以通过 workspace 内 canonical path + checksum 引用，避免每次 Run 盲复制；但不得引用 workspace 外路径。
8. 如果使用 hardlink / symlink / reflink 优化存储，必须在文件 API 和工具访问层做 realpath 边界校验，禁止指向 workspace 外部。
9. 每个输入文件必须出现在 `InputManifest.json` 中，并带 size、sha256、visibility、sensitivity。

## 8. 后续待设计

以下内容将在后续版本继续补充：

1. 工具标准参数规范的最终 schema。
2. 工具 `--print-json` 输出规范的完整字段集。
3. 每个工具的 step result 文件规范和 DB step 状态映射。
4. 上下游工具交接规则的自动校验脚本。
5. ModelCall 请求 / 响应审计文件与本地 usage / 未来网关 request id 的对账规则。
6. 页面绑定 result index / manifest 的业务 schema。
7. 迁移现有 OC-Analysis 01-17 工具的实施计划。

## 9. 单工具实现标准

本章定义每一个独立 Tool 的实现合同。这里的 Tool 指 Tool Library 中可被 Tool Use Session 调用的单个工具。

每个 Tool 必须满足：

1. 独立可运行。
2. 可查询当前运行状态。
3. 可从失败处断点续跑。
4. 可强制回到初始状态并重新运行。
5. 可自检依赖并给出缺失项和解决办法。
6. 可声明自己读写哪些 Session Context 变量。
7. 可声明自己消费哪些上游 Output。
8. 可声明自己产出哪些下游 Output、Report 和 Prompt 审计文件。

工具不应依赖调用方猜测它的状态、输入、输出和恢复方式。

## 10. 单工具状态管理标准

每个 Tool 必须管理自己的运行状态。

推荐状态：

```text
not_started
preparing
ready
running
partial
completed
failed
dirty
reset
```

状态语义：

1. `not_started`：工具目录尚未创建，或没有任何有效运行状态。
2. `preparing`：正在检查依赖、复制上游产物、准备本工具 Working 输入快照。
3. `ready`：依赖已满足，Working 输入快照已准备完成，可以正式运行。
4. `running`：工具正在运行。
5. `partial`：工具已经产出部分有效中间状态，可尝试断点续跑。
6. `completed`：工具已完成，Output 可供下游消费。
7. `failed`：工具运行失败，但 Working 中可能保留断点恢复所需状态。
8. `dirty`：工具目录或产物处于不可信状态，不能直接给下游消费。
9. `reset`：工具已清理回初始状态，可以重新 prepare / run。

每个 Tool 必须能回答三个问题：

1. 当前是否可以继续运行。
2. 当前是否必须强制 Rerun。
3. 当前 Output 是否可信并可交给下游使用。

每个 Tool 目录下必须有机器可读状态文件：

```text
State.json
```

最低字段：

```json
{
  "schema_version": "1.0",
  "tool_use_session_id": "",
  "attempt_id": null,
  "workflow_plan_run_id": null,
  "step_index": 1,
  "step_id": "",
  "tool_id": "",
  "tool_name": "",
  "status": "not_started",
  "started_at": "",
  "updated_at": "",
  "finished_at": "",
  "heartbeat_at": "",
  "retry_count": 0,
  "idempotency_key": "",
  "input_snapshot_hash": "",
  "output_manifest_path": "Output/OutputManifest.json",
  "error_summary": null
}
```

状态更新规则：

1. Tool 可以写本地 `State.json`，但 Plan Runner 仍是 DB step 状态的权威写入者。
2. `running` 状态必须更新 `heartbeat_at`；长任务 heartbeat 超时后，Runner 可以标记为 `failed` 或 `stale_running`。
3. `completed` 必须同时满足：状态文件完成、tool result JSON 成功、`Output/OutputManifest.json` 存在且可校验。
4. `failed` 必须包含错误分类、可读摘要和是否可 resume。
5. `dirty` 必须阻止下游消费，除非用户显式 force rerun 或 Runner 完成修复。

## 11. 断点续跑与强制 Rerun

### 11.1 断点续跑

当 Tool 失败时，如果 Working 中已有可用中间状态，Tool 应支持从失败处继续运行。

断点续跑要求：

1. Tool 必须在 Working 中保存进度状态。
2. Tool 必须能识别哪些子步骤已完成。
3. Tool 必须能跳过已完成且可信的子步骤。
4. Tool 必须能在修复缺失变量、缺失文件、模型错误或外部调用错误后继续完成。
5. Tool 必须在续跑前重新执行依赖自检。

断点续跑适用于：

1. 分批处理。
2. 长视频处理。
3. 多段 VLM / LLM 调用。
4. 可缓存的媒体处理。
5. 已生成部分可靠中间结果的工具。

### 11.2 强制 Rerun

当用户选择强制 Rerun，或 Tool 判断当前状态为 dirty 时，Tool 必须能恢复到本工具运行初始状态。

强制 Rerun 要求：

1. 清理本工具目录下不可信的 Working、Output、Report、Prompt 文件。
2. 重新执行 prepare。
3. 重新复制上游依赖到本工具 Working。
4. 重新生成本工具状态文件。
5. 重新运行工具逻辑。
6. 不复用 dirty 状态中的 Output。

强制 Rerun 不应删除：

1. 上游工具的 Output。
2. `0_SessionContext` 中非本工具声明写入的变量。
3. 其它工具目录。

强制 Rerun 的目标是让本工具回到“从未运行过”的干净状态，而不是清空整个 Tool Use Session。

强制 Rerun 必须生成新的 idempotency key 或 bump retry attempt，避免高成本模型调用在 resume / retry 中被误判为同一次已完成调用。对于 LLM / VLM / 付费外部服务步骤，如果已有成功 ModelCall 审计和 Output，默认不得重复调用，除非用户显式选择 force rerun。

## 12. 依赖自检标准

每个 Tool 正式运行前必须先执行依赖自检。

依赖自检必须检查：

1. 必需的 Session Context 变量是否存在。
2. 必需的 Session Context 文件是否存在。
3. 必需的上游 Tool Output 是否存在。
4. 上游 Output 是否处于 completed / usable 状态，且存在可校验的 `OutputManifest.json`。
5. 本工具 Working 输入快照是否已准备。
6. 必需的模型配置是否存在。
7. 必需的本地二进制或运行环境是否可用。

当依赖不满足时，Tool 必须输出可操作的错误信息：

```json
{
  "status": "blocked",
  "missing_dependencies": [
    {
      "kind": "upstream_output",
      "required_from": "S1_01_video_metadata_extractor",
      "required_path": "Output/video_metadata.json",
      "suggested_action": "先完成 S1_01_video_metadata_extractor，或强制 rerun 上游工具。"
    }
  ]
}
```

依赖不满足时，Tool 不应进入正式 run 阶段。

## 13. 工具目录命名规范

每个 Tool 在本次 `tool_session_root` 下创建自己的工具目录。

目录命名格式：

```text
S{step_index}_{tool_name}/
```

含义：

1. `S{step_index}` 表示本次 Tool Use Session 规划中的步骤序号。
2. `{tool_name}` 直接使用原工具名称。
3. 原工具名称可以包含工具编号，例如 `01_video_metadata_extractor`。

示例：

```text
S0_00_PrepareSessionVariables/
S1_01_video_metadata_extractor/
S2_02_audio_asr_pipeline/
S3_04_pyscenedetect_runner/
S4_03_semantic_llm_structure_builder/
```

注意：

1. `S1` 是本次 Tool Use Session 的运行顺序，不等于工具库中的工具编号。
2. 工具库编号仍保留在 `{tool_name}` 中。
3. 同一个工具在不同 Tool Use Session 中可能有不同的 `S{step_index}`。

## 14. 单工具目录结构规范

每个 Tool 目录下必须包含以下一层目录：

```text
S1_01_video_metadata_extractor/
  State.json
  Working/
  Output/
  Report/
  Prompt/
```

目录职责：

1. `Working/`：本工具运行中间状态、输入快照、缓存、断点恢复文件。
2. `Output/`：本工具交给下游消费的最终产物。
3. `Report/`：本工具给人查看的报告、校验结果、HTML 页面、可视化结果。
4. `Prompt/`：本工具大模型调用相关的模板、变量、渲染结果、参考文档和说明页面。

强制约束：

1. `Working/`、`Output/`、`Report/`、`Prompt/` 都必须是一层目录。
2. `0_SessionContext` 必须保持平铺；单个 Tool 的 `Working/`、`Output/`、`Report/`、`Prompt/` 允许创建受控子目录。
3. 子目录必须由 tool registry 声明，且必须出现在对应 manifest 中，不能通过任意目录扫描作为状态判断依据。
4. 需要分类时，优先通过 manifest + 稳定目录名表达；少量简单文件可以通过文件名前缀、工具名、用途名、编号区分。

示例：

```text
Working/InputFrom_S1_video_metadata.json
Working/State_progress.json
Working/Cache_batch_001.json
Output/asr_segments.json
Output/asr_sentence_timeline.json
Report/QA_asr_quality.html
Prompt/PromptTemplate_semantic_split.md
```

每个 Tool 的 `Output/` 必须包含：

```text
Output/OutputManifest.json
```

`OutputManifest.json` 用于声明本工具可交给下游消费的文件、文件类型、schema、checksum、visibility 和 sensitivity。下游 Tool 的 prepare 阶段必须读取 manifest，而不是扫描目录猜测产物。

## 15. 工具输入隔离与依赖快照

每个 Tool 运行时，除访问 `0_SessionContext` 外，不应跨工具文件夹直接读取文件。

禁止模式：

```text
S3_CurrentTool 运行中途直接读取 S1_PreviousTool/Output/a.json
```

允许模式：

```text
prepare 阶段读取 S1_PreviousTool/Output/a.json
  -> 拷贝到 S3_CurrentTool/Working/InputFrom_S1_a.json
  -> run 阶段只读取 S3_CurrentTool/Working/InputFrom_S1_a.json
```

每个 Tool 的执行流程必须拆成：

```text
prepare -> run -> finalize
```

### 15.1 prepare 阶段

prepare 阶段负责：

1. 检查依赖是否满足。
2. 从上游 Output 复制本工具需要的依赖文件。
3. 从 `0_SessionContext` 复制或读取本工具需要的全局变量和全局文件。
4. 将依赖文件快照写入本工具 `Working/`。
5. 写入本工具状态文件。
6. 判断是否具备 run 条件。

### 15.2 run 阶段

run 阶段负责：

1. 只读取本工具 `Working/` 中已准备好的输入快照。
2. 只读取本工具 `Prompt/` 中已准备好的提示词模板、变量和渲染结果。
3. 只读取必要的 `0_SessionContext/Variables.json`。
4. 不跨工具目录读取上游文件。
5. 不读取 workspace 外部文件。

### 15.3 finalize 阶段

finalize 阶段负责：

1. 将可供下游消费的最终产物写入 `Output/`。
2. 将人工可读报告、校验结果、可视化页面写入 `Report/`。
3. 将允许写入的全局变量以 patch 形式交给 Plan Runner 合并到 `0_SessionContext/Variables.json`。
4. 写入本工具最终状态。
5. 输出标准 tool result JSON。
6. 生成或刷新 `Output/OutputManifest.json`。

## 16. Session Context 写入约束

每个 Tool 必须严格声明自己允许写入 `0_SessionContext/Variables.json` 的变量。

原则：

1. 没有必要，绝对不写入全局变量。
2. 能作为本工具局部状态的变量，必须写入本工具 Working。
3. 只有会被多个后续工具复用的变量，才允许写入 Session Context。
4. Tool 不直接覆盖全局变量；Tool 输出 patch，Plan Runner 合并。
5. Tool 不得覆盖其它工具声明拥有的全局变量。
6. 变量合并失败时，当前 step 必须失败或进入 blocked，不得静默忽略。

每个 Tool 的 registry 或 tool spec 中应声明：

```json
{
  "reads_session_context": [
    "task_id",
    "opencode_session_id",
    "run_model_id"
  ],
  "writes_session_context": [
    "source_video_path"
  ],
  "consumes_outputs": [
    "S1_01_video_metadata_extractor/Output/video_metadata.json"
  ],
  "produces_outputs": [
    "Output/asr_segments.json",
    "Output/asr_sentence_timeline.json"
  ],
  "context_patch_schema": {
    "type": "object",
    "properties": {
      "source_video_path": { "type": "string" }
    }
  }
}
```

### 16.1 与现有 Tool Registry 的字段合并

当前 `ToolLibrary/Analysis/tool_registry.json` 已有 `schema_version: "1.0"` 和约 21 个工具条目，不能另起一套 registry。本文新增字段应通过扩展现有 registry 或生成兼容视图落地。

现有字段与目标字段的映射：

| 现有字段 | 目标字段 / 用途 | 迁移规则 |
| --- | --- | --- |
| `id` | `tool_id` / `step.tool_id` | 保留原值，作为 registry 主键。 |
| `name` | `tool_name` | 保留原值；目录名可继续使用脚本文件名或规范化 name。 |
| `script` | `script` | 保留；执行器负责解析 repo-relative 路径。 |
| `stage` | `stage` / planning metadata | 保留，用于 Plan Runner 分组和 UI 展示。 |
| `required_by_default` | planning metadata | 保留，不作为依赖自检条件。 |
| `cost_level` | `cost_level` / `confirmation_requirement` | `high`、`very_high` 默认需要显式确认，除非 workflow 配置覆盖。 |
| `uses_llm` / `uses_vlm` | `model_requirements` | 为 true 时必须声明通过本机 broker/resolver 调用模型。 |
| `supports_resume` | `supports_resume` | 保留，并要求实现 `State.json` / heartbeat / idempotency key。 |
| `supports_progress_log` | `progress_contract` | 为 true 时必须写 progress 或 heartbeat，并进入 DB step event。 |
| `hard_dependencies` | `consumes_outputs` / `reads_session_context` / `runtime_dependencies` / `manual_dependencies` | 只有规范 token 可自动映射；自由文本 token 必须人工归一化，不能由 adapter 猜。 |
| `soft_dependencies` | `optional_dependencies` / `manual_dependencies` | 不满足时可降级或 warning，不能静默改变输出语义；自由文本同样必须人工归一化。 |
| `main_outputs` | `produces_outputs` | 迁移期可保留旧 workspace path；新合同中应映射到本工具 `Output/` 或 `SessionOutput/` manifest。 |
| `run_when` / `skip_when` | planning metadata | 保留给 WorkflowAssistant 规划，不作为执行期真实状态判断。 |
| `estimated_runtime` | planning metadata / timeout hint | 可作为 timeout、用户确认和成本提示输入。 |
| `agent_notes` | planning metadata | 仅给 Agent / UI 参考，不作为机器依赖合同。 |

迁移约束：

1. `reads_session_context`、`writes_session_context`、`consumes_outputs`、`produces_outputs`、`context_patch_schema` 可以作为现有 tool entry 的新增字段。
2. 迁移期允许由 adapter 从旧字段推导新字段，但只允许推导规范 token；推导结果必须落入 Plan Runner 可见的 normalized registry。
3. Plan Runner 只执行 normalized registry 中声明的脚本、依赖和输出，不依赖自然语言 `agent_notes` 猜测。
4. schema 校验脚本必须同时检查旧字段兼容性和新字段完整性，避免 21 个现有工具一次性手改出错。
5. 对 adapter 无法归一化的 token，registry validation 必须失败，并列出 tool id、原 token、建议的 dependency kind 和待补字段。

#### 16.1.1 依赖 token 归一化规则

现有 `hard_dependencies` / `soft_dependencies` 混合了工具编号、Session Context、运行环境、provider 配置和自由文本。adapter 只能自动处理以下规范 token：

1. 工具编号：匹配 `01`、`02`、`05_1`、`13_01` 等已存在 tool id 的 token，映射为 `consumes_outputs`，并要求人工或 manifest 指定具体输出文件。
2. 已知 Session Context：例如 `source_video`、`source_video_or_audio`、`task_id`、`opencode_image_model`，映射为 `reads_session_context`。
3. 已知 runtime dependency：例如 `ffmpeg`、`demucs`、`paddleocr`、`rapidocr`、`easyocr`、`tesseract`、`gpu_or_mps_for_speed`，映射为 `runtime_dependencies`。
4. 已知 provider / config dependency：例如 `OPENCREW_DATABASE_URL`、`tool_asr_provider_configs` 在新合同中不能继续给沙盒工具直读，应映射为 broker / resolver 或第 0 步准备出的非 secret provider config。

以下自由文本 token 不能机械推导，必须人工归一化：

```text
13 detail scheme
14 detail segment descriptions
target balanced_or_summary
task_opencode_session_or_fallback
human_scene_transition_overrides
user_recomposition_instruction
```

建议归一化字段形态：

```json
{
  "source_token": "13 detail scheme",
  "kind": "tool_output",
  "required": true,
  "tool_id": "13",
  "output_contract": "detail_scheme",
  "path": "Output/<to-be-confirmed-by-tool-manifest>",
  "manual_review_required": true
}
```

自由文本 token 的处理要求：

1. `13 detail scheme`、`14 detail segment descriptions` 这类 token 必须明确上游 tool id、产物语义、文件 schema 和 manifest path。
2. `target balanced_or_summary` 这类 token 应归一为 plan parameter 或 Session Context 变量，必须有枚举值和默认策略。
3. `task_opencode_session_or_fallback` 这类 token 必须拆成明确的 `opencode_session_id` 依赖和 fallback 规则，不得保留为自然语言。
4. `human_scene_transition_overrides`、`user_recomposition_instruction` 这类 token 应归一为用户输入、人工 override 文件或 Session Context 字段，并声明缺失时是 blocked、warning 还是 skip。
5. normalized registry 中不得残留未归一化的自由文本 token；否则 Plan Runner 不得执行该工具。

## 17. Prompt 目录与大模型调用透明化

如果 Tool 涉及 LLM / VLM / Agent / OpenCode 模型调用，则工具代码中不应硬编码提示词正文。

所有提示词相关内容必须落入本工具的 `Prompt/` 目录。

`Prompt/` 目录可以创建受控子目录，但所有 Prompt 文件、参考文档、模型请求和响应审计必须由 `Prompt/PromptManifest.json` 索引。

推荐文件：

```text
Prompt/PromptManifest.json
Prompt/Ref_prompt_guide.md
Prompt/Ref_scene_rules.md
Prompt/PromptTemplate_semantic_split.md
Prompt/PromptVariables_semantic_split.json
Prompt/PromptRendered_semantic_split.txt
Prompt/PromptDescription.html
Prompt/ModelCall_semantic_split_request.json
Prompt/ModelCall_semantic_split_response.json
```

机器消费的 Prompt 文件名建议使用 ASCII；中文标题可以放在 JSON 元数据或 HTML 展示层中。需要给人阅读的报告可以使用中文显示名称，但 raw / zip / 下游引用路径应优先保持 ASCII。

### 17.1 参考提示词文档

如果模型调用需要参考提示词文档、规则文档、案例文档或模板说明，必须在 prepare 阶段复制到本工具的 `Prompt/` 目录。

参考文档命名必须以 `Ref_` 开头。

示例：

```text
Prompt/Ref_prompt_guide.md
Prompt/Ref_visual_scene_rules.md
Prompt/Ref_rebuild_field_spec.md
```

Tool Use Session 运行期间，工具不得再访问 workspace 外部的参考提示词文档。

### 17.2 提示词模板与拼接变量

如果提示词由模板拼接产生，必须保存：

1. 提示词模板。
2. 拼接变量。
3. 拼接规则。
4. 最终渲染提示词。

示例：

```text
Prompt/PromptTemplate_scene_transition.md
Prompt/PromptVariables_scene_transition.json
Prompt/PromptRendered_scene_transition.txt
```

工具调用模型时，应使用 `PromptRendered_*.txt` 或由 Prompt 目录中模板和变量渲染出的内容。

### 17.3 Prompt HTML 说明

每个涉及模型调用的 Tool，必须在 `Prompt/` 下生成结构化 Prompt 元数据文件。

推荐命名：

```text
Prompt/PromptManifest.json
```

`PromptManifest.json` 必须说明：

1. 本工具有哪些提示词。
2. 每个提示词用于哪个模型调用。
3. 每个提示词模板是什么。
4. 每个提示词使用了哪些变量。
5. 变量来自 Session Context、Working 输入快照还是人工配置。
6. 最终渲染后的提示词文件是什么。
7. 模型调用请求和响应审计文件是什么。

`PromptDescription.html` 或中文展示页可以由 Debug Console / SessionReport 根据 `PromptManifest.json` 按需渲染，不要求每个 Tool 自己手写 HTML 展示逻辑。

### 17.4 模型调用审计

每次模型调用必须保存请求和响应审计文件。

示例：

```text
Prompt/ModelCall_scene_transition_request.json
Prompt/ModelCall_scene_transition_response.json
```

审计文件应包含：

1. tool_use_session_id。
2. step_index。
3. tool_name。
4. task_id。
5. opencrew_session_id。
6. opencode_session_id。
7. attempt_id。
8. model provider。
9. model id。
10. system prompt 文件路径。
11. user prompt 文件路径。
12. 输入文件列表。
13. 输出摘要。
14. 错误摘要。
15. request_id / local_usage_id / gateway_request_id，按当前 provider mode 可为空。
16. provider_mode / billing_mode。
17. usage 摘要，例如 input tokens、output tokens、媒体秒数、字节数或 provider job id。
18. sensitivity 和 redaction 状态。

审计文件不得包含未脱敏的 API key、cookie、auth header、provider key、mihomo 订阅 URL 或其它 secret。对于客户素材、完整 Prompt、模型输入文件和响应正文，应按 `visibility` / `sensitivity` 标记，并通过 `session_files` 控制是否可下载或进入客户页。

## 18. 工具可访问范围

单个 Tool 在正式 run 阶段只允许访问：

```text
<tool_session_root>/0_SessionContext/
<tool_session_root>/Sx_CurrentTool/State.json
<tool_session_root>/Sx_CurrentTool/Working/
<tool_session_root>/Sx_CurrentTool/Output/
<tool_session_root>/Sx_CurrentTool/Report/
<tool_session_root>/Sx_CurrentTool/Prompt/
```

prepare 阶段可以只读访问上游工具的 `Output/`，但目的仅限于复制依赖到本工具 `Working/`。

禁止：

1. run 阶段跨工具目录读取文件。
2. run 阶段读取 workspace 外部文件。
3. 从旧 attempt 或历史目录中隐式读取文件。
4. 通过目录扫描猜测上游产物。
5. 通过 stale 文件推断当前状态。

该规则用于保证：

1. 强制 Rerun 时只需清理当前工具目录。
2. 断点续跑时依赖快照稳定。
3. 依赖缺失时能在 prepare 阶段清晰反馈。
4. Tool Use Session 的运行不依赖 workspace 外部目录。

## 19. Tool Use Session 级目录规范

除了每个单独 Tool 自己的目录外，整个 Tool Use Session 还必须在 workspace 下维护 Session 级目录。

推荐结构：

```text
<workspace>/
  tool_use_sessions/
    <tool_use_session_id>/
      0_SessionContext/
      SessionReport/
      SessionOutput/
      S0_00_PrepareSessionVariables/
      S1_01_video_metadata_extractor/
      S2_02_audio_asr_pipeline/
```

其中：

1. `SessionReport/` 用于汇总整个 Tool Use Session 的运行状态、校验状态和可读报告。
2. `SessionOutput/` 用于汇总整个 Tool Use Session 运行完成后需要交付、下载、页面绑定或后续 workflow 消费的最终文件。
3. Workspace 根目录只保存跨 attempt 的稳定入口、用户上传文件和历史 Tool Use Session 根目录，不作为当前运行状态的隐式来源。

## 20. SessionReport 目录

`SessionReport/` 是整个 Tool Use Session 的汇总报告目录。

职责：

1. 汇总所有 Tool 的运行状态。
2. 汇总所有 Tool 的依赖检查结果。
3. 汇总所有 Tool 的失败、重试、断点续跑和强制 Rerun 记录。
4. 汇总整体质量校验结果。
5. 汇总页面绑定状态。
6. 汇总大模型调用审计状态。
7. 提供人类可读的整体运行报告。

`SessionReport/` 可以有子文件夹。

推荐文件：

```text
SessionReport/
  SessionRunSummary.html
  SessionRunSummary.json
  SessionValidationReport.html
  SessionValidationReport.json
  ToolStatusMatrix.json
  DependencyCheckReport.json
  ModelCallAuditIndex.json
```

推荐子目录：

```text
SessionReport/
  tool_reports/
  validation/
  model_calls/
  page_binding/
```

说明：

1. `tool_reports/` 可以收集或索引每个 Tool 的 Report 结果。
2. `validation/` 保存整体校验报告。
3. `model_calls/` 保存或索引整个 Session 的模型调用审计。
4. `page_binding/` 保存页面绑定检查结果。
5. `SessionRunSummary.json` 必须包含每个 Tool 的状态、对应 DB step id、OutputManifest 路径和错误摘要，便于 Debug Console 不扫描目录也能展示运行矩阵。

## 21. SessionOutput 目录

`SessionOutput/` 是整个 Tool Use Session 的最终输出目录。

职责：

1. 汇总本次 Tool Use Session 完成后需要交付的最终文件。
2. 汇总页面需要直接绑定的最终文件。
3. 汇总后续 workflow 需要消费的最终文件。
4. 提供稳定的最终产物入口，避免页面或后续 workflow 到多个 Tool 目录中查找文件。

`SessionOutput/` 可以有子文件夹。

推荐结构：

```text
SessionOutput/
  manifests/
  schemes/
  reports/
  media/
  subtitles/
  json/
  packages/
```

说明：

1. `manifests/` 保存整体 result index、scheme manifest、page binding manifest。
2. `schemes/` 保存最终分镜方案或方案包。
3. `reports/` 保存最终对外交付报告。
4. `media/` 保存最终媒体文件。
5. `subtitles/` 保存最终字幕文件。
6. `json/` 保存最终结构化 JSON。
7. `packages/` 保存最终可下载压缩包或交付包。

关键约束：

1. 单个 Tool 的 `Output/` 是给下游 Tool 消费的局部最终产物。
2. `SessionOutput/` 是整个 Tool Use Session 完成后的全局最终产物。
3. 页面和后续 workflow 应优先读取 `SessionOutput/` 中的 manifest / result index。
4. `SessionOutput/` 可以复制、整理或索引各 Tool 的 Output，但不应直接依赖脏状态或历史残留文件。
5. `SessionOutput/` 的内容应由最后的汇总 / 校验 / finalize 步骤生成或刷新。
6. `SessionOutput/manifests` 中的 result index 必须写回 Attempt，页面绑定顺序应为 DB latest attempt -> attempt result index -> SessionOutput manifest -> legacy fallback。
7. 进入 `SessionOutput/` 的文件必须带 `visibility`、`sensitivity`、`downloadable`、`attempt_id` 和 `tool_use_session_id` 元数据，并同步到 `session_files`。
8. legacy fallback 只允许作为迁移期兼容路径，命中时必须显示 stale warning 并写 debug event。

## 22. 历史运行保留与 GC

因为每次 Run / Rerun 都会创建新的 Attempt 和新的 `tool_use_sessions/<tool_use_session_id>/` 目录，必须定义保留和清理策略。

最低规则：

1. latest attempt 对应的 `SessionOutput/`、result index、可下载交付物和页面绑定 manifest 不得被自动 GC 删除。
2. 任一被 DB attempt `result_index_json`、`session_files`、`session_events` 或 workflow plan run 引用的文件，在引用解除或归档前不得直接删除。
3. 历史 Tool Use Session 可以按策略标记为 `archived`、`stale` 或 `gc_candidate`，但 GC 必须先更新 DB 索引状态，再删除 workspace 文件。
4. 删除顺序必须是 DB-first：先在 DB 中记录归档 / GC 事件和将被删除的文件索引状态，再执行文件删除；文件删除失败时写 cleanup event，不静默失败。
5. Prompt / ModelCall / Debug 报告默认不进入客户可下载交付物；其保留周期可以短于最终交付物，但必须满足调试和审计需要。
6. GC 策略应配置化，至少包含：保留 latest attempt、保留最近 N 个历史 attempt、按时间清理历史 `Working/` 缓存、保留可下载 `SessionOutput/`。
7. GC 不得通过扫描目录判断业务状态，必须以 DB attempt、result index 和 `session_files` 为准。
