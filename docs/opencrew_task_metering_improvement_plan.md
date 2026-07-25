# OpenCrew Task 级计费追踪与展示改进方案

状态：改进方案 · 2026-06-15

相关文档：

- `docs/opencrew_llm_gateway_billing_design.md`
- `docs/opencrew_phase0_5_local_metering_billing_supplement.md`
- `docs/opencrew_local_artifact_billing_design.md`
- `docs/opencrew_provider_artifact_metering_implementation_plan.md`

## 1. 背景

现有本地计费底座已经具备基础能力：

- `local_usage_log` 记录 provider / model / modality / status / units / actual cost / estimated cost。
- `task_id / attempt_id / step_id / idempotency_key` 已进入表结构。
- `/api/local-metering/report` 能按时间窗口聚合 provider、modality、usage、成本、收费和利润。
- Analysis_V1 步骤详情中已有 step 级 metering tab，能把 usage 挂到每个 step 上展示。

但用户真实问题是：

> task 116 里每个动作用了多少 token、花了多少成本？总成本是多少？

当前系统能通过数据库和后端逻辑算出 task 116 总成本，但不能在 Metering 页面中清楚、可靠地自助查看。task 116 的核查也暴露出 attribution、单位和页面展示三个方向的缺口。

## 2. 当前问题

### 2.1 全局 Metering 页面没有 task 维度

`#/metering` 当前是全局时间窗口报表，只展示：

- totals
- by provider / model
- by modality
- pricebook
- recent usage details

缺少：

- task 搜索 / 过滤
- task / attempt / step 列
- 单 task 汇总
- 每个 action / step 的成本拆分
- 从 Analysis_V1 task detail 跳转到对应 metering 的入口

因此用户不能直接输入 `task 116` 查看费用。

更关键的是，当前页面的信息架构不是 task-first。首屏先展示全局 totals、provider/model 聚合、modality 聚合和价格规则，task 维度既不是默认入口，也不是主表。对于真实运营问题，Metering 页面的核心功能应该是“按 task 查账”，全局聚合只能作为 secondary view。

### 2.2 attempt attribution 不可靠

task 116 的生产数据中：

- `openclip_tasks.id = 116`
- `latest_attempt_id = 158`
- `local_usage_log` 中该 task 有 50 行 usage
- usage 只归到 attempt `152` 和 `154`
- latest attempt `158` 没有对应 usage 行

进一步检查 workspace 发现：

```json
{
  "task_id": 116,
  "current_attempt_id": 154
}
```

这说明当前 provider audit 仍从 `SessionContext/Variables.json.current_attempt_id` 读取旧 attempt。runner 创建了 attempt `158`，但没有把新的 attempt id 写回 Variables，也没有给非 00 step 通过 CLI/env 强制传入当前 attempt id。

影响：

- task 总成本按 `task_id` 还能粗略汇总。
- latest attempt 成本不可信。
- step 详情页按 attempt 展示时可能漏掉真实调用。
- 续跑、run-only-step、rerun-from-step 场景容易把新成本记到旧 attempt。

### 2.3 units 不统一，流量不可读

不同 provider 写入的 `units_json` 粒度不一致：

- Gemini TTS 有 `promptTokenCount / candidatesTokenCount / totalTokenCount`，可映射成 input/output token。
- xAI image / video 当前主要写 `cost_in_usd_ticks`，实际成本准确，但没有写 `image: 1`、`video_second`、输出文件或 duration。
- Sync lipsync 当前只写 `request: 1`，没有真实时长和成本。
- OpenCode 订阅路径没有真实 token 成本，只能通过 local artifact 计费表达一部分价值。

影响：

- 页面上能看到成本，但不能解释“流量”。
- 图片、视频、lipsync 的每动作成本难以对账。
- token 成本与媒体成本的展示口径不统一。

### 2.4 actual / estimated / charge 的含义不够明确

当前报表会优先使用 `actual_cost_micros`，没有实际成本时使用 pricebook 估算。页面同时展示 Actual Cost、Estimated Cost、Charge、Profit，但用户不一定能判断：

- 哪些是真实 provider 成本。
- 哪些只是本地 pricebook 估算。
- 哪些是给客户看的收费。
- 哪些行未计价或只按 artifact 计费。

## 3. 目标

改进后系统必须能稳定回答：

