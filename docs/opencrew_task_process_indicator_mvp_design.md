# OpenCrew Task Process Indicator MVP 设计文档

版本：v0.2
日期：2026-06-02
状态：MVP 设计草案

## 1. 背景

当前 Analysis_V1 页面已有“运行至 StoryBoard”弹窗，可以启动固定链条并显示步骤状态。但它本质上仍是一个专用弹窗：

1. 只能按固定链条运行。
2. 不能从某个脚本开始运行。
3. 不能只运行某个脚本。
4. 不能方便地重新运行失败步骤。
5. 不能可靠中断当前运行。
6. 不能查看每个脚本本次运行的实际参数、命令、输入、输出和日志。
7. 后端运行状态主要在内存中，刷新、重启和跨页面观察能力有限。

本设计只吸收旧 Task Process Indicator 的产品思想：运行过程必须可观察、可控制、可恢复、可审计。不照搬旧系统的状态机、XML 配置、接口命名或执行模型。

## 2. MVP 目标

MVP 要先把“线性工具链”的运行体验做好。

核心目标：

1. 一个标准 Indicator 组件可以展示一个 Attempt 的线性步骤列表。
2. 用户可以选择运行范围：全量运行、从某步开始、运行到某步、只运行某步。
3. 用户可以重跑：重跑全部、从失败步骤重跑、从指定步骤重跑、只重跑指定步骤。
4. 用户可以中断：请求停止、当前步骤不可安全 kill 时退化为 stop-after-current。
5. 用户可以查看每步实时参数：命令、参数、模型、输入文件、输出文件、prompt、Variables 摘要、stdout/stderr tail。
6. 用户可以在不刷新业务页面的情况下看到状态持续更新。
7. 所有运行控制操作写入 session event，便于审计。

首个落地对象是 Analysis_V1 的 `run_to_storyboard` 线性流程。后续再复用到 StoryBoard VideoPlan、Rebuild_V1 和其它 Tool Library。

## 3. 非目标

MVP 不做以下内容：

1. 不实现通用 DAG / 任意分支 / 任意循环工作流引擎。
2. 不照搬 WBC / TaskProcessEngine 的 canonical 状态集合。
3. 不把 `blocked`、`completed_with_sync_error`、`cancelled`、`stale_running` 降级成纯前端展示态。
4. 不默认允许前端编辑 `SessionContext/Variables.json`。
5. 不保证所有业务脚本都能被强制 kill 后保持产物一致。
6. 不在同一个 Attempt 中反复 reset 历史步骤。
7. 不迁移旧 XML / WCF / `.svc` 风格接口。
8. 不替代 Tool Use Session PRD 和 Plan Runner 设计。

## 4. 核心设计原则

### 4.1 Attempt 是运行边界

每一次用户点击运行 / 重新运行都创建新的 Attempt。

这样做的原因：

1. 避免同一个 Attempt 内反复 reset 后产生混合状态。
2. 避免旧产物、新产物、session_files 和 result index 难以解释。
3. 让“从某步开始”也有清晰的审计边界。

`从指定步骤重跑` 的语义不是修改旧 Attempt，而是创建新 Attempt，并在新 Attempt 中：

1. 明确声明 `start_step_id`。
2. 前序步骤显示为 `reused`，并在 `reuse_reason=start_step_before_boundary` 中说明原因。
3. 当前步骤及后续步骤真实执行。
4. result index 只绑定新 Attempt 的最终结果。

MVP 不新建平行的 attempt 表。Analysis_V1 直接复用现有 `openclip_attempts`：

1. `openclip_attempts.id` 是 Indicator 的 `attempt_id`。
2. `openclip_attempts.attempt_no` 继续按 `task_id` 递增。
3. `openclip_tasks.latest_attempt_id` 仍指向当前业务页面认为最新的 attempt。
4. step 级状态不塞进 `openclip_attempts.summary`，必须有单独 run state JSON；后续再决定是否迁移到专门 step 表。

### 4.1.1 Attempt family

现有 `openclip_attempts` 同时承载两类语义：

1. 传统 OpenClip LLM skill / analysis attempt。
2. Analysis_V1 `run_to_storyboard` 工具链 attempt。

MVP 必须显式区分 attempt family，避免 `latest_attempt_id` 和 attempt list 语义混乱。

推荐先用兼容方式实现：

1. 在 run state JSON 中写入 `attempt_family`，例如 `analysis_v1_tool_run`。
2. 在 `session_events.family` 中写入同样 family。
3. 在 API response 中返回 `attempt_family`。
4. 在前端列表和 Indicator 中显示 attempt family。

后续 schema 迁移建议给 `openclip_attempts` 增加：

```text
attempt_family TEXT
run_target TEXT
run_state_json TEXT 或 step_state_json TEXT
heartbeat_at BIGINT
cancel_requested_at BIGINT
pause_before_step_id TEXT
pause_requested_at BIGINT
paused_at BIGINT
resume_requested_at BIGINT
```

在迁移完成前，`openclip_attempts.status` 是 Attempt 主状态，step 细节以 run state JSON 为准。

`attempt_no` 在 MVP 阶段可以继续使用现有按 task 递增的共享序列，但 UI 和 API 必须显示 `attempt_family`，并允许按 family 过滤。后续如果产品需要分开编号，再增加 `family_attempt_no`，不要改变历史 `attempt_no` 的含义。

### 4.2 状态以 OpenCrew 现有执行合同为准

MVP 使用 OpenCrew 状态，不套用旧 WBC 状态。

