# OpenCrew Phase 0.5 本地授权计量与收费补充设计

版本：v0.1

日期：2026-06-02

状态：讨论稿，作为 `docs/opencrew_llm_gateway_billing_design.md` 的补充

适用范围：在 **无 Managed Gateway** 的早期交付阶段，真实 provider key 与业务脚本仍受保护地存放在 Mac mini 本机，但用户必须持有 OpenCrew 签发的唯一授权 key 才能使用这些能力；模型请求仍由 Mac mini 直连 provider，用量由本机记录并上报运营方 billing server。

---

## 1. 定位与边界

本文定义一个介于 `Phase 0 Local Box Trial` 与 `Phase 1 Managed Gateway` 之间的过渡商业化模式：

**Phase 0.5：Licensed Local Metering**

核心目标：

1. 在不建设 provider 转发网关的前提下，让早期 Mac mini 交付具备授权、用量上报、账单、利润核算与停用能力。
2. 用户必须使用 OpenCrew 签发的唯一授权 key 激活设备，才能调用本机预置的真实 provider key。
3. 所有 LLM / 图片 / 视频 / 音频 / ASR 调用从 Mac mini 直连 provider，不经过运营方 gateway。
4. 本机统一 broker/resolver 记录用量并上传运营方 billing server。
5. 通过本地授权门禁、远端用量上传、provider 侧硬预算、每设备独立 key 来控制商业风险。

非目标：

1. 不承诺 gateway 级强计费。客户物理持有 Mac mini，本地计量可以被 root 级攻击者绕过或篡改。
2. 不把 billing server 作为 provider API 转发层；billing server 不注入 provider key，不转发模型请求，不承载媒体字节。
3. 不以本地代码保护替代 provider 侧预算、限速、独立子账号和吊销策略。
4. 不解决所有合规问题。境外 provider、mihomo、生成式 AI 服务备案等仍按主设计文档 §14 处理。

一句话边界：

> Phase 0.5 可以做早期可运营收费闭环，但不是不可绕过的强计费系统；真正强扣费、强限额、强审计仍需要 Phase 1 Managed Gateway。

## 2. 总体架构

```
用户浏览器
  -> OpenCrew Web
  -> 本机 Plan Runner / ModelBroker
  -> 本机 provider resolver
      -> 校验本地 license lease
      -> 从盒子本地密钥库解析 auth_ref
      -> 选择 direct / mihomo 出口
  -> Mac mini 直连 provider

同时：
  -> local_usage_log
  -> usage_upload_queue
  -> 运营方 billing server
      -> 授权状态
      -> 价格本
      -> 用量事件
      -> 余额 / 账单 / 毛利报表
```

组件职责：

| 组件 | 位置 | 责任 |
| --- | --- | --- |
| OpenCrew Web | Mac mini | 登录、任务运行、模型配置、授权状态、用量展示 |
| ModelBroker / provider resolver | Mac mini | 模型调用唯一入口、license 检查、key 解析、用量记录 |
| 本地密钥库 | Mac mini | 存真实 provider key、mihomo 订阅 URL；数据库只存 `api_key_ref/has_key` |
| `local_usage_log` | Mac mini DB | 本地诊断、客户自查、UI 展示 |
| `usage_upload_queue` | Mac mini DB | 待上传的签名用量事件，支持断网重试 |
| billing server | 运营方 | 激活、授权续期、价格本、用量接收、账单、停用、运营报表 |
| provider | 第三方 | 实际模型服务与 provider 侧账单 |

关键原则：

- 业务脚本不直接读取 provider key。
- 业务脚本不直接调用 billing server 作为账单真相。
- 业务脚本可以提供计量辅助元数据，例如图片尺寸、生成秒数、输出文件路径、provider task id。
- 本机 ModelBroker / resolver 才是本地调用和用量记录的强制入口。

## 3. 唯一授权 Key 与 License Lease

### 3.1 概念

用户拿到的“唯一 key”建议定义为：