1. 某个 task 的总 provider 成本、收费、利润是多少。
2. 某个 task 的 latest attempt 成本是多少。
3. 某个 task 的每个 action / step 分别用了多少流量和成本。
4. 某个 step 内每次 provider 调用的 request id、model、usage、actual/estimated cost、charge 是什么。
5. 对每一行费用，能明确说明成本来源：actual provider response、local pricebook estimate、local artifact estimate、unpriced。

## 4. 非目标

1. 不在本阶段实现云端强计费账本。
2. 不改 `local_usage_log` 作为本地计费 source of truth 的地位。
3. 不把 OpenCode 订阅调用伪造成真实 token 成本。
4. 不用供应商费用长期反推视频时长；反推只能用于历史回填说明。
5. 不删除或重写历史 usage 行，历史纠偏以 backfill / diagnostic report 形式进行。

## 5. 目标体验

### 5.1 Metering 页面

Metering 页面的默认主视图必须是 **Task Billing**，而不是全局用量 dashboard。用户进入 `#/metering` 后，首屏应围绕“所有 task 的费用概览”组织，不能要求用户先手动输入 task id：

- 直接展示当前时间窗口内每个 task 的费用 overview 列表。
- 每行展示 task id、task title、task status、latest attempt id、requests、actual / estimated provider cost、charge、profit、usage summary、最近活动时间。
- 点击 task 行后，再展示该 task 的明细账单。
- task 明细可选选择 attempt：all attempts / latest / 指定 attempt。Metering 页面的 task 查询默认 **all attempts**，因为用户问的“task 总成本”应覆盖该 task 下全部可计量尝试；Analysis_V1 run detail 内的入口默认 latest attempt。
- 选中 task 后显示 task title、session id、task status、latest attempt id。
- 选中 task 后显示 totals：
  - requests
  - actual provider cost
  - estimated provider cost
  - charge
  - profit
  - cost basis counts
  - usage summary

推荐首屏顺序：

1. Task overview list：所有 task 的费用 overview，按用户计费 / 成本 / 最近活动排序。
2. Selected task cost summary：总 provider cost、charge、profit、requests、usage summary、cost basis。
3. Action Breakdown：按 step/action/modality 汇总。
4. Raw Calls：逐条 provider/local artifact 调用。
5. Diagnostics：归因异常、未计价行、缺失单位。

全局 totals、provider/model 聚合、modality 聚合和 pricebook 不应抢占首屏主位置，应放到 `Global Usage` / `Price Rules` tab，或作为 secondary section 展示。

新增 Action Breakdown：

| Step | Action | Requests | Usage | Cost Basis | Provider Cost | Charge | Profit |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: |
| 03_02 | TTSBuilderQuick | 6 | 2,202 input token / 4,229 output token | estimated | $0.0382 | $0.0956 | $0.0574 |
| 05_02 | VideoPlanExecutor / image | 29 | 29 images | actual | $0.7420 | $1.4840 | $0.7420 |
| 05_02 | VideoPlanExecutor / video | 4 | video seconds when available | actual | $0.9880 | $1.9760 | $0.9880 |

新增 Raw Calls：

- created_at
- request_id
- local_usage_id
- attempt_id
- step_id
- provider / model / modality
- status
- raw units
- normalized units
- cost basis
- actual cost
- estimated cost
- charge
- artifact / output references when available

### 5.2 Analysis_V1 task detail

在 task detail 顶部展示 task-level metering summary：

- Latest attempt cost
- All attempts cost
- Warning：如果 task 有 usage 行归属到非 latest attempt 且 created_at 晚于 latest attempt started_at，显示 attribution warning。

每个 step 详情继续展示 metering tab，但要增加：

- request id / local_usage_id
- actual vs estimated 标签
- unpriced reason
- 原始 usage JSON 折叠查看

## 6. 数据与归属改进

### 6.1 强制写入当前 attempt context

runner 创建新 attempt 后，必须立即把当前 attempt context 写入 workspace：

- `SessionContext/Variables.json.task_id`
- `SessionContext/Variables.json.current_attempt_id`
- `SessionContext/Variables.json.opencrew_session_id`
- `SessionContext/Variables.json.tool_use_session_id`
- `SessionContext/Variables.json.updated_at`

适用场景：

