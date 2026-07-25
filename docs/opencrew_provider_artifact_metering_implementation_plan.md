# OpenCrew Provider 产出物计量实施方案

状态：审核稿

日期：2026-06-05

相关文档：

- `docs/opencrew_llm_gateway_billing_design.md`
- `docs/opencrew_phase0_5_local_metering_billing_supplement.md`
- `docs/opencrew_local_artifact_billing_design.md`

## 1. 背景

现有 `local_usage_log` 已经能支撑 Metering 页面的核心账务口径：

- provider / model / modality
- request_id
- task_id / attempt_id / step_id
- status
- units_json
- actual_cost_micros
- charge / profit 的 pricebook 聚合

这对成本、收费、利润分析基本够用。最近核查 xAI/Grok 用量时暴露出一个缺口：

- xAI 图片和视频调用的真实费用可以从 `response.usage.cost_in_usd_ticks` 准确落库。
- 图片数量和视频数量只能按成功请求数推断。
- 视频真实时长没有直接落库，只能从 provider 费用反推，或扫描本地 mp4 文件补算。
- 产物路径主要在 `ModelCallAudit_*.json` 或工具输出结果中，不在 Metering 可直接查询的数据模型里。

这会影响运维、成本利润分析、客户账单解释和异常排查。尤其是不同 provider / model 的视频价格、生成时长、失败重试和本地后处理会逐步复杂，长期依赖反推不可持续。

## 2. 目标

新增一套 provider 产出物事实记录机制，使系统可以准确回答：

1. 每个 provider / model 在任意时间窗口内生成了多少张图片。
2. 每个 provider / model 在任意时间窗口内生成了多少个视频。
3. 每个 provider / model 在任意时间窗口内生成的视频总时长是多少。
4. 每个产物关联到哪次模型调用、哪个 task / attempt / step、哪个输出文件。
5. Metering 页面能在不改变现有费用口径的前提下展示这些产出物指标。

## 3. 非目标

1. 不改变 `local_usage_log` 作为计费账本的地位。
2. 不在产物表中重复记录成本、收费、利润。
3. 不把本地转码、字幕、合成产物错误地统计为 provider 生成量。
4. 不用 provider 费用反推出视频时长作为长期正式口径。
5. 不解决远端 billing server 的强计费、强上报、防篡改问题；本方案先服务本机 Metering 和运维分析。

## 4. Source of Truth 边界

本方案采用两个不同事实域，避免多个 source of truth。

### 4.1 调用和计费事实

`local_usage_log` 是唯一 source of truth：

- provider / model
- request_id
- modality
- task_id / attempt_id / step_id
- status
- raw usage
- actual cost
- pricebook charge / profit 聚合

所有成本、收费、利润只从 `local_usage_log` 聚合。

### 4.2 产出物事实

新增 `local_usage_artifacts` 作为唯一 source of truth：

- 这次 provider 调用实际产出了哪些文件
- 产物类型：image / video
- 产物来源：provider_output / local_postprocess / uploaded / backfilled
- 视频真实 duration_seconds
- 图片或视频尺寸、字节数、路径、hash

`local_usage_artifacts` 不存成本、收费、利润。需要 provider / model 维度时，正式报表通过 `local_usage_id` join `local_usage_log`；`request_id` 只用于写入时解析、审计和 backfill dry-run。

### 4.3 统计口径

必须把“provider 调用产出量”和“最终交付资产量”分开。

`image_count`

- 统计成功的 provider 图片输出记录数。
- 默认过滤条件：`artifact_kind = image`、`artifact_source = provider_output`、`artifact_role = primary_output`、`status = ok`。

`video_count`

- 统计成功的“产视频 provider 调用次数”，不是交付片段数，也不是最终合成文件数。
- 一个 segment 如果先由 Grok 生成 `*_Video_Raw.mp4`，再由 SyncSo lipsync 生成另一条视频，这应计为 2 个 provider video outputs，因为它们是两次付费 provider 视频产出。
- 本地 ffmpeg 换音轨、拼接、字幕、裁剪和 `*_TailFrame.jpg` 只记录为 `local_postprocess`，不进入 provider video_count。

