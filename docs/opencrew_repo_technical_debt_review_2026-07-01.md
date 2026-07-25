# OpenCrew Repo 技术债评审报告

- 日期：2026-07-01（含当日复审校正；2026-07-04 二次校正）
- 范围：OpenCrew repo 顶层结构、后端 FastAPI 服务、前端 Solid/Vite 应用、WorkflowAssistant、ModelConfig、ToolLibrary 关键集成路径
- 方法：静态代码审查、目录结构审查、关键文件抽样阅读、构建与后端 contract tests 验证、并行子代理交叉复核
- 目标：识别架构问题、目录结构问题、代码质量问题，并给出可执行的整改优先级

> 复审校正（2026-07-01）：本轮对原报告的关键数字做了实测复核，部分数字被低估，contract tests 情况较原报告恶化。新增/校正内容集中在文末「复审校正与新增发现」一节，并在下方相关明细处标注 `[复审]`。
>
> 二次校正（2026-07-04）：本轮补充核对了报告证据清单和当前仓库状态。后端 contract tests 当前已通过；`sys.path.insert`、全局注入、`/Users/duheng/` 硬编码路径、tracked 二进制/媒体资产、`issues/`、`StoryBoardRegression/`、`docs/` 等计数已按当前工作区重新校正。历史失败和历史计数仍保留为背景，但不再作为当前事实表述。

## 总体结论

OpenCrew 当前不是单纯的“局部代码风格”问题，而是已经出现了系统性维护债。最核心的问题有四类：

1. 模块边界不清：`frontend`、`backend`、`WorkflowAssistant`、`ModelConfig`、`ToolLibrary` 之间存在跨根目录 import、`sys.path` 注入和运行时桥接。
2. 状态和依赖管理不透明：Koubo storyboard 后端服务层大量使用 `globals().update()`、模块字典注入和共享运行时对象。
3. schema 与迁移机制分裂：SQLAlchemy metadata、自定义 migrations、启动时 `ALTER TABLE` 修补同时存在。
4. 质量门禁不足：前端 build 可通过，后端 contract tests 当前本机复跑已通过，但 CI 仍主要覆盖 cache guard，缺少 build/test/lint 的统一门禁。

这些问题会直接影响后续迭代速度：改动很难局部化，测试很难隔离，部署行为容易依赖机器目录、缓存状态和人工流程。

## 评审摘要

| 等级 | 问题 | 影响 | 建议优先级 |
|---|---|---|---|
| 高 | 模块边界被跨目录 import 和 `sys.path` 注入绕开 | 构建、测试、部署强耦合，目录调整风险高 | P0 |
| 高 | Koubo 服务层使用全局依赖注入（40 处、36 个文件 `[二次校正]`） | 并发和测试隔离风险，依赖关系不可见 | P0 |
| 高 | 数据库 schema 来源分裂 | 环境漂移、迁移不可复现、回滚困难 | P0 |
| 高 | 后端 contract tests 已从历史失败恢复为当前通过，但尚未进入 CI | 回归风险无法被 CI 阻断 | P0 |
| 中 | 前后端大文件单体化 | 审查、测试、拆分成本高 | P1 |
| 中 | 前端 API helper 重复且 TS 覆盖不足 | 错误处理分叉，类型检查有效性不足 | P1 |
| 中 | 手动 cache-busting 流程 | 发布正确性依赖人工记忆 | P1 |
| 中 | 大量媒体/二进制文件进入 Git：扩展名口径 487 个、约 298.1 MB `[二次校正]` | clone/pull 慢，历史膨胀 | P1 |
| 中 | CI/lint/test 工具链不完整 | 问题主要靠人工发现 | P1 |
| 中 | 后端 496 处 except / 152 宽泛 / 32 静默 pass，前端空 `.catch` `[复审]` | 错误静默吞掉，线上无可观测性 | P1 |
| 中 | koubo_storyboard 内 722 处 isinstance 防御样板 `[复审]` | 缺 schema 边界，代偿式类型判断 | P1 |
| 中 | ToolLibrary read_json/write_json 重复 117 份 + `/Users/duheng/` Python 源文件硬编码 6 处且不全在 ToolLibrary `[二次校正]` | 换机失效、维护面巨大 | P2 |
| 低到中 | 配置、价格、provider 信息硬编码 | 数据易过期，改配置需要发版 | P2 |

