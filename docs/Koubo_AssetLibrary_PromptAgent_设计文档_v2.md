# Koubo Asset Library 提示词 Agent 设计文档

## 1. 目标

在 Asset Library 中新增一个独立的"提示词 Agent"，用于提示词的**批注、优化、改写、模型适配和经验检索**。

它不替代现有的"图像生成 / 图像计划智能体 / 视频生成 / 视频计划智能体 / 数字人智能体"，而是定位为**提示词研究与编辑工作台**：

1. 用户输入原始提示词、目标模型、用途和约束。
2. 系统按模型类型检索本地提示词知识（P0 为 system prompt 内嵌规则，P0b 起为 FTS 检索）。
3. Agent 输出批注、问题清单、优化版本、模型适配说明，以及可直接复制/应用的最终提示词。
4. 优化过程写入当前 Task Session，保留可追溯的来源、检索片段和版本记录。

### 1.1 业务价值（为什么做）

- **提高一次成功率、降低试错成本**：视频/数字人生成贵且慢，提示词在生成前被审阅优化，直接省成本。
- **跨模型适配产品化**：让不熟悉某模型（如 Wan）的人也能写出适配该模型的提示词，把模型专家知识沉淀为工具。
- **经验资产化**：把本项目实际生成的成功记录、踩坑总结变成可检索、可复用的高权重知识，越用越聪明。
- **可追溯/可审计**：面向客户交付，"为什么这样生成"能被追溯，是质量与责任的基础。

---

## 2. 当前系统基础（已代码核实）

| 能力 | 当前落点 | 复用方式 |
| --- | --- | --- |
| Asset Library 左侧导航 | `frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx`（`FlowIcon` / `props.view()` / `props.setView()` 已确认存在） | 新增 `prompt-agent` 入口，放在"数字人智能体"下方 |
| 视图集合 | `UploadAssetLibraryOverlay.jsx` L19 `LIBRARY_VIEWS`（已含 `images / images-agent / videos / videos-agent / digital-human-agent / history`） | 新增 `"prompt-agent"` |
| 独立 Agent 目录范式 | `UploadAssetLibrary/digitalHuman/`（Workspace / AgentPanel / SettingsPanel 三件套） | **照搬此目录结构**新建 `promptAgent/`，不要塞进 `AgentPanel.jsx` |
| 右侧对话 / composer / 模型选择样式 | `components/AgentPanel.jsx`、`components/VideoAgentPanel.jsx` | 复用对话区、发送/停止、工具栏样式与视频参考图/参考视频上下文写法 |
| Prompt Builder 弹窗 | `components/PromptBuilderModal.jsx` | 继续作为"构建/填入 Prompt"的轻工具，不承担知识库检索 |
| 通用 Agent Chat 框架 | `backend/opcrew_backend/koubo/koubo_storyboard/agent_chat_routes.py` + `agent_chat_services.py` | 扩展 agent key，新增 `prompt_agent`（详见 §9.1，注意共 4 处改动） |
| Prompt Builder 运行时审计目录 | 运行时相对路径 `SessionContext/PromptBuilder/`（`asset_routes.py` `PROMPT_BUILDER_REL`，在 workspace 下创建） | 同机制新增运行时 `SessionContext/PromptAgent/` |
| 模板参考资料 | `ToolLibrary/Analysis_V1/Reference/05_02/`（静态参考文件，非结构化知识库） | 作为 P0b 首批种子资料人工整理后导入 |

### 2.1 现状关键约束

- **现有 agent_chat key**：`storyboard_edit / image_plan / video_plan / composer / asset_audio / asset_video`（`agent_chat_routes.py` L42）。`prompt_agent` 在此基础上新增。
- **数字人不在 agent_chat 体系内**，走独立的 `asset_digital_human_routes.py` + 前端 `digitalHuman/` 目录。Prompt Agent"应用到数字人"属于跨模块衔接，见 §10.5 与 §16.3。
- **仓库当前没有向量 / SQLite FTS / embedding 知识库基础设施**，§5 的知识库为从零新建，是范围与排期的最大风险点。
- 前端代码位于 `frontend/src/modules/koubo/...`，本设计以此为准。

---

## 3. 产品形态

### 3.1 左侧入口

建议排序（在 `digital-human-agent` 下方）：

```text
图像生成
图像智能体
视频生成
视频智能体
数字人智能体
提示词 Agent
History
```

图标优先复用已有 Material Symbols（`addNotes` 或更明确的 `edit-square-outline`），不新增视觉系统。

### 3.2 主区域：提示词工作台

| 区域 | 内容 |
| --- | --- |
| 顶部工具栏 | 搜索框、模型类型筛选、知识库状态、刷新/重建索引按钮（P0b 起生效） |
| 左中区域 | 当前 Prompt 版本列表、优化历史、检索来源摘要 |
| 右侧区域 | 与现有图像/视频智能体一致的 Agent 对话 |
| 底部 Composer | 粘贴提示词、选择用途、选择模型类别、发送给 Agent |

首版沿用右侧 Workspace 对话风格，中间区域只做简洁的 **Prompt Versions / Sources / Diff** 三栏，不做营销式大页面。

### 3.3 右侧对话交互

Composer 工具栏：

| 控件 | 用途 |
| --- | --- |
| `+` | 附加参考图片、视频或文本文件 |
| `image` | 加载人物/产品一致性参考图 |
| `addNotes` | 从当前 Prompt Builder 草稿导入 |
| `tune` | 打开提示词 Agent 设置 |
| `arrowForward` | 发送 |

模式选择（segmented control，放 Composer 上方或 Settings）：

```text
批注 / 优化 / 改写 / 模型适配 / 对比
```

### 3.4 界面布局草图（ASCII Mockup）

> 说明：左侧导航、右侧对话、Composer 复用现有 Asset Library 范式；中间"提示词工作台"为新形态。下列草图区分 **P0a 最小版** 与 **P1 完整版**。

#### 3.4.1 P0a 最小版：仅"侧栏入口 + 右侧对话"，不做中间工作台