Attempt 状态建议：

| 状态 | 含义 |
| --- | --- |
| `queued` | 已创建 Attempt，等待后台线程启动 |
| `running` | 至少一个步骤正在执行 |
| `paused` | 用户设置的暂停点已命中，等待继续运行 |
| `stopping` | 用户已请求停止，等待当前步骤结束或被终止 |
| `completed` | 所有计划步骤完成 |
| `completed_with_sync_error` | 运行完成，但 DB / file / result index 同步失败 |
| `failed` | 执行失败 |
| `blocked` | 依赖、授权或用户输入缺失导致阻断 |
| `cancelled` | 用户停止后已结束 |
| `stale_running` | heartbeat 超时或运行状态失联 |

当前 `openclip_attempts.status` 是 `Text` 字段，不需要为了这些状态做数据库枚举迁移；但 repository、API serializer、前端状态映射和测试必须把这些状态列入允许集合。

Step 状态建议：

| 状态 | 含义 |
| --- | --- |
| `pending` | 本次 Attempt 中尚未执行 |
| `reused` | 前序步骤产物被本次 Attempt 显式复用 |
| `running` | 正在执行 |
| `completed` | 执行完成 |
| `failed` | 执行失败 |
| `blocked` | 依赖或授权缺失 |
| `skipped` | 用户选择跳过，且后端确认可跳过 |
| `cancelled` | 用户停止导致该步骤结束 |
| `stale_running` | heartbeat 超时 |

前端可以把这些状态映射成更友好的 label，但不能改变后端语义。

### 4.3 线性计划先行

MVP 只执行有序数组：

```json
{
  "target": "analysis_v1.run_to_storyboard",
  "pause_before_step_id": "",
  "steps": [
    {"id": "00", "entrypoint": "00_PrepareSessionVariables.py"},
    {"id": "01", "entrypoint": "01_VideoProbeMetadata.py"},
    {"id": "02_01", "entrypoint": "02_01_AudioASR.py"},
    {"id": "02_02", "entrypoint": "02_02_VideoSRTFrame.py"},
    {"id": "03_02", "entrypoint": "03_02_TTSBuilderQuick.py"},
    {"id": "04_01", "entrypoint": "04_01_SRTRewrite.py"},
    {"id": "04_03", "entrypoint": "04_03_StoryBoardQuick.py"}
  ]
}
```

可选路线在运行前编译成确定的线性计划。例如：

1. TTS Builder 选择 `skip`、`03_01` 或 `03_02`。
2. StoryBoard 选择 `04_02` 或 `04_03`。
3. ASR 选择 `default`、`cloud` 或 `local`。

编译完成后，当前 Attempt 的 step list 不再动态变化。

`pause_before_step_id` 是线性计划上的控制点，不是分支，也不是新的 step。它只表示 Runner 在准备启动该 step 前进入 `paused`，不会改变 step 序列、依赖关系、result index 或 `plan_hash` 的基本语义。它可以在 Attempt 创建时随 plan payload 写入，也可以在运行过程中通过 `pause-before` API 更新；无论哪种方式，都必须落入 run state JSON 并写 session event。

### 4.4 API 命名空间策略

MVP 沿用现有 OpenClip / Analysis_V1 路由风格，不引入新的 `/api/opencrew/tool-runs/...` 命名空间。

原因：

1. 现有代码已经使用 `/api/openclip/tasks/{task_id}/analysis-v1/...`。
2. 现有启动和查询都以 `task_id` 为路径主键。
3. Analysis_V1 是首个落地对象，先减少迁移成本。

通用 `/api/opencrew/tool-runs/...` 可作为后续跨工具抽象层，但不是 MVP 前提。MVP 新增接口应和旧端点共存并逐步扩展旧端点，不替换现有路由。

### 4.5 Step 状态权威存储

MVP 中 step 状态的权威来源是：

```text
<workspace>/SessionReport/tool_runs/attempt_{attempt_id}/run_state.json
```

`openclip_attempts.status` 只保存 Attempt 主状态。Indicator 查询时必须合并：

1. `openclip_attempts`：attempt id、attempt_no、family、主状态、开始结束时间、summary、result manifest。
2. `run_state.json`：plan、steps、step logs tail、quick watch snapshot、heartbeat、cancel flag、pause point、resume flag。
3. `session_events`：审计事件和用户操作。
4. `session_files` / result manifest：可展示和可下载产物。

如果 run state JSON 缺失，Indicator 仍应返回 Attempt 主状态，但 step 明细显示为 `unavailable`，并提示“run state missing”。这比返回空步骤列表更安全。

### 4.6 并发模型

MVP 使用单机保守并发策略：

1. 同一个 `task_id` 同一时间只允许一个 `analysis_v1_tool_run` attempt 处于 `queued/running/paused/stopping`。
2. 不同 task 默认也先限制为单并发，避免多个视频/ASR/模型调用同时争用 Mac mini 资源。
3. 后端用 run lock 记录 `attempt_id`、`task_id`、`owner`、`started_at` 和 `heartbeat_at`。
4. Stop 只作用于指定 `attempt_id`，不得影响其它 attempt。
5. 如果已有活动运行，新的 Run 请求返回 `409 Conflict` 和当前 active attempt 摘要。

后续如果开放多并发，必须先把全局 in-memory lock 改成 DB / file backed lock，并给每个 attempt 独立维护 process handle 和 cancel token。

## 5. 用户能力

### 5.1 启动方式

运行弹窗提供以下模式：