## 关键问题明细

### 1. 模块边界不清，目录结构和运行时依赖不一致

#### 现象

前端代码从 `frontend` 目录直接跨根目录 import `ModelConfig` 和 `WorkflowAssistant`：

- `frontend/src/App.jsx:9` import `../../ModelConfig/frontend/src/ModelConfigModule`
- `frontend/src/modules/koubo/OpenClipModule.jsx:2` import `../../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx`
- `frontend/src/modules/koubo/OCRebuildModule.jsx:6` import 同一个 WorkflowAssistant drawer
- `frontend/vite.config.ts:32-34` 将 Vite `server.fs.allow` 放开到 repo 上一级目录

后端也通过路径注入和桥接绕开包边界：

- `backend/opcrew_backend/app.py:11-14` 将 `ModelConfig/backend` 插入 `sys.path`
- `backend/opcrew_backend/routes/asr_config.py:7-12` 将 `ModelConfig/backend` 插入 `sys.path` `[二次校正补漏]`
- `backend/opcrew_backend/routes/workflow_assistant_bridge.py:7-11` 将 repo root 插入 `sys.path`，再 import `WorkflowAssistant.backend.workflow_assistant`
- `WorkflowAssistant/backend/workflow_assistant/routes.py:14-20` 反向 import 主后端和 Koubo repository
- `backend/opcrew_backend/routes/media_model_config.py:7-12` 注入 ModelConfig backend path，并用 wildcard re-export

二次校正复核命令：

```bash
rg -n "sys\.path\.insert" backend/opcrew_backend/app.py backend/opcrew_backend/routes WorkflowAssistant/backend/workflow_assistant/routes.py ModelConfig/backend/opcrew_model_config
```

架构文档也记录了这种状态：

- `ARCHITECTURE.md:46-66` 说明 WorkflowAssistant 尚未迁移，且存在前后端 reverse import
- `ARCHITECTURE.md:125-135` 列出剩余 reverse dependencies

#### 影响

- 目录结构不能表达真实模块边界。
- `frontend`、`backend`、`WorkflowAssistant`、`ModelConfig` 不能独立构建和测试。
- 运行行为依赖当前 repo 相对路径和启动工作目录。
- 后续迁移到包管理、容器化、两节点部署或独立服务时，隐性依赖会集中爆发。

#### 建议

P0：先定义模块边界和 import 规则。

- 将 `WorkflowAssistant` 和 `ModelConfig` 要么正式迁入 `frontend/src/modules`、`backend/opcrew_backend`，要么作为 workspace package 明确发布和引用。
- 移除后端 `sys.path.insert`，改为标准 Python package/import。
- 移除 Vite 对 repo 根目录的宽泛 `fs.allow`，改为 package alias 或 monorepo workspace。
- 为跨模块 API 定义稳定接口，避免 UI 组件和 repository 互相直接穿透。

### 2. Koubo 服务层使用全局注入，依赖关系不可见

#### 现象

Koubo storyboard 后端把大量服务依赖注入到模块全局变量：

- 当前 `backend/opcrew_backend/koubo/koubo_storyboard/` 下 `globals().update` / `globals().get` / `module.__dict__.update` 合计 **40 处、36 个文件** `[二次校正]`
- `backend/opcrew_backend/koubo/koubo_storyboard/services.py:83-87` 使用 `module.__dict__.update(payload)` 同步服务全局状态
- `backend/opcrew_backend/koubo/koubo_storyboard/services.py:89-132` 构造 `SimpleNamespace`，再批量注册到多个 service module
- `backend/opcrew_backend/koubo/koubo_storyboard/asset_core_services.py:35-44` 使用 `globals().update(vars(ns))`
- `backend/opcrew_backend/koubo/koubo_storyboard/asset_search_services.py:145-147` 注册时更新 globals
- `backend/opcrew_backend/koubo/koubo_storyboard/asset_search_services.py:274-291` 运行时从 `globals().get("ctx")` 读取上下文
- `backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py:90-92` route 注册时 `globals().update(vars(deps))`
- 分布范围比原举例更广，还包括 `tts_routes.py`、`agent_chat_routes.py`、`hyperframe_template_routes.py`、`video_plan_routes.py`、`image_plan_routes.py`、`clean_image_routes.py` 等 route/service 文件 `[二次校正]`