中间区域留白或放一段简短引导，结果用对话气泡里的卡片承载。零未定项，纯复用。

> P0a 的 segmented control 只启用 `[批注][优化]`，`[改写][模型适配][对比]` 渲染为 disabled（见 §4 启用范围表）。下方 mockup 标 `*` 的为当前选中模式。

```text
┌───────────────┬─────────────────────────────────────────────────────────────┐
│ 左侧导航       │ 提示词 Agent                                                 │
│               │                                                              │
│ 图像生成       │   ┌──────────────────────────────────────────────────────┐ │
│ 图像智能体     │   │ 👤 把这张参考图当人物一致性，帮我优化这段视频 prompt   │ │
│ 视频生成       │   └──────────────────────────────────────────────────────┘ │
│ 视频智能体     │   ┌──────────────────────────────────────────────────────┐ │
│ 数字人智能体   │   │ 🤖 这个 prompt 的主要问题是参考图角色不清……          │ │
│▶提示词 Agent   │   │    ┌── 结果卡片（见 3.4.3）─────────────────────────┐ │ │
│ History       │   │    │ ⚠ 问题批注 / ✏ 优化后 Prompt / 🚫 Negative …  │ │ │
│               │   │    └─────────────────────────────────────────────┘ │ │
│               │   └──────────────────────────────────────────────────────┘ │
│               │  ┌─ Composer ───────────────────────────────────────────┐  │
│               │  │ [批注][优化*][改写][模型适配][对比]   image▾ wan▾     │  │
│               │  │ ┌──────────────────────────────────────────────────┐ │  │
│               │  │ │ 粘贴 / 输入提示词……                              │ │  │
│               │  │ └──────────────────────────────────────────────────┘ │  │
│               │  │  ＋   image   addNotes   tune                      ▶  │  │
│               │  └──────────────────────────────────────────────────────┘  │
└───────────────┴─────────────────────────────────────────────────────────────┘
```

#### 3.4.2 P1 完整版：三区（导航 / 中间工作台 / 右侧对话）

```text
┌────────────┬──────────────────────────────────────────┬───────────────────────────┐
│ 左侧导航    │ 提示词工作台                              │ Agent 对话                 │
│            │ ┌─ 工具栏 ───────────────────────────┐   │ ┌───────────────────────┐ │
│ 图像生成    │ │ 🔍搜索  [模型筛选▾] 知识库●就绪 ⟳重建│   │ │ 🤖 主要问题是参考图   │ │
│ 图像智能体  │ └────────────────────────────────────┘   │ │   角色不清……          │ │
│ 视频生成    │ ┌─版本/来源──┐ ┌─ 当前版本 / Diff ──────┐ │ │   ┌─结果卡片────────┐ │ │
│ 视频智能体  │ │ ● v3 当前  │ │  - host.png          ⎫  │ │ │   │⚠问题 ✏优化…     │ │ │
│ 数字人智能体│ │   v2       │ │  + HOST_REFERENCE:…  ⎬改 │ │ │   └─────────────────┘ │ │
│▶提示词Agent │ │   v1 原始  │ │  ……                  ⎭  │ │ └───────────────────────┘ │
│ History    │ ├────────────┤ │                        │ │ ┌─ Composer ───────────┐ │
│            │ │ 来源摘要    │ │  [复制][应用图像]      │ │ │[批注][优化*][改写]…  │ │
│            │ │ ·官方文档 3│ │  [应用视频][存为新版本]│ │ │ image▾  wan▾         │ │
│            │ │ ·本地经验 3│ │                        │ │ │ ┌──────────────────┐ │ │
│            │ │ ·社区   2 │ │                        │ │ │ │ 粘贴提示词……     │ │ │
│            │ │ [展开来源▾]│ │                        │ │ │ └──────────────────┘ │ │
│            │ └────────────┘ └────────────────────────┘ │ │ ＋ image notes tune ▶│ │
│            │                                            │ └──────────────────────┘ │
└────────────┴──────────────────────────────────────────┴───────────────────────────┘
   ↑固定          ↑左中：版本列表 + 来源摘要 ｜ 右中：当前版本/Diff      ↑复用现有对话
```

#### 3.4.3 结果卡片（对话气泡内，六分块，来源默认折叠）

对应 §8.4。`<PROMPT_AGENT_RESULT>` 标签块解析后渲染为此卡片。

```text
┌─ 优化结果 · video/wan · 模式：优化 ───────────────────────────┐
│ ⚠ 问题批注 (2)                                                │
│   • [高] 参考图角色不清 — 模型分不清首帧/人物参考             │
│   • [中] 缺少镜头稳定约束                                     │
│ ─────────────────────────────────────────────────────────── │
│ ✏ 优化后 Prompt                                              │
│   A cinematic medium shot of HOST_REFERENCE standing…  [复制] │
│ ─────────────────────────────────────────────────────────── │
│ 🚫 Negative Prompt                                           │
│   warped face, extra fingers, jitter…                  [复制] │
│ ─────────────────────────────────────────────────────────── │
│ 💡 模型注意事项                                              │
│   • wan 不适合在 prompt 里承诺读字幕，声音交给 TTS           │
│ ─────────────────────────────────────────────────────────── │
│ 📎 来源依据 (5)                              [展开 ▾]         │
│ ─────────────────────────────────────────────────────────── │
│ [复制 Prompt] [保存版本]   [应用到图像生成][应用到视频生成]  │
│                            └─ P1 启用，P0a 隐藏/disabled ─┘  │
└──────────────────────────────────────────────────────────────┘
```

> P0a 卡片只显示 `复制 / 保存版本`；"来源依据"分块（无知识库时）与两个"应用"按钮在 P0a 隐藏，P0b/P1 再启用（见 §10.5、§4 启用范围表）。

#### 3.4.4 对比模式（"对比"区别于其他四个模式，并排双栏）

选择"对比"模式时，中间工作台切换为左右并排，便于看模型适配/版本差异。