`video_duration_seconds`

- 对 `provider_output + primary_output` 视频，表示 provider 直接输出的原始视频文件时长，用于成本和产能口径。
- 不把本地 `setpts` 变速、换音轨后的最终交付时长混入该字段的 provider 聚合。
- 如果需要展示最终交付时长，单独记录 `artifact_source = local_postprocess`、`artifact_role = final_delivery`，并在查询字段中命名为 `delivery_duration_seconds`。

## 5. 数据模型

新增表：`local_usage_artifacts`

```sql
CREATE TABLE local_usage_artifacts (
  id BIGSERIAL PRIMARY KEY,
  local_usage_id BIGINT REFERENCES local_usage_log(id) ON DELETE SET NULL,
  request_id TEXT NOT NULL,

  task_id TEXT,
  attempt_id TEXT,
  step_id TEXT,

  artifact_kind TEXT NOT NULL,      -- image | video | audio | json | other
  artifact_source TEXT NOT NULL,    -- provider_output | local_postprocess | uploaded | backfilled
  artifact_role TEXT NOT NULL,      -- primary_output | preview | tail_frame | intermediate | final_delivery

  path TEXT NOT NULL,
  path_kind TEXT NOT NULL DEFAULT 'workspace_relative', -- workspace_relative | absolute | external_url
  content_hash TEXT,
  byte_size BIGINT,

  width INT,
  height INT,
  duration_seconds DOUBLE PRECISION,
  duration_basis TEXT,              -- provider_raw | final_delivery | provider_reported | file_probe | derived
  mime_type TEXT,

  provider_artifact_id TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

  status TEXT NOT NULL DEFAULT 'ok', -- ok | missing | failed | deleted
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
```

建议索引：

```sql
CREATE INDEX idx_local_usage_artifacts_usage_id
  ON local_usage_artifacts(local_usage_id);

CREATE INDEX idx_local_usage_artifacts_request_id
  ON local_usage_artifacts(request_id);

CREATE INDEX idx_local_usage_artifacts_task_attempt_step
  ON local_usage_artifacts(task_id, attempt_id, step_id);

CREATE INDEX idx_local_usage_artifacts_kind_source
  ON local_usage_artifacts(artifact_kind, artifact_source);

CREATE UNIQUE INDEX uq_local_usage_artifacts_request_path_role
  ON local_usage_artifacts(request_id, path, artifact_role);
```

### 5.1 字段说明

`local_usage_id`

- 首选关联 `local_usage_log.id`。
- 如果 `record_local_usage()` 首次插入返回 id，直接使用。
- 如果重跑命中 idempotency conflict 导致返回空 id，artifact writer 必须立刻按 `idempotency_key` / `request_id` 回查补齐。
- 只有无法唯一确认 usage 行时才允许为空，并标记 `metadata_json.unresolved_local_usage_id = true`；正式 Metering report 默认排除这类行。

`request_id`

- 必填，和 `local_usage_log.request_id` 对齐。
- 用于跨进程、跨工具、回填和审计关联。
- ToolLibrary media path 的 `request_id` 来自 `provider_audit.request_id_for_call()`，按 `tool_name / asset_key / kind / provider / model_id / request` 做 SHA-256 确定性哈希。相同请求重跑得到相同 `request_id` 是预期幂等行为，不应被当作脏数据。

`artifact_source`

- `provider_output`：provider 直接生成的图片或视频。用于“各 provider/model 生成了多少产物”的核心统计。
- `local_postprocess`：本地转码、加字幕、拼接、换音轨等后处理产物。
- `uploaded`：用户上传或系统导入的素材。
- `backfilled`：历史数据回填产物记录。

`artifact_role`

- `primary_output`：本次 provider 调用的主产物。
- `preview`：预览图、缩略图。
- `tail_frame`：视频尾帧。
- `intermediate`：中间文件。
- `final_delivery`：最终交付文件。

