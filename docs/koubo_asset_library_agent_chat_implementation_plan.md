# 资产库 Agent 对话化实现方案（基于 OpenCode 的提示词协作 + 生图）

- 日期：2026-06-08
- 模块：OpenClip / Koubo Storyboard — Upload Asset Library Agent
- 目标读者：本仓库后端 + 前端开发

### 修订记录
- **rev3（2026-06-08，实施落地）**：
  - [Done] OpenCode `prompt_async` 已支持 per-prompt `tools` 参数；Asset Agent chat 每次发消息都传 `ASSET_AGENT_CHAT_DISABLED_TOOLS`，禁用 shell/文件/搜索/子任务/patch 等已知工具，安全 gate 从 P3 前移到 P0。
  - [Done] 后端 chat 端点为 5 个：ensure-session / message / events / abort / messages；events 对 OpenCode 消息事件做字段清洗后透传。
  - [Done] 新 surface 实际命名为 `SURFACE_KOUBO_ASSET_AGENT_CHAT = "koubo.asset_library.agent_chat"`；`resolve_model(..., surface=...)` 已落地。
  - [Done] 前端已接入 OpenCode chat reducer、候选卡片、统一生成入口和 `confirmBeforeGenerate` 真确认。
- **rev2（2026-06-08，审核后修正）**：
  - [High] 候选提示词解析改为**前端 reducer + 完整 part 后解析**，后端对话事件不解析候选块（rev3 实施为字段清洗后透传；原"后端逐事件解析"假设不成立，OpenCode 是 `message.part.delta` 分片）。见 §4 ③、§5.5、§7.3。
  - [Medium] 明确 `resolve_model()` 增加 `surface` 形参（默认保持 host_product），新 surface 才能生效；补角色 surface wiring 测试。见 §6。
  - [Medium] `chat_opencode_session_id` 作为 **AgentSettings 顶层元数据**，读写时显式 preserve，不进 `settings` 对象（否则被 normalize 丢弃 / UI 覆写）。见 §5.1。
  - [Medium] `confirmBeforeGenerate` 目前是死设置，需**新增真实确认逻辑**并统一覆盖"直接出图 + 候选出图"两条路径。见 §7.3、§7.5。

---

## 1. 背景与目标

### 1.1 真实痛点
用户（尤其是不擅长写提示词的运营）在「资产库 Agent」里生成新人物图片时卡住。已核实的实例：会话 137 / task 78，用户用资产库 Agent 反复文生图（grok-imagine-image），最终落地的那段 1262 字英文提示词存在
`~/.opencrew/sessions/137/workspace/SessionOutput/storyboard/assets/images/1780905538629_agent_generated_8b990f89.json`。

### 1.2 现状的核心缺陷
当前「资产库 Agent」**名为 Agent，实为出图触发器**：

- 后端 `asset_routes.py` 的 `/asset-library-agent/generate/events`（:681）把用户输入的文本**原样**当图像提示词，直接交给 `generate_asset_library_image`（:435）→ `generate_image_bytes`（出图模型）。
- 整个 `asset_routes.py` **没有任何** `prompt_async / call_opencode / messages()` 调用——**没有任何文本 LLM 对话路径**。
- 前端 `AgentPanel.jsx` 的 `generate()`（:432）拿到输入就调 `props.generateImage()`，那些 `role:"assistant"` 消息（:33/:124/:298）全是状态/报错回显，不是模型回答。

结论：用户无法在这里问"怎么写一段真实、无 AI 味的人物提示词"，问了只会被当提示词去出图。

### 1.3 目标
把这个面板改造成**真正的对话式 LLM Agent**：用户与 LLM 多轮对话，Agent 协助答疑、产出/迭代候选提示词，用户**人工确认后**再走现有出图接口生成图片。

---

## 2. 已锁定的设计决策

| 维度 | 决定 | 理由 |
|---|---|---|
| 对话引擎 | 后台 **OpenCode** 服务（`http://127.0.0.1:4096`，`opencode_runtime.id=1`）| 统一计费/密钥通道，符合商业模式；多轮历史由服务端维护；客户端链路现成 |
| 对话会话 | 资产库 Agent **专属 OpenCode 会话**（`create_session`），存进 `AgentSettings.json` | 与分析会话（`sessions.opencode_session_id = ses_15b04...`）隔离，历史干净、可控 |
| Agent 是否懂背景 | **主动注入 task 背景包**（非靠会话记忆）| 与 `host_product_services.py:108-131` 同范式：curated 注入 > 脏历史复用 |
| Agent 自主度 | **人工确认后出图**（复用 `AgentSettings.confirmBeforeGenerate`）| 可控、计费清晰；先做 human-in-the-loop |
| 出图通道 | **复用现有 `generate_asset_library_image`**（grok/xai）| provider 配置与计费都在本侧，改动最小 |
| 传输 | **SSE 流式**回显（前端已支持 SSE）| 对话体验好；参考 WorkflowAssistant 范式 |