- run_all
- run_range
- run_from_step
- run_only_step
- rerun_all
- rerun_failed
- rerun_from_step
- pause/resume 后继续执行

建议新增 helper：

```python
sync_analysis_v1_run_context(task_id, session_id, attempt_id, workspace, tool_use_session_id="")
```

`sync_analysis_v1_variables_prompt_snapshot()` 只负责 prompt/model snapshot，不应承担运行归属更新；运行归属应由独立 helper 明确处理。

### 6.2 给子进程注入 env context

即使 Variables 文件陈旧，step 子进程也应收到当前运行上下文：

```text
OPENCREW_TASK_ID=116
OPENCREW_SESSION_ID=175
OPENCREW_ATTEMPT_ID=158
OPENCREW_STEP_ID=05_02
OPENCREW_TOOL_USE_SESSION_ID=...
```

`provider_audit.record_model_call_audit()` 归属优先级：

1. 显式函数参数 `task_id / attempt_id / step_id`
2. 环境变量 `OPENCREW_*`
3. `SessionContext/Variables.json`
4. 空值并记录 attribution warning

这样即使某些工具未改 CLI 参数，也不会把新调用记到旧 attempt。

### 6.3 step 级 idempotency 标准化

所有 provider 调用必须有稳定幂等键：

```text
model:{task_id}:{attempt_id}:{step_id}:{request_id}
artifact:{attempt_id}:{step_id}
```

要求：

- `record_model_call_audit()` 如果调用方没有传 `idempotency_key`，默认使用 `model:{task_id}:{attempt_id}:{step_id}:{request_id}`。
- audit JSON 中的 `idempotency_key` 必须和 DB 中一致。
- retry/resume 命中同一幂等键时返回既有 `local_usage_id`。

## 7. Usage 单位标准化

### 7.1 canonical units

`units_json` 是当前计费函数 `billable_units()` 的直接输入，因此不能无脑同时写 provider 原始 token 字段和规范字段。当前代码会把 `promptTokenCount` 映射成 `input_token`，也会把 `input_tokens` 映射成 `input_token`；如果两个字段同时存在，会重复计费。

因此采用以下规则：

1. `units_json` 中的 billable 字段优先写规范字段。
2. provider 原始 usage 保留在 `actual_cost_raw_json`、ModelCallAudit `usage_summary_raw` 或 artifact fact 中。
3. 兼容历史数据时，`billable_units()` 可以继续识别 provider 原始字段。
4. 如果必须在同一个 `units_json` 中保留 raw + canonical，则必须先改 `billable_units()`，让 canonical 字段存在时忽略对应 raw alias。

规范字段如下：

| 单位 | 含义 | 用途 |
| --- | --- | --- |
| `input_tokens` | 输入 token | token 成本与展示 |
| `output_tokens` | 输出 token | token 成本与展示 |
| `image` | 生成图片张数 | 图片成本与展示 |
| `video_second` | provider 生成视频秒数 | 视频成本与展示 |
| `audio_second` | provider 音频秒数 | ASR/TTS/lipsync 展示 |
| `request` | 请求数 | 无更细粒度时的兜底 |
| `artifact_json_kb` | JSON 产物 KB | 本地产物计费 |
| `artifact_image_kb` | 图片产物 KB | 本地产物计费 |
| `artifact_wav_kb` | WAV 产物 KB | 本地产物计费 |

历史兼容字段：

- `promptTokenCount` 可映射到 `input_tokens`
- `candidatesTokenCount` 可映射到 `output_tokens`
- `prompt_tokens` 可映射到 `input_tokens`
- `completion_tokens` 可映射到 `output_tokens`

`totalTokenCount / total_tokens` 只作为 raw diagnostic 展示，不参与重复计费。

### 7.2 provider 补齐要求

#### Gemini TTS

当前已有 token usage。需要：

- 保留 raw Gemini usage。
- 写入规范 `input_tokens / output_tokens`，但不要和 `promptTokenCount / candidatesTokenCount` 在同一 `units_json` 中同时参与计费；更推荐把 raw usage 移到 audit/raw 字段。
- 如果输出 wav duration 可得，额外写 `audio_second`，但默认不参与 token 计价，避免重复。

#### xAI image

当前有 actual cost。需要新增：

```json
{
  "image": 1,
  "cost_in_usd_ticks": 260000000
}
```