Provider 产出物统计默认只算：

```text
artifact_source = provider_output
artifact_role = primary_output
status = ok
```

这样可以避免把本地合成视频、字幕版视频、尾帧图片重复算进 provider 生成量。

`duration_seconds` / `duration_basis`

- `provider_output + primary_output` 视频：`duration_seconds` 是 provider 直接输出文件的 raw duration，`duration_basis` 推荐为 `provider_raw` 或 `file_probe`。
- `local_postprocess + final_delivery` 视频：`duration_seconds` 是交付文件时长，`duration_basis` 推荐为 `final_delivery`。
- Provider response 直接给出的 duration 可以先记为 `provider_reported`；如果后续从文件探测到更准确值，应更新 metadata 记录来源。
- 费用反推秒数只能临时用于历史分析或 backfill dry-run，正式写入不使用 `derived`，除非明确标记为低可信并从默认报表排除。

## 6. 记录流程

### 6.1 模型调用成功后记录 usage

现有流程保持：

```text
provider response
  -> provider_audit.record_local_usage() / existing backend usage recorder
  -> local_usage_log
```

不改变成本计算逻辑。

注意：`ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py` 这条主媒体路径运行在 ToolLibrary 脚本边界内，当前通过 `ToolLibrary/Analysis_V1/provider_audit.py` 使用 psycopg / psycopg2 裸 SQL 写入 `local_usage_log`。它不能依赖 `backend/opcrew_backend/services` 中的 SQLAlchemy 服务类。

### 6.2 产物落盘成功后记录 artifact

新增流程：

```text
provider response saved to file
  -> inspect output file
  -> ToolLibrary path: provider_audit.record_local_usage_artifact()
  -> backend-owned path: backend read/write helper if needed
  -> local_usage_artifacts
```

图片记录：

- artifact_kind = image
- artifact_source = provider_output
- artifact_role = primary_output
- path
- byte_size
- width / height
- content_hash

视频记录：

- artifact_kind = video
- artifact_source = provider_output
- artifact_role = primary_output
- path
- byte_size
- width / height
- duration_seconds
- content_hash

视频时长必须来自实际文件元数据或 provider 明确返回的 duration 字段。优先级：

1. 实际落盘文件读取 duration。
2. Provider response 明确返回的 duration。
3. 后台回填任务读取文件 duration。
4. 禁止长期使用费用反推作为正式字段。

## 7. 写入位置

第一阶段建议优先接入以下路径：

### 7.1 Analysis_V1 VideoPlanExecutor

文件：

- `ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py`
- `ToolLibrary/Analysis_V1/provider_audit.py`

写入点：

1. xAI / Gemini / OpenAI 图片生成成功并保存 `*_Image_01.*` 后。
2. xAI / Wan / Gemini / OpenAI 视频生成成功并保存 `*_Video_Raw.mp4` 或 `*_Video_Final.mp4` 后。
3. 只把 provider 直接返回或下载的原始产物记为 `provider_output`。
4. 本地换音轨、字幕、合成、tail frame 记为 `local_postprocess`，但不计入 provider 生成量。

实现约束：

- 主写入函数放在 `ToolLibrary/Analysis_V1/provider_audit.py`，与 `record_local_usage()` 并排。
- 使用和 `record_local_usage()` 一致的 `database_url_for_workspace()`、`normalize_database_url()`、psycopg / psycopg2 裸 SQL 连接方式。
- `05_02_VideoPlanExecutor.py` 只 import `provider_audit.py` 中的函数，不 import backend service。
- backend 下的服务类只能作为 API 读侧聚合、管理后台查询，或 backend 自己拥有的生成路径使用，不能作为 ToolLibrary 主写入依赖。

### 7.2 Koubo StoryBoard Host/Product Builder

如果人物参考图、产品参考图调用 provider 生成图片，也要在图片保存成功后写 artifact。

### 7.3 其它媒体 provider