| 模式 | 用户语义 | 后端行为 |
| --- | --- | --- |
| 全量运行 | 从第一步运行到目标步骤 | 新建 Attempt，执行完整线性计划 |
| 范围运行 | 从 A 步运行到 B 步 | 新建 Attempt，A 前步骤标记为 `reused`，A 到 B 执行，B 后步骤 `pending` |
| 从指定步骤开始 | 从指定步骤运行到默认目标 | 新建 Attempt，指定步骤前 `reused`，指定步骤及后续执行 |
| 单独运行某一步 | 只运行一个脚本 | 新建 Attempt，仅执行该 step，要求 step 支持独立运行 |
| 从失败步骤重跑 | 从失败步骤开始重跑 | 新建 Attempt，失败步骤前 `reused`，失败步骤及后续执行 |
| 全量重跑 | 全量重跑 | 新建 Attempt，全步骤重新执行 |

暂停点是运行控制项，不是独立启动模式。用户可以在创建 Attempt 前选择“运行到某步前暂停”，也可以在 Attempt 运行中通过 step 菜单设置暂停点。前端把它表达为 `pause_before_step_id=<step>`；后端仍按上表的运行模式创建或继续 Attempt，只是在启动目标 step 前暂停。

### 5.2 复用前序产物

`reused` 不是简单地假装成功。后端必须记录：

1. 复用的是哪个 previous_attempt_id。
2. 复用了哪些文件或 result index。
3. 当前 step 的输入依赖是否满足。
4. 复用前序产物是否与当前 plan hash 匹配。

如果依赖不满足，当前运行必须进入 `blocked`，并返回标准 blocked reason。

`plan_hash` 用于判断本次 Attempt 是否可以安全复用 previous attempt 的产物。它不是只 hash step id，也不能只 hash UI 选项。MVP 至少必须覆盖以下输入：

1. step 序列、每步 entrypoint、脚本版本或脚本内容 hash。
2. 关键运行 options：`asr_mode`、`allow_cloud_asr_data_transfer`、`tts_builder_mode`、`storyboard_mode`、`run_model_provider`、`run_model_id` 等会影响输出的字段。
3. 源视频内容 hash；路径变化但内容相同时可以视为同一输入，内容变化必须得到不同 hash。
4. `SessionContext/Variables.json` 中会影响后续脚本输出的关键字段，例如 source media refs、语言/字幕/人设/行业/目标受众、prompt version refs、已选方案、模型设置 refs。
5. Tool / registry capability 版本，例如 `supports_run_only`、依赖声明和输出 contract 版本。

`plan_hash` 不应覆盖 volatile 字段，例如 attempt_id、started_at、heartbeat、stdout tail、临时目录名。若某字段是否影响输出不确定，默认纳入 hash，而不是允许复用。

### 5.3 单步运行

`单独运行某一步` 只对声明 `supports_run_only=true` 的步骤开放。

默认策略：

1. `00` 支持单步运行。
2. `01` 支持单步运行。
3. `02_01` 支持单步运行，但必须已有 `Variables.json` 和源音频/视频。
4. `02_02` 支持单步运行，但必须已有 ASR 输出。
5. `03_01` / `03_02` 支持单步运行，但必须已有 SRT / dialogue 输入。
6. `04_01` 支持单步运行，但必须已有 SRT frame items 和 rewrite prompt。
7. `04_02` / `04_03` 支持单步运行，但必须已有 rewritten SRT 和 storyboard prompt 或 quick config。

不满足依赖时不启动脚本，直接返回 `blocked`。

### 5.4 中断

MVP 中断分两层：

| 层级 | 行为 |
| --- | --- |
| graceful stop | 写入 `cancel_requested=true`，Runner 在步骤之间停止 |
| terminate current process | 后续能力；对当前子进程发送 terminate，超时后可 kill |

默认不是所有 step 都允许强制 kill。每个 step capability 必须声明：

```json
{
  "supports_graceful_stop": true,
  "supports_terminate": false,
  "safe_to_discard_partial_outputs": false
}
```

如果当前 step 不支持安全 terminate，停止按钮显示为“当前步骤结束后停止”。

MVP 只实现 graceful / stop-after-current。`terminate_current` API 字段可以预留，但前端不显示、后端不执行，直到至少一个脚本确认 `supports_terminate=true`。

### 5.4.1 步骤边界暂停

MVP 支持在某个未来 step 开始前暂停，语义是“步骤边界暂停”，不是脚本内部断点。

规则：

1. 用户在 step 菜单选择 `运行到此步前暂停`。
2. Runner 不打断当前正在运行的脚本。
3. Runner 每次准备启动下一步前检查 `pause_before_step_id`。
4. 如果下一步命中暂停点，Attempt 状态进入 `paused`，该 step 保持 `pending`。
5. 用户点击 `继续运行` 后，同一个 Attempt 从暂停 step 正常开始执行。
6. 用户可以在暂停点命中前点击 `取消暂停点`。
7. pause / resume 不新建 Attempt，也不重置已完成 step。
8. 暂停期间不产生 provider 用量；继续后真实执行的新 provider 调用照常记录和计费。

边界：

1. 如果目标 step 已经 `completed/reused/skipped/failed/blocked/cancelled`，本次 Attempt 不能设置“运行前暂停”，前端禁用并提示“该步骤已通过或已结束，无法在本次运行中设置暂停点”。
2. 如果目标 step 正在 `running`，不能设置“运行前暂停”；用户只能使用 `当前步骤结束后停止`。
3. 如果 Attempt 已经是 terminal 状态，不能设置暂停点。
4. 同一 Attempt MVP 只允许一个 active pause point；设置新暂停点会覆盖旧暂停点，并写审计事件。
5. 页面刷新后必须从 run state JSON 恢复暂停点和 `paused` 状态。

