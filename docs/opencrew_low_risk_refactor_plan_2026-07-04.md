# OpenCrew 低风险高收益重构规划

- 日期:2026-07-04(v4,在 v3 基础上修正 issues/OC 中非截图需求文档的处置:截图外置,需求文档迁入 tracked docs)
- 依据:`opencrew_repo_technical_debt_review_2026-07-01.md`(已二次校正)+ 当日实测复核
- 前置状态:Step 0 已完成(561 contract tests 全绿;`ci-gate.yml` 同时挂 `pull_request` 与 `push: main`;pre-push 经 `core.hooksPath=.githooks` 生效)
- 关系:Step 1(koubo_storyboard `globals()` → 显式 Context,方案 v5 已拍板)是主线 P0。**本规划只收录风险低、且不与 Step 1 改动半径冲突的任务**,为 Step 1 铺路或与其并行。

## 排期原则

1. **安全网优先**:每个任务落地前后跑本地 contract tests + frontend build;前端行为改动加跑 e2e。团队若继续 direct-to-main,则 **pre-push 是前置门禁、CI 是事后确认**;走 PR 路径时 CI(`pull_request` 触发)可作为合入前 merge gate。
2. **避开 Step 1 半径**:`backend/opcrew_backend/koubo/koubo_storyboard/` 下 28 个 services + 13 个 routes 即将大改,本期任务不进这些文件做"顺手清理"(唯一例外见 T1 的 per-file-ignores,那是配置不是代码)。
3. **单任务单 commit**:每项独立提交、独立可 revert,不打包混合改动。

## 任务清单(按性价比排序)

### T1:lint 门禁(ruff + ESLint 最低规则集)——收益最大

**定位(v2 修正)**:T1 是**通用 lint 基建**,不承诺覆盖 Step 1 迁移期的裸名漏改风险——那个风险由 Step 1 v5 自带的 AST 门禁负责(见下)。此前"F821 是 Step 1 的静态网"的说法与"豁免 koubo_storyboard 的 F821"互相抵消,系论证错误,予以撤回。

**内容**
- 后端:引入 ruff,仅启用 `F401`(unused import)、`F811`(重复定义)、`F821`(undefined name)、`E9`(语法错误)。机械修复存量 F401。
- `koubo_storyboard/` 因运行时 globals 注入会 F821 大面积误报 → `per-file-ignores` 豁免该目录的 F821。**豁免采用棘轮机制**:Phase S 每迁移完一个模块,就把该文件从豁免清单移出——F821 由此成为「已迁移模块不得回退」的守门员,而非迁移期的检查网。
- 前端:引入 ESLint flat config,仅启用 correctness 规则(`no-undef`、`no-dupe-keys`、`no-unreachable`、`no-unused-vars` warn)。
- `ci-gate.yml` 增加第三个 job(lint),与现有两个 job 并行。

**与 Step 1 的真实关系**
1. 迁移期的漏改检查网是 Step 1 的 `scripts/check_storyboard_context_migration.py`(AST 门禁)。**T1 必须交付该脚手架**(空清单即可):T1 交付 lint job 时把 AST 门禁挂进同一个 CI job,Step 1 Phase 0 只需填充清单,不再碰 CI。
2. F821 豁免棘轮为 Step 1 提供「迁移完成后的防回退」保障,这是 T1 对 Step 1 的实际贡献,发生在每个模块迁移**之后**,而非期间。

**风险**:近零。删 unused import 是机械操作,contract tests 兜底。
**验证**:contract tests + build 全绿;ruff/ESLint 通过;空清单 AST 门禁通过;CI 三 job 并行通过。

### T2:仓库卫生包(每项独立 commit)

| 子项 | 操作 | 依据(已实测) |
|---|---|---|
| 删 `frontend/src/main.jsx` | 直接删除 | 无任何引用,入口是 `main.tsx` |
| `api.ts`/`api.js` 二选一(v2 事实反转) | **先用 build/resolve 实证生效入口,再删败者**——不能按"删死代码"直接执行 | `vite.config.ts:16` 自定义 `extensions` 中 `.ts` 在 `.js` 之前,extension-less import 实际解析到 **`api.ts`(550 行,真实现)**;`api.js`(109 行)才是疑似 stale 副本。且 tsconfig include 全 `src`,`api.ts` 参与类型检查 |
| `.gitignore` 清理 + `issues/` 外置 | 删 stale 的 `ToolLibrary/Rebuild/`;`issues/` 中截图整体 ignore 并 `git rm --cached`(本地保留);`issues/OC/Analysis_V1_06_01_VideoPlanComposer_工具需求整理.md` 迁入 `docs/requirements/` 保留 tracked | `issues/` 不能按整目录“全是截图”处理:截图/问题附件外置,非截图需求文档必须先迁入 tracked 文档目录;**`StoryBoardRegression/` 不在此项处理**(见 T6,它是 52 个全文本的回归测试包,不是二进制产物) |
| tsconfig stale exclude | 删 `"exclude": ["src/App.tsx"]`(文件不存在) | v2 复核新发现 |
| `/Users/duheng/` 清零 | 6 个 py + 2 个 cjs/mjs 改环境变量或相对路径 | 清单见技术债报告 §D(二次校正版) |
| `sharedAudioContext` 合一 | 抽 `frontend/src/modules/koubo/shared/audioContext.js`,两处引用 | `OCRebuildTTSBuilder.jsx:3` 与 `AnalysisV1TTSBuilder.jsx:5` 各持一份;浏览器 AudioContext 有实例上限,合一是修复不是风险 |
| 双启动手册合并 | `ACTUAL_STARTUP_AND_VERIFICATION.md` + `CORRECT_STARTUP_PLAYBOOK.md` → 单一 runbook,旧文件顶部加 deprecation 指针 | 纯文档,零代码风险 |