后续扩展到：

- OpenAI image / video
- Gemini image / video
- Wan video
- Sync lipsync
- TTS audio 产物

本方案重点先覆盖 image / video；audio 可复用同表，但 Metering UI 第一阶段可以不展示。

## 8. 查询与 Metering API

在 `/api/local-metering/report` 中增加 artifact 聚合，但不改变现有 totals 的成本口径。

新增字段建议：

```json
{
  "artifact_totals": {
    "provider_output": {
      "image_count": 51,
      "video_count": 55,
      "video_duration_seconds": 328.0
    }
  },
  "artifacts_by_provider_model": [
    {
      "provider": "xai",
      "model_id": "grok-imagine-video-1.5-preview",
      "modality": "video",
      "image_count": 0,
      "video_count": 55,
      "video_duration_seconds": 328.0
    }
  ]
}
```

推荐查询口径：

```sql
SELECT
  u.provider,
  u.model_id,
  u.modality,
  COUNT(*) FILTER (WHERE a.artifact_kind = 'image') AS image_count,
  COUNT(*) FILTER (WHERE a.artifact_kind = 'video') AS video_count,
  COALESCE(SUM(a.duration_seconds) FILTER (WHERE a.artifact_kind = 'video'), 0) AS video_duration_seconds
FROM local_usage_artifacts a
JOIN local_usage_log u
  ON u.id = a.local_usage_id
WHERE u.created_at >= :since
  AND u.status = 'ok'
  AND a.status = 'ok'
  AND a.artifact_source = 'provider_output'
  AND a.artifact_role = 'primary_output'
GROUP BY u.provider, u.model_id, u.modality;
```

如果同一业务 segment 经过多个付费视频 provider，例如 Grok raw video 后再 SyncSo lipsync，以上查询会按两条 provider video artifact 计数。这是本方案的 provider 成本/产能口径，和最终交付片段数不是同一个指标。

若部分历史行只有 `request_id`，则 reconciliation 完成前可以临时使用：

```sql
JOIN local_usage_log u
  ON u.id = a.local_usage_id OR u.request_id = a.request_id
```

上述 `OR request_id` 只能用于一次性诊断或 backfill dry-run，不能作为 Metering 正式报表查询。正式实现必须在写入 artifact 时立即补齐 `local_usage_id`，或者在 backfill 写入前先完成解析，避免历史 `request_id` / 空 `idempotency_key` 数据造成重复 join。

## 9. 前端展示

Metering 页面新增以下列或卡片：

- 图片数
- 视频数
- 视频总时长
- 平均视频时长
- 每秒真实成本
- 每张图真实成本
- 每个视频真实成本

推荐展示层级：

1. 总览：所有 provider 输出图片数、视频数、视频总时长。
2. Provider / model 明细：和现有成本表同一行展示 artifact 统计。
3. Task / attempt / step 明细：用于排障和利润复盘。

费用仍来自 `local_usage_log`，产物数量和时长来自 `local_usage_artifacts`。

## 10. 历史回填

需要提供一次性 backfill 脚本。

输入来源：

1. `local_usage_log` 的历史 image / video 记录。
2. `ModelCallAudit_*.json` 的 `request_id`、`output_summary`。
3. `S9_05_02_VideoPlanExecutor/Report/Result.json` 的 `segments[].model_calls` 和 outputs。
4. `SessionOutput/storyboard/Working/*_Image_01.*`、`*_Video_Final.mp4`。

回填规则：

1. 能通过 `request_id` 找到 `ModelCallAudit` 的，优先使用 audit 的 output path。
2. 找不到 audit 的，只在明确能用 task / attempt / asset_key 关联时回填。
3. 不能可靠关联的历史文件不写 `provider_output`，避免污染正式统计。
4. 回填必须先解析 `local_usage_id`；无法可靠解析的行只进入 dry-run 报告，不进入正式 Metering 统计。

注意：为了长期报表一致，建议回填时仍保留真实 source：

