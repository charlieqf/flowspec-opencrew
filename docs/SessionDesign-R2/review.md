# OpenCrew 素材库综合能力 首版实现 代码评审

版本：v1.0

日期：2026-07-20

状态：历史评审快照。本文记录评审当日发现，不是当前开放缺陷清单；后续修复和发布门禁以[开发实施设计 v1.1.4](./OpenCrew_素材库综合能力_开发实施设计_v1.md)与[M0–M4 验收记录](./OpenCrew_素材库综合能力_M0-M4_验收记录_2026-07-21.md)为准。未在本文逐条回写“已修复”的条目，不得据此推断仍未解决。

评审对象：素材库综合能力 M0–M4 首版实现（工作树，约 90 个文件、1.5 万+ 行新增）

评审方式：多智能体工作流评审（high effort）——4 个 finder 分角度扫描 + 16 个独立 verifier 逐条对抗式验证 + 综合。共 16 个候选，3 个被否决，保留 10 条发现。

关联文档：
- [素材库文档索引](./OpenCrew_素材库文档索引.md)
- [需求评审](./OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_需求评审.md)
- [开发实施设计 v1.1.4](./OpenCrew_素材库综合能力_开发实施设计_v1.md)
- [M0–M4 验收记录](./OpenCrew_素材库综合能力_M0-M4_验收记录_2026-07-21.md)

---

## 评审结论

**核心设计不变量基本守住，问题集中在边界校验、启动、脱敏兜底与清理，不在难写的并发/事务/安全核心。** 对这个体量的首版实现，这是很强的正面信号。

但有 **3 条必须先修的问题会在真实视频上立刻触发**（其中 2 条会掐断"分析→检索"主链路、且违反需求自列的验收样本），需在任何真实素材联调前处理。

严重度速览：

| 级别 | 数量 | 说明 |
| --- | --- | --- |
| 🔴 P0 必须先修 | 3 | 真实视频上立即触发，阻断核心流程或启动 |
| 🟠 P1 应修 | 3 | 正确性问题，触发条件较窄（含 1 条模型泄露兜底） |
| 🔵 P2 清理项 | 4（+2） | 质量/性能/重复，不阻塞发布 |

---

## 一、已核对且干净的核心不变量

以下硬合同交给 finder 重点攻击后**未被攻破**，可作为回归基线信任：

- 事务式 fragment 发布 + stale 级联（新旧原子切换、失败回滚保留旧 active）。
- 并发 partial unique index 阻止同 (asset, scheme) 双 active run / 双 is_current。
- 时间秒→毫秒一次转换，新字段整数毫秒，`[start_ms, end_ms)`。
- FFmpeg 前置 `-ss` + `-accurate_seek` + 参数数组，不经 shell，输出不覆盖源，按 `OPENCREW_FFMPEG_PATH → ToolLibrary/.bin → which`（bundled 7.0）解析。
- clip job 进程内幂等、重启 `clip_job_lost` 410、成功原子写 文件+derivative+session_files。
- 外部检索候选 `asset_id=null`、无 `open_editor` action、只 `import_whole`，不写入 `media_library_assets`。
- 跨 Session 导入按 `asset_id/clip_id` 重读权威记录、校验拷贝 sha256 == 源 `content_sha256`、由 task 解析目标 Session、不信任前端路径。
- 检索资格过滤（ready/未归档/dialogue current+active/非派生）、按 asset 聚合、planner 失败降级不阻断、SQL 参数化。
- composite 不读源视频/Keyframe 图像字节；单帧 `action` 强制 `null`；视觉事实需 `visual_claim_refs`。
- Tool Session finalize/result-sync 在成功与失败路径均调用（`lifecycle.finalize_analysis_tool_session`）。

---

## 二、发现清单（按严重度排序）

### 🔴 P0 —— 必须先修

#### [1] `contracts.py:138`（及 `:228`）成功的 ASR/画面分析被边界校验判成失败 — CONFIRMED