`OpenCrew activation/license key`

它不是 provider key，也不是网页登录密码。它用于把一台 Mac mini 激活到一个客户、套餐、设备和价格层级。

授权 key 的生命周期：

1. 运营方创建客户和订单，生成一次性或长期 activation key。
2. Mac mini 首次初始化时输入 activation key。
3. Mac mini 向 billing server 激活，提交 `box_id`、设备指纹、公钥、版本信息。
4. billing server 校验后返回 signed license lease。
5. Mac mini 保存 license lease 与设备私钥；后续调用 provider 前先校验 lease。
6. lease 到期前自动刷新；刷新失败进入宽限期；超过宽限期后禁止使用运营方预置 provider key。

### 3.2 License Lease 内容

建议 lease 使用 JWS/JWT 或等价签名 JSON，Mac mini 内置运营方 public key 校验签名。

```json
{
  "schema_version": "1.0",
  "license_id": "lic_...",
  "customer_id": "cus_...",
  "box_id": "box_...",
  "price_tier": "default",
  "status": "active",
  "billing_mode": "licensed_local_metering",
  "provider_mode": "local_box",
  "entitlements": {
    "modalities": ["chat", "image", "video", "tts", "asr"],
    "models": ["gpt-5.5", "gemini-tts", "wan-video"],
    "max_daily_sell_micros": 5000000000,
    "max_single_request_sell_micros": 200000000
  },
  "usage_policy": {
    "requires_upload": true,
    "offline_grace_seconds": 86400,
    "max_unuploaded_events": 500,
    "high_cost_requires_online_reserve": true
  },
  "issued_at": 1780339200000,
  "expires_at": 1780425600000
}
```

### 3.3 Gate 点

本机必须在以下位置强制检查 lease：

1. provider resolver 解包 `auth_ref` 前。
2. ModelBroker 发起 provider 请求前。
3. 高成本媒体任务提交前。
4. OpenCode / 对话模型调用前，如果该调用使用运营方预置 key。
5. Web UI 保存或测试运营方预置 provider 配置前。

如果 lease 失效：

- 客户 BYOK 模式可以继续按合同策略运行。
- 使用运营方 provider key 的调用必须拒绝。
- UI 显示授权过期、欠费、未上报用量过多或设备解绑等具体原因。

## 4. Billing Server 职责

Phase 0.5 的 billing server 是控制面和账务系统，不是 provider gateway。

最小职责：

1. 激活设备：校验 activation key，绑定 `box_id`。
2. 签发和刷新 license lease。
3. 下发价格本、模型白名单、套餐限制。
4. 接收本机上传的 usage events。
5. 按价格本生成账单、余额扣减或应收账款。
6. 汇总客户、设备、模型、脚本维度的成本、售价和毛利。
7. 返回停用、欠费、超额、需升级、需轮换 key 等状态。

明确不做：

1. 不代理 provider 请求。
2. 不持有所有真实 provider key 作为转发用途。
3. 不转存媒体字节。
4. 不作为强防绕过边界。

## 5. 本地用量记录与上报

### 5.1 本地两层记录

继续保留主设计文档中的 `local_usage_log`，用于本机 UI、诊断和试用复盘。

Phase 0.5 需要新增 `usage_upload_queue`，用于向运营方 billing server 上传账单事件。

建议本地表：

```sql
usage_upload_queue (
  id BIGSERIAL PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL,
  sequence_no BIGINT NOT NULL,
  event_hash TEXT NOT NULL,
  previous_event_hash TEXT,
  payload_json JSONB NOT NULL,
  signature TEXT NOT NULL,
  status TEXT NOT NULL, -- pending | uploaded | failed | rejected
  retry_count INT NOT NULL DEFAULT 0,
  last_error_code TEXT,
  created_at BIGINT NOT NULL,
  uploaded_at BIGINT
)
```

字段说明：