---

## 3. 关键范式参考（照搬，降低风险）

WorkflowAssistant 已经实现了一套"基于 OpenCode 的多轮聊天"，是最贴近的范式，**直接对标移植**：

- `WorkflowAssistant/backend/workflow_assistant/routes.py`
  - `GET .../assistant/messages`（:601）→ `client.messages(opencode_session_id, limit=160)` 拉历史
  - `POST .../assistant/message`（:615）→ `client.prompt_async(opencode_session_id, text, model=, system=build_assistant_system_prompt(context))`
  - `POST .../assistant/abort`（:637）→ `client.abort(...)`
  - `GET .../assistant/events`（:652，SSE）→ 后台线程 `client.collect_events(opencode_session_id, stop_event, on_event)`，把事件入队再 SSE 转发
- 背景注入范式：`OpenClip/backend/openclip_backend/koubo_storyboard/host_product_services.py:108-131`（把 storyboard 文本/参考图/一致性指南拼进 `user_content`）

差异点：WorkflowAssistant **复用 task 的 `opencode_session_id`**；我们要**新建专属会话**并存进 `AgentSettings.json`。

---

## 4. 架构总览

```
AgentPanel.jsx (对话 UI)
   │  ① 打开面板 → 确保专属会话存在
   ├─► POST /asset-library-agent/chat/ensure-session  ── create_session + 写 AgentSettings.json
   │
   │  ② 用户发一句话（提问 / 修改提示词意图）
   ├─► POST /asset-library-agent/chat/message ─────────► OpenCode.prompt_async(
   │        {text}                                          chat_opencode_session_id,
   │                                                        text, model, system=ASSET_AGENT_SYSTEM_PROMPT(背景包),
   │                                                        tools=ASSET_AGENT_CHAT_DISABLED_TOOLS)
   │
   │  ③ 流式拿回复（后端字段清洗后透传 OpenCode 事件；前端 reducer 拼装）
   ├─► GET  /asset-library-agent/chat/events (SSE) ◄──── collect_events(chat_session) → 字段清洗后转发
   │        前端按 messageID/partID 拼 message.part.delta，待 part 完整后
   │        从完整助手文本中解析「候选提示词块」→ 渲染候选卡片
   │
   │  ④ 用户点候选提示词上的「用这段生成」
   └─► POST /asset-library-agent/generate/events (复用现有, :681) → generate_asset_library_image (grok/xai)
```

要点：
- 步骤 ②③ 走 OpenCode（**纯文本对话**，计费走统一通道）。
- 步骤 ④ 走**现有出图接口**（不改出图逻辑）。
- 「候选提示词」由**前端**从拼装完整的助手文本里解析（约定包裹块），渲染成可一键采用的卡片；**后端不在分片事件里做 substring 解析**（见 §5.5 的修正说明）。

---

## 5. 后端改造

文件：`OpenClip/backend/openclip_backend/koubo_storyboard/asset_routes.py`
（如该文件偏大，可新增 `asset_agent_chat_services.py` + 在 `asset_routes.py` 注册路由，保持与现有 `register_*` 风格一致。）

### 5.1 AgentSettings.json 扩展（⚠ 必须作为顶层元数据，勿放进 settings）
现有 schema：`upload_asset_library_agent_settings_0.1`（`asset_routes.py:22`，`default_agent_settings()`:97 / `normalize_agent_settings()`:106）。

**坑（审核 #4）**：`normalize_agent_settings()`（:106-119）只保留 5 个白名单字段、丢弃其余；`agent_settings_payload()`（:121）每次**从零重建** payload、只从 `previous` 取 `created_at`；`save_agent_settings_payload()`（:158）整文件覆写、前端保存也只提交 UI settings（`UploadAssetLibraryOverlay.jsx:255`）。
→ 若把会话 id 放进 `settings`，会被 normalize 丢弃；放顶层但不在 payload 里 preserve，会被下一次 UI 保存覆写。