```text
┌─ 工具栏 ── 模式：对比 ──── 左：[v2 ▾]  右：[v3 ▾] ──────────────┐
├──────────────────────────────┬───────────────────────────────┤
│ v2 · image/openai            │ v3 · video/wan                │
│                              │                               │
│ A photo of a host holding…   │ A cinematic shot, HOST_REF…   │
│                              │                               │
│ ＋ 加了构图                  │ ＋ 加了首帧/镜头稳定/Negative │
│ － 无 Negative               │ － 删除冲突动作               │
│                              │                               │
│ [设为当前]                   │ [设为当前]  ● 当前            │
└──────────────────────────────┴───────────────────────────────┘
```

> 落地优先级：3.4.1 属 P0a；3.4.2 的版本列表/来源摘要/Diff 属 P1；3.4.4 对比模式属 P2（见 §13）。3.4.3 结果卡片 P0a 即需要（无来源时"来源依据"显示为空/隐藏）。

---

## 4. Agent 能力边界

提示词 Agent（全周期）聚焦四件事：

1. **批注**：指出目标不清、模型不适配、负向约束缺失、参考图角色不明、镜头/动作冲突等问题。
2. **优化**：在不改变用户核心意图的前提下，补齐结构、模型关键字、约束、参考图说明和失败规避项。
3. **模型适配**：把同一提示词改写为图像、视频、数字人口播、对嘴等不同模型版本。
4. **引证**：每次优化返回"依据了哪些知识片段"并标明来源类型，不直接暴露大段原文。

**各阶段启用范围**：

| 能力 | P0a | P0b | P1 |
| --- | --- | --- | --- |
| 批注 / 优化 | ✅ 启用 | ✅ | ✅ |
| 模型适配 / 改写 | ❌ 模式置灰（disabled，不渲染结果） | ❌ | ✅ 启用 |
| 引证（used_sources） | ❌ 无知识库，卡片不显示"来源"分块 | ✅ 启用（种子库） | ✅ |
| 对比 | ❌ | ❌ | P2（见 §13） |

> P0a 的 segmented control 只高亮"批注/优化"，其余模式渲染为 disabled 态（hover 提示"P1 启用"），不发请求。

**不做**：首版不直接发起图片/视频生成。先输出可审阅 Prompt，再由用户复制到生成入口或点击"应用到当前输入框"。这条边界保证它与已有四个生成功能不重叠。

> **P0a 价值预期**：P0a 没有知识库，Agent 的能力来自 §12 system prompt **内联的最小批注/优化清单** + 通用 LLM 能力。现有 `agent_chat_system_prompt()`（`agent_chat_services.py:328`）只做 f-string 拼接，无加载 .md/Skill 文件的机制，因此 P0a 的清单**直接写进 system prompt 字符串**（见 §12）。Skill 抽离为单一真源是 P1 的事（需配套 Skill 加载器，见 §6.3）。"来源可追溯"在 P0b 引入种子规则后才有实质内容（见 §14）。

---

## 5. 知识库组织方式（P0b 起，主体在 P1）

### 5.1 存储原则：扁平存储 + 元数据过滤

分类与召回**完全靠元数据字段过滤**（`model_family/provider/model_ids`，见 §5.3），物理存储扁平化，目录只按"原始/规范化/索引"分层，不按 provider 分层。模型迭代极快（参考 Max alias 版本演进），若用目录树编码 provider 会导致新增模型要建目录、迁数据，因此一律走元数据。

```text
PromptKnowledge/
  registry/
    sources.json          # 来源登记，所有外部资料先登记后采集
    models.json           # 模型元数据（family/provider/model_ids/别名）
    licenses.json
  raw/                    # 采集原文（按 source_id 归档，不按 provider）
  normalized/             # 规范化 JSONL（每条带 model_family/provider/model_ids 字段）
  index/
    fts.sqlite            # P0b：SQLite FTS5
    vectors/              # P2：向量索引
    manifests/
  reports/
    crawl_runs/
    quality_audits/
```

当前 Task 运行痕迹（运行时相对路径，随 workspace 创建）：

```text
SessionContext/PromptAgent/
  ChatState.json
  Drafts/        # 未保存的草稿
  Versions/      # 已保存的 Prompt 版本（§9.3 PUT/POST 落点）
  Applied/       # 已"应用到生成入口"的记录（§10.5），与 Versions 区分
  Retrieval/     # 每次检索的 retrieval_<id>.json，后端落盘的权威来源副本（§9.2/§9.4）
  Critiques/     # SSE 解析到 <PROMPT_AGENT_RESULT> 后自动落盘的批注记录（§9.5）
  Exports/
```

### 5.2 Source Registry

所有外部资料先进入 `registry/sources.json`，不让 Agent 临时抓网页。

```json
{
  "source_id": "github_prompt_repo_xxx",
  "source_type": "github_repo",
  "model_family": ["general", "image"],
  "provider": "",
  "url": "https://github.com/...",
  "license": "unknown",
  "trust_level": "community_high_signal",
  "refresh_policy": "weekly",
  "enabled": true,
  "notes": "GitHub stars ranking source, requires crawl-time star count"
}
```

来源分级：

| trust_level | 说明 | 使用方式 |
| --- | --- | --- |
| `official` | 官方文档、官方 cookbook、provider 示例 | 高权重依据 |
| `community_high_signal` | 高 star repo、长期维护文章 | 中高权重，保留来源与时间 |
| `local_experience` | 本项目实际生成记录、人工评测、踩坑总结 | 高权重，尤其适合同类任务 |
| `community_article` | 博客、教程、案例 | 中权重，只提取方法不照搬表达 |
| `experimental` | 未验证技巧、论坛经验 | 低权重，仅作候选提醒 |

### 5.3 Normalized Document（统一 JSONL）

