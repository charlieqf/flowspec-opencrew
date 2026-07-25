# Koubo Asset Library 提示词 Agent 设计文档

## 1. 目标

在 Asset Library 中新增一个独立的“提示词 Agent”，用于提示词优化、批注、改写、模型适配和经验检索。

这个 Agent 不直接替代“图像生成”“视频生成”“图像智能体”“视频智能体”，而是作为一个提示词研究与编辑工作台：

1. 用户输入原始提示词、目标模型、用途和约束。
2. 系统根据模型类型检索本地提示词知识库。
3. Agent 输出批注、问题清单、优化版本、模型适配说明和可直接复制/发送的最终提示词。
4. 优化过程写入当前 Task Session，保留可追溯的来源、检索片段和版本记录。

## 2. 当前系统基础

当前仓库已经有可以复用的 UI 和后端基础：

| 能力 | 当前落点 | 复用方式 |
| --- | --- | --- |
| Asset Library 左侧导航 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx` | 新增 `prompt-agent` 入口，放在“数字人智能体”下方 |
| 图像 Agent 右侧对话 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/AgentPanel.jsx` | 复用对话区、模型选择、发送/停止、工具栏样式 |
| 视频 Agent 右侧对话 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx` | 复用视频模式参考图/参考视频上下文写法 |
| Prompt Builder 弹窗 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/PromptBuilderModal.jsx` | 继续作为“构建/填入 Prompt”的轻工具，不承担知识库检索 |
| 通用 Agent Chat 接口 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/agent_chat_routes.py` | 扩展 agent key，新增 `prompt_agent` |
| Prompt Builder 审计目录 | `SessionContext/PromptBuilder/` | 新增 `SessionContext/PromptAgent/`，避免混淆 |
| 模板参考库 | `OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/` | 作为首批本地种子资料的一部分 |

说明：旧需求文档中出现过 `OpenCrew/OpenClip/frontend/...` 路径，当前真实代码已迁移到 `OpenCrew/frontend/src/modules/koubo/...`，本设计以当前代码为准。

## 3. 产品形态

### 3.1 左侧入口

在 Asset Library 左侧导航新增：

```text
提示词 Agent
```

建议排序：

```text
图像生成
图像智能体
视频生成
视频智能体
数字人智能体
提示词 Agent
History
```

图标继续使用 `addNotes` 或新增更明确的 `edit-square-outline`。如果保留现有 Material Symbols 风格，优先使用已有图标，不新增一套视觉系统。

### 3.2 主区域

提示词 Agent 打开后，中间主区域不再展示素材网格作为核心，而是展示“提示词工作台”：

| 区域 | 内容 |
| --- | --- |
| 顶部工具栏 | 搜索框、模型类型筛选、知识库状态、刷新/重建索引按钮 |
| 左中区域 | 当前 Prompt 版本列表、优化历史、检索来源摘要 |
| 右侧区域 | 与现有图像/视频智能体一致的 Agent 对话 |
| 底部 Composer | 粘贴提示词、选择用途、选择模型类别、发送给 Agent |

为了和现有界面一致，首版可以直接沿用右侧 Workspace 对话风格，中间区域只做简洁的“Prompt Versions / Sources / Diff”三栏，不做营销式大页面。

### 3.3 右侧对话交互

Composer 工具栏建议：

| 控件 | 用途 |
| --- | --- |
| `+` | 附加参考图片、视频或文本文件 |
| `image` | 加载人物/产品一致性参考图 |
| `addNotes` | 从当前 Prompt Builder 草稿导入 |
| `tune` | 打开提示词 Agent 设置 |
| `arrowForward` | 发送 |

新增一个紧凑的模式选择：

```text
批注 / 优化 / 改写 / 模型适配 / 对比
```

建议做成 segmented control，放在 Composer 上方或 Settings 中，不放一大段说明文字。

## 4. Agent 能力边界

提示词 Agent 首版必须聚焦四件事：

1. 批注：指出提示词中目标不清、模型不适配、负向约束缺失、参考图角色不明、镜头/动作冲突等问题。
2. 优化：在不改变用户核心意图的前提下，补齐结构、模型关键字、约束、参考图说明和失败规避项。
3. 模型适配：把同一提示词改写为图像模型、视频模型、数字人口播模型、对嘴模型等不同版本。
4. 引证：每次优化必须返回“依据了哪些知识库片段”，并标明来源类型，不直接暴露大段原文。

不建议首版让它直接发起图片/视频生成。它应该先输出可审阅 Prompt，再由用户复制到图像/视频生成，或点击“应用到当前输入框”。

## 5. 知识库组织方式

### 5.1 总目录

建议新增 Repo 级知识库目录：

```text
OpenCrew/PromptKnowledge/
  registry/
    sources.json
    models.json
    licenses.json
  raw/
    github/
    articles/
    official_docs/
    local_templates/
  normalized/
    image/
    video/
    digital_human/
    lipsync/
    general/
  index/
    sqlite/
    vectors/
    manifests/
  reports/
    crawl_runs/
    quality_audits/