注意：当前 `provider_audit.units_from_response()` 一旦发现 `response.usage` 是非空 dict 就直接返回该 dict，不会再执行 image fallback。因此 `image: 1` 必须写入 `response.usage`，或修改 `units_from_response()` 在 provider usage 上合并 canonical fallback。

如能拿到输出尺寸，写入 artifact facts，不进入 `units_json` 计价。

#### xAI video

当前有 actual cost。需要新增：

```json
{
  "video_second": 4,
  "cost_in_usd_ticks": 2120000000
}
```

`video_second` 必须来自 provider response 或输出 mp4 duration。无法确定时不反推，标记 `duration_source=missing`。

注意：同 xAI image，`video_second` 必须进入最终传给 `record_model_call_audit()` 的 `response.usage`，或由 `units_from_response()` 合并外层 `duration / requested_duration_seconds`。

#### Sync lipsync

当前只写 `request: 1`。需要：

- 写入 `video_second` 或 `audio_second`。
- 如果 provider response 有成本，写 `actual_cost_micros`。
- 没有成本时继续按 pricebook 或 unpriced 明确展示。

#### OpenCode 订阅路径

不写 fake token，不写 fake actual cost。可选：

- 保留 local artifact metering。
- 未来如能从 OpenCode event 拿到 token usage，可写 `provider_mode=subscription`、`billing_mode=local_usage_only`，cost_basis 仍为 estimate 或 subscription allocation。

## 8. API 改进

### 8.1 Task metering endpoint

新增：

```text
GET /api/local-metering/tasks/{task_id}
```

Query：

- `attempt=all|latest|{attempt_id}`，Metering task 查询默认 `all`
- `include_items=true|false`，默认 true
- `include_raw=true|false`，默认 false

返回：

```json
{
  "schema_version": "1.0",
  "task": {
    "task_id": 116,
    "session_id": 175,
    "title": "...",
    "status": "completed",
    "latest_attempt_id": 158
  },
  "scope": {
    "attempt_mode": "latest",
    "attempt_ids": [158]
  },
  "totals": {},
  "by_attempt": [],
  "by_step": [],
  "items": [],
  "warnings": []
}
```

### 8.2 Attribution diagnostics

新增 diagnostics：

```text
GET /api/local-metering/tasks/{task_id}/diagnostics
```

检查项：

- usage attempt 是否属于该 task
- usage created_at 是否晚于 latest attempt started_at 但 attempt_id 不是 latest
- `SessionContext/Variables.json.current_attempt_id` 是否等于 latest attempt
- audit JSON `attempt_id` 与 DB `local_usage_log.attempt_id` 是否一致
- DB `idempotency_key` 是否为空
- 同一 request_id 是否重复落库

对 task 116 应能给出类似 warning：

```text
Variables current_attempt_id=154, latest_attempt_id=158; usage rows created after latest attempt start are attributed to attempt 154.
```

### 8.3 复用现有 report 聚合

不要复制一套价格计算逻辑。Task endpoint 应复用：

- `enrich_usage_row()`
- `add_amount()`
- `finalize_price_lines()`
- `group_rows()`

避免全局 Metering 和 task Metering 对同一行算出不同成本。

## 9. 前端改进

### 9.1 Metering 页面 task-first 改造

`#/metering` 不应继续以全局时间窗口报表作为主体验。新的页面结构：

1. 默认打开 `Task Billing` tab。
2. 首屏固定展示 Task ID 搜索框、attempt scope segmented control、刷新按钮。
3. 查询成功后，Task summary、Action Breakdown、Raw Calls 按这个顺序展示。
4. attribution warnings banner 必须贴近 task summary，不能藏在 raw detail 里。
5. `Global Usage` tab 保留当前 totals、provider/model、modality 和 recent usage。
6. `Price Rules` tab 展示 pricebook，避免价格配置表挤占 task 查账主流程。

Task Billing 新增：

- Task ID 搜索框
- Attempt scope segmented control：Latest / All / Attempt
- Task summary panel
- Action Breakdown 表
- Raw Calls 表
- Attribution warnings banner

明确不采用的方案：

- 不把 task id 做成全局 Usage Details 的普通过滤器。
- 不把 task 查询放在右侧 drawer。
- 不让用户先看 provider/model 聚合，再自己反推 task 成本。