时间戳统一 **UTC ISO8601**。

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
  "captured_at": "2026-06-23T00:00:00Z",
  "updated_at_source": "",
  "chunk_id": "chunk_0007",
  "chunk_type": "principle",
  "tags": ["reference_images", "negative_prompt", "composition"],
  "content": "normalized summary or short allowed excerpt",
  "summary": "结构化摘要",
  "rules": [
    { "rule_id": "image_reference_role_order", "rule_type": "do", "text": "明确每张参考图的角色和顺序。", "confidence": 0.84 }
  ],
  "examples": [],
  "hash": "sha256..."
}
```

### 5.4 模型分类（作为元数据，不作为目录）

按"用途 + 模型家族"作为可过滤维度，登记在 `registry/models.json`：

```text
model_family: image | video | digital_human | lipsync | general
provider(image):   openai | grok | gemini | midjourney | flux | ...
provider(video):   sora | veo | kling | wan | seedance | grok_video | ...
provider(d_human): heygen | talking_avatar | ...
provider(lipsync): syncso | kling_lipsync | ...
general:           prompt_structure | negative_prompt | critique_methods
```

新增模型只改 `models.json`，不动目录与已索引数据。

---

## 6. 采集与更新流程（P1 主体）

### 6.1 不在前端实时全网搜索

全网搜索、GitHub 下载、官方文档抓取是后端工具或定时任务，不发生在每次对话时。原因：响应慢、来源质量不可控、官网结构常变、license/robots/限流需处理、临时搜索导致相同输入结果不稳定。

### 6.2 工具目录（序号命名，与 ToolLibrary 现有风格一致）

```text
ToolLibrary/PromptKnowledge/
  01_SourceDiscover.py
  02_SourceFetch.py
  03_NormalizePromptDocs.py
  04_BuildPromptIndex.py
  05_SearchPromptKnowledge.py
  06_PromptCritiquePackage.py
  README.md
```

| 工具 | 职责 | 阶段 |
| --- | --- | --- |
| `01_SourceDiscover.py` | 通过 GitHub API / 搜索 API / 人工 seed 发现候选来源，记录 star、更新时间、license | P1 |
| `02_SourceFetch.py` | 下载 repo / 文章 / 官方文档写入 `raw/` | P1 |
| `03_NormalizePromptDocs.py` | 清洗 MD/HTML/PDF，切块、去重、抽规则/示例/标签 | P0b 最小版 → P1 完整 |
| `04_BuildPromptIndex.py` | 构建 SQLite FTS、manifest（P2 加向量） | P0b 最小版 |
| `05_SearchPromptKnowledge.py` | 后端/Agent 检索入口 | P0b 最小版 |
| `06_PromptCritiquePackage.py` | 把用户 Prompt + 检索结果 + 批注要求打包成 Agent 上下文 | P1 |

> **P0b 最小集**：只需 `03`（手工/半自动整理 5–10 条种子）、`04`（建一张 FTS5 表）、`05`（关键词 + trust 排序的 top-k）。`01/02/06` 与多路召回属 P1。

### 6.3 Tool / Skill / Agent 分工与单一真源

| 类型 | 是否需要 | 负责什么 |
| --- | --- | --- |
| Tool | 必须 | 抓取、清洗、索引、检索、生成引用包 |
| Skill | 建议 | 提示词批注方法、输出格式、评分维度、模型适配原则 |
| Agent | 必须 | 与用户对话，调用检索结果，给批注和优化稿 |

**单一真源**：批注方法/输出格式/评分维度从 P1 起以 Skill 文件（`SKILL_prompt_optimization.md`）为**唯一权威**；后端配套 Skill 加载器把 `skill_text` 注入 system prompt，system prompt 自身只保留边界与触发指令，避免两处维护漂移。P0a 阶段尚无加载器，标准内联在 system prompt 字符串里（见 §12）。

```text
ToolLibrary/PromptKnowledge/SKILL_prompt_optimization.md
```

Skill 要点：保持用户原意优先；先批注再改写；图像关注主体/构图/光线/参考图角色/比例/风格边界；视频关注首尾帧/动作幅度/镜头稳定/时长/口型/文字风险；数字人关注脚本/语气/Avatar/Voice/口型驱动；输出必含 `issues / revised_prompt / model_notes / used_sources`。

---

## 7. 检索策略

### 7.1 查询输入

```json
{
  "intent": "optimize | critique | rewrite | adapt | compare",
  "model_family": "image | video | digital_human | lipsync | general",
  "provider": "grok | openai | gemini | wan | kling | ...",
  "model": "具体模型 ID",
  "prompt_text": "用户原始提示词",
  "assets": [],
  "constraints": { "aspect": "9:16", "duration": 4, "language": "zh-CN", "preserve_original_intent": true }
}
```

### 7.2 分阶段检索（重新分期）

- **P0b（最小可用）**：单路 FTS 关键词召回 top-k（k≤8），仅按 `trust_level` 降序 + FTS rank 排序。无多路召回、无加权公式。
- **P1（多路召回）**：

  | 召回来源 | 数量 | 说明 |
  | --- | --- | --- |
  | 模型官方文档 | 3 | 高权重 |
  | 同模型/同 provider 经验 | 3 | 模型特化 |
  | 本项目本地经验 | 3 | 最贴近业务 |
  | 社区高信号资料 | 2 | 补充技巧 |
  | 通用 Prompt 结构 | 1 | 兜底 |

### 7.3 重排规则（P2，依赖向量与评测数据）

```text
score = semantic_score * 0.45      # 需向量 → P2
      + trust_weight * 0.25
      + model_match * 0.15
      + recency_weight * 0.10
      + local_success_weight * 0.05 # 需历史评测 → P2