```

当前 Task 的运行痕迹写入 Session：

```text
SessionContext/PromptAgent/
  ChatState.json
  Drafts/
  Applied/
  Retrieval/
  Critiques/
  Exports/
```

### 5.2 Source Registry

所有外部资料都必须先进入 `registry/sources.json`，不要让 Agent 临时随意抓网页。

示例：

```json
{
  "source_id": "github_prompt_repo_xxx",
  "source_type": "github_repo",
  "model_family": ["general", "image"],
  "provider": "",
  "url": "https://github.com/...",
  "license": "unknown",
  "trust_level": "community",
  "refresh_policy": "weekly",
  "enabled": true,
  "notes": "GitHub stars ranking source, requires crawl-time star count"
}
```

来源分级：

| trust_level | 说明 | 使用方式 |
| --- | --- | --- |
| `official` | 官方文档、官方 cookbook、模型 provider 示例 | 可作为高权重依据 |
| `community_high_signal` | 高 star GitHub repo、长期维护文章 | 中高权重，需保留来源和时间 |
| `local_experience` | 本项目实际生成记录、人工评测、踩坑总结 | 高权重，尤其适合同类任务 |
| `community_article` | 博客、教程、案例文章 | 中权重，只提取方法，不照搬表达 |
| `experimental` | 未验证技巧、论坛经验 | 低权重，只作为候选提醒 |

### 5.3 Normalized Document

所有资料进入索引前统一成 JSONL：

```json
{
  "doc_id": "official_openai_image_prompting_20260623_001",
  "source_id": "official_openai_docs",
  "source_url": "https://...",
  "source_title": "Image prompting guide",
  "source_type": "official_doc",
  "trust_level": "official",
  "model_family": ["image"],
  "provider": "openai",
  "model_ids": ["gpt-image-1"],
  "language": "en",
  "license": "official_docs_terms",
  "captured_at": "2026-06-23T00:00:00+08:00",
  "updated_at_source": "",
  "chunk_id": "chunk_0007",
  "chunk_type": "principle",
  "tags": ["reference_images", "negative_prompt", "composition"],
  "content": "normalized summary or short allowed excerpt",
  "summary": "结构化摘要",
  "rules": [
    {
      "rule_id": "image_reference_role_order",
      "rule_type": "do",
      "text": "明确每张参考图的角色和顺序。",
      "confidence": 0.84
    }
  ],
  "examples": [],
  "hash": "sha256..."
}
```

### 5.4 模型分类

首版按“用途 + 模型家族”建库：

```text
image/
  openai/
  grok/
  gemini/
  midjourney/
  flux/
video/
  sora/
  veo/
  kling/
  wan/
  seedance/
  grok_video/
digital_human/
  heygen/
  talking_avatar/
lipsync/
  syncso/
  kling_lipsync/
general/
  prompt_structure/
  negative_prompt/
  critique_methods/
```

检索时不要只按用户选择的 provider 检索。建议策略：

1. 先检索具体模型库，例如 `video/wan`。
2. 再检索同类模型库，例如 `video/general`。
3. 再检索本项目本地经验，例如 `local_experience/video`。
4. 最后检索通用提示词结构库。

## 6. 采集与更新流程

### 6.1 不建议让前端实时“全网搜索”

全网搜索、GitHub 下载、官方文档抓取应该是后端工具或定时任务，不应该发生在用户每次对话时。原因：

1. 响应慢。
2. 来源质量不可控。
3. 官方网页结构经常变化。
4. GitHub license、robots、限流都需要处理。
5. 每次对话临时搜索会导致同样输入得到不稳定结果。

### 6.2 推荐新增工具

建议新增工具目录：

```text
OpenCrew/ToolLibrary/PromptKnowledge/
  01_SourceDiscover.py
  02_SourceFetch.py
  03_NormalizePromptDocs.py
  04_BuildPromptIndex.py
  05_SearchPromptKnowledge.py
  06_PromptCritiquePackage.py
  README.md