二次校正复核命令：

```bash
rg -n "globals\(\)\.(update|get)|__dict__\.update" backend/opcrew_backend/koubo/koubo_storyboard
```

#### 影响

- 服务函数的依赖无法从函数签名看出。
- 测试需要按特定顺序注册全局状态，单元测试隔离困难。
- 多 app 实例、多 worker 或并发任务中，状态互相污染风险高。
- 局部 refactor 容易遗漏隐式全局变量。

#### 建议

P0：将全局注入改为显式 context/service 对象。

- 定义 `StoryboardServices` 或 `StoryboardContext`，在 app startup 创建一次。
- 路由通过 FastAPI dependency 或闭包持有 service 对象。
- service 函数通过参数或实例属性获取依赖，禁止 `globals().get()`。
- 对共享 locks/jobs/runtime state 做单独生命周期管理，明确线程安全边界。

### 3. 数据库 schema 和迁移机制分裂

#### 现象

当前数据库初始化和迁移同时存在三套机制：

- `backend/opcrew_backend/db/bootstrap.py:131-136` 启动时调用 `metadata.create_all(engine)`、`run_migrations(engine)` 和多个 `ensure_*_columns`
- `backend/opcrew_backend/db/bootstrap.py:13-119` 通过 information_schema 检查列，再执行 `ALTER TABLE`
- `backend/opcrew_backend/db/migrations.py:35-38` 自定义 `add_column_if_missing`
- `backend/opcrew_backend/db/migrations.py:228-257` 使用手写 migration 列表执行迁移
- `backend/opcrew_backend/db/schema.py:383-418`、`backend/opcrew_backend/db/schema.py:482-518` 任务表持续积累大量 nullable workflow/prompt 字段

#### 影响

- schema 的真实来源不唯一。
- 本地、测试、生产数据库可能因启动路径不同产生结构漂移。
- 无法可靠回滚或重放某个版本的 schema。
- 宽表继续增长后，字段语义会越来越依赖业务流程分支。

#### 建议

P0：统一迁移机制。

- 选用 Alembic 或保留一套自定义迁移系统，但不能同时用 `create_all`、migration、启动补丁三套机制表达 schema 演进。
- 将现有生产 schema 做 baseline migration。
- 后续字段变化只能通过 migration 进入。
- `ensure_*_columns` 作为一次性兼容逻辑迁出后删除。
- 对 workflow-specific 的可变字段，评估拆表或 JSONB schema version，而不是继续扩宽主任务表。

### 4. 后端 contract tests 历史失败已恢复，但尚未进入 CI

#### 验证结果

历史执行命令：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache backend/.venv/bin/python -m unittest discover -s backend/tests/contracts
```

历史结果（原始）：

- Ran 545 tests / 5 failures / 3 errors / 1 skipped

历史结果（2026-07-01 复审实测，情况恶化 `[复审]`）：

- Ran 550 tests
- 4 failures
- 14 errors（较原报告 3 → 14，错误数明显上升）
- 1 skipped
- 具体样例：`test_dance_mimic_backend_surface_contract` 期望 `ToolLibrary/DanceMimic_V1/test_fixtures/dance_solo_frontal_studio.mp4` 但实际只返回临时会话内的 `dance_reference_clip.mp4`；`test_koubo_storyboard_dialogue_asset_key_contract` 中 `srt_0004` 被意外索引进 index。

主要失败方向：

- `test_analysis_v1_framework_bridge_contract`：registry dependency token 未解析，包括 `04_01_free:tts_reference_speed`、`05_03:video_generation_plan_logic`、`05_04:image_generation_plan_json`、`05_05:video_generation_plan_logic`、`05_06:video_only_generation_plan_json`
- `test_analysis_v1_srt_rewrite_free_contract`：前端按钮契约与当前 UI 文案或结构不一致
- `test_analysis_v1_video_plan_executor_resilience_contract`：音频/视频复用路径与预期不一致
- `test_dance_mimic_backend_surface_contract`：Dance Mimic fixture asset 未按契约出现

二次校正（2026-07-04）当前复跑结果：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache backend/.venv/bin/python -m unittest discover -s backend/tests/contracts
```