- `event_id`：本地生成，上传幂等键。
- `request_id`：一次模型调用的幂等键。
- `sequence_no`：设备内单调递增，帮助发现缺口。
- `event_hash/previous_event_hash`：形成 hash chain，降低静默删改事件的风险。
- `signature`：用设备私钥签名 payload，server 侧验证来源。
- `status`：上传队列状态。

注意：签名与 hash chain 只能证明“收到的事件来自某设备且未被传输篡改”，不能证明“设备没有漏报”。漏报风险仍由 lease 刷新、上传水位、provider 硬预算和抽样对账控制。

### 5.2 Usage Event Schema

```json
{
  "schema_version": "1.0",
  "event_id": "usevt_...",
  "request_id": "req_...",
  "customer_id": "cus_...",
  "license_id": "lic_...",
  "box_id": "box_...",
  "tool_session_id": "tus_...",
  "opencrew_session_id": 92,
  "script_id": "03_01_TTSBuilderG",
  "provider": "google",
  "model_id": "gemini-2.5-flash-preview-tts",
  "modality": "tts",
  "provider_mode": "local_box",
  "billing_mode": "licensed_local_metering",
  "proxy_policy": "direct",
  "status": "ok",
  "units": {
    "audio_second": 37,
    "request": 1
  },
  "metering_source": "provider_usage|local_file_probe|script_hint|tokenizer_estimate",
  "provider_request_id": "optional-provider-id",
  "provider_task_id": "optional-provider-task-id",
  "result_ref": "SessionOutput/tts/xxx.wav",
  "started_at": 1780339200000,
  "finished_at": 1780339280000,
  "error_code": null
}
```

不得写入 usage event：

- provider key
- Authorization header
- mihomo 订阅 URL
- 完整 prompt
- 用户上传原文或敏感素材内容
- 可直接下载私有文件的长期 URL

### 5.3 上传策略

上传策略：

1. provider 调用完成后立即写本地 usage event。
2. 后台 uploader 批量上传 pending events。
3. billing server 按 `event_id/request_id` 幂等接收。
4. 上传成功后本地标记 `uploaded`。
5. 上传失败指数退避重试。
6. `pending` 数超过 lease 中 `max_unuploaded_events`，禁止继续高成本调用。
7. 超过 `offline_grace_seconds` 未成功刷新 lease，禁止继续使用运营方 provider key。

高成本调用建议在线化：

- 文本类低成本调用可以在 lease 有效期内离线运行一段时间。
- 图片、视频、批量 TTS 等高成本调用应先向 billing server 做 quote/reserve；无法联网时拒绝或要求管理员确认。

## 6. 计量规则

计量必须按模型、provider 和 modality 版本化，写入价格本。

### 6.1 文本 / Chat

计量单位：

- `input_token`
- `output_token`

优先级：

1. 使用 provider 返回的官方 usage。
2. provider 未返回时，使用与模型匹配的 tokenizer 本地估算。
3. OpenCode 不暴露 usage 时，ModelBroker 应记录 prompt/response 的 token 估算，不记录完整 prompt 内容。

计费建议：

- input/output token 分开定价。
- 流式中断按已产生 output token 计。
- 工具内部重试默认计入运营成本，不重复向客户收费，除非客户主动重复生成。

### 6.2 图片

计量单位：

- `image`
- 维度桶：`size_bucket`，例如 `1024x1024`、`1024x1536`、`1536x1024`
- 可选质量桶：`quality`

计量来源：

1. provider 返回的生成数量和尺寸。
2. 本地落盘文件 probe 的尺寸。
3. 脚本返回的 `result_paths` 和 metadata。

收费建议：

```
units = generated_image_count
price_key = model_id + size_bucket + quality
sell = units * sell_micros_per_image
```

### 6.3 视频

计量单位：

- `video_second`
- 可选维度桶：`resolution_bucket`
- 可选质量桶：`quality`

计量来源：

1. provider usage / task metadata。
2. 本地 `ffprobe` 读取输出视频时长。
3. 脚本提交参数中的目标时长，仅作为 fallback。