**正确做法**：
- schema 版本升到 `..._0.2`。
- 新增**顶层元数据字段**（与 `created_at` 同级，**不进 `settings` 对象**，UI 不可见、不可改）：
  - `chat_opencode_session_id: str`（默认 ""）
  - `chat_session_created_at: int`
  - `chat_last_message_at: int`
- 改 `agent_settings_payload(task, settings, previous)`：像保留 `created_at` 一样，从 `previous` **显式 preserve** 这两个字段：
  ```python
  return {
      "schema_version": AGENT_SETTINGS_SCHEMA,
      "task_id": ..., "session_id": ...,
      "settings": normalize_agent_settings(settings),
      "chat_opencode_session_id": text((previous or {}).get("chat_opencode_session_id")),
      "chat_session_created_at": int_value((previous or {}).get("chat_session_created_at")),
      "chat_last_message_at": int_value((previous or {}).get("chat_last_message_at")),
      "created_at": created_at or timestamp,
      "updated_at": timestamp,
  }
  ```
- `ensure-session` 显式写入 `chat_opencode_session_id/chat_session_created_at`，`chat/message` 更新 `chat_last_message_at`；其余读写一律 preserve。
- `read_or_create_agent_settings` 的 `should_write` 判定（:145）需把这些顶层字段纳入比较，避免误判。

### 5.2 专属会话创建：`POST /api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/ensure-session`
逻辑：
1. `task = task_or_404(task_id)`；`session_row = safe_session(task["session_id"])`。
2. 读 `AgentSettings`；若 `chat_opencode_session_id` 已存在 → 直接复用。
3. 否则 `client = opencode_client_for(session_row)`（参考 `provider_services.py:57`）→ `op = client.create_session(title=f"Koubo Asset Library Agent Chat - Task {task_id}")` → 把 `op["id"]` 写回 `AgentSettings.json`。
4. `add_event(session_id, "koubo_storyboard.asset_library_agent.chat.session_created", {...})`。
5. 返回 `{ok, chat_opencode_session_id, prompt_models, settings}`。

> 自愈：若后续调用发现会话失效（404/not found），复用 `ToolLibrary/Analysis_V1/opencode_autoheal.py` 的思路重建并回写。

### 5.3 背景包构造：`build_asset_agent_system_prompt(task, workspace) -> str`
对标 `host_product_services.py:108-131`。System prompt = **角色约束** + **task 背景包**：

角色约束（强约束，关键安全项）：
```
你是 OpenCrew 资产库的「图像提示词教练」。职责：
1. 与用户多轮对话，理解其要生成的图片意图（尤其"新人物/新主播"）。
2. 主动追问缺失的人物身份维度：性别、年龄段、脸型、发型、体型、服装、气质、与原人物的差异点。
3. 当意图清晰时，产出一段可直接用于图像模型的高质量提示词；写实人物需强调真实皮肤纹理/瑕疵、自然光、手机直出感，并给出 negative prompt。
4. 你只做"提示词与创意答疑"。绝不读写文件、绝不执行命令、绝不访问与本任务无关的资源。
5. 当且仅当给出候选提示词时，用如下不可省略的包裹块输出，便于系统提取：
   <PROMPT_CANDIDATE>
   {"positive":"...","negative":"...","aspect":"9:16"}
   </PROMPT_CANDIDATE>
   包裹块外正常用中文与用户对话解释你的思路与改法。
```

背景包（注入 system 尾部或首轮 user 消息）：
- storyboard / 分镜 / 对白文本（`load_plan(task)`，参考 host_product:104-105 的 plan_text 抽取）
- 已有一致性参考状态（HOST/Product 是否已生成 → 读 builder section / `SessionContext/Consistency/`）
- 任务 `product_info` / `constraints`（若该 task 关联分析任务则带上）
- 已生成资产清单（读 `koubo_storyboard_assets.json`，给最近 N 条，去重避免重复风格）
- 一致性提示词指南 `read_consistency_guide()`（`CONSISTENCY_REFERENCE_GUIDE_PATH`，constants:35）

背景包按需在每次 `message` 时重建（成本可控；storyboard 不大），保证 Agent 始终拿到最新状态。