- Ran 561 tests
- OK（skipped=1）

因此本项当前事实应改为「历史失败已修复，但 contract tests 仍未进入 CI 作为强制门禁」。

#### 影响

- 历史上 contract tests 曾经失败并恶化，说明回归风险是真实存在的。
- 当前本机复跑虽然已通过，但如果 CI 不运行这些测试，后续失败仍可能长期积压。
- 架构改动和功能改动需要可靠回归网，而不是依赖人工复跑。

#### 建议

P0：当前先把 contract tests 放进 CI，并保留失败 triage 记录。

- 对历史失败保留 triage 记录，确认哪些是契约更新、哪些是代码回归。
- 对新增失败要求同 PR 内处理，避免重新积压。
- CI 至少增加后端 contract tests、前端 build、基础 lint。

### 5. 大文件单体化严重

#### 现象

典型大文件：

- `backend/opcrew_backend/koubo/router.py`：5188 行
- `backend/opcrew_backend/koubo/rebuild_router.py`：5270 行
- `frontend/src/App.jsx`：4312 行
- `frontend/src/modules/koubo/OCRebuildModule.jsx`：3553 行
- `frontend/src/modules/koubo/KouboStoryBoardModule.jsx`：1408 行
- `frontend/src/modules/koubo/OpenClipModule.jsx`：1554 行

前端构建结果也显示单包偏大：

- `dist/assets/index-*.js` 约 1,337 KB
- `dist/assets/index-*.css` 约 535 KB
- Vite 输出 chunk size 警告

#### 影响

- 单文件承担过多路由、状态、UI、业务逻辑和 API 调用。
- 代码审查难以定位影响面。
- 单元测试无法覆盖小粒度逻辑。
- 前端首屏加载和缓存策略压力增加。

#### 建议

P1：按业务边界渐进拆分。

- 后端 router 拆成 routes、services、repositories、schemas、background jobs。
- 前端 App shell 只保留路由、导航和全局状态。
- Koubo 大模块拆为 workspace、task list、storyboard editor、asset panel、agent chat、settings 等组件。
- 使用动态 import/lazy route 拆前端 chunk。

### 6. 前端 API 层重复，TypeScript 覆盖不足

#### 现象

同类 API helper 在多个位置重复：

- `frontend/src/lib/api.js:1-23`
- `frontend/src/lib/api.ts:1-124`
- `frontend/src/modules/koubo/api.js:1-24`
- `frontend/src/modules/koubo/KouboTaskList/kouboTaskListApi.js:1-23`
- `frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js:2-50`
- `frontend/src/modules/koubo/AnalysisV1/analysisV1Api.js:3-31`
- `frontend/src/modules/koubo/OCRebuildModule.jsx:12-44`

TypeScript 配置开启了 `strict`，但主应用仍大量使用 `.jsx`：

- `frontend/tsconfig.json:1-20` 只 include `src`，未启用 `allowJs/checkJs`
- `frontend/src/main.tsx:2` 用 `@ts-ignore` 引入 `App.jsx`
- `frontend/src/main.jsx` 仍存在，但实际入口是 `main.tsx`

#### 影响

- 不同 API helper 的错误解析、credentials、base URL、response handling 会逐渐分叉。
- TypeScript build 通过不代表主应用逻辑被类型检查覆盖。
- 重构 API contract 时需要人工搜索多个副本。