run state 至少记录：

```json
{
  "pause_before_step_id": "04_01",
  "pause_reason": "user_requested",
  "pause_requested_at": 1780380000000,
  "paused_at": null,
  "resume_requested_at": null
}
```

### 5.5 实时参数与参数快照

每个 step 必须能打开参数快照。

参数快照 MVP 展示：

1. step id、名称、entrypoint、timeout。
2. 本次执行命令，已脱敏。
3. 本次运行 payload，例如 ASR 模式、模型、TTS 模式、StoryBoard 模式。
4. 关键环境摘要，例如 Python、cwd、PYTHONPATH、OPENCREW_DATA_DIR，敏感值脱敏。
5. 读取的 Session Variables 摘要。
6. 输入文件列表。
7. 输出文件列表。
8. Prompt 文件列表。
9. stdout tail。
10. stderr tail。
11. result JSON 摘要。

参数快照只读，不编辑变量。

### 5.6 计费语义

运行控制必须和本地 metering 语义一致。

规则：

1. 真实执行 provider / model / ASR / TTS / media API 调用的 step 会产生新的 `local_usage_log` 记录，并按现有 metering 规则计费或估算成本。
2. `reused` 前序步骤不重新调用 provider，不产生新的 usage，不重复计费。
3. `从指定步骤开始` 只对实际执行的 step 计费。
4. `单独运行某一步` 如果真实调用 provider，则计费；如果只是本地诊断或纯文件检查，不计费。
5. `全量重跑` / `从失败步骤重跑` / `从指定步骤重跑` / `单独运行某一步` 属于用户触发的新运行；只要重新执行高成本 API step，就必须按新 provider 调用产生新的成本和客户收费。
6. 用户停止后，已经发生的 provider 调用仍按实际用量记录；未执行 step 不计费。
7. `diagnostic run` 必须在 payload 和 audit 中标记 `billing_scope=diagnostic`。是否向客户收费由业务策略决定，但 provider 成本仍必须记录。
8. Indicator 必须能显示本 Attempt 的 metering 摘要：provider cost、customer charge、profit、cost_basis。

MVP 不允许把旧 Attempt 的 usage 复制到新 Attempt 里充当新收入。重复运行产生的新收费必须来自本次 Attempt 的真实 provider 调用；复用产物只能引用 previous_attempt_id，不能复制 billing rows。

## 6. 后端 MVP 合同

### 6.1 Plan 查询

```text
GET /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/plan
```

返回当前可运行的线性计划、可选步骤、默认 start/end step、capabilities 和禁用原因。

### 6.2 创建 Attempt 并启动

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard
```

请求：

```json
{
  "mode": "run_from_step",
  "start_step_id": "02_02",
  "end_step_id": "04_03",
  "run_only_step_id": "",
  "previous_attempt_id": 101,
  "options": {
    "asr_mode": "default",
    "allow_cloud_asr_data_transfer": true,
    "tts_builder_mode": "quick",
    "storyboard_mode": "quick",
    "run_model_provider": "openai",
    "run_model_id": "gpt-5.5"
  }
}
```

`task_id` 以路径参数 `{task_id}` 为准，body 不再重复传递。若兼容旧客户端需要临时接受 body.task_id，后端必须校验它与路径一致；不一致返回 `400`。

`run_model_provider` 和 `run_model_id` 的值必须来自当前已连接 provider / model discovery 结果，不是固定枚举。示例中的 provider/model 只是占位；实现时不得把 `"opencode"` 当作 provider 常量。

返回 Indicator payload。

兼容要求：

1. 旧请求不传 `mode/start_step_id/end_step_id/run_only_step_id` 时，行为保持为当前全量运行。
2. 旧 `GET /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}` 状态查询继续可用。
3. 新字段只扩展 payload，不改变现有 `task_id` 路径主键。

### 6.3 查询 Indicator

```text
GET /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}
```

返回：

```json
{
  "ok": true,
  "attempt": {
    "attempt_id": 102,
    "attempt_no": 8,
    "attempt_family": "analysis_v1_tool_run",
    "task_id": 36,
    "session_id": 92,
    "target": "analysis_v1.run_to_storyboard",
    "status": "running",
    "display_status": "running",
    "started_at": 1780380000000,
    "updated_at": 1780380010000
  },
  "plan": {
    "plan_hash": "sha256:...",
    "mode": "run_from_step",
    "start_step_id": "02_02",
    "end_step_id": "04_03",
    "pause_before_step_id": "04_01"
  },
  "progress": {
    "completed": 2,
    "total": 4,
    "current_step_id": "04_01"
  },
  "steps": [
    {
      "id": "04_01",
      "name": "04_01_SRTRewrite",
      "entrypoint": "04_01_SRTRewrite.py",
      "status": "running",
      "started_at": 1780380005000,
      "finished_at": null,
      "duration_seconds": null,
      "message": "",
      "capabilities": {
        "supports_run_only": true,
        "supports_graceful_stop": true,
        "supports_terminate": false
      }
    }
  ],
  "capabilities": {
    "can_stop": true,
    "can_set_pause_point": true,
    "can_cancel_pause_point": true,
    "can_resume": false,
    "can_rerun_all": true,
    "can_rerun_from_step": true,
    "can_run_only_step": true
  }
}
```

### 6.4 停止

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/stop
```