收费建议：

```
billable_seconds = ceil(output_duration_seconds)
sell = billable_seconds * sell_micros_per_second
```

需要规定：

- 最小计费秒数，例如 1 秒或 5 秒。
- 失败但 provider 已扣费时，客户是否收费。建议默认客户不收费，成本进入运营毛利损耗。
- 同一任务重试生成多个废片时，只对最终交付物收费；废片成本由运营方承担或进入高级套餐成本。

### 6.4 TTS / 音频生成

计量单位优先级：

1. provider 原生计费单位。如果 provider 按字符收费，则记录 `character`。
2. 面向客户展示可以折算为 `audio_second`。
3. 如果 provider 按秒计费，则以 `ffprobe` 输出音频秒数为准。

建议同时记录：

- `character`
- `audio_second`
- `request`

最终收费使用价格本指定的 primary metric。

### 6.5 ASR

计量单位：

- `audio_second`

计量来源：

1. 输入音频文件 `ffprobe` 时长。
2. provider 返回 usage。

收费建议：

```
billable_seconds = ceil(input_audio_seconds)
sell = billable_seconds * sell_micros_per_second
```

### 6.6 失败、取消和重试

统一规则：

| 情况 | 客户收费 | 运营成本 |
| --- | --- | --- |
| provider 未被调用前失败 | 不收费 | 无 |
| provider 调用失败且未产生成本 | 不收费 | 无 |
| provider 已产生成本但未给客户可用结果 | 默认不收费 | 记录 wholesale 损耗 |
| 客户主动取消文本流式 | 按已生成 token 收费 | 按 provider 实际成本 |
| 客户主动重复生成 | 新 request，正常收费 | 正常记录 |
| 系统内部重试 | 默认只对最终成功结果收费 | 重试成本计入运营损耗 |

## 7. 价格本与利润

Phase 0.5 billing server 应维护价格本。价格本既要能表达 provider 成本，也要能表达客户售价。

建议字段：

```sql
price_book (
  id BIGSERIAL PRIMARY KEY,
  model_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  modality TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'default',
  metric TEXT NOT NULL,
  unit_bucket TEXT NOT NULL DEFAULT 'default',
  wholesale_micros_per_unit BIGINT NOT NULL,
  sell_micros_per_unit BIGINT NOT NULL,
  currency TEXT NOT NULL,
  fx_rate_micros BIGINT,
  effective_from BIGINT NOT NULL,
  effective_to BIGINT
)
```

利润计算：

```
gross_margin_micros = sell_micros - wholesale_micros
gross_margin_rate = gross_margin_micros / sell_micros
```

售价建议不要只做固定百分比。更稳的公式：

```
sell_price =
  provider_cost
  + provider_cost * target_markup_rate
  + failure_retry_buffer
  + fx_buffer
  + infra_support_buffer
  + minimum_margin
```

早期商业包装建议：

1. 设备/安装费：覆盖 Mac mini、部署、初始化。
2. 月度软件授权费：覆盖产品功能、维护、升级。
3. 包含一定模型额度：降低客户试用心理门槛。
4. 超额按 rate card 计费：图片按尺寸和张，视频/音频按秒，文本按 token。
5. 高成本模型单独开关：视频和境外高价模型默认需要余额或管理员授权。
6. 每客户月封顶：超过封顶后停用运营方 key 或要求充值。

## 8. 与业务脚本的关系

Phase 0.5 不建议让每个业务脚本自行完成完整收费逻辑。原因：

- 多脚本各自上传会导致字段口径不一致。
- retry/resume/idempotency 难以统一。
- secret redaction 容易漏。
- 后续迁移 Managed Gateway 时会出现双账本漂移。

推荐职责划分：