#### 建议

P1：统一 API client。

- 保留一个 `frontend/src/lib/api.ts` 作为唯一 fetch wrapper。
- 业务模块只写 typed endpoint function，不重复 request helper。
- 为统一错误结构定义 `ApiError`。
- 分阶段将核心 JSX 模块迁移到 TSX，或至少启用 JS check 并消除 `@ts-ignore`。

### 7. 手动 cache-busting 已经成为流程债

#### 现象

源码和文档都依赖手动维护 `?v=`：

- `frontend/index.html:11` script 带版本 query
- `frontend/src/main.tsx:3` import `App.jsx?v=...`
- `frontend/src/App.jsx:2`、`frontend/src/App.jsx:5`、`frontend/src/App.jsx:6` 多处 import 带版本 query
- `ARCHITECTURE.md:74-98` 明确要求改动后 bump `?v=`
- `frontend/vite.config.ts:21-34` 关闭 HMR，并设置 no-store header

#### 影响

- 发布正确性依赖人工记忆。
- 忘记 bump 会造成用户看到旧代码。
- 代码变更和缓存策略耦合，增加审查噪音。

#### 建议

P1：修复 dev/proxy/cache 根因。

- 生产环境依赖 Vite hashed assets，不在源码 import 中手写 query version。
- 开发环境恢复 HMR 或建立明确的 no-cache dev server 策略。
- 移除手动 `?v=`，保留 CI 检查一段过渡期后删除。

### 8. Git 中追踪大量媒体二进制资产

#### 现象

当前 tracked 媒体/二进制资产按扩展名口径约 **487 个，总计约 298.1 MB** `[二次校正，原报告 480 个/288 MB 已过期]`。

复核口径：

```bash
git ls-files | rg -i '\.(wav|mp4|gif|png|jpe?g|webp|pdf|zip|mov|m4a|mp3|aac|bin)$'
```

扩展名分布：

- `wav`：206 个
- `png`：258 个
- `mp4`：12 个
- `gif`：8 个
- `jpg`：1 个
- `zip`：2 个

如果按 MIME 泛化口径统计 image/audio/video 及常见 application binary，当前约 **528 个**。本报告后续建议以扩展名口径跟踪，便于 CI/脚本稳定复现。

主要集中在：

- `ToolLibrary/Analysis_V1/VoiceCatalog/**`：206 个 WAV
- `ToolLibrary/Analysis_V1/Reference/05_02/*.mp4`
- `issues/` 与 `StoryBoardRegression/` 下的截图和回归产物

#### 影响

- clone、pull、checkout、CI 缓存都变慢。
- Git 历史持续膨胀，后续清理成本高。
- 媒体资产变更难以 code review。

#### 建议

P1：迁移资产存储。

- 小型固定 fixture 保留在 Git。
- 大型语音库、视频参考素材迁移到对象存储、artifact registry 或 Git LFS。
- repo 中保留 manifest、checksum、下载脚本和最小测试 fixture。

### 9. CI、lint、测试门禁不足

#### 现象

- `.github/workflows/openclip-bridge-guard.yml:1-28` 当前主要是 Koubo frontend cache guard
- 根 `package.json:9-14` 的 `test` 是占位失败命令
- `frontend/package.json:6-25` 有 build/e2e/test scripts，但没有统一 lint script
- 未发现 repo 根统一的 `pyproject.toml`、`pytest.ini`、`ruff.toml`、`mypy.ini`、ESLint/Prettier 配置
- `backend/tests/contracts/README.md:5-10` 仍以手动 unittest discover 方式说明 contract tests

#### 影响

- 质量问题主要靠人工审查发现。
- 大文件、重复 API、死代码、全局状态等问题难以及时收敛。
- contract tests 失败不会自动阻断合入。

#### 建议

P1：建立最低限度门禁。