**风险**:近零;每项可独立验证、独立回滚。

### T3:前端观测性最小包

- 7 处空 `.catch(() => {})` 接入现有的 `debugAdapter.emitDebugError`(基础设施已存在,`App.jsx` 已在用)。
- **不动**后端 `io_utils.read_json` 和 152 处宽泛 except:它们大多在 Step 1 半径内,吞错治理并入 Step 1 的逐模块迁移(改函数签名时顺带补日志),避免双重 churn。

**风险**:低。`.catch` 从空变为记录日志,不改变控制流。
**验证**:build + e2e;手动触发一次失败路径看 DebugConsole 有输出。

### T4:前端 API helper 收敛(渐进,与 Step 1 并行不冲突)

1. 第一步(本期):处理 `api.js`/`api.ts` 同名碰撞(见 T2),确立 `frontend/src/lib/api` 为唯一 fetch wrapper;定冻结规则——**新代码禁止再写独立 request helper**。
2. 第二步(渐进):存量 5 份模块级 helper(`koubo/api.js`、`kouboTaskListApi.js`、`kouboStoryboardApi.js`、`analysisV1Api.js`、`OCRebuildModule.jsx` 内联)逐模块迁移,一模块一 commit,每次迁移跑 e2e。

**风险**:第一步近零;第二步中低(helper 每份约 25-50 行,行为差异集中在错误解析,迁移时逐一比对)。
**收益**:错误处理统一;为将来移除 `?v=` 手动 cache-bust 提供统一收口点。

### T5:后端 `sys.path` 注入收敛(第一步:集中化,零行为变更)

1. 本期只做**集中化**:4 处 `sys.path.insert`(`app.py`、`routes/asr_config.py`、`routes/media_model_config.py`、`routes/workflow_assistant_bridge.py`)收敛到单一 bootstrap 模块(如 `opcrew_backend/_paths.py`),各处改为 import 它。行为完全不变,边界债从 4 处散落变为 1 处可见。
2. 第二步(单独决策,不在本期):ModelConfig/WorkflowAssistant 做成 editable package(`pip install -e`),牵涉 CI 与部署脚本,需单独出方案。

**风险**:第一步近零(纯搬移);contract tests 对 import 路径覆盖充分。

### T6:媒体资产——只止血,不搬迁

- **止血(本期)**:`issues/` 截图附件 ignore(并入 T2),但非截图需求文档先迁入 `docs/requirements/`;CI 加轻量 guard——PR/push 中新增 >1MB 的二进制文件报警。
- **`StoryBoardRegression/` 单列(v2 修正)**:实测 52 个 tracked 文件全部是文本(31 md + 19 py + 2 mjs,含回归 runbook、测试脚本、contract test 副本),**是回归测试包而非二进制产物,禁止整目录 `git rm --cached`**。正确处理是另立「归档/去重评估」任务:确认其中 contract test 副本与 `backend/tests/contracts` 的关系、runbook 与双启动手册的关系,再决定归位、合并或归档。
- **不搬迁(明确排除)**:`VoiceCatalog/` 206 个 WAV 被 contract tests(`test_analysis_v1_tts_quick_*`)和运行时(`router.py`、`registry_normalizer.py`、`tts_quick_adv/`)直接引用,搬迁需要 manifest + 下载脚本 + CI fixture 策略,**不属于低风险**,另立任务。
- **不做历史重写**:git history 清理收益低、风险高,永不排期或等仓库迁移时一并做。

## 明确不做/推迟(及原因)

| 项 | 原因 |
|---|---|
| 移除 `?v=` 手动 cache-bust(93 处) | 根因疑似生产环境用 vite dev server 直服(`hmr:false` + no-store header 佐证)。真正修复是部署方式改为构建产物 + hashed assets,属部署变更,风险中高。先立调查任务确认当年缓存事故根因。 |
| schema 迁移三机制统一 | P0 但属 DB 变更,回滚成本高,建议作为 Step 2 单独出方案(baseline migration + 冻结 `ensure_*_columns`)。 |
| 巨型文件/函数拆分(`router.py` 5291 行等) | 与 Step 1 同一批文件,Step 1 本身就会重构 services/routes 结构,现在拆是双重 churn。Step 1 落地后做。 |
| `isinstance` 729 处治理 | 同上,依赖 Step 1 的显式 Context 边界,之后在入口加 pydantic 校验才有意义。 |
| ToolLibrary common 抽取(117 份重复) | 收益大但涉及 117 个脚本,等 T1 的 lint 网建好后机械化推进,列 P2。 |

## 排期与依赖

```
第 1 周   T1 lint 门禁 ──────┐(通用 lint + Step 1 AST 门禁脚手架)
          T2 仓库卫生包       │(与 T1 无冲突,可并行)
第 2 周   T3 前端观测性       │
          T4 第一步 api 收口   │
          T5 第一步 path 集中  │
之后      Step 1 开工(v5 方案)◄┘
          T4 第二步逐模块迁移(前端,与 Step 1 后端改动并行不冲突)
Step 1 后 巨文件拆分 / 后端吞错治理 / isinstance 治理(Step 3)
          schema 迁移统一(Step 2,单独方案)
```

## 每任务通用验收

1. 本地 `PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache backend/.venv/bin/python -m unittest discover -s backend/tests/contracts` 全绿(561/OK)——固定用 venv 解释器,避免本机 Python 环境差异。
2. `npm --prefix frontend run build` 通过。
3. 前端行为改动加跑 e2e。
4. 单独 commit,CI Gate 红灯即 revert 当条 commit。