```

> 说明：完整公式属 P2——`semantic_score` 需向量、`local_success_weight` 需历史评测，二者 P0/P1 不具备。P0b 用 trust + FTS rank 兜底。

---

## 8. Agent 输出格式

### 8.1 结构化约定：流式文本 + 标签 JSON 块（对齐现有实现）

现有 `image_plan` / `video_plan` 的结构化输出是在**流式文本里嵌一个标签块**（如 `<IMAGE_PLAN_CANDIDATE>{...}</IMAGE_PLAN_CANDIDATE>`），前端解析标签块渲染结构化卡片，正文仍是流式对话。Prompt Agent **沿用同一约定**：

```text
<PROMPT_AGENT_RESULT>{ ...见下方 schema... }</PROMPT_AGENT_RESULT>
```

**可复用与需新增**：
- **可复用**：SSE 传输（`/chat/events`）、流式半包容错*模式*（现有 `AgentPanel.jsx` 对未闭合标签块 `try/catch` 忽略，可照抄）、"标签块嵌在流式正文里"的整体约定。
- **必须新增**：`PROMPT_AGENT_RESULT` 的**专用 parser**。现有解析是**硬编码标签**——`AgentPanel.jsx` 只抽 `<PROMPT_CANDIDATE>`；`VideoAgentPanel.jsx` 的正则 `<([A-Z_]*(?:CANDIDATE|ACTION|ADVICE|DIAGNOSIS))>` 只匹配这四种后缀结尾的标签，`...RESULT` **不会被命中**。因此新面板需自写：抽取 `PROMPT_AGENT_RESULT`、隐藏原始标签、流式半包容错、结果卡片渲染，以及对应测试。否则 §14 P0a 验收第 2 条会失败。

### 8.2 批注模式

```json
{
  "mode": "critique",
  "summary": "这个 Prompt 的主要问题是...",
  "issues": [
    { "severity": "high", "span": "原文片段", "problem": "参考图角色不清",
      "why_it_matters": "视频模型可能不知道哪张是首帧、哪张是人物一致性参考。",
      "suggestion": "明确写成 TARGET_FRAME / HOST_REFERENCE / PRODUCT_REFERENCE。" }
  ],
  "used_sources": [
    { "doc_id": "...", "title": "...", "trust_level": "official", "reason": "参考图角色说明" }
  ]
}
```

### 8.3 优化模式

```json
{
  "mode": "optimize",
  "revised_prompt": "...",
  "negative_prompt": "...",
  "changes": ["补充画幅与镜头稳定约束", "参考图角色拆为 TARGET_FRAME / HOST_REFERENCE / PRODUCT_REFERENCE", "删除互相冲突的动作描述"],
  "model_notes": ["该模型不适合在视频提示词里承诺准确读字幕，建议声音交给 TTS。"],
  "used_sources": []
}
```

### 8.4 前端展示

对话气泡分块：① 问题批注 ② 优化后 Prompt ③ Negative Prompt ④ 模型注意事项 ⑤ 来源依据（可折叠，P0b 起）⑥ 操作按钮。不显示大段说明文字。

按钮分阶段（与 §10.5 一致）：**P0a 仅 `复制 / 保存版本`**；`应用到图像生成 / 应用到视频生成` P0a 隐藏或 disabled，P1 启用。

### 8.5 `<PROMPT_AGENT_RESULT>` canonical schema 与 shared fixtures

同一标签会被**后端**（§9.5 校验/落盘）和**前端**（§8.1 展示）各解析一次。为避免两套 parser 漂移，约定**单一权威 schema + 共享测试向量**：

- **职责切分**：**后端**是权威——负责抽取标签、JSON 解析、schema 校验、`used_sources` doc_id 校验（§9.4）、落盘；**前端**只做展示性解析（抽标签、隐藏原文、渲染卡片），**不重做校验逻辑**，受审计字段以后端规范化结果为准。规范化结果回前端的具体通道（即时展示 + SSE 对齐事件 + GET 兜底）见 §9.5.1。
- **Canonical schema**：字段与类型固定为 `mode∈{critique,optimize}`、`issues[]`、`revised_prompt`、`negative_prompt`、`model_notes[]`、`used_sources[]`（见 §8.2/§8.3）。schema 文件随工具目录维护：`ToolLibrary/PromptKnowledge/prompt_agent_result.schema.json`。
- **Shared fixtures**：前后端测试引用同一组样例，至少覆盖：① 正常完整块；② 流式半包（标签未闭合，应忽略不报错）；③ 非法 JSON；④ 同一消息出现重复标签（取最后一个有效块，约定一致）；⑤ `used_sources` 含库外 doc_id（后端丢弃标红、前端按规范化结果展示）。fixtures 落 `ToolLibrary/PromptKnowledge/fixtures/prompt_agent_result/`。

---

## 9. 后端接口设计

### 9.1 Agent Chat（扩展通用框架，共 4 处必改）

复用通用路由 `/api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/...`（ensure-session / messages / message / events / abort 已存在，无需新增路由）。

新增 `prompt_agent` 需改 **4 处**（缺一不可，否则缺权限或 system prompt）：

1. `agent_chat_routes.py` L42 — `AGENT_CHAT_KEYS` 加入 `"prompt_agent"`。
2. `agent_chat_routes.py` L43–50 — `AGENT_CHAT_SURFACES` 加映射，复用 `SURFACE_KOUBO_ASSET_AGENT_CHAT`（与 asset_audio/asset_video 同 surface）。
3. `agent_chat_routes.py` L51–58 — `AGENT_CHAT_TITLES["prompt_agent"] = "Koubo Prompt Optimization Agent"`。
4. `agent_chat_services.py` — 在 `agent_chat_context()`（L312）与 `agent_chat_system_prompt()`（L328）各加 `prompt_agent` 分支：context 注入 `model_family/provider/model + 当前 Prompt`，以及**按 `retrieval_id` 从服务器落盘读取的检索片段**（见 §9.4，不信任前端回传的 items）；system prompt 见 §12。

### 9.2 Knowledge Search（P0b 起）

```text
POST /api/koubo-storyboard/tasks/{task_id}/prompt-agent/knowledge/search
```

权限：复用 `SURFACE_KOUBO_ASSET_AGENT_CHAT` 做角色校验（与 §9.1 一致）。

请求：

```json
{ "query": "用户提示词或抽取后的搜索问题", "mode": "optimize",
  "model_family": "video", "provider": "wan", "model": "wan2.7-i2v-2026-04-25", "limit": 8 }
```

检索时后端把完整结果**落盘**到 `Retrieval/retrieval_<id>.json`（这是后续唯一可信的权威副本），响应回 `retrieval_id`：

```json
{ "ok": true,
  "retrieval_id": "retrieval_1782144000000_a1b2",
  "items": [
    { "doc_id": "...", "chunk_id": "...", "title": "...", "source_type": "official_doc",
      "trust_level": "official", "model_family": ["video"], "provider": "wan",
      "summary": "...", "rules": [], "score": 0.87 }
  ] }
