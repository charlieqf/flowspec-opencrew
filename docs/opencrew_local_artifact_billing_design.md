# OpenCrew 本地产出物计费设计（Local Artifact Billing）

状态：设计稿 · 2026-06-03

## 1. 背景与目标

现有计费只覆盖 **LLM / 媒体 API 流量**（token、image、video_second、audio_second），通过
`local_usage_log` 表 + pricebook + markup + `/api/local-metering/report` 报表展示。

但有两类工作**目前完全没有计费**，却都产生对最终结果有价值的产出（json / wav / jpg）：

1. **纯本地算法**：ffmpeg 抽帧、本地 whisper ASR、目录匹配 TTS、快速 storyboard 等——不调用任何付费 API。
2. **OpenCode 订阅路由**：`04_01_SRTRewrite` / `04_02_StoryBoard` 经 `opencode_request → /session/.../prompt_async`（HTTP）调 OpenCode，用的是 **ChatGPT 订阅而非 API key**，所以**没有按 token 的成本**，也完全不经过 `model_broker`、不落 `local_usage_log`。这类模型工作现在等于免费送出。

目标：对这两类产出物按文件大小计费，**且不与已有的 LLM/媒体 API 计费重复**。

判别器（与下文 Step 级去重一致）：**这一步是否已被 API 计量？** 走 model_broker（有 API key）→ 已计费 → 不计产出物；走 OpenCode（订阅）或纯本地 → 无任何计费行 → 计产出物。OpenCode 步骤与本地步骤在此规则下属同一类（"未被 API 计量"），自动归入产出物计费，无需特判。

## 2. 核心决策（已拍板）

| 维度 | 决策 |
| --- | --- |
| 定价维度 | **分文件类型的每 KB 单价**（json / wav / image 各自费率），单文件封顶 |
| 去重粒度 | **Step 级自动判定**：同 attempt+step 下无任何 `modality<>'local_artifact'` 的成功计量行 → 对其产出计费；有则整步跳过 |
| MVP 范围 | **未被 API 计量的步骤**：本地（01、02_02、04_03、local_whisper 模式 02_01）+ OpenCode 订阅（04_01、04_02） |

## 3. 复用现有基础设施（不另起系统）

`local_usage_log + units_json + pricebook + markup + report` 本质是通用的
"单位 × 单价 × 加价率"引擎。`ToolLibrary/Analysis_V1/provider_audit.py` 已证明
Analysis_V1 的模型调用就是写进同一张表的（`record_model_call_audit → record_local_usage
→ INSERT INTO local_usage_log`）。

因此产出物计费 = **新增一种计量单位**，不是新系统：

- `modality = "local_artifact"`
- `provider = "analysis_v1"`，`model_id = 步骤名`（如 `02_02_VideoSRTFrame`）
- `unit_key` 分类型：`artifact_json_kb` / `artifact_wav_kb` / `artifact_image_kb`

好处：报表的 `by_modality` / `by_provider_model` 维度**自动**展示"每个本地算法赚了多少"，
markup / 利润 / Metering 网页**零改动**复用。

## 4. 步骤分类（逐脚本确认结果）

每步有三种"成本性质"，决定计费方式：
- **API 付费**：有真实 per-call 成本，应走 audio_second/token/media 计量 → 不计产出物（避免重复）。
- **OpenCode 订阅**：ChatGPT 订阅、无 per-call 成本、不落库 → 产出物计费是其唯一变现，零重复。
- **纯本地**：无任何成本 → 产出物计费。