- 如果能确认文件是 provider 原始输出：`artifact_source = provider_output`，`metadata_json.backfilled = true`
- 如果只能确认是本地最终产物：`artifact_source = local_postprocess`，`metadata_json.backfilled = true`
- 如果不能确认真实来源：`artifact_source = backfilled`，并从默认 provider 产量报表排除。

## 11. 迁移与兼容

### 11.1 数据库迁移

必须同时修改两处：

1. `backend/opcrew_backend/db/schema.py`
2. `backend/opcrew_backend/db/migrations.py`

原因：

- `backend/opcrew_backend/db/bootstrap.py` 先执行 `metadata.create_all(engine)`，再执行 `run_migrations(engine)`。
- sqlite / contract test 环境的表结构主要来自 `schema.py`。
- `migrations.py` 里的存量迁移面向 Postgres 部署升级，会使用 `BIGSERIAL` / `JSONB` 等 PG 类型。
- 如果只改 `migrations.py`，sqlite 测试环境可能没有 `local_usage_artifacts` 表；如果只改 `schema.py`，存量 PG 部署不会升级。

`schema.py` 要新增 SQLAlchemy `Table("local_usage_artifacts", ...)`：

- 主键使用现有风格 `Column("id", BigInteger, Identity(), primary_key=True)`。
- JSON 字段使用 SQLAlchemy `JSON`，以兼容 sqlite contract tests。
- 列名、nullable、默认值要和 migration 保持语义对齐。
- 同步定义 indexes / unique constraint。

`migrations.py` 要新增下一号 migration：

- `CREATE TABLE IF NOT EXISTS local_usage_artifacts (...)`
- Postgres 使用 `BIGSERIAL`、`JSONB NOT NULL DEFAULT '{}'::jsonb`。
- 创建必要索引。
- 保证重复执行幂等。

两边必须保持以下列集合一致：

```text
id
local_usage_id
request_id
task_id
attempt_id
step_id
artifact_kind
artifact_source
artifact_role
path
path_kind
content_hash
byte_size
width
height
duration_seconds
duration_basis
mime_type
provider_artifact_id
metadata_json
status
created_at
updated_at
```

### 11.2 写入 API

写入 API 分两层，不能混成一个 backend service。

#### 11.2.1 ToolLibrary 主写入 API

在 `ToolLibrary/Analysis_V1/provider_audit.py` 新增裸 SQL writer，例如：

```python
def record_local_usage_artifact(
    *,
    database_url: str,
    request_id: str,
    local_usage_id: str | int | None = None,
    idempotency_key: str | None = None,
    task_id: str | int | None = None,
    attempt_id: str | int | None = None,
    step_id: str | None = None,
    artifact_kind: str,
    artifact_source: str,
    artifact_role: str,
    path: str,
    path_kind: str = "workspace_relative",
    byte_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: float | None = None,
    duration_basis: str = "",
    mime_type: str = "",
    content_hash: str = "",
    provider_artifact_id: str = "",
    metadata: dict[str, Any] | None = None,
    status: str = "ok",
) -> str:
    ...
```

实现要求：

- 使用 `normalize_database_url()` 和 psycopg / psycopg2，风格与 `record_local_usage()` 一致。
- `ON CONFLICT (request_id, path, artifact_role) DO UPDATE` 或等价 upsert，保证重跑同一请求不会重复计数。
- 不在 ToolLibrary 中 import `backend.opcrew_backend.services`。
- `record_model_call_audit()` 在调用 `record_local_usage()` 前应把 `idempotency_key` 归一为 `idempotency_key or request_id`，确保 ToolLibrary media path 的重跑幂等可被 usage 表稳定表达。

#### 11.2.2 `local_usage_id` 解析

不能长期依赖“后续 reconciliation job”补齐 `local_usage_id`。artifact writer 在写入前应当场解析：