### 5.4 发消息：`POST .../asset-library-agent/chat/message`
对标 WorkflowAssistant `:615`：
1. 校验 `text` 非空；确保 `chat_opencode_session_id` 存在（不存在先 ensure）。
2. `model = resolve_model(session_row, provider, model_id, role, SURFACE_KOUBO_ASSET_AGENT_CHAT)`（复用 `host_product_services.py:102` 的 `resolve_model`；模型来自 prompt_models，**文本对话模型**，与出图模型分离）。
3. `client.prompt_async(chat_session_id, text, model=model, system=build_asset_agent_system_prompt(...), tools=ASSET_AGENT_CHAT_DISABLED_TOOLS)`。
4. `add_event(..., "chat.message.requested", {preview})`；返回 `{ok:true}`。

### 5.5 流式事件：`GET .../asset-library-agent/chat/events`（SSE）— 后端字段清洗后透传
对标 WorkflowAssistant `:652` 与 `:662`：

1. 后台线程 `client.collect_events(chat_session_id, stop_event, on_event)`，事件入 `queue.Queue`。
2. `on_event` 对 OpenCode 事件做最小字段清洗（`message.updated` / `message.part.updated` / `message.part.delta` / `message.part.removed` / `session.status`），SSE 下发；无事件发 heartbeat；`finally: stop_event.set()`。
3. **不在后端逐事件解析候选块**（审核 #2）。原因：OpenCode 助手文本以 `message.part.delta` **分片增量**到达（见 `WorkflowAssistantDrawer.jsx:167-217` 的 `partsByMessageId` reducer，后端 `routes.py:662` 仅透传）。逐 delta 做 `<PROMPT_CANDIDATE>` substring 检测会被切断、重复或漏解析。候选解析放到前端，在 part 完整后做一次（见 §7.3）。

**可选的后端审计**（非流式，安全）：若需服务端记录被产出的候选，仅在收到该 message 的**终态**（`message.updated` 且 `time.completed` 有值）时，用 `last_completed_assistant()`（`provider_services.py:142`）取完整文本解析一次，落 `add_event(..., "chat.prompt_candidate")`。这条只用于审计，不参与前端渲染，避免分片问题。

> 候选包裹块约定（system prompt 强制，见 §5.3）：
> ```
> <PROMPT_CANDIDATE>
> {"positive":"...","negative":"...","aspect":"9:16"}
> </PROMPT_CANDIDATE>
> ```
> 前端解析需容忍：多块（取最后一块或全部列出）、JSON 非法（跳过）、块未闭合（等下一帧）。

### 5.6 中止：`POST .../asset-library-agent/chat/abort`
对标 `:637`：`client.abort(chat_session_id)`。

### 5.7 历史：`GET .../asset-library-agent/chat/messages`
对标 `:601`：`client.messages(chat_session_id, limit=160)`，供面板重开时回填对话。

### 5.8 出图衔接（不改逻辑，仅补字段）
- 复用现有 `POST .../asset-library-agent/generate/events`（:681）。
- 前端在用户采用候选提示词后，把 `positive\n\nNegative prompt:\nnegative` 作为 `prompt` 传入；`aspect` → `size`。
- 可选：在 `request_detail` 里加 `chat_session_id` 与 `prompt_candidate_origin` 便于审计（事件 `image.generated` 已落 JSON sidecar）。

---

## 6. 模型策略 / 遮罩 Surface

文件：`backend/opcrew_backend/model_policy.py` + `OpenClip/backend/openclip_backend/koubo_storyboard/provider_services.py`

**坑（审核 #3）**：`provider_services.py:138` 的 `resolve_model()` 把 `SURFACE_KOUBO_HOST_PRODUCT_PROMPT` **写死**：
```python
def resolve_model(session_row, provider, model_id, role="admin"):
    payload = serialize_prompt_models(session_row)
    return resolve_prompt_model_for_role(ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, payload, provider, model_id, "Prompt")
```
仅新增 surface 常量不会生效，必须改调用方。