`_validate_interval` 对 `end_ms > source_duration_ms` 严格报错。真实视频里**最后一条字幕的 end 常四舍五入到/超过 ffprobe 时长 1ms**，或极短字幕 `start==end`（→ `end<=start`）。一旦命中，`dialogue.py:426` 把 `ValueError` 吞进 `_run` 的 except，整个对白分析标 `failed`、不发布任何 fragment，用户看到"对白分析失败 / analysis_execution_failed"。

- **影响**：常规视频频繁触发，直接掐断"分析→检索"主链路。
- **根因**：设计文档 §3.4"assert end<=duration、拒绝越界"本身过严；代码忠实实现了偏严的规格。
- **修法**：边界处**钳制**（`end_ms = min(end_ms, duration_ms)`）而非拒绝；`end<=start` 的退化片段跳过而非整体失败。规格与代码一起从"拒绝"改为"钳制"。

#### [2] `contracts.py:164` 无对白视频被判失败（回归）— CONFIRMED

静音/纯音乐视频 `final_srt_frame_items.json` 的 `items=[]` → 抛 `dialogue_fragments_empty` → `dialogue.py:426` 传播 → run 标 `failed`。旧的 `summarize_dialogue_output` 容忍 0 条、ready 且 `fragment_count=0`。

- **影响**：**直接违反需求 §14.3 自列的 E2E 验收样本 #5「无对白 B-roll」**——按现状那条样本必然挂。
- **修法**：允许空 fragment，run 正常 ready（0 条），不进召回即可。

#### [3] `clips/router.py:106` 启动时急切构造 ClipJobManager，ffmpeg 不可用则 app 起不来 — CONFIRMED

`build_media_library_clip_router` 在 boot 期（`app.py:145` include 路由时）就 `ensure_clip_job_manager` → `resolve_media_binary` + `inspect_media_runtime`（跑 3 个 ffmpeg/ffprobe 探测）+ `startup_cleanup()` 全量扫描每个 workspace，全部同步。`resolve_media_binary` 抛 `MediaClipError`（RuntimeError，非 HTTPException）在 `build_*` 中未捕获，**把本该降级成 503 的路径变成启动即崩**。

- **影响**：ffmpeg 无法解析时整个 app 无法启动；即便 bundled 7.0 通常能解析，兜底已死、启动同步开销大。
- **修法**：懒构造（首次请求时），构造失败走已写好的 `require_clip_manager()` 503。

### 🟠 P1 —— 应修

#### [4] `model_policy.py:228` 角色兜底 fail-open 到 admin — PLAUSIBLE（模型泄露）

`request_role()`：`return role if role in {ADMIN, USER} else ADMIN`。当 `request.state.opencrew_auth_role` 未被中间件写入时（request=None、中间件外端点、顺序问题），返回 `admin` → `mask_prompt_models_for_role`/`mask_model_fields_for_role` 直接返回**未脱敏的真实 provider/model id**（如 `openai`/`gpt-5.5`）给普通用户。

- **影响**：踩模型泄露红线（见 model-leakage-audit）。
- **待确认 + 修法**：确认所有返回模型信息的路径都经过写 role 的中间件；兜底应 **fail-closed 到 user**，不是 admin。

#### [5] `run_repository.py:410` 失败的重跑把持久投影改回上一次成功态 — PLAUSIBLE

已有 `is_current='ready'` 结果时点"重跑"，若重跑失败：新 run 标 failed，但因 `is_current` 仍是旧 ready run，`finish_unsuccessful` 的 else 分支用 `current` 重投影 → `_set_projection` 把 `media_library_tasks.dialogue_status` 写回 `ready`、`error=None`（旧的 `dialogue.py/visual.py` 失败路径原本无条件写 `status='failed'`）。

- **影响**：详情页 `/current` 显示一切正常，**用户以为重跑成功，永远看不到失败**。
- **附带核对**：验证 agent 提到会写回 asset `analysis_status='completed'`——若属实同时违反"业务态不写 completed"，一并核。

#### [6] `clips/ffmpeg.py:329` `int(sample_rate)` 未接 ValueError — PLAUSIBLE

`sample_rate=int(sample_rate) if _positive_decimal(sample_rate) else None`——`_positive_decimal` 用 `Decimal(str(...))` 判真，对 `'44100.0'` 或有理数形式为真，但 `int()` 作用在**原始字符串**上，`int('44100.0')` 抛 `ValueError`，一个本应成功的 clip 被报 `media_clip_execution_failed`。