| 层 | 责任 |
| --- | --- |
| 业务脚本 | 调用 ModelBroker；提供 `script_id/tool_session_id/request_id`；返回结果路径和计量辅助 metadata |
| ModelBroker | 发起 provider 请求、记录 request/response audit、生成 usage event、写本地队列 |
| provider resolver | 校验 license、解析 `auth_ref`、选择 proxy policy、拒绝未授权模型 |
| uploader | 批量上传 usage events |
| billing server | 定价、账单、余额、报表、停用 |

过渡期如果某些 Analysis_V1 脚本仍直接调用 provider，必须先接入受控 wrapper：

1. wrapper 负责 license 检查。
2. wrapper 负责 key 解析。
3. wrapper 负责 request_id/idempotency。
4. wrapper 负责本地 usage event。
5. wrapper 禁止把 key、Authorization、完整 prompt 写入日志。

## 9. Billing Server API 草案

### 9.1 激活

`POST /v1/activation/activate`

请求：

```json
{
  "activation_key": "ock_act_...",
  "box_id": "box_...",
  "device_public_key": "base64...",
  "app_version": "0.7.0",
  "hardware_fingerprint": "..."
}
```

响应：

```json
{
  "license": "{signed-license-lease}",
  "price_book_version": "2026-06-01.default",
  "server_time": 1780339200000
}
```

### 9.2 Lease 刷新

`POST /v1/license/refresh`

请求：

```json
{
  "license_id": "lic_...",
  "box_id": "box_...",
  "last_uploaded_sequence_no": 1288,
  "pending_event_count": 4,
  "app_version": "0.7.0"
}
```

响应：

```json
{
  "license": "{signed-license-lease}",
  "action": "continue|warn|suspend",
  "message": null
}
```

### 9.3 用量上传

`POST /v1/usage/events`

请求：

```json
{
  "box_id": "box_...",
  "events": [
    {
      "event_id": "usevt_...",
      "sequence_no": 1289,
      "payload": {},
      "signature": "base64..."
    }
  ]
}
```

响应：

```json
{
  "accepted": ["usevt_..."],
  "rejected": [],
  "last_accepted_sequence_no": 1289
}
```

### 9.4 价格本

`GET /v1/price-book?version=current&tier=default`

响应：

```json
{
  "version": "2026-06-01.default",
  "currency": "CNY",
  "items": []
}
```

### 9.5 高成本请求预授权（可选但建议）

`POST /v1/usage/reserve`

用于图片、视频、批量 TTS 等高成本请求。无 gateway 时它不是绝对强制边界，但能降低欠费和滥用风险。

```json
{
  "request_id": "req_...",
  "box_id": "box_...",
  "model_id": "wan-video",
  "modality": "video",
  "estimated_units": {
    "video_second": 20
  }
}
```

响应：

```json
{
  "status": "reserved|rejected",
  "reserve_id": "rsv_...",
  "max_sell_micros": 120000000,
  "reason": null
}
```

## 10. 安全与风控

必须承认的事实：

- 客户物理持有 Mac mini。
- 守护进程能自动解包真实 provider key，root 级攻击者理论上也能提取。
- 本地 usage 上传不能证明完整性，只能提高运营可见性与绕过成本。

Phase 0.5 的可执行风控：

1. 每台设备独立 provider key 或 provider 子账号。
2. provider 侧设置硬预算、RPM/TPM、模型白名单。
3. activation/license key 与 `box_id` 绑定。
4. license lease 短有效期，自动刷新。
5. pending usage 过多或长时间不上报时停用运营方 key。
6. 高成本调用在线 reserve。
7. 本地密钥库不落明文 key，日志和支持包脱敏。
8. 发布包不交付源码 repo、测试目录、开发脚本和 `.git`。
9. 运营后台监控 provider 账单与本地上报差异。
10. 试用结束或异常时吊销/轮换该设备 provider key。

建议告警：

- provider 账单增长但 usage event 未增长。
- 设备超过 `offline_grace_seconds` 未上线。
- sequence_no 出现缺口。
- 单设备单日成本异常。
- 同一 activation key 绑定多台设备。
- 本地版本过旧，不支持最新 metering policy。