```text
1. 如果调用方传入 local_usage_id，直接使用。
2. 如果 local_usage_id 为空且 idempotency_key 非空：
   SELECT id FROM local_usage_log WHERE idempotency_key = :idempotency_key LIMIT 1
3. 如果仍为空：
   SELECT id FROM local_usage_log
    WHERE request_id = :request_id
      AND task_id / attempt_id / step_id / provider / model_id / modality 能匹配时尽量匹配
    ORDER BY id DESC
    LIMIT 1
4. 如果仍无法唯一确认，写入 artifact 时允许 local_usage_id 为空，但 metadata_json.unresolved_local_usage_id = true，并让正式 Metering report 默认排除这些 unresolved 行。
```

`record_local_usage()` 当前使用 `ON CONFLICT (idempotency_key) DO NOTHING RETURNING id`。重跑命中冲突时可能返回空行，导致调用方拿不到 `local_usage_id`。因此 artifact writer 必须支持上面的 idempotency_key / request_id 回查路径，把重跑场景当场闭环。

#### 11.2.3 backend 读侧 API

backend 可以新增服务类，例如 `LocalUsageArtifactQueryService`，但职责限定为：

- Metering API 聚合查询。
- 管理后台查询 artifact 明细。
- backfill dry-run / reconciliation 管理。

只有 backend 自己拥有的生成路径才可以直接调用 backend writer。`05_02_VideoPlanExecutor.py` 这条 ToolLibrary 主路径不能依赖它。

### 11.3 文件探测工具

新增统一工具函数：

- image：用 Pillow 或 cv2 读取 width / height。
- video：优先 ffprobe；没有 ffprobe 时用 cv2 `VideoCapture` 读取 fps / frame_count / width / height。
- byte_size：`Path.stat().st_size`
- content_hash：可选，第一阶段可以只对小文件或按配置开启。

## 12. Rollout 计划

### Phase A：Schema 与写入/读侧分层

- 新增 `local_usage_artifacts` 表。
- `schema.py` 和 `migrations.py` 同时落表结构。
- 在 `ToolLibrary/Analysis_V1/provider_audit.py` 新增 `record_local_usage_artifact()` 裸 SQL writer。
- 在 backend 新增读侧聚合 helper / service。
- 新增文件探测工具。
- 新增 contract tests。

### Phase B：接入 xAI/Grok

- 接入 `05_02_VideoPlanExecutor` 的 xAI image / video 成功写入。
- 确保每次 provider 输出只写一条 `provider_output primary_output`。
- 确保 Grok raw video 和 SyncSo lipsync video 作为两次付费 provider 视频输出分别记录；ffmpeg 后处理不进入 provider 产量。
- 增加失败调用不写 ok artifact 的测试。

### Phase C：Metering API

- `/api/local-metering/report` 增加 artifact 聚合字段。
- 保持现有 totals / by_provider_model 不变。
- 前端 Metering 展示图片数、视频数、视频总时长。

### Phase D：历史回填

- 提供 dry-run。
- 输出可回填、不可回填、风险回填三类统计。
- 审核后执行回填。

### Phase E：扩展 provider

- OpenAI image / video
- Gemini image / video
- Wan video
- Sync lipsync
- TTS audio

## 13. 测试计划

### 13.1 Contract tests

必须覆盖：

1. sqlite `metadata.create_all()` 后 `local_usage_artifacts` schema 存在，索引存在。
2. 成本、收费、利润不从 artifact 表读取。
3. provider_output image 聚合为 image_count。
4. provider_output video 聚合为 video_count 和 duration_seconds。
5. local_postprocess 不计入 provider_output 聚合。
6. 同一 request_id + path + role 重复写入不会重复计数。
7. 没有 duration 的视频不应进入总时长，或应以 warning 暴露。
8. report 的现有 totals 与修改前一致。
9. `schema.py` 和 `migrations.py` 列集合一致，至少通过 contract test 校验核心列存在。

### 13.2 集成测试