### 9.2 Analysis_V1 页面

新增：

- task header 中展示 latest attempt cost 和 all attempts cost。
- 每个 run attempt 顶部展示 metering summary。
- step metering tab 中展示 local usage id、request id、raw units。
- 当 attribution diagnostics 有 warning 时，在 run detail 顶部显示。

## 10. 历史数据处理

### 10.1 不直接改历史账本

`local_usage_log` 是计费审计账本，历史行默认不 UPDATE。对于 task 116 这类归属问题，先做诊断展示。

### 10.2 可选 backfill

后续可提供只读 dry-run + 显式确认的修复脚本：

```text
scripts/backfill_local_usage_attempt_attribution.py --task-id 116 --dry-run
```

候选规则：

1. 只处理 `task_id` 明确匹配的行。
2. 如果 usage created_at 落在某个 attempt started/finished 窗口内，可建议归属到该 attempt。
3. 如果 audit JSON 的 `local_usage_id` 指向该行，且 audit attempt_id 与 DB 不一致，列为冲突。
4. 所有建议输出 CSV/JSON，不自动修改。

正式更新必须记录：

- before / after
- operator
- reason
- timestamp
- backup path

## 11. 实施分期

### Phase 1：归属修复

目标：新产生的 usage 不再归错 attempt。

工作：

1. 新增 `sync_analysis_v1_run_context()`。
2. attempt 创建后和 runner 启动前写入当前 attempt context。
3. 子进程 env 注入 `OPENCREW_TASK_ID / OPENCREW_ATTEMPT_ID / OPENCREW_STEP_ID`。
4. `provider_audit` 归属优先读取显式参数/env，再读 Variables。
5. `record_model_call_audit()` 默认生成 deterministic idempotency key。
6. audit JSON `idempotency_key` 与 DB `local_usage_log.idempotency_key` 必须来自同一个值；当前代码在 DB 传入参数和 audit 固定写 `request_id` 之间可能不一致。

验收：

- run-only-step 03_02 新产生的 TTS usage 归到新 attempt。
- run-only-step 05_02 新产生的 image/video usage 归到新 attempt。
- Variables 中 `current_attempt_id` 等于 latest attempt。
- `local_usage_log.idempotency_key` 非空。
- audit JSON 和 DB 行的 `idempotency_key` 一致。

### Phase 2：Task Metering API

目标：后端能直接回答 task 级费用问题。

工作：

1. 新增 `LocalMeteringService.task_report(task_id, attempt=...)`。
2. 新增 `/api/local-metering/tasks/{task_id}`。
3. 新增 diagnostics endpoint。
4. 增加 contract tests，覆盖 actual / estimated / local artifact / attribution warning。

验收：

- task 116 all-attempt report 返回总成本。
- latest attempt 无 usage 时明确返回 empty + warning，而不是误导性 0。
- by_step 与 raw items 成本加总等于 totals。

### Phase 3：前端 Task Metering 展示

目标：Metering 页面把“每个 task 的计费”作为第一优先级，用户可在页面自助查看。

工作：

1. Metering 页面默认进入 `Task Billing` tab。
2. Task Billing 首屏展示 task id lookup、attempt scope、task summary。
3. Task summary 下方依次展示 Action Breakdown、Raw Calls、Diagnostics。
4. 当前全局 totals/provider/model/modality 迁移到 `Global Usage` tab。
5. Pricebook 迁移到 `Price Rules` tab。
6. Analysis_V1 task detail 增加 metering summary。
7. Step metering item 增加 request id、local usage id、raw units drawer。
8. warnings banner 可点击查看 diagnostics。

验收：

- 打开 `#/metering` 时，首屏就是 task billing 查询和 task 成本摘要区域。
- 输入 `116` 后能看到 task 总成本、按 step 成本、raw calls。
- 用户能区分 actual / estimated / unpriced。
- latest attempt attribution 异常有明显提示。
- 不需要先阅读 provider/model 或 modality 聚合，就能回答“task 116 总成本是多少”。

### Phase 4：Provider units 补齐

目标：流量展示从“成本可见”升级为“成本和用量都可解释”。

工作：