```

工具职责：

| 工具 | 职责 |
| --- | --- |
| `01_SourceDiscover.py` | 通过 GitHub API、搜索 API、人工 seed list 发现候选来源，记录 star、更新时间、license |
| `02_SourceFetch.py` | 下载 GitHub repo、文章、官方文档，写入 `raw/` |
| `03_NormalizePromptDocs.py` | 清洗 Markdown/HTML/PDF，切块、去重、抽取规则、示例和标签 |
| `04_BuildPromptIndex.py` | 构建 SQLite FTS、向量索引、manifest |
| `05_SearchPromptKnowledge.py` | 给后端/Agent 使用的检索入口 |
| `06_PromptCritiquePackage.py` | 把用户 Prompt、检索结果、批注要求打包成 Agent 上下文 |

P0 可以只做 SQLite FTS + JSONL manifest；P1 再加向量索引。这样最稳，也方便直接查文件和排错。

### 6.3 是否需要 Skill

建议“工具 + Skill”都做，但边界要清楚：

| 类型 | 是否需要 | 负责什么 |
| --- | --- | --- |
| Tool | 必须 | 抓取、清洗、索引、检索、生成引用包 |
| Skill | 建议 | 规定提示词批注方法、输出格式、评分维度、模型适配原则 |
| Agent | 必须 | 与用户对话，调用检索结果，给出批注和优化稿 |

Skill 不应该负责爬网页，也不应该直接管理索引。Skill 只负责“怎么判断一个提示词好不好，以及怎么写批注/修改建议”。

建议新增 Skill 文件：

```text
OpenCrew/ToolLibrary/PromptKnowledge/SKILL_prompt_optimization.md
```

Skill 内容包括：

1. 保持用户原意优先，不擅自改产品、人物、品牌、镜头目标。
2. 先批注，再给可用改写。
3. 对图像模型关注主体、构图、光线、参考图角色、比例、风格边界。
4. 对视频模型关注首帧/尾帧、动作幅度、镜头稳定性、时长、口型、文字风险。
5. 对数字人模型关注脚本、语气、Avatar、Voice、口型驱动方式。
6. 输出必须包含 `issues`、`revised_prompt`、`model_notes`、`used_sources`。

## 7. 检索策略

### 7.1 查询输入

从用户消息中抽取：

```json
{
  "intent": "optimize | critique | rewrite | adapt | compare",
  "model_family": "image | video | digital_human | lipsync | general",
  "provider": "grok | openai | gemini | wan | kling | ...",
  "model": "具体模型 ID",
  "prompt_text": "用户原始提示词",
  "assets": [],
  "constraints": {
    "aspect": "9:16",
    "duration": 4,
    "language": "zh-CN",
    "preserve_original_intent": true
  }
}
```

### 7.2 多路召回

首版建议召回 12 条以内：

| 召回来源 | 数量 | 说明 |
| --- | --- | --- |
| 模型官方文档 | 3 | 高权重 |
| 同模型/同 provider 经验 | 3 | 模型特化 |
| 本项目本地经验 | 3 | 最贴近业务 |
| 社区高信号资料 | 2 | 补充技巧 |
| 通用 Prompt 结构 | 1 | 兜底 |

### 7.3 重排规则

重排分数：

```text
score = semantic_score * 0.45
      + trust_weight * 0.25
      + model_match * 0.15
      + recency_weight * 0.10
      + local_success_weight * 0.05
```

其中 `local_success_weight` 来自本项目的历史评测，比如某个视频模型在人物一致性、产品文字、口型、镜头稳定性上的实际得分。

## 8. Agent 输出格式

### 8.1 批注模式

```json
{
  "mode": "critique",
  "summary": "这个 Prompt 的主要问题是...",
  "issues": [
    {
      "severity": "high",
      "span": "原文片段",
      "problem": "参考图角色不清",
      "why_it_matters": "视频模型可能不知道哪张是首帧，哪张是人物一致性参考。",
      "suggestion": "明确写成 TARGET_FRAME / HOST_REFERENCE / PRODUCT_REFERENCE。"
    }
  ],
  "used_sources": [
    {
      "doc_id": "...",
      "title": "...",
      "trust_level": "official",
      "reason": "参考图角色说明"
    }
  ]
}
```

### 8.2 优化模式

```json
{
  "mode": "optimize",
  "revised_prompt": "...",
  "negative_prompt": "...",
  "changes": [
    "补充了画幅和镜头稳定约束",
    "把参考图角色拆成 TARGET_FRAME / HOST_REFERENCE / PRODUCT_REFERENCE",
    "删除了互相冲突的动作描述"
  ],
  "model_notes": [
    "该模型不适合在视频提示词里承诺准确读字幕，建议声音交给 TTS。"
  ],
  "used_sources": []
}
```

### 8.3 前端展示

对话气泡中建议分块展示：

1. 问题批注
2. 优化后 Prompt
3. Negative Prompt
4. 模型注意事项
5. 来源依据
6. 操作按钮：复制、应用到图像生成、应用到视频生成、保存版本

不要在 UI 中显示大段说明文字；详细来源可折叠。

## 9. 后端接口设计

### 9.1 Agent Chat

扩展通用 Agent Chat：

```text
POST /api/koubo-storyboard/tasks/{task_id}/agents/prompt_agent/chat/ensure-session
GET  /api/koubo-storyboard/tasks/{task_id}/agents/prompt_agent/chat/messages
POST /api/koubo-storyboard/tasks/{task_id}/agents/prompt_agent/chat/message
GET  /api/koubo-storyboard/tasks/{task_id}/agents/prompt_agent/chat/events
POST /api/koubo-storyboard/tasks/{task_id}/agents/prompt_agent/chat/abort
```

需要修改：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/agent_chat_routes.py
```