```

> `items` 仅供前端**展示**（来源摘要面板）。它**不会**被前端回传作为上下文来源——发消息时只传 `retrieval_id`，后端按 id 读盘注入（见 §9.4）。这样前端无法伪造出处，"可追溯/可审计"才成立。

### 9.3 Prompt 版本（create / update / list）

创建与覆盖分开，避免"首次保存无 id、路径参数不能留空"的问题。三个端点：

```text
POST /api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions             # 创建，后端生成并返回 version_id
PUT  /api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions/{version_id} # 全量覆盖已存在版本
GET  /api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions             # 列表，供 §3.4.2 版本列表 / 恢复
```

约定：

- **`version_id` 由后端在 POST 时生成**（`prompt_agent_<ms时间戳>_<短随机>`）并回写到响应体；前端 POST 不传 id。
- **PUT 为全量覆盖**；同一 id 重复 PUT 覆盖整条记录。"保存为新版本"= 再发一次 POST 得新 id。
- 落点：写入 `SessionContext/PromptAgent/Versions/`（**不是 Applied/**；Applied/ 专记"应用到生成入口"的动作，见 §10.5）。
- 权限：均复用 `SURFACE_KOUBO_ASSET_AGENT_CHAT`。
- **`task_id` 取自路径、`session_id` 由后端从 task 派生**（`task_or_404(task_id)["session_id"]`）。请求体**不含**这两个字段，避免前端传值与路径不一致导致错写/越权。

POST 请求体（不含 version_id / task_id / session_id；后端派生后写入落盘 record）：

```json
{ "schema_version": "koubo_prompt_agent_version_0.1",
  "mode": "optimize",
  "model_family": "video", "provider": "wan", "model": "wan2.7-i2v-2026-04-25",
  "original_prompt": "...", "revised_prompt": "...", "negative_prompt": "...",
  "issues": [], "used_sources": [], "retrieval_id": "retrieval_..." }
```

响应（回派生值）：`{ "ok": true, "version_id": "prompt_agent_1782144000000_a1b2", "task_id": 5, "session_id": 58, "created_at": 1782144000000 }`

### 9.4 P0b 检索编排时序（来源信任链在后端）

`send_koubo_agent_chat_message()`（`agent_chat_routes.py:290`）只从 payload 取 `client_context` 交给 `agent_chat_system_prompt()` 构造，**后端不会自动检索**。为保证可追溯/可审计，**检索片段与出处一律由后端按 `retrieval_id` 从落盘副本读取，前端只传 id、不传来源内容**：

```text
用户点发送
  → 前端 POST /prompt-agent/knowledge/search  (后端落盘 Retrieval/retrieval_<id>.json，返回 retrieval_id)
  → 前端把 { knowledge: { retrieval_id } } 放进 client_context（仅 id，不含 items/path）
  → 前端 POST /agents/prompt_agent/chat/message
  → 后端 agent_chat_context("prompt_agent", ...) 按 retrieval_id 读服务器落盘 JSON → 注入 system prompt
  → Agent 产出 used_sources（doc_id 取自注入片段）
  → 后端落盘前做确定性校验（见下）