## 11. UI 与运营流程

Mac mini Web UI：

1. 授权状态：active / expiring / expired / suspended / upload_overdue。
2. 当前套餐、模型权限、到期时间、剩余额度。
3. 本地用量统计：按任务、工具、模型、modality 展示。
4. 上传状态：pending、失败原因、最近成功上传时间。
5. 高成本模型开关与提示。
6. 欠费/停用时显示明确行动：续费、联系运营、切换 BYOK。

运营后台：

1. 客户、设备、license、套餐管理。
2. 用量、账单、充值、发票或应收。
3. 成本、售价、毛利报表。
4. provider key 分配、预算、吊销记录。
5. 异常设备告警。
6. 价格本版本发布。

## 12. 实施阶段

### M0：文档与口径

- 明确 Phase 0.5 的商业边界。
- 确认“不是 gateway 级强计费”的销售和合同表述。
- 定义首批模型的计费单位和价格本。

### M1：本机 License Gate

- 新增本地 license service。
- activation key 输入与 lease 保存。
- provider resolver 解包 key 前校验 lease。
- lease 失效时拒绝运营方 provider key。

### M2：本地 Usage Event

- 扩展 ModelBroker 用量记录。
- 新增 `usage_upload_queue`。
- 实现 request_id、event_id、sequence_no、hash chain、signature。
- 业务脚本只返回 metering hints，不直接写账单。

### M3：Billing Server MVP

- activation / refresh / price-book / usage upload API。
- usage events 幂等入库。
- 价格本计价。
- 客户账单和运营报表最小闭环。

### M4：高成本风控

- 图片、视频、批量 TTS 请求前 reserve。
- pending usage / offline grace 超阈值停用。
- provider 预算配置检查和告警。

### M5：对账与商业化

- provider 账单导入或手工对账。
- 成本、售价、毛利分析。
- 试用转正式、套餐、充值、月封顶。
- 异常设备处置流程。

## 13. 待决问题

1. Phase 0.5 首批是否默认预付余额，还是月结后付。
2. 每台设备 provider 侧默认日/月预算是多少。
3. 高成本调用的阈值，例如超过多少预估金额必须在线 reserve。
4. 离线宽限期设置，建议 24 小时以内。
5. 本地 usage 与 provider 账单差异超过多少触发停用。
6. 哪些模型允许 Phase 0.5，哪些必须等 Managed Gateway。
7. 客户 BYOK 模式是否允许绕过 OpenCrew 计费，仅收软件授权费。
8. 合同中如何描述本地计量、停用、provider 成本转嫁和失败重试。

## 14. 与主设计文档的关系

本文不替代 `opencrew_llm_gateway_billing_design.md`：

- 主文档的 Phase 0 仍是最保守的 Local Box Trial：本地 usage 只做诊断，不做强计费。
- 本文定义的 Phase 0.5 是新增商业化过渡模式：无 provider gateway，但有授权、上报、账单和利润核算。
- 主文档的 Phase 1 Managed Gateway 仍是长期目标：真实 key 迁到运营方网关，强制计量、余额、限额和扣费。

建议后续在主文档 §1、§6.1、§7.9、§8、§12、§15 增加对本文的引用，并把模式表扩展为：

| 模式 | `provider_mode` | `billing_mode` | 真实 key 位置 | 计费强度 |
| --- | --- | --- | --- | --- |
| Phase 0 Local Box Trial | `local_box` | `local_usage_only` | Mac mini | 诊断/试用，不做强收费 |
| Phase 0.5 Licensed Local Metering | `local_box` | `licensed_local_metering` | Mac mini | 授权 + 本地计量 + 上报收费，弱强制 |
| Phase 1 Managed Gateway | `managed_gateway` | `managed_resale` | 运营方网关 | 强计量、强限额、强扣费 |
| Local Direct BYOK | `local_byok` | `customer_byok` | 客户环境 / Mac mini | 不赚模型差价，只收软件服务费 |