- CI 增加 `npm --prefix frontend run build`
- CI 增加后端 contract tests
- 引入 Python ruff，先只启用低风险规则：unused import、undefined name、syntax、basic complexity
- 前端引入 ESLint，先覆盖 TS/TSX 和明显错误规则
- 将 “cache guard” 从唯一门禁降级为发布辅助检查

### 10. 配置、价格和 provider 元数据硬编码

#### 现象

- `frontend/src/App.jsx:19-83` 硬编码媒体价格表
- `ModelConfig/backend/opcrew_model_config/media_model_config.py:38-41` 硬编码 provider endpoint、app key、resource id 等默认值
- `ModelConfig/backend/opcrew_model_config/media_model_config.py:63-130` 硬编码模型、价格和能力摘要
- `ToolLibrary/Analysis_V1/__init__.py:3-5` 存在默认 workflow id 和默认 DB URL
- `backend/opcrew_backend/config.py:8-26` 存在默认 DB/frontend/backend URL

#### 影响

- provider 价格、模型能力、endpoint 属于高变动信息，代码内硬编码容易过期。
- 修改配置需要代码发布。
- 不同环境的默认值容易混淆。

#### 建议

P2：配置数据外部化。

- 价格、模型能力、provider endpoints 移入数据库或版本化配置文件。
- 默认值只用于本地开发，并在命名上明确 `DEV_DEFAULT_*`。
- 对展示给用户的价格信息标注来源和更新时间。

## 复审校正与新增发现（2026-07-01）

本节为当日并行复审新确认的问题，均带实测证据。二次校正后，原报告大部分问题方向仍成立；其中 contract tests 的当前状态已由「失败」变为「通过但未纳入 CI 门禁」，不能再按待修复测试处理。

### A. 错误被静默吞掉（后端，高）

- 后端共 **496 处 `except`**，其中 **152 处是宽泛的 `except:` / `except Exception:`**，**32 处直接 `pass` 吞掉**。
- `backend/opcrew_backend/koubo/koubo_storyboard/io_utils.py` 的 `read_json()` 对任何异常（JSONDecodeError / FileNotFoundError / OSError）一律返回 `{}`，调用方无法区分「文件不存在」「文件损坏」「解析失败」。
- 前端同样存在 `.catch(() => {})` 静默吞错（如 `AnalysisV1TTSBuilder.jsx:544`、`KouboComposerModal.jsx:225`、`DigitalHumanAgentPanel.jsx:245` 等 6+ 处）。
- 影响：网络失败、数据损坏、权限问题都表现为「空结果」，线上问题无可观测性、难定位。
- 建议（P1）：宽泛 except 至少 `log.exception` 保留上下文；`read_json` 区分「缺失」与「损坏」；前端 `.catch` 不得为空。

### B. 防御式类型判断样板泛滥（后端，中）

- 仅 `koubo_storyboard/` 目录内 `isinstance` 出现 **722 次**，大量形如 `x.get("k") if isinstance(x.get("k"), dict) else {}` 的链式判断（如 `video_plan_signature_services.py`、`asset_history_services.py`、`asset_digital_human_services.py`）。
- 影响：认知负担高、易错、重构困难，本质上是缺少 schema/dataclass 边界校验的代偿。
- 建议（P1）：入口处用 pydantic/dataclass 做一次结构校验，内部函数信任类型，删除内联 isinstance 样板。

### C. 单函数体巨型化（后端，中，`[复审量化]`）

- 除文件行数外，问题在于「单个函数」过大：`asset_routes.py:90` 的 `register_asset_routes()` 一个函数体约 **2786 行**（几乎整文件）；`router.py:332` 的 `build_openclip_router()` 约 4896 行。路由注册、业务逻辑、I/O、错误处理全部内联在一个闭包里，无法单测。
- 建议（P1）：将 route builder 拆为独立 handler 函数 + service 层，闭包只做装配。

### D. ToolLibrary 工具脚本重复与硬编码（中到高）