1. 模拟 xAI 图片响应，保存图片，检查 `local_usage_log` 和 `local_usage_artifacts`。
2. 模拟 xAI 视频响应，保存 mp4，检查 duration_seconds。
3. 模拟视频本地后处理，检查 `local_postprocess` 不进入 provider 统计。
4. 模拟重跑同一 segment，检查 artifact 幂等。
5. 模拟 `record_local_usage()` 因 idempotency conflict 返回空 id，检查 artifact writer 能通过 idempotency_key / request_id 回查并写入 `local_usage_id`。
6. 模拟 Grok raw video + SyncSo lipsync video，检查 provider video_count 为 2，final_delivery 不计入 provider video_count。
7. 静态或 import 测试确认 `ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py` 不 import backend artifact service。

### 13.3 UI 测试

1. Metering 页面显示 provider/model 的图片数量。
2. Metering 页面显示视频数量和总时长。
3. 成本、收费、利润与旧版报告一致。
4. 过滤时间窗口后 artifact 数量随窗口变化。

## 14. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 重复统计本地后处理视频 | provider 产量虚高 | 默认只算 `provider_output + primary_output` |
| 产物写入拿不到 usage id | join 不完整 | artifact writer 先按 idempotency_key / request_id 当场回查 `local_usage_id`； unresolved 行默认不进正式报表 |
| 写入 API 放错进程边界 | ToolLibrary 调不到，线上不落表 | ToolLibrary 主写入放在 `provider_audit.py` 裸 SQL；backend service 只做读侧或 backend-owned 写入 |
| 只改 migration 不改 schema | sqlite / contract test 缺表 | `schema.py` 和 `migrations.py` 同步新增表和索引 |
| 历史 `request_id` / 空 `idempotency_key` 关联不唯一 | 回填重复 | 新 ToolLibrary media path 使用确定性 request_id + idempotency_key 表示幂等；历史回填必须先解析 local_usage_id |
| 视频 duration 读取失败 | 时长缺失 | report 暴露 missing_duration_count |
| 把交付片段数当作 provider 视频数 | video_count 口径错误 | video_count 定义为产视频 provider 调用次数；交付资产另用 final_delivery 统计 |
| 产物被删除 | 报表和文件不一致 | status 标记 deleted/missing；保留历史事实 |
| Artifact 表重复成本字段 | 多 source of truth | 禁止存成本、charge、profit |
| 把费用反推秒数固化 | 长期口径不准 | duration 只来自文件/provider 明确字段 |

## 15. 审核问题

需要审核确认：

1. 表名是否采用 `local_usage_artifacts`。
2. `artifact_source` 枚举是否足够：`provider_output / local_postprocess / uploaded / backfilled`。
3. Provider 产出物统计是否默认只算 `provider_output + primary_output + ok`。
4. 未解析 `local_usage_id` 的 artifact 是否默认从 Metering 正式报表排除，只在 diagnostics/backfill 页面展示。
5. Metering 页面是否第一阶段只展示 image/video，不展示 audio/json。
6. 历史回填是否只做 dry-run，不自动写入。
7. 最终交付资产数量和交付时长是否需要第一阶段在 Metering 展示；如果需要，字段应与 provider_output 产量分开命名。

## 16. 验收标准

1. 新生成的 xAI/Grok 图片调用，在 Metering API 中能看到 image_count 增加 1。
2. 新生成的一次 xAI/Grok 视频 provider 输出，在 Metering API 中能看到 provider video_count 增加 1，video_duration_seconds 为该 provider raw 输出文件时长。
3. 同一 provider/model 的 actual_cost、charge、profit 与现有 Metering 报表完全一致。
4. 本地合成、字幕、转码产物不会进入 provider_output 统计。
5. 重跑不会重复统计同一个 request/path/role。
6. 可以按 24h、48h、task、attempt、provider、model 查询图片数量、视频数量和视频总时长。
7. Grok raw video + SyncSo lipsync 同一 segment 场景下，provider video_count 按两次付费 provider 视频输出计数；final_delivery 另算。
8. sqlite contract tests 和 Postgres migration 路径都能创建同一张 `local_usage_artifacts` 表。