**正确做法**：
1. `model_policy.py`：新增 `SURFACE_KOUBO_ASSET_AGENT_CHAT = "koubo.asset_library.agent_chat"`（:18 附近），并在策略表（:109 附近，仿 `SURFACE_KOUBO_HOST_PRODUCT_PROMPT` 块）登记其角色可见模型规则（对话文本模型，与出图模型区分）。
2. `provider_services.py`：给 `resolve_model` 增加 `surface` 形参（**默认值保持 `SURFACE_KOUBO_HOST_PRODUCT_PROMPT`**，不破坏现有 host_product 调用）：
   ```python
   def resolve_model(session_row, provider, model_id, role="admin", surface=SURFACE_KOUBO_HOST_PRODUCT_PROMPT):
       payload = serialize_prompt_models(session_row)
       return resolve_prompt_model_for_role(ctx, role, surface, payload, provider, model_id, "Prompt")
   ```
   资产 Agent 的 `chat/message` 调用传 `surface=SURFACE_KOUBO_ASSET_AGENT_CHAT`。
   （或新增 `resolve_asset_agent_chat_model(...)` 包一层，二选一；改形参更省代码。）
3. `serialize` 资产 Agent 状态时用 `mask_prompt_models_for_role(ctx, role, SURFACE_KOUBO_ASSET_AGENT_CHAT, prompt_models)` 暴露可选对话模型。
4. **测试**：补轻量角色 surface wiring 契约测试（仿现有 `test_lightweight_role_model_policy_contract.py`），覆盖新 surface 的角色遮罩与 `resolve_model(surface=...)` 选路。

---

## 7. 前端改造

文件：
- `OpenClip/frontend/src/UploadAssetLibrary/components/AgentPanel.jsx`
- `OpenClip/frontend/src/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx`（props 注入，:522）
- `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardApi.js`（API 层，:70 附近）

### 7.1 API 层（kouboStoryboardApi.js）
新增方法（与 `assetLibraryAgentSettings` 同风格）：
```js
assetAgentChatEnsureSession: (taskId) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/ensure-session`, { method: "POST" }),
assetAgentChatMessages: (taskId) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/messages`),
assetAgentChatSend: (taskId, body) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/message`, { method: "POST", body }),
assetAgentChatAbort: (taskId) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/abort`, { method: "POST" }),
// SSE：用 EventSource 或现有 streaming helper 连 /chat/events
```

### 7.2 Overlay 注入（UploadAssetLibraryOverlay.jsx:522-540）
给 `<AgentPanel>` 新增 props：`ensureChatSession / loadChatMessages / sendChatMessage / abortChat / openChatEvents`，分别包 7.1 的 api。

### 7.3 AgentPanel.jsx 对话化
核心：**区分两种用户动作**——「对话」与「出图」，不再让回车直接出图。

- `onMount`：`ensureChatSession()` → `loadChatMessages()` 回填历史 → 订阅 `/chat/events` SSE。
- 新增 `sendChat()`：
  - 取 `draft()`，`addMessage({role:"user", text})`，清空输入，`abortable` 置忙。
  - 调 `sendChatMessage({text})`；助手回复经 SSE 流式 append 到一条 `role:"assistant"` 消息（边到边渲染）。
- **SSE 处理需自带 message/part reducer**（审核 #2，对标 `WorkflowAssistantDrawer.jsx:167-217`）：
  - 维护 `partsByMessageId` signal；处理 `message.updated` / `message.part.updated` / `message.part.delta`（按 `field` 累加 `delta`）/ `message.part.removed`。
  - 渲染时把某 messageID 的各 part 文本按序拼成助手消息正文（边到边流式显示）。
  - **候选解析在 part 完整后做一次**：当某 part 收到 `message.part.updated`（终态）或该 message 收到带 `time.completed` 的 `message.updated` 时，对**拼装完整**的文本做 `<PROMPT_CANDIDATE>…</PROMPT_CANDIDATE>` 提取（容忍多块/非法 JSON/未闭合，见 §5.5）。解析成功 → 在该消息下挂候选卡片。**绝不**对单个 delta 做解析。
- 候选卡片动作（两个按钮）：
  - 「采用并填入」→ `setDraft(candidate.prompt)`（沿用现有 `lastAppliedPromptBuilder` 衔接逻辑，:458），用户可继续改后手动出图。
  - 「采用并生成」→ 调统一出图入口 `requestGenerate(candidate.prompt, {aspect})`（见 §7.5），由它按 `confirmBeforeGenerate` 决定是否弹确认。
- 入口/交互调整：
  - 主输入框默认动作改为 **`sendChat()`**（对话）；
  - 出图改为「候选卡片按钮」或保留一个显式「直接出图」次级按钮（兼容老用户：仍可不对话直接出图）。
  - `placeholder` 改为「问我，或描述你想要的人物/画面…」。
- 首屏欢迎语 + suggestions（:31-42）更新为引导性问题，例如：
  - 「帮我写一个真实、没有 AI 味的新主播提示词」
  - 「这个人物要和原主播明显不同，怎么改？」