请求：

```json
{
  "mode": "graceful",
  "reason": "user_requested"
}
```

`mode` 可选：

1. `graceful`：步骤之间停止，MVP 默认支持。
2. `terminate_current`：后续能力；MVP 不开放给前端。

### 6.5 暂停与继续

设置暂停点：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/pause-before
```

请求：

```json
{
  "step_id": "04_01",
  "reason": "user_requested"
}
```

返回更新后的 Indicator payload。

取消暂停点：

```text
DELETE /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/pause-before
```

返回更新后的 Indicator payload。

继续运行：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/resume
```

请求：

```json
{
  "reason": "user_requested"
}
```

要求：

1. 只有 `queued/running/paused` 的 Attempt 可以设置或取消暂停点。
2. 只有 `paused` 的 Attempt 可以 resume；其它状态返回 `409` 和当前状态。
3. `pause-before` 只能设置到尚未开始的 step；如果目标 step 已经开始或结束，返回 `409` 和 disabled reason。
4. pause/resume/cancel-pause 都必须写 session event。
5. pause/resume 不创建新 Attempt，不改变 `attempt_no`，不复制 billing rows。

### 6.6 参数快照

```text
GET /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/steps/{step_id}/quick-watch
```

返回命令、参数、变量摘要、文件、prompt、日志和 result 摘要。

### 6.7 日志

```text
GET /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/steps/{step_id}/logs?cursor=...
```

MVP 可先返回 stdout/stderr tail 和 session event，不必一开始实现完整 cursor 历史分页。

## 7. Runner MVP 要求

当前 Analysis_V1 `run-to-storyboard` 后端需要从 `subprocess.run()` 升级为可控 runner。

最低要求：

1. 使用 `subprocess.Popen()` 启动当前 step。
2. stdout/stderr 写入 step log 文件，同时保留 tail 到 step state。
3. 每个 step 开始前写 step state。
4. 每个 step 结束后写 step state。
5. 每秒更新 Attempt `heartbeat_at` 或 run state `updated_at`。
6. Stop 请求写入可被 runner 读取的控制状态。
7. Pause 请求写入可被 runner 读取的控制状态。
8. runner 每次启动下一 step 前检查 stop 和 pause point。
9. runner 命中 pause point 后把 Attempt 标记为 `paused`，不启动该 step，并等待 resume。
10. runner 等当前 step 结束后停止后续步骤。
11. runner 结束时写 result index 或进入 `completed_with_sync_error`。
12. 所有状态至少落到 workspace `SessionReport/tool_runs/attempt_{attempt_id}/run_state.json`，并镜像 Attempt 主状态到 DB。
13. `terminate_current` 通路只预留接口，不作为 MVP 验收项。

事件命名必须随新 runner 收敛。新实现统一写入：

```text
analysis_v1.run_to_storyboard.attempt.created
analysis_v1.run_to_storyboard.attempt.started
analysis_v1.run_to_storyboard.step.started
analysis_v1.run_to_storyboard.step.completed
analysis_v1.run_to_storyboard.step.failed
analysis_v1.run_to_storyboard.step.blocked
analysis_v1.run_to_storyboard.pause.requested
analysis_v1.run_to_storyboard.pause.cancelled
analysis_v1.run_to_storyboard.attempt.paused
analysis_v1.run_to_storyboard.attempt.resumed
analysis_v1.run_to_storyboard.attempt.completed
analysis_v1.run_to_storyboard.attempt.failed
analysis_v1.run_to_storyboard.attempt.cancel_requested
```

旧事件名 `analysis_v1.run_to_04_02.*`、`tool_chain=run_to_04_02`、`tool_id=analysis_v1_run_to_04_02` 只作为迁移期兼容读取对象。新 runner 即使目标步骤是 `04_02` 或 `04_03`，也不得继续发新的 `run_to_04_02` 事件，避免审计和前端订阅出现两套命名。

后续如果 step 状态需要 DB 化，优先在现有 OpenClip attempt 体系内扩展，例如给 `openclip_attempts` 增加 `run_state_json`，或新增从属于 `openclip_attempts.id` 的 `openclip_attempt_steps` 表。MVP 不引入独立的 `workflow_plan_runs` / `workflow_plan_steps` 平行体系。

## 8. 前端 MVP 设计

### 8.1 运行设置区

原来的 Select Run Model 弹窗升级为运行设置弹窗，标题建议为 `运行设置`。面向用户的 UI 文案必须使用中文；API 字段、`mode` 值、step id、脚本名和日志里的原始命令保持代码原值。

必须包含：

1. 运行模式：全量运行 / 范围运行 / 从某步开始 / 只运行单步 / 从失败步骤重跑 / 全量重跑。
2. 起始步骤。
3. 结束步骤。
4. 单步运行步骤。
5. 模型服务商 / 模型。
6. ASR 模式。
7. 云端 ASR 授权。
8. TTS Builder：跳过 / 03_01 / 03_02。
9. StoryBoard：04_02 / 04_03。
10. 当前计划预览。

### 8.1.1 前端入口

MVP 使用三个层级的操作入口：

1. 页面工具栏主按钮：原播放按钮改为 `运行...`，打开运行设置弹窗。按钮旁保留最近 attempt 状态提示。
2. Indicator 命令栏：在进度弹窗顶部提供 `停止`、`继续运行`、`取消暂停点`、`刷新`、`重新运行`、`打开结果`、`关闭`。
3. Step 行操作：每个 step 行右侧提供菜单，包含 `运行至此步`、`从此步开始运行`、`单独运行此步`、`重跑此步及后续`、`运行到此步前暂停`、`查看详情`。菜单项按 capability 和当前状态启用或禁用。

