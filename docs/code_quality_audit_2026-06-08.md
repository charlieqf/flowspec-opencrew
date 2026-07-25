# OpenCrew 代码质量审查报告

- 日期：2026-06-08
- 审查范围：整个 repo（Python ~10 万行，前端 ~2.9 万行；前端为 SolidJS，非 React）
- 方法：按模块并行静态审查 + 对最严重项逐条在源码核实

## 总体结论

安全基本面良好——**未发现** SQL 拼接、用户可控的 shell 注入、`eval/exec/pickle`、硬编码密钥、可变默认参数。
（注：存在 `shell=True`，见下方「备注」，但命令为常量/int pid，非用户可控注入面。）
问题集中在三类：少量**可复现 bug**、**巨型单体结构**、**系统性复制粘贴 + 死代码**。

图例：✅ = 已在源码逐条核实。

---

## 🔴 严重（可复现缺陷，建议优先修）

### S1. `log_lines` 未定义 → endpoint 必崩 ✅
- 位置：`backend/opcrew_backend/routes/sessions.py:756`
- 证据：`return {"lines": log_lines(session_id)}`，但 `log_lines` 全文件无定义、无导入。
- 影响：任何 `GET /api/session-tasks/{id}/logs` 请求直接 `NameError` → 500。
- 根因：S10 巨型闭包导致静态检查无法发现漏定义。
- 建议：补实现或删除该 endpoint。

### S2. 共享页存储型 XSS ✅
- 位置：`backend/opcrew_backend/routes/sessions.py:918, 947, 948`
- 证据：用 f-string 把 `title` / `status` / `group_id` / `sender_name` 原样拼进 HTML，无转义；其中 `group_id`、`sender_name` 来自 `/api/sessions/im/send` 请求体（`sessions.py:778-779`）。
- 影响：攻击者注入 `</script><script>…` 可在共享链接访问者浏览器执行。
- 建议：所有插值统一 `html.escape()`。

### S3. 计费记录异常被静默吞掉 = 静默丢收入 ✅
- 位置（根因，主要）：`ToolLibrary/Analysis_V1/provider_audit.py:278, 280`
- 位置（外层调用，次要）：`ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py:1586-1602`
- 证据：DB 落库失败主要在 `provider_audit.py` **内部就被双层 `except Exception: return ""` 吞掉并返回空字符串**（`:278` 内层、`:280` 外层），调用方拿到的只是空 audit id，无从判断成败；`05_02` 外层的 `except: pass` 只是再吞一次，**只改 05_02 抓不到大部分 usage 落库失败**。
- 影响：按商业模式（计量加价 = 利润），落库失败将无声丢失计费且事后无法追溯。
- 建议：改 `provider_audit.record_local_usage()`（`:189`）及内部落库路径，让失败**可记录（日志）、可返回（明确成败/异常）、可告警**；外层调用方据返回值处理，而非各自 `except: pass`。

---

## 🟠 中等

### M1. 死代码：约 6,600 行前端大模块整体无人引用 ✅
- 位置：`OpenClip/frontend/src/OCRebuildModule.jsx`(3559)、`OCStoryBoardModule.jsx`(1474)、`OpenClipModule.jsx`(1554)
- 证据：运行时零 import 引用；`frontend/src/App.jsx:13` 已把对应路由列入 `RETIRED_NAV_HASH_PREFIXES`。
- ⚠️ 删除前需处理隐藏依赖：`backend/scripts/check_workflow_assistant_p1_p3_contract.py:12` 会读取 `OpenClipModule.jsx`，并在 `:74` 断言其内容（`SharedWorkflowAssistantDrawer`、`Task Assistant`）。直接删旧模块会让该契约检查脚本失败——需同步更新或迁移该断言。
- 这些死模块内部本身也有缺陷，删除后一并消失（故不单列、仅记录于此）：
  - `OCRebuildModule.jsx:859-862, 2894` `attachKeyframeContextMenu` 在 `<For>` ref 回调加 capture 阶段 `contextmenu` 监听且永不解绑，同节点又有 `onContextMenu` → 双重绑定 + 泄漏。
  - `OCRebuildModule.jsx:440 runAction` `busy` 为单一字符串信号，并发 action 互相覆盖 busy / 抹掉错误。
- 建议：整体删除（连带消除上述拖拽泄漏/重复/竞态问题），但**先改 contract 脚本**（见上）。

### M2. 硬编码他人用户绝对路径（转售机上必失效）
- 位置：
  - `ToolLibrary/Rebuild_V1/05_01_Shot_StoryboardReferencePromptRefresh.py:471`（fallback 默认 `/Users/duheng/.opencrew/...`）
  - `ToolLibrary/Rebuild_V1/PlanARealChain.py:631`（`--workspace` 默认写死 `/Users/duheng/...`）
  - `ToolLibrary/Analysis_V1/03_02_TTSBuilderQuick.py:29-31`（`VIDEO_ANALYSIS_ROOT = /Users/duheng/Development/...`）
  - `ToolLibrary/Analysis_V1/02_01_AudioASR.py:1074, 1337`（错误/权限文案硬编码 `/Users/duheng/.opencrew`）