新增：

```python
AGENT_CHAT_KEYS += {"prompt_agent"}
AGENT_CHAT_TITLES["prompt_agent"] = "Koubo Prompt Optimization Agent"
```

并在 system prompt 中注入：

1. 当前用户选择的 `model_family/provider/model`。
2. 当前 Prompt。
3. 检索到的知识片段。
4. 输出格式要求。

### 9.2 Knowledge Search

新增：

```text
POST /api/koubo-storyboard/tasks/{task_id}/prompt-agent/knowledge/search
```

请求：

```json
{
  "query": "用户提示词或抽取后的搜索问题",
  "mode": "optimize",
  "model_family": "video",
  "provider": "wan",
  "model": "wan2.7-i2v-2026-04-25",
  "limit": 12
}
```

响应：

```json
{
  "ok": true,
  "items": [
    {
      "doc_id": "...",
      "chunk_id": "...",
      "title": "...",
      "source_type": "official_doc",
      "trust_level": "official",
      "model_family": ["video"],
      "provider": "wan",
      "summary": "...",
      "rules": [],
      "score": 0.87
    }
  ],
  "retrieval_path": "SessionContext/PromptAgent/Retrieval/retrieval_....json"
}
```

### 9.3 保存 Prompt 版本

新增：

```text
PUT /api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions/{version_id}
```

保存：

```json
{
  "schema_version": "koubo_prompt_agent_version_0.1",
  "version_id": "prompt_agent_...",
  "task_id": 5,
  "session_id": 58,
  "mode": "optimize",
  "model_family": "video",
  "provider": "wan",
  "model": "wan2.7-i2v-2026-04-25",
  "original_prompt": "...",
  "revised_prompt": "...",
  "negative_prompt": "...",
  "issues": [],
  "used_sources": [],
  "created_at": 1782144000000
}
```

## 10. 前端实现设计

### 10.1 新增 View

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx
```

新增：

```js
const LIBRARY_VIEWS = new Set([
  "images",
  "images-agent",
  "videos",
  "videos-agent",
  "digital-human-agent",
  "prompt-agent",
  "history",
]);
```

### 10.2 新增 Sidebar 项

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx
```

新增按钮：

```jsx
<button class={props.view() === "prompt-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("prompt-agent")}>
  <span class="ual-nav-icon"><FlowIcon name="addNotes" /></span>
  <span class="ual-nav-label">提示词 Agent</span>
</button>
```

### 10.3 新增组件

建议新增：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/promptAgent/
  PromptAgentWorkspace.jsx
  PromptAgentPanel.jsx
  PromptAgentSettings.jsx
  PromptVersionList.jsx
  PromptSourceDrawer.jsx
  promptAgent.css