| 步骤 | 成本性质 | 当前是否已计量 | 代表产出 | 计产出物？ |
| --- | --- | --- | --- | --- |
| 01 VideoProbeMetadata | 纯本地 | —（无需） | metadata json | ✅（极小） |
| 02_01 AudioASR | local_whisper=本地 / fun-asr=**API 付费** | 本地无需；**云端未计量(缺口)** | Audio_Reference.wav | 本地→✅；云端→❌（应补 audio_second 计量） |
| 02_02 VideoSRTFrame | 纯本地 | —（无需） | srt_frames/*.jpg + srt_frame_map.json | ✅ |
| 03_01 TTSBuilderG | **API 付费**（Gemini TTS） | **未计量(缺口)** | wav | ❌（应补 audio_second 计量） |
| 03_02 TTSBuilderQuick | 本地匹配为主，偶尔 gemini 重试（**已接 provider_audit**） | 重试已计量 | candidate_*.wav | 混合，**MVP 不含**（需逐候选 provenance） |
| 04_01 SRTRewrite | **OpenCode 订阅** | 不落库（设计如此） | rewritten_srt_items.json | ✅（json 高价值密度） |
| 04_02 StoryBoard | **OpenCode 订阅** | 不落库（设计如此） | srt_storyboard.json | ✅（json 高价值密度） |
| 04_03 StoryBoardQuick | 纯本地 | —（无需） | srt_storyboard.json | ✅ |

> ⚠️ **已确认的计量缺口（与产出物计费区分对待）**：`02_01_AudioASR.py`（云端 fun-asr）与
> `03_01_TTSBuilderG.py:1052`（`call_gemini_tts` 直接 `post_json`）**做了真实付费 API 调用却没有任何
> `record_*`/`provider_audit`/`local_usage` 写入**——我们在替供应商付费而没向客户计费。这**不是**
> OpenCode 那种"订阅无成本"的情形，正确处理是**补 audio_second 计量**（让它们走现有 LLM/媒体计费），
> 而不是用产出物计费顶替。MVP 通过"02_01 仅 local_whisper 才计产出物 / 03_01 不在白名单"已规避错计，
> 但补这两处 API 计量应作为**并行的独立工作项**。
>
> 注：04_01/04_02 始终经 OpenCode HTTP 路由、永不落库，Step 级"无任何 `modality<>'local_artifact'` 计量行→计产出物"会自动纳入它们。

## 5. 数据模型改动

给 `local_usage_log` 增加归属列 + 幂等键（新迁移）：

```
task_id          TEXT   -- 关联 OpenClip task
attempt_id       TEXT   -- run_to_storyboard attempt
step_id          TEXT   -- 短码 step id，与 run state 一致："01"/"02_01"/"04_02"（不是长名）
idempotency_key  TEXT   -- 幂等键；普通唯一索引（Postgres 默认 NULLS DISTINCT，多条 NULL 不冲突）
```

> `step_id` 必须存 **短码**（`router.py:1918` 的 spec `id`，如 `"02_01"`），run state 也按短码键；
> 长名（`02_01_AudioASR`）放在 `model_id` 仅作报表展示，二者解耦。

作用：
1. 让 **LLM 费用与产出物费用归到同一次运行** → 报表可出"每个 Task 的成本/利润"。
2. 让 **Step 级去重** 直接按 `attempt_id`+`step_id` 列查询（取代现有 `request_id LIKE %attempt_…%` 的 hack，触点见 §11 清单）。

**幂等（High 修正）**：现 `request_id` 只是普通 `Text` 列（`schema.py:145`）、无唯一约束，且已被
`analysis_v1_usage_rows_by_request_match` 用作 `LIKE` 匹配键——**不能重载它做幂等**。改为：
- 新增 `idempotency_key` 列，产出物行写 `artifact:{attempt_id}:{step_id}`；非产出物行留 NULL；
- 建**普通唯一索引/约束** `UNIQUE (idempotency_key)`——Postgres 默认 `NULLS DISTINCT`，存量及 LLM 行的多个 NULL 不冲突，无需部分索引；
- 两个写入端（`local_usage.py` 与 `provider_audit.py`）的 INSERT 改 **`ON CONFLICT (idempotency_key) DO NOTHING`**。
  > ⚠️ 切勿用"部分唯一索引 `WHERE idempotency_key IS NOT NULL`" + `ON CONFLICT (idempotency_key) DO NOTHING`：
  > Postgres 会报 *no unique or exclusion constraint matching*。若坚持用部分索引，必须重述谓词为
  > `ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING`。本设计选普通唯一索引以规避。

产出物计费行的字段约定：

- `units_json` 计费单位：`{"artifact_json_kb": 48.2, "artifact_wav_kb": 5123.0, ...}`
- `actual_cost_raw_json` 存审计明细：
  `{"artifacts": [{"path": "SessionOutput/storyboard/srt_storyboard.json", "type": "json", "bytes": 49356}, ...]}`
- `idempotency_key = artifact:{attempt_id}:{step_id}` —— 重跑/恢复时不重复计费

## 6. Step Spec 扩展

在 `analysis_v1_run_step_specs()`（`OpenClip/backend/openclip_backend/router.py`）每个步骤
增加两个字段：

```python
"artifact_billable": True,                     # 该步价值是否"未被 API 计量"（本地算法 or OpenCode 订阅）
"billable_outputs": [                          # workspace 相对路径（含根目录，glob）；逐文件计 size
    "SessionOutput/storyboard/srt_storyboard.json",
],
```

`artifact_billable=True` 是声明意图；运行时仍叠加 Step 级"无任何 `modality<>'local_artifact'` 计量行"安全网，二者皆满足才计费。

> **规范路径 / 防重复计数（Medium 修正）**：同一逻辑产物常被写到最多 3 个根——工具 `S<n>_…/Output`、
> `SessionContext`、`SessionOutput`。`billable_outputs` 必须**对每个产物只锁定一个规范路径**，且**每个文件
> 只归属一个 step**（如 `rewritten_srt_items.json` 归 04_01，04_02 仅读不计）。下表已按实测落盘路径校正。

MVP 配置（step_id 用短码；含 OpenCode 订阅步骤——它们正是当前免费送出的高价值产物）：

| step_id | name（展示用 model_id） | 类型 | billable_outputs（实测规范路径） |
| --- | --- | --- | --- |
| `01` | 01_VideoProbeMetadata | 本地 | `SessionContext/Video_Metadata.json`（**无 SessionOutput 副本**）|
| `02_01` | 02_01_AudioASR | 本地（仅 provider=local_whisper） | `SessionOutput/Audio_Reference.wav` —— **本期仅此一项**；字幕 json 在 `SessionContext/ASR_Segments.json`，**本期不计**（跨根复杂、后续若要体现 ASR 算法价值再单列）|
| `02_02` | 02_02_VideoSRTFrame | 本地 | `SessionOutput/visual/srt_frame_map.json`, `SessionOutput/visual/srt_frames/*.jpg`, `SessionOutput/subtitle/final_srt_frame_items.json` |
| `04_01` | 04_01_SRTRewrite | OpenCode 订阅 | `SessionOutput/subtitle/rewritten_srt_items.json` |
| `04_02` | 04_02_StoryBoard | OpenCode 订阅 | `SessionOutput/storyboard/srt_storyboard.json` |
| `04_03` | 04_03_StoryBoardQuick | 本地 | `SessionOutput/storyboard/srt_storyboard.json` |

## 7. 计费触发流程

在 `analysis_v1_run_process_step()` 单步完成（status=completed）后：

```
1. if not spec.get("artifact_billable"): return           # 未标记计费的步骤跳过
2. if 已存在 attempt_id+step_id 且 modality<>'local_artifact' 且 status='ok' 的计量行: return   # Step级去重安全网（按新列查，非 LIKE）
3. files = glob(spec.billable_outputs) 并 stat 大小（每个产物只取规范路径，去重）
4. 按文件后缀分桶 → 累加各 *_kb 单位（单文件按 per-file cap 截断）
5. usage_recorder.record(
       provider="analysis_v1",
       model_id=spec["name"],            # 长名仅作展示
       modality="local_artifact",
       units={"artifact_json_kb": ..., "artifact_wav_kb": ...},
       idempotency_key=f"artifact:{attempt_id}:{step_id}",   # ON CONFLICT DO NOTHING
       task_id=..., attempt_id=..., step_id=spec["id"],      # step_id 存短码
       actual_cost_raw={"artifacts":[...]},
   )
```

02_01 特例：读取该步骤实际使用的 provider，仅 `local_whisper` 时才计 wav。云端 fun-asr
**当前并未计量（见 §4 缺口）**——需先补 audio_second 计量，在此之前云端模式既不计产出物也不计 API（漏费）。

## 8. 定价（分类型每 KB · 占位值待调参）

在 `services/local_metering.py` 的 pricebook 增加 `local_artifact` 条目。费率体现**价值密度**
而非单纯体积——json 是算法浓缩产物（高密度），wav 体积大但密度低：

| unit_key | 文件类型 | cost (micros/KB) | sell (micros/KB) | 单文件封顶(sell) | 说明 |
| --- | --- | --- | --- | --- | --- |
| artifact_json_kb | json | 待定 | ~200 ($0.20/MB) | $0.05 | 高价值密度 |
| artifact_image_kb | jpg | 待定 | ~5 | $0.02 | 帧截图，量大 |
| artifact_wav_kb | wav | 待定 | ~2 ($0.002/MB) | $0.05 | 体积大、密度低 |

> 上述数值为占位，正式费率需结合 LLM 计费量级与目标毛利联合标定，确保产出物计费是
> **有意义的补充**而非账单噪声/主导项。

**OpenCode 订阅步骤的定价提醒**：04_01/04_02 的 json 价值在于模型推理，不严格正比于字节数——
一个精炼但高价值的 storyboard 可能反而很小，纯按 KB 会**偏低**。建议二选一：
(a) 对 OpenCode 路由步骤用更高的 json/KB 费率；(b) 叠加"每步基础价"（per-file/per-step base fee）。
此项待与真实量级数据一起标定。

## 9. 报表与前端

后端 `report()` 无需改动即可把 `local_artifact` 纳入 totals / by_modality /
by_provider_model。前端 Metering 网页需要：

- modality 列表加入 `local_artifact` 的展示/配色；
- （依赖归属列）增加"按 Task 汇总成本/利润"视图（后续迭代）。

## 10. 已知疑点 / 后续

1. **04_01 / 04_02 = OpenCode 订阅（已确认）**：经 `opencode_request` HTTP 调 OpenCode，用
   ChatGPT 订阅、无 token 成本、不落 `local_usage_log`。这不是 bug 而是设计——这类模型工作
   现在完全免费送出。**产出物计费正是其变现方式，零重复**（不存在 LLM 行去重复）。已纳入 MVP。
2. **API 计量缺口（独立工作项）**：`02_01` 云端 fun-asr 与 `03_01` Gemini TTS 做真实付费 API 调用
   却未写 `local_usage_log`（`03_01_TTSBuilderG.py:1052`、`02_01_AudioASR.py` 全文件无 record）。
   需补 audio_second 计量；与本设计并行，不阻塞 MVP（MVP 白名单已规避错计）。
3. 03_02 逐候选 provenance（本地匹配 vs gemini 生成）——二期。
4. jpg 帧是否计费、费率高低——MVP 先计但费率保守，观察占比后调。
5. 计费时点：MVP 按每个完成的 step 计；重跑靠 `idempotency_key` + ON CONFLICT 去重。

## 11. MVP 实现清单

- [ ] 迁移：`local_usage_log` 增 `task_id / attempt_id / step_id / idempotency_key` + `UNIQUE(idempotency_key)` 普通唯一索引
- [ ] 透传新列到**两个写入端**：`services/local_usage.py:record()`（SQLAlchemy）与
      `ToolLibrary/Analysis_V1/provider_audit.py:record_local_usage()`（硬编码直连 SQL，列清单需同步）
- [ ] 两个写入端 INSERT 加 `ON CONFLICT (idempotency_key) DO NOTHING`
- [ ] 报表/汇总查询选新列并改用列归属：
      `services/local_metering.py:349`（SELECT 加新列）、
      `OpenClip/.../router.py` 的 `analysis_v1_usage_rows_by_request_match`（由 `request_id LIKE` 迁到 `attempt_id` 列）
- [ ] pricebook 增 `local_artifact` 三类单位 + 单文件封顶逻辑
- [ ] `analysis_v1_run_step_specs` 增 `artifact_billable` / `billable_outputs`（MVP 6 步：01、02_01、02_02、04_01、04_02、04_03；step_id 用短码）
- [ ] `analysis_v1_run_process_step` 完成后接入计费触发（artifact_billable 判定 + Step 级去重 + 规范路径去重 + 02_01 provider 判定）
- [ ] 契约测试：去重(modality<>'local_artifact')不重复、幂等(ON CONFLICT)不重复计费、分类型计价正确、02_01 云端不计、02_01 仅计 wav 不计字幕 json、同文件多根不重复计数
- [ ] 前端 Metering 展示 `local_artifact`
- [ ] **（并行独立）** 补 02_01 云端 fun-asr 与 03_01 Gemini TTS 的 audio_second 计量