前端不得只依赖本地硬编码判断。Plan 查询返回的 `steps[].capabilities`、`disabled_reason` 和 attempt `capabilities` 是按钮启用状态的权威来源。

### 8.1.2 运行模式到 payload 的映射

前端所有运行动作都调用：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard
```

不同操作映射如下：

| 前端操作 | UI 入口 | payload |
| --- | --- | --- |
| 全量运行 | 工具栏 `运行...` -> 运行模式 = 全量运行 | `mode=run_all`，不传 start/end/run_only |
| 运行至此步 | step 菜单或运行设置弹窗选择结束步骤 | `mode=run_range`，`end_step_id=<step>`，start 默认第一步 |
| 从此步开始运行 | step 菜单或运行设置弹窗选择起始步骤 | `mode=run_from_step`，`start_step_id=<step>`，end 默认当前目标 StoryBoard step |
| 范围运行 | 运行设置弹窗选择起始步骤 + 结束步骤 | `mode=run_range`，传 `start_step_id/end_step_id` |
| 只运行此步 | step 菜单或运行设置弹窗选择单步运行步骤 | `mode=run_only_step`，`run_only_step_id=<step>` |
| 全量重跑 | Indicator 命令栏 `重新运行` 菜单 | `mode=rerun_all`，`previous_attempt_id=<current/latest>` |
| 从失败步骤重跑 | Indicator 命令栏 `从失败步骤重跑` | `mode=rerun_failed`，`previous_attempt_id=<failed_attempt>` |
| 从此步重新运行 | step 菜单 | `mode=rerun_from_step`，`start_step_id=<step>`，`previous_attempt_id=<selected_attempt>` |
| 运行到此步前暂停 | step 菜单 | 调用 `pause-before`，`step_id=<step>` |
| 继续运行 | Indicator 命令栏，Attempt 为 `paused` 时显示 | 调用 `resume` |
| 取消暂停点 | Indicator 命令栏，存在 pause point 且尚未命中时显示 | 调用 `DELETE pause-before` |

前端必须在确认运行前展示本次计划预览：

1. 哪些 step 将执行。
2. 哪些 step 将显示为 `reused`。
3. 哪些 step 将保持 `pending`。
4. 本次是否可能产生新的 provider 调用和计费。
5. `previous_attempt_id` 和复用来源。

### 8.1.3 重新运行

重新运行必须始终创建新 Attempt，前端不得在旧 Attempt 上重置 step 状态。

`重新运行` 菜单规则：

1. terminal attempt 显示 `全量重跑`、`从指定步骤重跑`、`只运行单步`。
2. failed / blocked attempt 额外显示 `从失败步骤重跑`。
3. running / stopping attempt 禁用 rerun，并提示当前 attempt 仍在运行。
4. rerun 触发前必须提示“重新执行的高成本 API step 会重新计费；reused step 不重复计费”。

### 8.1.4 单步运行

`单独运行此步` 的前端规则：

1. 只对 `supports_run_only=true` 的 step 显示为可点击。
2. 如果 plan 查询返回缺依赖，菜单项禁用并展示 `disabled_reason`。
3. 单步运行默认标记为 `diagnostic run`，除非后端声明该 step 会更新最终 result index。
4. 单步运行完成后，Indicator 展示该 Attempt；业务页面结果区只有在后端返回 result index 更新后才自动刷新。

### 8.1.5 暂停与继续

`运行到此步前暂停` 不是常驻按钮，而是 step 行右侧菜单项。

前端规则：

1. step 还未开始时显示为可点击；点击后该 step 行显示 `暂停点` 标记。
2. step 已开始或已结束时禁用，并显示 tooltip：`该步骤已通过或已开始，无法在本次运行中设置暂停点`。
3. Indicator 顶部显示：`将在 04_01 开始前暂停`。
4. 暂停点尚未命中时显示 `取消暂停点`。
5. Attempt 进入 `paused` 后，顶部主操作按钮显示 `继续运行`。
6. `paused` 状态下允许查看参数、日志和文件；禁用新的运行 / 重新运行，直到继续或停止。
7. 点击 `继续运行` 后按钮临时显示 `正在继续...`，最终状态以 Indicator 查询结果为准。

### 8.1.6 中断

Indicator 命令栏中的停止按钮根据 capability 展示不同文案：

| 状态 | 按钮 |
| --- | --- |
| attempt `queued/running` 且当前 step 不支持 terminate | `当前步骤结束后停止` |
| attempt `queued/running` 且当前 step 支持 terminate | 后续能力：`停止` / `终止当前步骤` |
| attempt `stopping` | disabled，显示 `正在停止...` |
| terminal attempt | disabled |

MVP 前端只发送：

```json
{
  "mode": "graceful",
  "reason": "user_requested"
}
```

发送后前端立即把本地按钮状态置为 `正在停止...`，但最终状态必须以 Indicator 查询结果为准。

### 8.1.7 查看参数

点击 step 行或 `查看参数` 打开右侧步骤详情。步骤详情不能作为单独弹窗堆叠在进度弹窗上，避免遮挡运行状态。

步骤详情展示优先级：

1. 概览：状态、耗时、exit code、message、warnings、blocked reasons。
2. 参数：本次 mode/options、provider/model、ASR/TTS/StoryBoard 设置、`previous_attempt_id`、`plan_hash`、step input dependency 摘要。
3. 命令：脱敏后的 command、cwd、env 摘要、Python/venv/PYTHONPATH。
4. 文件：输入文件、输出文件、prompt 文件、result 文件。
5. 日志：stdout/stderr tail。

敏感字段必须脱敏；真实 API key、Authorization、cookie、database URL、mihomo 订阅 URL 不得出现在任何 tab。

### 8.1.8 轮询与恢复

运行设置弹窗提交成功后立即切换到 Indicator 进度视图。

前端轮询规则：

1. active attempt：每 1 秒查询 Indicator。
2. stale 或网络错误：退避到 3 秒，并显示连接状态。
3. `paused` attempt：每 3 秒查询 Indicator，并保留手动 Refresh。
4. terminal attempt：停止自动轮询，但保留手动 Refresh。
5. 页面刷新后，根据 latest attempt 和 attempt family 恢复 Indicator 状态。
6. 用户关闭弹窗不取消后台运行。

恢复合同：

1. 运行状态、step 状态、暂停点、停止请求、日志 tail、参数快照必须从后端 Indicator API 和 run state JSON 恢复，前端本地状态不能作为权威来源。
2. 用户刷新页面后，前端先加载当前 task，再查询 latest `analysis_v1_tool_run` attempt；如果该 attempt 是 `queued/running/paused/stopping`，自动恢复进度弹窗。
3. 用户关闭弹窗但不刷新页面时，后台继续轮询；再次打开弹窗显示同一个 attempt 的最新状态。
4. 用户重新进入页面时，如果 latest attempt 是 terminal 状态，不自动弹出进度弹窗，但在工具栏旁显示最近状态，用户可点击“查看最近运行”重新打开。
5. 弹窗内的纯 UI 偏好可以保存在 `localStorage`，例如当前选中的 step、当前 tab、日志展开状态；这些偏好丢失不影响运行正确性。
6. 未提交的运行设置表单不要求跨刷新保留；一旦点击开始运行，实际 payload 必须写入 run state，并可在“参数”页恢复查看。
7. 如果 Indicator API 找不到 run state JSON，只能显示 Attempt 主状态和“运行状态文件缺失”，不得伪造 step 明细。

### 8.2 运行进度区

必须包含：

1. Attempt 状态。
2. Task / Session / Attempt。
3. 当前 step。
4. 完成数 / 总数。
5. 总耗时。
6. 命令栏：停止、继续运行、取消暂停点、刷新、重新运行、打开结果、关闭。
7. Step List。
8. 步骤详情。

### 8.3 步骤详情

MVP 先做三个 tab：

1. 概览：状态、时间、exit code、message、warnings、blocked reasons。
2. 参数快照：命令、参数、变量摘要、文件、prompt、result。
3. 日志：stdout/stderr tail、session events。

文件 tab 可以并入参数快照的文件列表，后续再拆。

## 9. Analysis_V1 线性计划

默认计划：

| Step | Script | supports_run_only | supports_terminate | 关键产物 |
| --- | --- | --- | --- | --- |
| `00` | `00_PrepareSessionVariables.py` | true | false | `SessionContext/Variables.json` |
| `01` | `01_VideoProbeMetadata.py` | true | false | metadata report |
| `02_01` | `02_01_AudioASR.py` | true | false | ASR result |
| `02_02` | `02_02_VideoSRTFrame.py` | true | false | `final_srt_frame_items.json` |
| `03_01` | `03_01_TTSBuilderG.py` | true | false | TTS candidates |
| `03_02` | `03_02_TTSBuilderQuick.py` | true | false | TTS candidates |
| `04_01` | `04_01_SRTRewrite.py` | true | false | rewritten SRT |
| `04_02` | `04_02_StoryBoard.py` | true | false | storyboard |
| `04_03` | `04_03_StoryBoardQuick.py` | true | false | storyboard |

MVP 默认 `supports_terminate=false` 是保守选择。之后逐个脚本确认 signal handling 和 partial output 处理后再开放。

## 10. 分支与循环策略

### 10.1 MVP 不支持运行时分支

MVP 不做运行过程中由脚本结果决定下一步的动态分支。

可以支持的只是“运行前选择路线”，例如：

1. 选择 `03_01` 或 `03_02`。
2. 选择 `04_02` 或 `04_03`。
3. 选择是否跳过 TTS Builder。

这些选择在 Attempt 创建前被编译成固定线性计划。

### 10.2 下一阶段支持受限 DAG

后续可以支持受限 DAG，但必须由后端展开成明确 step instances。

例如：

```text
segment_001.audio -> segment_001.image -> segment_001.video -> segment_001.sync
segment_002.audio -> segment_002.image -> segment_002.video -> segment_002.sync
```

前端可以显示矩阵，但执行状态仍然是明确实例：

```json
{
  "step_instance_id": "segment_001.video",
  "parent_step_id": "video",
  "segment_id": "segment_001",
  "status": "running"
}
```

### 10.3 循环必须有边界

未来如果支持循环，只允许 bounded loop：

1. 按 segment 数量展开。
2. 按 retry policy 展开，最大次数固定。
3. 按用户确认后的下一批次展开。

不支持无限 while loop。原因是无限循环会破坏进度计算、取消语义、计费、幂等和 result index。

## 11. 安全要求

1. 参数快照、日志、命令快照必须脱敏 API key、Authorization、database URL、provider cookie、proxy secret。
2. 前端不展示真实 key。
3. 前端不允许直接编辑 Variables。
4. Stop / terminate / pause / resume / rerun all / run from step 必须写 session event。
5. 从指定步骤开始如果会复用旧产物，必须显示 previous_attempt_id。
6. 单独运行某一步如果不会更新最终 result index，必须标记为 diagnostic run。

## 12. MVP 验收标准

### 12.1 后端

1. 能创建新的 Attempt 并启动线性计划。
2. 能按 `start_step_id` 和 `end_step_id` 运行范围。
3. 能只运行支持单步运行的 step。
4. 能从失败 step 重跑为新 Attempt。
5. 能请求 stop，当前不支持 terminate 时在当前 step 后停止。
6. 能查询 Indicator 状态。
7. 能查询参数快照。
8. 能查询 stdout/stderr tail。
9. 能写 session events。
10. DB 写回失败时进入 `completed_with_sync_error`。
11. `openclip_attempts.status` 能容纳 `queued/running/paused/stopping/completed/completed_with_sync_error/failed/blocked/cancelled/stale_running`。
12. step 状态重启后仍能从 run state JSON 恢复。
13. 同一时间第二个 Analysis_V1 run 请求会得到清晰的 `409 active_run_exists`。
14. `reused` step 不写新的 metering row。
15. 能在尚未开始的 step 前设置暂停点、命中后进入 `paused`、resume 后继续同一 Attempt。
16. 页面刷新后能恢复 pause point 和 `paused` 状态。

### 12.2 前端

1. 运行设置弹窗能选择模式、start step、end step、单步 step。
2. 进度弹窗能显示实时状态、耗时和当前 step。
3. Step 行可点击打开详情。
4. 参数快照能显示本次命令和参数。
5. 日志能显示 stdout/stderr tail。
6. 停止按钮能根据 capability 显示为 `当前步骤结束后停止` 或后续 terminate 能力。
7. 完成后可打开故事板。
8. 失败后可 `从失败步骤重跑`。
9. 关闭弹窗不影响后台运行。
10. 重新打开页面后能根据 latest attempt 恢复查看状态。
11. Indicator 能显示 attempt family，区分普通 LLM analysis attempt 和 Analysis_V1 tool run attempt。
12. 活动运行存在时，运行按钮禁用并显示 active attempt。
13. step 菜单能设置 `运行到此步前暂停`，命中后显示 `继续运行`。
14. 刷新页面后能恢复运行弹窗内容、暂停点、当前 step 和参数快照。

### 12.3 测试

至少覆盖：

1. 全量运行 happy path。
2. 从 `02_02` 开始运行到 `04_03`。
3. 单独运行 `04_01`。
4. 缺 ASR 云端授权返回 blocked。
5. 当前步骤结束后停止。
6. 脚本失败后从失败步骤重跑。
7. 参数快照脱敏。
8. stdout/stderr tail 脱敏。
9. `completed_with_sync_error` 可见。
10. active run 并发冲突返回 409。
11. reused step 不重复计费。
12. 设置 `pause_before_step_id=04_01` 后，Runner 在 `04_01` 前进入 `paused`，resume 后继续执行。
13. 刷新页面后恢复 active / paused attempt 弹窗。
14. 前端 build 和基础 UI 自动化截图。

## 13. 分阶段实现

### M1：后端线性 run plan 合同

1. Analysis_V1 step specs 标准化。
2. 增加 plan 查询 API。
3. 增加统一 Indicator 查询 API。
4. 将当前 in-memory run state 落盘到 `SessionReport/tool_runs/attempt_{id}/run_state.json`。
5. 在 run state 和 session events 中写入 `attempt_family=analysis_v1_tool_run`。
6. 如需 schema 迁移，优先扩展 `openclip_attempts`，不新建平行 attempt 表。

### M2：运行范围和单步运行

1. 启动 API 支持 `mode/start_step_id/end_step_id/run_only_step_id`。
2. 所有运行 / 重新运行创建新 Attempt。
3. 实现前序 `reused` 状态。
4. 实现依赖检查和 blocked 输出。

### M3：可中断 runner

1. `subprocess.run()` 改为 `Popen()`。
2. stdout/stderr 流式写日志。
3. 支持 stop-after-current。
4. 支持 pause-before-step 和 resume。
5. 每秒更新 heartbeat。
6. terminate current process 仅预留接口，暂不作为 MVP 实现项。

### M4：前端 Indicator MVP

1. 重构当前运行弹窗。
2. 增加运行模式 / 起始步骤 / 结束步骤 / 单独运行某一步。
3. 增加 step 菜单：运行至此步、从此步开始、单独运行此步、重跑此步及后续、运行到此步前暂停、查看详情。
4. 增加步骤详情。
5. 增加参数快照和日志。
6. 支持刷新页面后恢复 active / paused attempt。
7. 保留现有 StoryBoard 结果入口。

### M5：测试和硬化

1. 后端 contract tests。
2. 前端 build。
3. 本地真实 Analysis_V1 smoke。
4. UI 截图检查。
5. secret redaction tests。

## 14. 关键结论

MVP 的正确方向不是复刻旧系统，也不是一步到位做通用工作流引擎，而是先把当前最痛的线性流程跑扎实：

```text
明确计划
  -> 新建 Attempt
  -> 可选择运行范围
  -> 可单步运行
  -> 可在步骤边界暂停和继续
  -> 可停止
  -> 可看实时参数和日志
  -> 可重跑失败步骤
  -> 可审计
```

分支和循环应在后续通过“后端展开为明确 step instances”的方式支持，而不是让前端或脚本运行时临时决定不可预测的流程。