- **修法**：用已解析的 Decimal（`int(Decimal(sample_rate))`）或 try/except。

### 🔵 P2 —— 清理项（不阻塞发布）

#### [7] `clips/manager.py:116` job 缓存无界增长 — CONFIRMED

`_jobs_by_id` / `_job_id_by_idempotency_key` 永不淘汰终态 job（唯一淘汰 `_finish_active` 只动 `_active_job_ids_by_asset`）。长期运行内存随累计出片量单调增长。→ 加 TTL/上限淘汰终态 job。

#### [8] `imports/service.py:825` `import_clip` 逐行复制 `import_original` — CONFIRMED

约 230 行近乎逐行重复（幂等、claim、part/manifest 写、原子替换、回滚；`798-812` 与 `1063-1077` 的 except/finally 回滚块字节级相同），仅源解析与 hash 字段不同。任一路径修 crash-recovery/manifest-restore 需改两处、易漂移。→ 抽共享 helper，按 source kind 参数化。

#### [9] `search/service.py:88` 每次搜索跑 capacity 双 COUNT — CONFIRMED

`begin_search` 每次请求都 `repository.capacity()`（两条 4 表 join 的 `COUNT(*)`）仅为喂 `telemetry.observe_capacity`，先于检索执行。大库上给检索热路径加可观测的延迟与 DB 负载。→ 采样或缓存 gauge。

#### [10] `model_policy.py:778` 脱敏时对每对/每节点深拷贝整份策略 — CONFIRMED

`_mask_payload_value` 对每个 dict 节点遍历 `MODEL_FIELD_PAIRS`，每对 → `_option_for_real → _alias_options → surface_policy → user_model_policy` 都 `copy.deepcopy(DEFAULT_USER_MODEL_POLICY)`。大嵌套响应触发 O(节点×字段对) 全量深拷贝，浪费在每个非 admin 请求。→ 每次调用解析一次 surface policy / alias options。

#### 附加重复（verify 另确认，未进主清单）

- `derive_asset_status` 在 `media_library_search/repository.py:35` 与 `run_repository.py:52` 各一份相同的状态优先级链。
- orientation 规则在 `service.py:276` 内联复制了 `repository.py:1001` 的 helper。

---

## 三、被否决的候选（对抗验证后剔除，仅供透明）

| 候选 | 否决理由 |
| --- | --- |
| `contracts.py:41` result_hash 含相对路径 | canonical 已剥离绝对路径与 `*_url`；fragment 侧 keyframe 为逻辑 ID 非路径，不影响 hash 稳定性 |
| `contracts.py:243` keyframe_time==end 报错 | 唯一生产者 `03_02` 采样比例恒 <1.0，`keyframe_time` 严格小于 end，不可触发 |
| `model_policy.py:715` `provided_fields` 参数未用 | 有意保留：非 admin 固定字段服务端所有、提交值忽略，是文档化的安全设计 |

---

## 四、修复优先级建议

1. **先修 [1][2]** —— 一跑真实视频/B-roll 就触发，是"能不能用"的门槛；[1] 需连设计文档 §3.4 边界规格一起从"拒绝"改成"钳制"。
2. **本轮一并处理 [3][6]** —— 关系到"起得来"与短片剪切成功率，改动小而局部。
3. **确认后修 [4]** —— 先核实中间件覆盖面，再决定改法；建议兜底 fail-closed 到 user。
4. **[5] 与全部清理项** —— 可排后续迭代。

[1][2][3][6] 均为小而局部的改动，可一并修复后重跑相关合同测试确认；[4] 需先确认中间件覆盖面。

---

## 附：评审方法与可复现

- 工作流脚本：`.../workflows/scripts/code-review-wf_504285b7-53a.js`
- 4 finder 角度（3 正确性 + 1 清理）→ 每个候选独立 verifier 对抗验证（尝试证伪）→ 综合。
- 统计：finder 16 候选，verifier 16 验证，3 否决，10 保留（6 correctness + 4 cleanup 分布，含 CONFIRMED/PLAUSIBLE 裁决）。