- `read_json` / `write_json` 在 **117 个** ToolLibrary 脚本中各自重复定义；`ArgumentParser`、`class Paths`、密钥加载、provider client 等样板在 100+ 脚本重复。
- **硬编码开发者绝对路径 `/Users/duheng/`** 的原报告清单不完整：Python 源文件口径是 **6 个**，但不全在 ToolLibrary `[二次校正]`：
  - `ToolLibrary/Analysis_V1/02_01_AudioASR.py`
  - `ToolLibrary/Analysis_V1/03_02_TTSBuilderQuick.py`
  - `ToolLibrary/Rebuild_V1/05_01_Shot_StoryboardReferencePromptRefresh.py`
  - `ToolLibrary/Rebuild_V1/PlanARealChain.py`
  - `docs/SessionDesign-R2/run_koubo_plan_case_screenshots.py`
  - `scripts/opencrew_postgres.py`
- 如果把 runnable JS/MJS 也算入脚本口径，还包括 `docs/SessionDesign-R2/screenshot_koubo_plan_actual_cases.cjs` 和 `StoryBoardRegression/script/storyboard-node/fixed_reorganize_task18_regression.mjs`。如果把 Markdown/runbook 也算入治理口径，命中文档更多，不能再表述为「6 处且都在 ToolLibrary」。
- 硬编码本地 DB 串 `opencrew:opencrew@127.0.0.1:5433` 散落在 `backend/opcrew_backend/config.py`、多处脚本及启动文档中（本地默认值，风险较低，但应统一为环境变量并从文档移除）。
- 工具脚本命名不一致：`Analysis/` 用小写 `01_video_metadata_extractor.py`，`Analysis_V1/` 用大写 `00_PrepareSessionVariables.py`，`Rebuild_V1/` 混用；V1 与非 V1 目录并存，难判断哪套在用。
- 建议（P2）：抽 `toollib/common`（io、paths、args、secrets、provider client）；`/Users/duheng/` 在源文件和 runnable 脚本中清零，文档/runbook 中只保留变量化示例；统一命名与 V1 归并。

### E. 前端状态与组件复杂度（中）

- 前端 `createSignal` 共 **811 处**、`createEffect` 158 处、`props.` 访问 1735 处；`App.jsx` 单文件 112 个 signal，`AnalysisV1TTSBuilder` 51 signal + 105 prop 引用。
- 手动 cache-bust（`?v=Date.now()` / `?v=2026...` / `?v=attempt.id`）散落 **85 处** fetch URL，远超原报告举例范围。
- `sharedAudioContext` 这类可变全局在 `OCRebuildTTSBuilder.jsx:3` 与 `AnalysisV1TTSBuilder.jsx:5` 各定义一份（重复单例，同会话可能互相干扰）。
- `main.jsx` 与 `main.tsx` 双入口并存，`main.tsx` 用 `@ts-ignore` + `?v=` 引入 `App.jsx`。
- 178 处 API 调用直接写在 `modules/koubo/**` 组件里，缺 service 层，难测。
- 建议（P1）：signal 密集组件下沉为 store/context；统一 fetch wrapper 承担 cache 策略；删重复入口与全局单例。

### F. 目录与文档卫生（中）

- 两份竞争性启动手册：`ACTUAL_STARTUP_AND_VERIFICATION.md`（742 行）与 `CORRECT_STARTUP_PLAYBOOK.md`（887 行），内容大量重叠且各自含硬编码凭据/路径——运营者不知以哪份为准。
- `docs/` 原报告“约 55 个文件”口径不清。二次校正当前复核：`docs/` 顶层 tracked 文件 **54 个**，当前工作区顶层文件 **57 个**（含未跟踪文件），递归 tracked 文件 **414 个**。因此后续不应再只写一个无口径的 docs 总数。
- `issues/` 曾 tracked **50 个**；其中绝大多数是截图/附件,但包含 1 份非截图需求文档 `issues/OC/Analysis_V1_06_01_VideoPlanComposer_工具需求整理.md`,不能按整目录纯截图处理 `[三次校正]`。
- `StoryBoardRegression/` 当前 tracked **52 个**；原报告“约 27 个测试产物”明显低估 `[二次校正]`。
- `issues/` 中截图/附件应外置或 ignore；非截图需求文档应迁入 tracked docs 后再 untrack 原目录。`StoryBoardRegression/` 需单独归档/去重评估,不能按二进制产物整目录外置 `[三次校正]`。
- `.gitignore` 有 stale 项 `ToolLibrary/Rebuild/`（目录已不存在）。
- 好的一面：`.DS_Store`、`test-results/`、`__pycache__`、`node_modules`、`.opencrew-e2e-auth.env`、`docs/apikeys.csv`、`scripts/deploy/00_config.env` 均已正确 ignore，未发现明文密钥被 tracked。
- 建议（P2）：两份 startup 合并为单一 runbook；`issues/`、`StoryBoardRegression/` 截图外置或 ignore；清理 docs 版本碰撞与 stale ignore 项。