```

要点：
- **不信任前端来源**：前端即使在 `client_context` 里塞 `items`/`path` 也被忽略；唯一可信来源是 `retrieval_id` 对应的落盘 JSON。这样无法伪造出处。
- **`retrieval_id` 自身必须校验（它仍是客户端输入）**：
  - 格式白名单：`^retrieval_\d+_[a-z0-9]{4,}$`，不匹配直接拒绝，**绝不把原值拼进文件路径**。
  - 归属隔离：只解析到**当前 task** 的 `SessionContext/PromptAgent/Retrieval/` 下；解析后对最终路径做 realpath 校验，确认仍在该目录内（防 `..` 穿越 / 绝对路径注入）。
  - 找不到 / 不属于当前 task → **降级为空来源**（`used_sources=[]`），不报错中断、也不读任何其它文件。
- **确定性校验（不只靠 system prompt 软约束）**：保存 version / critique 前，后端逐条核对 `used_sources[*].doc_id` 是否 ∈ 本次 `retrieval_id` 的 item set；非法项**丢弃并标红**，并把 `{kept, dropped}` 校验结果写进落盘 record。§12 的提示词约束是第一道软防线，本校验是硬保证，二者叠加才满足 §14 的"可追溯"验收。
- **检索失败 / 未命中策略（P0b 冷启动高频）**：`/knowledge/search` 失败、FTS 未建好、或零命中时——**降级继续发送**，不带 `retrieval_id`，`used_sources=[]`，前端提示"本次无知识库命中"。核心批注/优化能力来自 §12 内联清单，不依赖检索，**绝不因检索失败阻断对话**。
- 备选（P1+）：把检索移到后端 message handler 内部（前端连 `retrieval_id` 都不用传，后端自检自取），更安全；P0b 先用"前端传 id"跑通。

### 9.5 批注自动落盘（Critiques/）

`Critiques/` 不依赖"保存版本"——否则未保存的对话批注不留痕，违背 §1 第 4 条"优化过程写入当前 Task Session"。落盘**自动触发**：

- 后端在一条 assistant 消息的 SSE 完成、且解析出 `<PROMPT_AGENT_RESULT>` 后，自动写一份 critique record 到 `SessionContext/PromptAgent/Critiques/critique_<msgId>.json`（含 `mode / original_prompt / issues / used_sources（已校验）/ retrieval_id / created_at`）。
- 这是被动留痕，不需用户操作；"保存版本"是用户主动固化到 `Versions/`，二者并存。
- **不能只依赖 live SSE（否则断线漏记）**：事件收集线程在 `/chat/events` 请求内启动，客户端断开即 `stop_event.set()`（`agent_chat_routes.py:446/469`）。若用户发送后关页面 / EventSource 掉线，断开后才完成的 assistant 消息就不会被 `on_event` 落盘。因此必须补 **catch-up/backfill**：重开 events 或拉 `messages` 时扫描**已完成的** assistant message，**按 message id 幂等**写 `Critiques/`（已存在则跳过）。直接复用现有 `asset_video` 的 catch-up 范式——`enqueue_existing_agent_video_generations()`（连接时 + 每 8s 轮询）+ `triggered_*`/`claim_*` 幂等键（`agent_chat_routes.py:404-448`）。
- 可选补充端点（P1）：`POST /prompt-agent/critiques` 供前端显式补存或编辑批注。

#### 9.5.1 规范化结果如何回到前端（兑现 §8.5 "以后端结果为准"）

前端即时展示和后端权威结果分两层，按 message id 对齐：

1. **即时展示（乐观）**：前端本地解析 `<PROMPT_AGENT_RESULT>` 立刻渲染卡片正文（prompt / issues / model_notes 等展示性字段），保证流式体验，不等后端。
2. **权威对齐（SSE 事件）**：后端解析+校验完成、落盘 `Critiques/` 后，在同一 `/chat/events` 流里发一个 `prompt_agent.result.normalized` 事件，载 `{ message_id, used_sources(已校验), validation:{kept,dropped}, critique_path }`。前端按 `message_id` 用该事件**覆盖**本地的 `used_sources` 与校验标记（库外来源标红/移除）。
3. **断线兜底（GET catch-up）**：若该 SSE 事件因断线未收到，前端重连或打开历史时通过 `GET …/chat/messages`（或读 `Critiques/`）按 message id 拉回规范化结果再对齐——与 §9.5 的 backfill 同源同幂等。

要点：展示性字段可信本地解析；**`used_sources` 这类受审计字段一律以后端规范化结果为准**，本地值仅作占位。

---

## 10. 前端实现设计

### 10.1 新增 View

`UploadAssetLibraryOverlay.jsx` L19 `LIBRARY_VIEWS` 加 `"prompt-agent"`：

```js
const LIBRARY_VIEWS = new Set([
  "images", "images-agent", "videos", "videos-agent",
  "digital-human-agent", "prompt-agent", "history",
]);
```

### 10.2 新增 Sidebar 项

`components/LibrarySidebar.jsx`（SolidJS，注意 `class=` 非 `className`）：

```jsx
<button class={props.view() === "prompt-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("prompt-agent")}>
  <span class="ual-nav-icon"><FlowIcon name="addNotes" /></span>
  <span class="ual-nav-label">提示词 Agent</span>
</button>
```

### 10.3 新增组件（照搬 digitalHuman/ 范式）

```text
frontend/src/modules/koubo/UploadAssetLibrary/promptAgent/
  PromptAgentWorkspace.jsx
  PromptAgentPanel.jsx
  PromptAgentSettings.jsx
  PromptVersionList.jsx
  PromptSourceDrawer.jsx
  promptAgent.css
```

不要把代码塞进 `AgentPanel.jsx`（它已承载图像生成/OpenCode 对话/Prompt Builder/参考图）。`digitalHuman/` 已证明独立目录是项目认可的范式。

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

| 按钮 | 行为 | 阶段 |
| --- | --- | --- |
| 复制 | 复制 `revised_prompt` | P0a |
| 保存版本 | POST `/versions` → 写入 `SessionContext/PromptAgent/Versions/`（§9.3） | P0a |
| 应用到图像生成 | 切到 `images`/`images-agent`，填入 composer，并在 `Applied/` 记一条 | P1 |
| 应用到视频生成 | 切到 `videos`/`videos-agent`，填入 composer，并在 `Applied/` 记一条 | P1 |
| 应用到数字人 | 跨模块衔接（数字人不在 agent_chat 体系），需对接 `digitalHuman/` 入口 | P2 |

> `Versions/`（保存的版本） 与 `Applied/`（已应用到生成的动作）是两类记录，不要混用。

---

## 11. 后台库更新策略（P1 起）

### 11.1 更新频率

| 来源 | 频率 |
| --- | --- |
| 官方文档 | 每周或手动 |
| GitHub 高 star repo | 每周 |
| 社区文章 | 手动为主 |
| 本项目本地经验 | 每次生成评测后追加 |
| Prompt Builder 模板 | 代码变更时快照 |

### 11.2 质量门槛（采集必录）

source url、captured_at、license、hash、source type、trust level、model family、是否允许作为示例展示。

license 不明的社区资料：只存摘要/规则/短片段；最终回答不大段复述原文；不整篇展示给用户。

---

## 12. System Prompt 建议（P0a 内联清单；Skill 抽离为 P1）

> `agent_chat_system_prompt()`（`agent_chat_services.py:328`）只做 f-string 拼接、无 Skill/.md 加载机制，因此 P0a 的批注/优化清单**内联在下方字符串里**。P1 引入 Skill 加载器后，把内联清单抽到 `SKILL_prompt_optimization.md` 作为单一真源（见 §6.3），本段缩减为边界 + `{skill_text}` 注入。

```text
你是 Koubo Asset Library 的提示词优化 Agent。

任务：根据用户目标、目标模型、参考素材（以及知识库检索结果，若有），批注并优化提示词。

边界：
1. 保留用户原始意图，不擅自替换产品、人物、品牌、画幅和核心动作。
2. 不要声称已保存或已生成；保存/应用/生成由用户点击前端按钮完成。
3. 不要调用工具、读写文件、访问网络或执行命令。
4. 使用知识库时只引用摘要和规则，不输出长篇原文。
5. used_sources 的 doc_id 只能取自下方上下文注入的来源；没有注入来源时 used_sources 返回 []，不得编造。（此为软约束；后端保存前还会按 retrieval_id 做确定性校验、丢弃非法 doc_id，见 §9.4。）

批注/优化清单（P0a 内联标准）：
- 先批注、再给可用改写；不改用户核心意图。
- 通用：主体是否明确、结构是否完整、负向约束是否缺失、是否存在互相冲突的描述。
- 图像模型：主体 / 构图 / 光线 / 参考图角色与顺序 / 画幅比例 / 风格边界 / negative。
- 视频模型：首帧·尾帧 / 动作幅度 / 镜头运动与稳定性 / 时长 / 口型 / 文字风险 / 声音责任边界（不在画面 prompt 里承诺读字幕，声音交 TTS）。
- 数字人模型：脚本 / 语气 / Avatar / Voice / 口型驱动方式 / 音频来源。
- 严重度用 high|medium|low；每条问题给 why_it_matters 与 suggestion。

输出：
- 正文用自然语言简述。
- 结构化结果放入一个标签块：
  <PROMPT_AGENT_RESULT>{"mode":"optimize|critique","issues":[],"revised_prompt":"...","negative_prompt":"...","model_notes":[],"used_sources":[]}</PROMPT_AGENT_RESULT>

当前上下文：
{context_text}
```

> P0a 只需支持 `mode` = `critique|optimize`；`rewrite|adapt|compare` 在 P1+ 再加入清单与 mode 枚举（与 §4 启用范围表一致）。

---

## 13. 实施顺序

### P0a：跑通产品形态（不含知识库，能独立交付）

1. 后端加 `prompt_agent`（§9.1 共 4 处改动）+ §12 system prompt。
2. 前端 `LIBRARY_VIEWS` 加 `prompt-agent`、侧栏加项、照 `digitalHuman/` 建 `promptAgent/` 目录、复用对话区。
3. Agent 仅启用 `critique|optimize` 两种 mode（其余模式置灰），能力来自 §12 **内联清单**（非 Skill）。
4. **后端解析 `<PROMPT_AGENT_RESULT>` + 批注自动留痕**：SSE 完成后解析、写 `Critiques/`，并补**断线 backfill + 按 message id 幂等**（§9.5）；解析按 §8.5 canonical schema。这是 P0a 验收第 3 条的硬要求，勿按"只做对话"低估。
5. 复制 + 保存版本（POST `/versions`，落 `SessionContext/PromptAgent/Versions/`）；卡片暂不显示"来源"分块。

> 注：P0a 已含一条**后端解析 + 落盘 + backfill** 工作（第 4 步），不是纯前端对话；§14 P0a #3 对应验收。

### P0b：最小知识库（补"来源可追溯"）

1. 建 `PromptKnowledge/` 扁平目录 + `registry/sources.json`。
2. 人工整理 5–10 条种子规则（含 `ToolLibrary/Analysis_V1/Reference/05_02/` 提炼）→ normalized JSONL。
3. `03/04/05` 最小版：清洗 → SQLite FTS5 → 关键词+trust 排序 top-k。
4. `/knowledge/search` 接口 + 输出卡片显示 `used_sources` 摘要。

### P1：真正知识库

GitHub 高 star 发现/下载、官方文档 fetcher、文章 fetcher + 人工 registry、多路召回与来源权重、应用到图像/视频生成输入框、来源折叠与版本 diff。

### P2：高质量生产

向量索引、生成结果反馈闭环、按模型评分表、A/B 对比、成功 Prompt 自动沉淀为本地经验、按 Task/客户/产品线建私有子库、应用到数字人跨模块。

---

## 14. 验收标准

### P0a 验收

1. 左侧出现"提示词 Agent"，点击进入独立页面，风格与图像/视频智能体一致。
2. 输入 Prompt 后 Agent 能输出批注和优化版（结构化 `<PROMPT_AGENT_RESULT>` 块被正确解析渲染）。
3. 复制与保存版本可用：保存版本写入 `SessionContext/PromptAgent/Versions/`，批注自动留痕写入 `SessionContext/PromptAgent/Critiques/`（不得写到根目录或 `Applied/`）。
4. 不影响现有图像生成、视频生成、Prompt Builder、数字人。
   > 注：P0a **不**验收"来源可追溯"，该项由 P0b 承接。

### P0b 验收

1. Agent 输出包含来自种子知识库的 `used_sources` 摘要，可追溯到 `Retrieval/*.json`。
2. `/knowledge/search` 返回带 `trust_level`/`score` 的 top-k。

### P1 验收

1. 可按图像/视频/数字人/对嘴模型筛选知识库。
2. 官方文档/GitHub repo/文章/本地经验能被分层检索。
3. 同一 Prompt 选不同模型，优化结果明显不同。
4. 可把优化后 Prompt 应用到图像或视频生成入口。
5. 每条结果能追溯到 retrieval JSON 与 used sources。

---

## 15. 主要风险与缓解

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 知识库基建从零、易拖垮可用版本 | 仓库无向量/FTS 基础 | 拆 P0a/P0b，P0a 不依赖知识库即可交付 |
| 召回/重排公式无数据支撑 | 向量与评测数据 P0 没有 | 公式下沉 P2，P0b 用 FTS+trust 兜底 |
| 知识库冷启动空洞 | 种子稀少 | P0a 价值来自 system prompt 内联清单；P0b 人工种子起步 |
| 模型分类僵化 | provider 迭代快 | 元数据过滤替代目录树编码 |
| 标准两处维护漂移 | Skill 与 system prompt 重叠 | Skill 为单一真源，system prompt 只留边界并指向 Skill |
| 跨模块衔接被低估 | 数字人不在 agent_chat 体系 | "应用到数字人"列为 P2，明确跨模块对接 |

---

## 16. 关键设计结论

1. 做成**独立"提示词 Agent"**，照 `digitalHuman/` 范式建独立目录，不塞进 Prompt Builder 或 AgentPanel。
2. Prompt Builder 保持轻量（结构化生成/填入）；Prompt Agent 负责检索、批注、优化、模型适配。
3. 知识库**后端离线采集索引**，不每次对话实时全网搜索；首版 SQLite FTS + JSONL，资料起规模后再上向量。
4. **必须同时做 Tool 和 Skill**：Tool 处理资料，Skill 定义批注/优化标准并作为该标准的单一真源。
5. 前端复用 Asset Library 现有侧栏、Workspace 对话、composer、modal 与"流式文本 + 标签 JSON 块"约定。
6. 范围按 **P0a（产品形态）→ P0b（最小知识库）→ P1（真知识库）→ P2（生产）** 推进，避免可用版本卡在数据工程上。