```

不要继续把代码塞进 `AgentPanel.jsx`。图像 Agent 已经承载图像生成、OpenCode 对话、Prompt Builder、参考图和一致性图，提示词 Agent 应该独立目录维护。

### 10.4 前端状态

```js
const [promptAgentMode, setPromptAgentMode] = createSignal("optimize");
const [promptModelFamily, setPromptModelFamily] = createSignal("image");
const [promptProvider, setPromptProvider] = createSignal("");
const [promptModel, setPromptModel] = createSignal("");
const [promptDraft, setPromptDraft] = createSignal("");
const [retrievalItems, setRetrievalItems] = createSignal([]);
const [versions, setVersions] = createSignal([]);
```

### 10.5 应用到生成入口

提示词 Agent 输出卡片提供：

| 按钮 | 行为 |
| --- | --- |
| 应用到图像生成 | 切换到 `images` 或 `images-agent`，填入 composer |
| 应用到视频生成 | 切换到 `videos` 或 `videos-agent`，填入 composer |
| 保存版本 | 写入 `SessionContext/PromptAgent/Applied/` |
| 复制 | 复制 `revised_prompt` |

首版可以只做“复制”和“保存版本”；P1 再做跨 view 填入。

## 11. 后台库更新策略

### 11.1 更新频率

| 来源 | 频率 |
| --- | --- |
| 官方文档 | 每周或手动刷新 |
| GitHub 高 star repo | 每周 |
| 社区文章 | 手动加入为主 |
| 本项目本地经验 | 每次生成评测后可追加 |
| Prompt Builder 模板 | 代码变更时自动快照 |

### 11.2 质量门槛

采集时必须记录：

1. source url
2. captured_at
3. license
4. hash
5. source type
6. trust level
7. model family
8. 是否允许作为示例展示

对于 license 不明确的社区资料：

1. 只存摘要、规则和短片段。
2. 不在最终回答中大段复述原文。
3. 不把整篇文章直接展示给用户。

## 12. Prompt Agent System Prompt 建议

```text
你是 Koubo Asset Library 的提示词优化 Agent。

你的任务是根据用户目标、目标模型、参考素材和知识库检索结果，批注并优化提示词。

规则：
1. 保留用户原始意图，不擅自替换产品、人物、品牌、画幅和核心动作。
2. 先判断模型类型，再套用对应优化标准。
3. 图像模型优先检查主体、构图、参考图角色、画幅、风格、负向约束。
4. 视频模型优先检查首帧/尾帧、动作幅度、镜头运动、时长、口型、文字风险和声音责任边界。
5. 数字人模型优先检查脚本、语气、Avatar、Voice、口型驱动和音频来源。
6. 必须指出关键问题，再给优化后的可用版本。
7. 使用知识库时只引用摘要和规则，不输出长篇原文。
8. 输出必须包含 issues、revised_prompt、negative_prompt、model_notes、used_sources。
```

## 13. 实施顺序

### P0：可用版本

1. 新增 `PromptKnowledge/` 目录和 source registry。
2. 把现有 `ToolLibrary/Analysis_V1/Reference/05_02/` 模板复制/索引为第一批本地知识。
3. 新增 SQLite FTS 检索工具，不做向量索引。
4. 新增 `prompt_agent` 通用 Agent Chat key。
5. 新增左侧“提示词 Agent”入口和独立 `PromptAgentPanel.jsx`。
6. Agent 支持批注、优化、保存版本。

### P1：真正知识库版本

1. 增加 GitHub 高 star repo 发现和下载工具。
2. 增加官方文档 fetcher。
3. 增加文章 fetcher 和人工 source registry。
4. 增加多路召回和来源权重。
5. 支持“应用到图像/视频生成输入框”。
6. 输出卡片支持来源折叠和版本 diff。

### P2：高质量生产版本

1. 加向量索引。
2. 加本项目生成结果反馈闭环。
3. 按模型维护独立评分表。
4. 支持提示词 A/B 对比。
5. 支持把成功 Prompt 自动沉淀为本地经验。
6. 支持按 Task / 客户 / 产品线建立私有 Prompt 子库。

## 14. 验收标准

### P0 验收

1. 左侧出现“提示词 Agent”，点击后进入独立页面。
2. 页面风格与图像/视频智能体一致。
3. 用户输入 Prompt 后，Agent 能输出批注和优化版。
4. Agent 输出包含可追溯来源摘要。
5. 优化记录写入 `SessionContext/PromptAgent/`。
6. 不影响现有图像生成、视频生成、Prompt Builder。

### P1 验收

1. 可按图像/视频/数字人/对嘴模型筛选知识库。
2. 官方文档、GitHub repo、文章、本地经验能被分层检索。
3. 同一 Prompt 选择不同模型时，优化结果明显不同。
4. 可把优化后的 Prompt 应用到图像或视频生成入口。
5. 每条结果能追溯到 retrieval JSON 和 used sources。

## 15. 关键设计结论

1. 新功能应该做成独立“提示词 Agent”，不要继续塞进现有 Prompt Builder。
2. Prompt Builder 保持轻量，负责结构化生成/填入；Prompt Agent 负责检索、批注、优化和模型适配。
3. 知识库必须后端离线采集和索引，不建议每次对话实时全网搜索。
4. 首版用 SQLite FTS + JSONL manifest，等资料规模起来后再上向量索引。
5. 必须同时做 Tool 和 Skill：Tool 负责资料处理，Skill 负责批注与优化标准。
6. 前端样式应复用 Asset Library 现有侧栏、Workspace 对话、composer 和 modal 风格。