## 验证记录

### 前端 build

执行命令：

```bash
npm --prefix frontend run build
```

结果：通过。

关键信息：

- Vite 成功构建 151 个 modules
- JS bundle 约 1,337 KB
- CSS bundle 约 535 KB
- Vite 输出 chunk size warning

### 后端 contract tests

执行命令：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache backend/.venv/bin/python -m unittest discover -s backend/tests/contracts
```

结果（2026-07-04 二次校正复跑）：通过。

摘要：

- Ran 561 tests
- OK
- 1 skipped

历史记录：2026-07-01 原报告/复审曾记录失败（545/550 tests，failure/error 数不一致），但该状态已不是当前事实。当前 P0 应改为：将 contract tests 加入 CI，防止已恢复的契约测试再次回退。

## 整改路线建议

### P0：先处理会持续放大风险的问题

1. 将当前已通过的后端 contract tests 加入 CI，历史失败保留 triage 记录。
2. 定义 repo 模块边界，禁止新增跨根目录 import 和 `sys.path` 注入。
3. 为 Koubo storyboard 建立显式 service/context，停止新增 `globals().update()`。
4. 统一数据库迁移机制，冻结 `ensure_*_columns` 扩散。
5. CI 加入前端 build 和后端 contract tests。

### P1：降低维护成本和发布风险

1. 拆分 `App.jsx`、Koubo 大模块、后端巨型 router。
2. 统一前端 API client 和错误处理。
3. 移除手动 `?v=` cache-busting。
4. 迁移大型媒体/二进制资产到 Git 外部存储，并用固定计数口径持续跟踪。
5. 引入 ruff/ESLint，先只启用低风险规则。

### P2：治理配置和长期演进问题

1. 将 provider 价格、模型能力、endpoint 等高变动数据外部化。
2. 清理重复入口文件和历史兼容路径。
3. 为 ToolLibrary workflow 定义 manifest schema 和依赖校验。
4. 将宽任务表逐步拆分为主任务表、attempt 表、artifact 表和 workflow-specific metadata。

## 建议的目标状态

理想状态下，OpenCrew repo 应满足以下结构约束：

- `frontend` 只通过 package alias 或 workspace package 引用共享 UI/SDK，不直接跨目录 import 任意源码。
- `backend` 只 import 已安装或同包内模块，不修改 `sys.path`。
- `WorkflowAssistant`、`ModelConfig` 要么是正式包，要么被迁入主应用目录。
- Koubo 服务依赖通过显式对象传递，测试可以直接构造 service/context。
- 数据库 schema 由一套迁移系统管理，启动过程不再临时修 schema。
- CI 至少覆盖 build、contract tests、基础 lint。
- 大型媒体资产不再进入普通 Git 历史。

## 附录：本次未深入展开的风险

本次评审聚焦架构、目录结构和代码质量，没有逐行审计所有 ToolLibrary 脚本的安全边界、性能热点和外部 provider 调用。建议后续单独做以下专题审查：

- ToolLibrary workflow registry 和依赖 token 完整性
- Provider 调用、计费记录和失败补偿
- 文件上传、workspace path、artifact 清理策略
- 两节点部署下的路径、缓存、DB migration 一致性
- 前端 E2E 对 Koubo 主流程的覆盖率