1. xAI image 写 `image: 1`。
2. xAI video 写 `video_second`。
3. Sync lipsync 写 `video_second/audio_second` 和 actual cost when available。
4. Gemini TTS 写规范 `input_tokens / output_tokens`。
5. 对无法提供单位的 provider 写 `usage_quality=partial` diagnostic。

验收：

- task 116 类似任务中，05_02 image 行展示图片数。
- 05_02 video 行展示视频秒数。
- 03_02 TTS 行展示 input/output token。
- lipsync 不再只有 request，除非 provider 确实无法提供。

### Phase 5：历史诊断与回填

目标：历史任务可解释，必要时可修复。

工作：

1. backfill dry-run 脚本。
2. task diagnostics 页面暴露历史归属异常。
3. 管理员确认后允许生成修复 SQL 或执行修复。

验收：

- task 116 能生成 attribution mismatch report。
- dry-run 不修改 DB。
- 正式修复前要求备份并输出审计文件。

## 12. Contract Test 清单

新增或扩展测试：

1. `test_local_metering_task_report_contract.py`
   - task all attempts 汇总
   - latest attempt 汇总
   - by_step 加总等于 totals
   - actual cost 优先于 pricebook estimate
   - Metering task 查询默认 all attempts，Analysis_V1 run detail 默认 latest attempt

2. `test_analysis_v1_usage_attribution_contract.py`
   - 新 attempt 创建后 Variables 更新
   - 子进程 env 包含 attempt id
   - provider_audit 优先使用 env attempt id
   - run-only-step 不污染旧 attempt
   - audit JSON idempotency key 与 DB idempotency key 一致

3. `test_provider_usage_units_contract.py`
   - Gemini TTS raw usage 映射 input/output token
   - xAI image 写 image unit
   - xAI video 写 video_second unit
   - lipsync 写 duration unit 或明确 unpriced reason
   - raw token 字段和 canonical token 字段同时存在时不会双计

4. `test_metering_diagnostics_contract.py`
   - Variables current_attempt_id 与 latest_attempt_id 不一致时返回 warning
   - usage created_at 晚于 latest attempt started_at 但 attempt_id 非 latest 时返回 warning
   - idempotency_key 为空时返回 warning

## 13. Task 116 作为回归样例

当前观察值：

- task id：116
- session id：175
- latest attempt：158
- usage rows：50
- usage attempts：152、154
- latest attempt 158 usage rows：0
- task 全量 provider cost：约 `$1.7744`
- task 全量 charge：约 `$3.7286`
- `SessionContext/Variables.json.current_attempt_id = 154`

验收目标：

1. Task Metering 页面输入 `116` 能看到 all-attempt 总成本。
2. latest attempt 视图明确提示“latest attempt 158 没有 usage；检测到可能归属到旧 attempt 154 的新 usage”。
3. 修复后新跑一次 03_02 或 05_02，新增 usage 必须归到新 attempt。
4. `03_02` 显示 input/output token。
5. `05_02` image/video 显示图片数、视频秒数和 actual cost。

## 14. 风险与约束

1. `local_usage_log` 是本地审计账本，历史 UPDATE 必须谨慎。
2. 不同 provider usage schema 不一致，标准化应保留 raw usage，不能丢失原始字段。
3. OpenCode 订阅路径没有真实 per-token 成本，不应为了页面好看伪造 actual cost。
4. Task detail 和全局 Metering 必须复用同一价格计算函数，否则会出现账目不一致。
5. 续跑/暂停/恢复是 attribution 最容易出错的场景，必须作为核心测试覆盖。

## 15. 推荐优先级

最高优先级：

1. Phase 1 attribution 修复。
2. Phase 2 task report API。
3. Phase 3 task metering UI。

原因：如果 attempt attribution 不可靠，再漂亮的页面也会展示错误数据。先保证新数据正确，再补齐查询和展示，最后处理 provider units 与历史回填。

## 16. 代码对照自审（2026-06-15）

本节按当前代码重新审查本方案，结论是：总体方向正确，但实施时必须注意以下代码级约束。

### 16.1 高风险：canonical units 不能造成双计

当前 `backend/opcrew_backend/services/local_metering.py` 的 `billable_units()` 会把以下字段映射为同一计费单位：

- `input_tokens`、`promptTokenCount` → `input_token`
- `output_tokens`、`candidatesTokenCount` → `output_token`