- 影响：出货 Mac mini 用户名不是 `duheng`，未传参时指向不存在路径或误导用户。

### M3. 临时音频文件永不清理
- 位置：`ToolLibrary/Rebuild_V1/12_01_Shot_PlanD_TTSDrivenLipSyncVideoGenerate.py:908-911, 2139`
- 证据：每个 shot 在 `$TMPDIR/opencrew_plan_d_lipsync_audio/` 生成 uuid WAV（可达数 MB），全文件无 `unlink`。
- ⚠️ 不能简单 `finally unlink`：`:2139` 生成的 tmp 音频路径随后在 `:2170` 写入 `plan_d_scene_tts_manifest.json`（`temporary_lipsync_audio` 字段），下游会消费该路径。
- 影响：长期运行无限累积。
- 建议：把音频落到 **workspace 受管目录**（随 session 生命周期清理）；或在下游消费完成后清理并**同步更新 manifest**，避免悬挂引用。

### M4. 并发竞态（设计层）
- `next_attempt_no` 读 `max()+1` 与 insert 不在同一事务，并发 rerun 同一 task 会重复 attempt_no。**两处都有**：
  - `OpenClip/backend/openclip_backend/repository.py:148`（`openclip_attempts`）
  - `OpenClip/backend/openclip_backend/rebuild_repository.py:85`（`oc_rebuild_attempts`）
  - 建议：两张表都加 `(task_id, attempt_no)` 唯一约束 + 单事务/失败重试。
- `backend/opcrew_backend/routes/openflow_analysis.py:1209` 与 `sessions.py:578`：用入口快照判 `running/queued` 后才清空 workspace，存在 TOCTOU，两个并发 rerun 可互相清空对方工作区。

### M5. SSRF / 不一致
- 位置：`ModelConfig/backend/opcrew_model_config/media_model_config.py:1034-1037`
- 证据：`audio_url_bytes` 用裸 `urllib.request.urlopen` 抓 provider 响应里的任意 URL（来源 `:998/1007` 透传），绕过本文件统一的 `provider_urlopen(proxy_policy=...)`。

### M6. VideoCapture 性能与资源
- `ToolLibrary/Analysis_V1/02_02_VideoSRTFrame.py:783-800`：在「每句 × 每候选」循环里反复 `cv2.VideoCapture()` + seek，长视频是 O(句数×候选数) 次全文件打开，应缓存复用单个 cap。
- `OpenClip/backend/scripts/openclip_analysis_runner.py:132/196/329/1157`：`capture.release()` 不在 try/finally，中途异常时句柄泄漏。

---

## 🟡 结构性 / 重复（最大维护债，非即时 bug）

### S10. 巨型单体函数（整路由模块塞进一个闭包）
- `OpenClip/backend/openclip_backend/rebuild_router.py:220` `build_oc_rebuild_router` **4984 行**
- `OpenClip/backend/openclip_backend/router.py:261` `build_openclip_router` **3667 行**
- `OpenClip/backend/openclip_backend/storyboard_router.py:54` 1611 行
- `backend/opcrew_backend/routes/openflow_analysis.py` ~1280 行；`sessions.py` ~960 行
- 影响：handler/helper 全嵌在一个闭包内，无法单测、无法静态检查（S1 漏检的根因）。

### S11. 系统性死导入 + `from .constants import *`
- `OpenClip/backend/openclip_backend/koubo_storyboard/` 几乎每个 `*_services.py` 顶部有逐字节相同的导入头（各约 26 个未用导入，全目录 400+ 处）。
- 10+ 处 `from .constants import *`（如 `asset_routes.py:18`、`composer_services.py:29`、`tts_routes.py:15`），使静态分析失效、掩盖真实未定义错误。
- 其它单点：`router.py:10 import queue`、`router.py:27 StreamingResponse`、`storyboard_router.py:14 Form` 未使用。
- 建议：改显式导入，清理死导入。