### 7.4 兼容
- 保留 PromptBuilder（Grok 模板，asset_routes:255）与一致性参考选择器——它们与对话互补：对话产出 positive/negative，PromptBuilder 仍可做模板化精修。
- 保留「直接出图」路径，避免老用户习惯被破坏。

### 7.5 统一出图确认入口（审核 #5）
**坑**：当前 `AgentPanel.generate()`（:432）**根本没读** `settings().confirmBeforeGenerate`，直接出图——该开关是死设置。方案要求"候选直接生成受 `confirmBeforeGenerate` 控制"，必须**真正实现确认逻辑**，且**统一覆盖两条出图路径**（直接出图 + 候选出图），避免只在候选路径加确认导致行为不一致。

做法：抽一个统一入口，原 `generate()` 与候选卡片都走它：
```js
// 统一出图入口：所有出图都经此
const requestGenerate = async (prompt, opts = {}) => {
  const text = String(prompt ?? draft()).trim();
  if (!text || busy()) return;
  if (settings().confirmBeforeGenerate) {
    const ok = await confirmGenerate(text, opts);   // 弹确认框（展示将用的提示词/比例/数量/模型）
    if (!ok) return;
  }
  return runGenerate(text, opts);   // = 原 generate() 内的出图 SSE 逻辑（:435-484）抽出
};
```
- `runGenerate` = 把现有 `generate()`（:432-484）的出图 SSE 主体抽出（不含确认）。
- `generate()`（输入框「直接出图」按钮/快捷键）→ 改为调 `requestGenerate(draft(), {...})`。
- 候选卡片「采用并生成」→ 调 `requestGenerate(candidate.prompt, {aspect: candidate.aspect})`。
- `confirmGenerate` 可用一个轻量确认弹层（展示提示词预览 + provider/model/aspect/count），用户确认后才出图；`confirmBeforeGenerate === false` 时直接出图。
- AgentSettings 面板里把 `confirmBeforeGenerate` 做成真正可切换并保存（值已在 settings，:99）。

---

## 8. 安全与计费（关键，勿省略）

1. **OpenCode 是编码 Agent，默认带 shell/文件工具**（进程：`opencode serve`）。当前所有 `prompt_async` 都未传工具/权限限制，仅靠 system prompt 约束。本功能是**自由对话**，必须：
   - system prompt 强约束角色（见 5.3 第 4 条："绝不读写文件/执行命令"）。
   - `adapters/opencode.py` 暴露 per-prompt `tools` 参数；资产 Agent chat 每次 `prompt_async` 都传 `ASSET_AGENT_CHAT_DISABLED_TOOLS`，显式禁用 shell、read/write/edit、grep/glob、webfetch/websearch、codesearch、task、skill、apply_patch 等已知 OpenCode 工具。
   - SSE 侧如观察到工具事件，立即 `abort(chat_session_id)` 并下发 `asset_agent.chat.tool_blocked`。
   - 输入长度/频率限制，防滥用。
2. **计费**：对话经 OpenCode = 走统一计费通道（符合商业模式：批发 key 隐藏 + 按量加价）。需确认对话 token 计入计费线（与现有 `prompt_async` 一致）。本地产出物（生成的图）仍按既有产出物计费线计（见 [[local-artifact-billing]]）。
3. **网络**：交付为 LAN-only（见 [[phase0-network-posture]]），OpenCode 在 `127.0.0.1`，无额外暴露。

---

## 9. 数据 / 落盘

- 专属会话 id → `SessionContext/AgentSettings.json`（schema `..._0.2`）。
- 对话历史：**由 OpenCode 服务端维护**（`messages()` 拉取），本地不强制落盘；如需审计，可把每轮 `add_event` 落 `session_events`（`koubo_storyboard.asset_library_agent.chat.*`）。
- 候选提示词被采用并出图后，沿用现有出图落盘（`assets/images/{batch}_agent_generated_*.png` + `.json` sidecar，asset_routes:492-504）。

---

## 10. 错误与边界