因此如果 Gemini TTS 同时写入 raw `promptTokenCount` 和 canonical `input_tokens`，会重复计费。Phase 4 必须选择以下方案之一：

1. `units_json` 只保存 canonical billable fields，raw provider usage 放入 audit/raw 字段。
2. 修改 `billable_units()`，当 canonical 字段存在时忽略对应 raw alias。

### 16.2 高风险：`units_from_response()` 不会合并 fallback units

当前 `ToolLibrary/Analysis_V1/provider_audit.py` 中 `units_from_response()` 逻辑是：

1. 如果 `response.usage` / `usageMetadata` / `usage_metadata` 是非空 dict，直接返回。
2. 只有没有 usage dict 时，才按 modality fallback 写 `image: 1` 或 `video_second`。

这意味着 xAI 返回 `usage={"cost_in_usd_ticks": ...}` 时，不会自动补 `image: 1` 或 `video_second`。实现 Phase 4 时不能只在 response 外层写 `duration`，必须：

- 让 provider module 把 canonical units 写进 `response.usage`；或
- 修改 `units_from_response()`，在 raw usage 上合并 canonical fallback。

### 16.3 高风险：attempt context 只传给 step 00

当前 `OpenClip/backend/openclip_backend/router.py` 的 `analysis_v1_step_command()` 只有 step `00` 会带：

- `--task-id`
- `--session-id`
- `--attempt-id`

其他步骤只接收：

- `--workspace`
- `--print-json`
- 若干 provider/model 参数

而 `provider_audit.record_model_call_audit()` 在没有显式参数时读取 `SessionContext/Variables.json.current_attempt_id`。这正是 task 116 新调用被记到旧 attempt 154 的核心原因之一。Phase 1 必须同时做两件事：

1. 新 attempt 创建后写回 Variables。
2. 每个子进程 env 注入 `OPENCREW_ATTEMPT_ID`，并让 provider audit 优先读 env。

只改其中一个不够稳。

### 16.4 中风险：audit idempotency 与 DB idempotency 当前可能不一致

当前 `record_model_call_audit()` 调用 `local_usage_record_result(..., idempotency_key=idempotency_key)`，但写入 audit JSON 时固定：

```json
{"idempotency_key": request_id}
```

如果调用方传入了非空 `idempotency_key`，DB 行与 audit JSON 会不一致。Phase 1 要先归一：

```python
effective_idempotency_key = idempotency_key or f"model:{task_id}:{attempt_id}:{step_id}:{request_id}"
```

然后 DB 和 audit 都写同一个值。

### 16.5 中风险：Task endpoint 默认 latest 不适合“总成本”

用户问“task 116 总成本”时，默认 latest attempt 容易误导，尤其 task 116 当前 latest attempt 158 没有 usage 行。已将方案修订为：

- Metering task 查询默认 `attempt=all`
- Analysis_V1 run detail 默认 latest attempt
- latest 为空但 all 有成本时必须显示 attribution warning

### 16.6 中风险：Task API 不能复制价格逻辑

当前全局报表和 Analysis_V1 step metering 都依赖 `enrich_usage_row()` / `add_amount()` / `finalize_price_lines()`。Task endpoint 必须继续复用这些函数。

注意当前 `OpenClip/backend/openclip_backend/router.py` 内部已有一套 `analysis_v1_empty_metering_totals()` 等包装逻辑。实现时可以保留包装，但 cost/charge/profit 计算不得分叉。

### 16.7 中风险：历史 backfill 需要先做 diagnostics

task 116 证明历史数据可能存在归属漂移。直接 UPDATE `local_usage_log.attempt_id` 有审计风险。正确顺序是：

1. Task diagnostics 先展示异常。
2. backfill dry-run 输出候选修复。
3. 明确备份和审计记录后再允许正式修复。

### 16.8 方案调整后的最小落地顺序

最小安全实现顺序调整为：

1. Phase 1A：provider audit 归属优先级 + idempotency 归一 + contract tests。
2. Phase 1B：Analysis_V1 runner 写 Variables + 子进程 env。
3. Phase 2：Task Metering API，默认 all attempts，带 diagnostics warning。
4. Phase 3：前端 Metering task-first 改造，默认 Task Billing，再补 Analysis_V1 summary。
5. Phase 4：provider canonical units，先修 `billable_units()` 双计风险，再补 provider modules。