### S12. 跨文件大面积复制粘贴（已出现分叉，改一处漏一处）
- 多处重复实现：`post_json_request`（14 处）、`first_url`（11 处）、`dashscope_upload_file`（5 处）；`download_binary`/`safe_float`/`redact_config` 多处。各副本已出现细微分叉（如重试逻辑、状态集合不一致），不宜用「逐字节相同」的绝对表述。
- `safe_upload_name` 三份逐字重复：`storyboard_router.py:129`、`rebuild_router.py:667`、`koubo_storyboard/builder_state_services.py:45`。
- `audio_duration_seconds` 两份：`rebuild_router.py:1657` 与 `koubo_storyboard/media_tts_provider_services.py:72`。
- 前端 `providerTTSModel` 三份逐字相同：`OCRebuildModule.jsx:1245`、`OCStoryBoardModule.jsx:285`、`KouboStoryBoard/kouboStoryboardTts.js:37`。
- `openflow_analysis.py` 与 `sessions.py` 的 `stream_prompt` / `on_event` / `opencode_client_for` 等几乎逐行复制（仅超时 1800/600 不同）。
- 建议：抽公共工具模块。

### S13. 死函数 / 无效逻辑残留
- `ToolLibrary/Rebuild_V1/12_01_...PlanD...py`：`load_media_config`(262)、`tempo_from_tts_selection`(470)、`apply_tts_tempo`(506)、`generate_tts_audio`(545)、`parse_srt_entries`(791)、`ensure_shot_srt_text`(949)、`normalize_srt_to_audio`(972)、`tts_prompt_for_shot`(1906) 等约 8 个函数从未调用（数百行，TTS 路径已重写）。
- `backend/opcrew_backend/routes/sessions.py:100` `safe_read_join` 定义后 0 调用。
- `OpenClip/backend/openclip_backend/router.py:3894` `rerun_openclip_task` 的 `task_row = get_task(task_id)` 赋值后未使用——属**重复 404 校验 + 死赋值**（`get_task` 缺失时确会抛 404，但紧接调用的 `run_openclip_task:3863` 开头又 `get_task(task_id)` 校验了一次）。建议删去该行死赋值。

### S14. 存活模块（`frontend/src/App.jsx`）的小问题
> 注：原报告此节前两条针对 `OCRebuildModule.jsx`，但该文件已由 M1 认定为死代码模块，相关缺陷（双重绑定/泄漏、`runAction` busy 竞态）随 M1 整体删除一并消失，已移至 M1 说明，不再单列。本节仅保留存活模块问题。
- `frontend/src/App.jsx:1754`：向公网 `https://open.er-api.com/v6/latest/USD` 取汇率，与 Phase 0「LAN-only 不出公网」部署姿态冲突（有 `FALLBACK_USD_CNY_RATE` 兜底）。
- `frontend/src/App.jsx:11, 2656`：`DEFAULT_NPC_SERVER_ADDR = "113.125.202.171:8024"`、导航硬编码 `#/koubo-storyboard/tasks/31`。

### S15. 静默吞异常（无日志，排障困难）
- `OpenClip/backend/openclip_backend/storyboard_router.py:1018`、`rebuild_router.py:315/948/1665/2051`、`router.py:2033/2681`、`koubo_storyboard/media_tts_provider_services.py:80`：多为合理回退，但 `except (Exception|HTTPException): pass` 完全静默。
- `backend/opcrew_backend/routes/openflow_analysis.py:735-737`、`sessions.py:423-424`：故障被静默降级为空结果。
- （勘误：原报告所列 `media_model_config.py:138-139` 不成立——该处是 `voice_option()` 的返回 dict，非异常吞噬，已移除。）
- 建议：至少 `logger.debug/warning` 记录。

---

## 建议处理顺序

| 优先级 | 动作 | 收益 |
|---|---|---|
| 立即 | S1（补 `log_lines` 或删 endpoint）、S2（`html.escape`）、S3（计费失败记日志/告警） | 消除崩溃、XSS、丢收入 |
| 本周 | 删 M1 三个死前端模块、S13 死代码；改 M2 硬编码路径 | 砍 ~7000 行 + 出货可用性 |
| 规划 | S12 抽公共工具模块、S11 清死导入；S10 拆巨型路由（最大债，需渐进） | 可测试性、可维护性 |

---

## 备注

- 安全方面已确认良好：路径遍历有 `safe_workspace_rel`/`safe_name`/`safe_upload_name` 防护；SQL 全 SQLAlchemy 参数化；密钥走 `resolve_secret_value` + `redact_*`，无硬编码密钥（仅有硬编码路径问题，见 M2）。
- 关于 `shell=True`（勘误原报告「无 shell=True」的表述）：`backend/opcrew_backend/adapters/opencode.py:336/339/341/486` 确有 4 处 `shell=True`，但均位于 **Windows discovery 分支**，命令为常量字符串或 int pid（`tasklist`/`netstat`/`findstr` 等），无用户可控输入，不构成 RCE。结论应表述为「**未发现用户可控的 shell 注入**」，而非「无 `shell=True`」。建议仍逐步改为列表参数以消除隐患。
- 其余 ToolLibrary subprocess 调用为列表参数、带 timeout，未发现注入面。
- 行号基于审查时的 `main` 分支快照（最近提交 `d0b1f95`），后续改动可能偏移。