| 场景 | 处理 |
|---|---|
| OpenCode 不可达 | `ensure-session`/`message` 返回 503，前端提示「对话服务不可用」，仍允许「直接出图」降级 |
| 专属会话失效（404） | 自愈重建并回写 AgentSettings，重试一次 |
| 助手回复无候选块 | 当作纯答疑，不出卡片 |
| 候选 JSON 解析失败 | 跳过结构化事件，文本照常显示 |
| 用户连发/中止 | `/chat/abort` → `client.abort()`；前端禁用重复发送 |
| 出图阶段失败 | 复用现有 `generate/events` 的 `failed` 事件渲染（:466） |

---

## 11. 分阶段实施

- **P0（后端对话闭环，已实施）**：AgentSettings 顶层元数据扩展（§5.1）+ ensure-session + message + events(SSE，字段清洗后透传) + abort + messages + system prompt/背景包 + `resolve_model(surface=...)` 改造与新 surface（§6）+ per-prompt 工具禁用。
- **P1（前端对话化，已实施）**：API 层 + Overlay 注入 + AgentPanel 改造（**message/part reducer 流式渲染** + 完整文本候选解析 + 候选卡片 + 统一出图入口 §7.5）。
- **P2（衔接与体验，已实施核心项）**：候选→出图一键流、`confirmBeforeGenerate` 真确认（§7.5）、PromptBuilder 共存、历史回填。
- **P3（后续加固）**：对话计费校验；速率限制；若 OpenCode 新增更强 session/agent permission API，再叠加到当前 per-prompt `tools` 禁用之上。

---

## 12. 测试计划

- **契约测试**（参考 `backend/tests/contracts/`）：
  - 新增 `test_koubo_asset_agent_chat_contract.py`：校验五个新端点存在、入参/出参形状、per-prompt 工具禁用和前端 wiring。
  - 新增/扩展角色 surface wiring 测试（仿 `test_lightweight_role_model_policy_contract.py`）：`SURFACE_KOUBO_ASSET_AGENT_CHAT` 角色遮罩 + `resolve_model(surface=...)` 选路（审核 #3）。
  - 校验 `AgentSettings` schema `..._0.2` 向后兼容：读 `..._0.1` 自动补字段；且**保存 settings 后 `chat_opencode_session_id` 不被冲掉**（审核 #4，关键回归点）。
- **前端解析单测**：`<PROMPT_CANDIDATE>` 提取（正常 / 缺字段 / 非法 JSON / 多块 / 未闭合）——在拼装完整文本上运行，不在 delta 上（审核 #2）。
- **前端 reducer 单测**：`message.part.delta` 累加、`message.part.updated` 终态、候选只在 part 完整后触发一次（无重复）。
- **端到端手测**：会话 137 / task 78 复现——问"帮我写真实无 AI 味的新主播提示词"→ 得到候选 → 一键生成 → 落盘校验。
- **降级**：停掉 OpenCode（或改坏 base_url）验证 503 与"直接出图"降级。

---

## 13. 风险与回滚

- **风险**：OpenCode 自由对话的工具越权（per-prompt `tools` 禁用 + SSE 工具事件 abort 缓解）；候选块格式模型不遵守（system prompt 强约束 + 解析容错）；对话延迟（SSE 流式 + abort）。
- **回滚**：所有改动是**新增端点 + 面板增量**，旧「直接出图」路径保留。出问题可在前端隐藏对话入口、回退到纯出图，后端新端点不影响既有功能。

---

## 附：涉及文件清单

后端
- `OpenClip/backend/openclip_backend/koubo_storyboard/asset_routes.py`（新增 5 个 chat 端点 + 注册 + 背景包/system prompt）
- `OpenClip/backend/openclip_backend/koubo_storyboard/provider_services.py`（`resolve_model(surface=...)`）
- `backend/opcrew_backend/adapters/opencode.py`（`prompt_async` 支持 `tools/agent`）
- `backend/opcrew_backend/model_policy.py`（新增 surface）

前端
- `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardApi.js`（5 个 chat api 方法）
- `OpenClip/frontend/src/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx`（props 注入）
- `OpenClip/frontend/src/UploadAssetLibrary/components/AgentPanel.jsx`（对话化）

测试
- `backend/tests/contracts/test_koubo_asset_agent_chat_contract.py`（新增）

参考范式（勿改，仅对照）
- `WorkflowAssistant/backend/workflow_assistant/routes.py:601-708`（OpenCode 多轮聊天 + SSE）
- `OpenClip/backend/openclip_backend/koubo_storyboard/host_product_services.py:108-134`（背景注入 + prompt_async）
- `backend/opcrew_backend/adapters/opencode.py:156-269`（OpenCodeSessionClient）
