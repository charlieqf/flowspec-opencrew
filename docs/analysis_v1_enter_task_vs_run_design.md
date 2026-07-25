# Analysis V1「进入任务」与「开始运行」解耦实施方案

## 状态

本文件是 implementation-ready 设计。它定义前端改造点、交互合同、边界处理、测试断言和手工 smoke。目标是让实现者可以直接按本文改代码，不再临场决定交互边界。

**不涉及后端运行接口 / 业务逻辑**；但需**同步更新后端 contract test**（`test_analysis_v1_task_process_indicator_mvp_contract.py` 的源码文案断言，详见「需同步更新的现有 contract test」一节），否则 CI 会红。

## 背景

「视频分析（口播）」(Analysis V1) 的「运行至 storyboard」流程当前把「进入任务视图」和「发起运行」绑死在同一个按钮上。

涉及文件：`OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx`

现有结构：

- 主页面 Step 1 卡片头部动作区（`717-733`）：
  - `最近运行：{状态}` 按钮（`719`）：仅当 `runProgress()` 存在时显示，点击打开运行进度弹窗。
  - `运行...` 按钮（`727`）→ `openRunModelDialog()` → 打开「运行设置」弹窗。
- 「运行设置」弹窗（`865-920`，标题在 `870`）：模型/模式/步骤/ASR/TTS/StoryBoard 等配置项；底部按钮（`914-918`）：`取消` / `保存模型` / `开始运行`（`917`，调用 `runAnalysis()`）。
- 「运行进度」弹窗（`922+`）：显示 Attempt、步骤、命令栏。渲染条件为 `<Show when={runProgressOpen() && runProgress()}>`。本文称之为 **task 弹窗**。

## 问题根因

`runAnalysis()`（`546-569`）一次做了三件事：

1. `saveConfig`
2. 调 `runToStoryBoard`（真正发起运行）
3. `setRunProgressOpen(true)`

因此进入 task 视图的唯一路径就是点「开始运行」，每次都会真的发起运行。用户的真实诉求是：有时只想打开 task 弹窗查看，不想触发运行。

`runProgress()` 只有在 task **运行过至少一次**时才会被 `restoreLatestRun`（`479-495`）填充。对**从未运行过**的 task，`runProgress()` 为 `null`，task 弹窗当前依赖它，无法渲染。

## 目标

1. 把「进入任务视图」与「发起运行」解耦。
2. 「运行设置」弹窗右下角由 `开始运行` 改为 `进入任务`，只打开 task 弹窗、不发起运行。
3. `开始运行` 的按钮和功能移到 task 弹窗顶部命令栏。
4. 从未运行过的 task 也能「进入任务」，并在顶部发起首次运行。

## 非目标

- 不改后端，不改 `runToStoryBoard` / `saveConfig` 接口。
- 不改运行设置弹窗里的配置项本身。
- 不引入新的任务状态机或新的弹窗。

## 已确认决策

1. 「task 弹窗」= 现有「运行进度」弹窗（`runProgressOpen` + `runProgress`），复用，不新建弹窗。
2. 从未运行过的 task：点「进入任务」照常进入，顶部可「开始运行」。
3. 点「进入任务」时：**先保存当前运行设置配置，再进入**。

## 前端实施合同

文件：`OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx`

> **定位说明**：该文件在并行开发中，行号会漂移（例如本文写的 `727 运行...` 当前已是 `762`，`940 重新运行` 当前是 `982`）。**以 class 名 / 按钮文案 / 函数名为准定位，行号仅作参考。** 关键锚点：工具栏运行按钮 `class="analysis-v1-run-main-button"`；运行设置弹窗 `class="openclip-run-model-actions"`（底部按钮区）；进度弹窗顶部命令栏 `class="analysis-v1-run-commandbar"`；进度弹窗底部 `class="analysis-v1-run-progress-actions"`；step 行菜单 `class="analysis-v1-step-menu"`。

### 0. 前置改造（必须先做）：task 切换时清理运行状态

本方案空态前提依赖「从未运行过的 task 的 `runProgress()` 为 `null`」。但当前 `loadTask()`（`323-356`）调 `restoreLatestRun()`（`350`）前不清空任何运行状态，而 `restoreLatestRun()`（`479-495`）在 `latest_attempt_id` 缺失（`482`）或 attempt_family 非 `analysis_v1_tool_run`（`485`）时直接 `return`，**不清理旧状态**。

结果：从「有运行的 task」切到「从未运行的 task」后，`runProgress()` 仍是旧 task 的 attempt，空态假设失效；同时残留 `selectedRunStepId`、`stepQuickWatch`、`stepMenuOpenId` 和仍在运行的轮询 timer（切换 task 不调用 `clearRunProgressTimer`）。

清理**必须限定在「切换到不同 task」时做，不能在同 task 刷新时做**。原因：`pollRunProgress()` 检测到 terminal 状态后会 `await loadTask(taskId)` 刷新**同一个** task（`505`）；若无条件 `setRunProgressOpen(false)`，运行刚结束弹窗就会被关，而 `restoreLatestRun()` 只对 active 状态重开（`488-489`），terminal 不重开 → 用户运行完弹窗消失（回归）。

利用 `task = createMemo(() => detail()?.task)`（`175`）在 `loadTask` 内 `setDetail`（`330`）之前仍指向上一个 task，可用 `taskId !== task()?.id` 区分：

```js
async function loadTask(taskId) {
  if (!taskId) return;
  const switchingTask = taskId !== task()?.id;   // 同 task 刷新时为 false，不动弹窗/运行状态
  if (switchingTask) {
    clearRunProgressTimer();
    setRunProgress(null);
    setRunPlan(null);              // run-scoped，否则旧 task 的计划残留到新 task
    setSelectedRunStepId("");
    setStepQuickWatch(null);
    setStepMenuOpenId("");
    setRunCommandBusy("");         // run-scoped 命令忙标志，避免残留禁用态
    setRunProgressOpen(false);
  }
  beginLoading();                  // 计数式 loading（第 0b 节），不是 setLoading(true)
  setError("");
  // ... 原有逻辑放进 try；身份守卫见 0b；finally 里 endLoading()（无条件）
}
```

要点：

- 跨 task 才清空，再 `restoreLatestRun`。这样 `restoreLatestRun` 的两个早退分支天然保持「无有效 tool-run attempt → `runProgress()` 为 null」，空态前提成立。
- **同 task 刷新（如 poll terminal 后的 `loadTask`）不清空、不关弹窗**，终态结果继续展示。
- `clearRunProgressTimer()` 防止切走后旧 task 的轮询继续写 `runProgress`（但挡不住已在途的请求，见下一节身份守卫）。
- 也可改为在 `selectTask()` 的跨 task 分支里清理，效果等价；关键是「同 task 刷新不清」。

### 0b. 前置改造（必须先做）：poll / restore 写入前的 task 身份守卫

`clearRunProgressTimer()` 只能取消未触发的 timer。若 `pollRunProgress(taskA)` 的请求已发出（`500` 处 await 中），切到 taskB 后它仍会返回并 `setRunProgress(next)`（`501`）污染 taskB 状态。

在 `pollRunProgress` 和 `restoreLatestRun` 写入前加身份守卫，taskId 与当前选中 task 不一致就丢弃结果：

```js
// pollRunProgress：await 拿到 next 之后、setRunProgress 之前
if (taskId !== selectedTaskId()) return;   // 已切走，丢弃这次在途结果
setRunProgress(next);
```

用 `selectedTaskId()`（`selectTask` 在 `360` 同步更新，早于 `task()`）作为身份基准。`restoreLatestRun` 同理：`setRunProgress(latest)` 前确认 `taskId === selectedTaskId()`。

**`loadTask` 自身的写入也要守卫**。`loadTask` 在 `await taskDetail`（`328`）后直接 `setDetail`/`setDraft` 及一批 workspace/step 写入（`329-349`）；快速切 A→B 时两个 `loadTask` 并发（`selectTask` 不 await），A 的响应若后返回会把 `detail` 回写成 A，`task()` 与 `selectedTaskId()` 错位，连累后续 `enterTask()` / `runAnalysis()` 操作错 task。在 `loadTask` 每个 `await` 之后、批量 state 写入之前校验身份，不一致直接丢弃：

```js
const nextDetail = await analysisV1Api.taskDetail(taskId);
if (taskId !== selectedTaskId()) return;     // 已切走，丢弃这次在途结果
setDetail(nextDetail);
// ... 同理，Promise.all 的 workspace 读取 await 之后、setDialogueItems 等写入之前再校验一次
```

这是既有竞态，但新流程对 `task()` 正确性依赖更强，必须一并修。

**通用规则：所有「await 后写 UI 状态」的 task-scoped 函数都要守卫。** 不止 poll/restore/loadTask。统一做法：函数入口 `const taskId = task().id`（或参数传入），每个 `await` 之后、写 state 之前校验 `taskId === selectedTaskId()`，并用捕获的 `taskId` 调用下游 API（而非再读 `task().id`）。至少覆盖：

| 函数 | 危险写入 | 守卫要点 |
| --- | --- | --- |
| `loadTask` | `setDetail` / `setDraft` / workspace 写入（`329-349`） | 见上 |
| `pollRunProgress` / `restoreLatestRun` | `setRunProgress`（`501` / `486`） | 见上 |
| `runAnalysis`（`547`） | `saveConfig(task().id)` / `runToStoryBoard(task().id)` / `setRunProgress(started)` / `scheduleRunProgressPoll(task().id, …)` | 入口捕获 `const taskId = task().id`；`saveConfig`/`runToStoryBoard`/`scheduleRunProgressPoll` 全用 `taskId`；每个 `await` 后 `if (taskId !== selectedTaskId()) return`，避免把 A 的 `started` 写进 B、或用错 task 调度轮询 |
| `enterTask`（新增） | `saveConfig` 后 `setRunProgressOpen(true)` | 入口捕获 `taskId`；`saveConfig(taskId)`；开弹窗前校验 `taskId === selectedTaskId()` |
| `loadRunPlan`（`303`）/ `openRunModelDialog`（`311`） | `setRunPlan` + `applyRunPlanDefaults`（写 draft 步骤）/ `setRunModelDialogOpen(true)` | `loadRunPlan` await 后、`setRunPlan` 前校验 `taskId === selectedTaskId()`；`openRunModelDialog` 入口捕获 `taskId`，开弹窗前再校验一次 |
| 进度弹窗命令：`stopRunProgress`（`588`）/ `resumeRunProgress`（`602`）/ `cancelPausePoint`（`616`）/ `setPauseBeforeStep`（`629`）/ `refreshRunProgress`（`583`） | await 后 `setRunProgress(next)`（`593`/`607`/`621`/`635`） | 入口捕获 `taskId`；`setRunProgress` 前校验 `taskId === selectedTaskId()` |
| `loadStepDetail`（`643`） | `setStepQuickWatch` / `setSelectedRunStepId` | 入口捕获 `taskId`；await 后写入前校验 `taskId === selectedTaskId()` |
| `saveRunModel`（`486`）/ `generateFinalPrompt`（`464`）及其他 await `saveConfig`/generate 后写 `setDetail`/`setDraft` 的函数 | `setDetail(res)` / `setDraft`（`493-494` / `476`） | 入口捕获 `taskId`；`saveConfig(taskId)`；await 后、`setDetail`/`setDraft` 前校验 `taskId === selectedTaskId()` |

> 这一类「await 后写 `setDetail`/`setDraft`」的函数不止上面两个（还有 `uploadTargetVideo` 等）。实现时统一用一个 `withTaskGuard(taskId, () => { ...写入... })` 包装写入段，而不是逐函数手写 `if`，以免漏。

**两类状态用两种守卫，不能混用：**

1. **task-scoped 数据**（`detail` / `draft` / `runProgress` / `runPlan` / `stepQuickWatch` / `selectedRunStepId` 等）：用 `taskId === selectedTaskId()` 守卫，stale 直接丢弃。**`catch` 里的 `setError(...)` 也属此类**——错误是某次操作的消息，必须 `if (taskId === selectedTaskId()) setError(...)`，否则 A 的失败会写到已切走的 B 页面。但 `finally` 的 `endBusy()`/`endLoading()` 仍**无条件**执行（属第 2 类）。
2. **布尔型全局标志**（`busy` / `loading` / `uploadingVideo`）：**禁止用 taskId 守卫**。`finally` 若因 `taskId !== selectedTaskId()` 跳过 `setBusy(false)`，会没人释放、标志永真卡死。必须用 **in-flight 计数**：

```js
// 每个标志一个计数器，finally 无条件释放自己那一次
let busyInFlight = 0;
function beginBusy() { busyInFlight += 1; setBusy(true); }
function endBusy()   { busyInFlight = Math.max(0, busyInFlight - 1); setBusy(busyInFlight > 0); }
// 用法：try 前 beginBusy()，finally 里 endBusy()（永远执行，不按 taskId 跳过）
```

3. **语义型命令标志 `runCommandBusy`**：它**不是 boolean**，而是「当前在途命令」字符串（`"stop"` / `"resume"` / `"cancelPause"` / `pause:${stepId}`），UI 按**具体值**判某个命令按钮的 disable 和文案（`978-980` / `1022`）。**不能计数成 boolean**。用「当前命令值 + token」释放：发起命令时记一个自增 token 并 `setRunCommandBusy(cmd)`；`finally` 里**只在自己的 token 仍是最新时**才 `setRunCommandBusy("")`，否则保留（避免覆盖后发命令的状态）。跨 task 清理时直接 `setRunCommandBusy("")`（见第 0 节）。

要点：

- 布尔标志 `endXxx()` **无条件执行**，不会卡死，也不会在仍有在途操作时误清（计数 > 0 时保持 true）；每个标志各自一个计数器，不共用。
- `runCommandBusy` 用 token 而非计数，因为要保留「哪个命令在跑」的语义。
- 数据写入仍走第 1 类的 `taskId` 守卫；三者职责不同，分开实现。

### 1. 新增 `enterTask()`

放在 `runAnalysis` 附近。等价于 `runAnalysis` 去掉「发起运行」那一步——只保存配置 + 打开 task 弹窗：

**示例已按第 0 / 0b 节的守卫规则写**（捕获 `taskId` + payload 快照、await 后校验身份、busy 用计数）：

```js
async function enterTask() {
  const t = task();
  if (!t) return;
  const taskId = t.id;
  const payload = normalizePromptBundle({ ...t, ...draft() }); // 入口快照，避免 await 后 task()/draft() 已变
  beginBusy();                                                 // 计数式 busy（第 0b 节），不是 setBusy(true)
  setError("");
  try {
    await analysisV1Api.saveConfig(taskId, payload);          // 用捕获的 taskId，不内联 task().id
    if (taskId !== selectedTaskId()) return;                  // 已切走 → 丢弃后续 UI 写入
    setRunModelDialogOpen(false);
    setRunProgressOpen(true);                                  // 进入 task 弹窗，不调 runToStoryBoard / 不启动轮询
  } catch (exc) {
    if (taskId === selectedTaskId()) setError(exc instanceof Error ? exc.message : String(exc)); // error 是 task-scoped，守卫
  } finally {
    endBusy();                                                 // 无条件释放（第 0b 节），不按 taskId 跳过
  }
}
```

要点：

- 必须保存配置（与「开始运行」走同一份 `normalizePromptBundle({ ...t, ...draft() })`），保证后续顶部「开始运行」用的是已保存配置。
- `taskId` / `payload` 入口快照；`saveConfig(taskId)`；await 后写 UI（含 `catch` 的 `setError`）前校验 `taskId === selectedTaskId()`；busy 用 `beginBusy/endBusy` 计数（finally 无条件）——都遵循第 0b 节，**实现者照抄即合规**。
- 不调用 `runToStoryBoard`，不调用 `scheduleRunProgressPoll`。
- 若已有活跃运行，进入后顶部仍照常显示进度（`runProgress()` 已由 `restoreLatestRun` 填充）。
- 其余 task-scoped 函数（`runAnalysis` / `saveRunModel` / `loadTask` …）按同一模板改造。

### 2. 「运行设置」弹窗底部按钮（`914-918`）

把 `917` 的「开始运行」替换为「进入任务」：

```jsx
<button
  class="openclip-model-confirm"
  type="button"
  disabled={!draft().run_model_provider || !draft().run_model_id || busy()}
  onClick={() => void enterTask()}
>{busy() ? "进入中..." : "进入任务"}</button>
```

要点：

- 去掉原来的 `|| Boolean(activeRunProgress())` 禁用条件。注意这只是**防御性清理**，不是为了「运行中也能进入」：进入「运行设置」弹窗的唯一入口是工具栏 `运行...`（`727`），它本身在 `activeRunProgress()` 时已禁用，所以运行中根本到不了「进入任务」按钮。**运行中查看进度的入口是「最近运行」按钮（`719`），不是本弹窗。** 若未来确实要支持「运行中也能从设置进入任务」，必须同步放开工具栏 `运行...` 的禁用条件，那是另一个范围。
- `取消`（`915`）保留。
- `保存模型`（`916`）保留；它与「进入任务」的保存有重叠，可保留作为「保存但不离开」入口，也可后续移除（不在本版强制处理）。

### 3. task 弹窗渲染条件放宽（`922`）

```jsx
<Show when={runProgressOpen() && task()}>   {/* 原: runProgressOpen() && runProgress() */}
```

这样从未运行过的 task（`runProgress()` 为 `null`）也能打开。

### 4. task 弹窗顶部命令栏新增「开始运行」（`930-937` 区间）

建议放在命令栏最前，作为主操作。直接复用现成的 `runAnalysis()`（它已包含 `saveConfig` + `runToStoryBoard` + `setRunProgressOpen(true)` + 轮询）。按方案 B，**始终渲染**（对从未运行 / 已运行 task 都可用），仅运行中等条件 `disabled`：

```jsx
<button
  class="primary analysis-v1-run-start"
  type="button"
  disabled={!draft().run_model_provider || !draft().run_model_id || busy() || Boolean(activeRunProgress())}
  onClick={() => void runAnalysis()}
>{busy() ? "启动中..." : "开始运行"}</button>
```

同时**删除硬编码的「重新运行」按钮（`940`）**、把「从设置重新运行」（`1049`）改名为「运行设置」/「调整设置」，详见下文「运行类按钮去重」。

要点：

- 有活跃运行时禁用（不能在运行中重复发起）。
- **必须处理无 attempt 时的 rerun 语义**（否则会发非法 rerun 请求）。`RUN_MODE_OPTIONS`（`48-55`）含 `rerun_failed` / `rerun_all`，运行模式 select（`876`）不过滤，用户可对从未运行的 task 选 rerun；`buildRunPayload()`（`528/541`）此时会取 `runProgress()?.attempt_id`——清空后为 `undefined`（无源 rerun），未清空时甚至是别的 task 的 attempt（跨 task rerun）。
  - **`runAnalysis` 守卫必须做（不可省）**：`runMode` 是独立 signal（`155`），顶部「开始运行」直接调 `runAnalysis()`，**不经过 select 的 onChange**，所以 select 过滤改不了已经设成 rerun 的 `runMode()`。在 `runAnalysis` 里：`mode.startsWith("rerun")` 且 `previous_attempt_id` 缺失时，`setError("没有可重跑的历史运行，请改用全量运行")` 并中止，不发请求。
  - **以下为补充优化（非必须）**：设置弹窗 select 在 `!runProgress()?.attempt_id` 时不渲染 `rerun_*` 选项；切 task 时把 `runMode` 重置为 `run_all`。这两条改善体验，但不能替代上面的 `runAnalysis` 守卫。
- 满足 `runAnalysis` 守卫后，`runAnalysis()` 主流程无需改动。

**顶部「开始运行」的 mode 语义必须钉死**。`runAnalysis()` 用当前 `runMode()`（`549` → `buildRunPayload`），用户在运行设置里选过 `rerun_*` 时，顶部按钮会发 rerun，而非「全新运行」。本方案的取法：

- **顶部按钮沿用设置里配置的 mode**（推荐）。整个「配置 → 进入任务 → 开始运行」流程的意义就是带着配置去跑，强制 `run_all` 会丢掉用户选的范围/单步等模式。因此按钮语义是「**按当前运行设置发起运行**」，文案可保留「开始运行」，但**描述/文档不得称其为「全新运行」**；无 attempt 的非法 rerun 由上面的 `runAnalysis` 守卫兜住。
- 备选：顶部按钮恒为 `runAnalysis({ mode: "run_all" })`，语义固定为全量新跑，但会忽略设置里的 mode 选择。仅当产品确认「顶部按钮永远全量」时才用。

### 4b. rerun 守卫的放置（实现约束）

守卫必须放在 `runAnalysis()` 内 `buildRunPayload()`（`549`）之后、`saveConfig`（`559`）/`runToStoryBoard` 之前，并基于 `runPayload` 解析出的**有效 `mode` 和 `previous_attempt_id`** 判断（不是只看 `runMode()` 字符串）：

```js
const runPayload = buildRunPayload(overrides);
if (String(runPayload.mode || "").startsWith("rerun") && !runPayload.previous_attempt_id) {
  setError("没有可重跑的历史运行，请改用全量运行");
  return;                       // 必须早于 saveConfig / runToStoryBoard
}
```

这样即使前端 mode 重置/select 过滤被绕过，也不会先保存配置或先发请求。

### 5. 弹窗头部用 `task()` 兜底（`928`）

当前头部用 `runProgress()?.task_id` / `runProgress()?.session_id`，空态会显示 `-`。改为优先 `task()`：

```jsx
<p>
  Task #{task()?.id || "-"} / Session #{task()?.session_id || "-"}
  <Show when={runProgress()}>
    {" "}/ Attempt #{runProgress()?.attempt_id || "-"} / {runProgress()?.attempt_family || runProgress()?.attempt?.attempt_family || "-"}
  </Show>
</p>
```

### 6. 空态步骤预览（建议做，提升体验）

`runProgress()` 为空时，body 的步骤列表当前是 `runProgress()?.steps || []`（`955`），会渲染成空列表。建议在空态下用 `runPlanSteps()`（`211` = `runPlan()?.steps || runProgress()?.steps || []`）渲染一份「计划步骤」只读预览，告知用户「尚未运行，点顶部开始运行」。

**必须用独立空态分支，不能复用现有步骤行组件**：现有步骤行（`960`）的 `onClick=loadStepDetail` 和「操作」菜单（`968-978`，含 `runStepAction` / `setPauseBeforeStep` / 重跑）都依赖真实 attempt。空态分支只渲染 step 的 `name` / `id`，**不挂操作菜单、不绑 onClick、不触发 quick-watch/log 请求**。

```jsx
<Show when={runProgress()} fallback={
  <div class="analysis-v1-run-step-list">
    <For each={runPlanSteps()}>{(step) => (
      <div class="analysis-v1-run-step is-idle">
        <div class="analysis-v1-run-step-main"><strong>{step.name || step.id}</strong></div>
        <span class="analysis-v1-run-step-status tag-idle">未运行</span>
      </div>
    )}</For>
  </div>
}>
  {/* 原有依赖 runProgress() 的完整步骤列表 + 操作菜单 */}
</Show>
```

注：`loadStepDetail`（`647`）本身有 `!runProgress()?.attempt_id → return` 守卫，不会误发请求；上面要求只读分支是为了避免渲染出无效的「操作」入口。`runPlan` 在 `openRunModelDialog` → `enterTask` 链路中已加载，数据现成。若本版不做预览，至少在空态显示一行「尚未运行」提示，不要只留空白。

### 7. 背景点击关闭放宽（`923`）

当前背景点击关闭条件：

```jsx
onClick={() => isTerminalRunStatus(runProgress()?.status) && setRunProgressOpen(false)}
```

空态下 `runProgress()` 为 `null`，`isTerminalRunStatus(undefined)` 为 `false`，点背景关不掉。放宽为：

```jsx
onClick={() => (!runProgress() || isTerminalRunStatus(runProgress()?.status)) && setRunProgressOpen(false)}
```

注意：头部 X 按钮（`937`）一直能关，本项非阻塞，但建议一并处理以保持一致。

## 运行类按钮去重（已定：方案 B —— 顶部单一「开始运行」）

目标：task 弹窗**顶部命令栏**里**任何时刻只有一个「发起运行」主按钮**，且它永远按设置跑。

> 范围限定：本次去重只针对**命令栏**的整体运行按钮。step 行菜单里的 per-step 能力（「运行至此步 / 从此步开始运行 / 单独运行此步 / 重跑此步及后续」，`976` 附近）是不同粒度的功能，**保持不动，不要删**。

关键约束：现有「重新运行」（`940`）硬编码 `runAnalysis({ mode: "rerun_all", previous_attempt_id })`，**忽略**用户在设置里选的 `run_range` / `run_from_step` / `run_only_step`。而顶部「开始运行」调 `runAnalysis()` 沿用配置 mode，是**唯一能按设置跑的入口**。因此不能「有 attempt 就隐藏开始运行」——否则已运行 task 走「从设置重新运行 → 选范围 → 进入任务」后无法执行配置的运行。

实现规则：

| 按钮 | 位置 | 处理 |
| --- | --- | --- |
| 开始运行 | 顶部命令栏 | **始终渲染**，`onClick = runAnalysis()`（沿用配置 mode）；仅 `busy()` / 未选模型 / `activeRunProgress()` 时 `disabled`。对从未运行 / 已运行 task 都可用 |
| 重新运行（`940`，硬编码 `rerun_all`） | 顶部命令栏 | **删除**。它忽略配置、与「开始运行」重复，是「两个跑按钮」的根源。需要全量重跑：设置里选「全量重跑」→ 进入任务 → 开始运行 |
| 从设置重新运行（`1049`，`openRunModelDialog`） | **底部动作区**（`analysis-v1-run-progress-actions`，`1047`） | 保留（本身不发起运行，只回设置改配置）；建议改名「运行设置」/「调整设置」，去掉「重新运行」误导字样。位置不变，不要挪到顶部 |

结果：

- 顶部只有一个「发起运行」按钮 = 开始运行，永远按设置跑，无 attempt 限制、无重叠。
- 从未运行 task：开始运行 = 首次运行。
- 已运行 task：开始运行 = 按当前设置再跑一轮（含范围/单步/全量重跑，取决于设置里的 mode）。

> 已定：硬编码「重新运行」**直接删除**。需要全量重跑统一走「调整设置 → 选『全量重跑』→ 进入任务 → 开始运行」。

## 附带润色（可选）

工具栏 `运行...` 按钮（`727`）改造后不再直接发起运行，而是进入「运行设置 → 进入任务」。文案 `运行...` 略有误导，可改为 `设置...` 或 `打开任务...`。非必须。

## 测试合同

### 前端 source contract tests

至少断言：

1. 存在 `enterTask` 函数，且其实现不调用 `runToStoryBoard`。
2. 「运行设置」弹窗底部按钮文案为 `进入任务`，`onClick` 绑定 `enterTask`，不再是 `runAnalysis`。
3. task 弹窗渲染条件为 `runProgressOpen() && task()`（不再要求 `runProgress()`）。
4. task 弹窗顶部命令栏存在文案为 `开始运行` 的按钮，`onClick` 绑定 `runAnalysis`。
5. 顶部「开始运行」在 `activeRunProgress()` 为真时 `disabled`。
6. `loadTask` **仅在跨 task（`taskId !== task()?.id`）时**清理 `runProgress` / `selectedRunStepId` / `stepQuickWatch` / `stepMenuOpenId` 并 `clearRunProgressTimer` / 关闭弹窗；同 task 刷新不清理、不关弹窗。
7. 所有 await 后写 task-scoped 数据的函数在写入前有 `taskId === selectedTaskId()` 身份守卫，至少包括：`loadTask` / `pollRunProgress` / `restoreLatestRun` / `runAnalysis` / `enterTask` / `saveRunModel` / `generateFinalPrompt` / `loadRunPlan` / `openRunModelDialog` / `loadStepDetail` / `stopRunProgress` / `resumeRunProgress` / `cancelPausePoint` / `setPauseBeforeStep` / `refreshRunProgress`；这些函数入口捕获 `taskId` 并用其调用 `saveConfig` / `runToStoryBoard` / `scheduleRunProgressPoll`，不内联读 `task().id`。
7b. 布尔标志 `busy` / `loading` / `uploadingVideo` 用 in-flight 计数（`finally` 无条件释放，**不得**用 `taskId` 守卫，否则卡死）；`runCommandBusy` 用「当前命令值 + token」释放。`catch` 里的 `setError` 受 `taskId === selectedTaskId()` 守卫（属 task-scoped 数据）。
7c. 跨 task 清理（`switchingTask`）包含 `setRunPlan(null)` 和 `setRunCommandBusy("")`。
8. rerun 守卫位于 `runAnalysis` 内 `buildRunPayload()` 之后、`saveConfig` / `runToStoryBoard` 之前，且基于 `runPayload.mode` / `runPayload.previous_attempt_id` 判断；缺 `previous_attempt_id` 的 rerun 模式在**任何 saveConfig/请求之前**中止（仅靠 select 过滤或只查 `runMode()` 字符串不满足）。
9. （方案 B）顶部「开始运行」始终渲染（不按 attempt 隐藏），`onClick` 绑定 `runAnalysis`（无参，沿用配置 mode）；硬编码「重新运行」（`rerun_all`）按钮已移除；「从设置重新运行」改名且仍调 `openRunModelDialog`。

### 需同步更新的现有 contract test（否则会失败）

`backend/tests/contracts/test_analysis_v1_task_process_indicator_mvp_contract.py` 用 `assertIn(token, module_source)` 断言源码包含一组文案（`104-129`）。本方案删除/改名按钮会撞上它，必须同步改：

- **移除** `"重新运行"` 断言（`121`）——顶部硬编码 `rerun_all` 按钮已删除；「从设置重新运行」改名为「运行设置」/「调整设置」后，`"重新运行"` 子串在源码中完全消失。
- **新增** `"进入任务"`（运行设置弹窗底部）和确认 `"开始运行"` 仍在（移到顶部命令栏）两个断言，锁定新 UI。
- **新增** `assertNotIn("重新运行", module_source)`，防止硬编码 rerun_all 按钮回潮（前提：「从设置重新运行」确实改名，不再含该子串）。
- 保留 step 菜单相关文案断言（`"运行至此步"`/`"从此步开始运行"`/`"重跑此步及后续"`/`"运行到此步前暂停"`/`"查看详情"`），这些不在去重范围。
- 若把工具栏 `"运行..."` 改名（可选润色），同步更新 `105` 的断言。

### 手工 smoke

前置：进入「视频分析（口播）」，选中一个 task。

**A. 从未运行过的 task**

1. 点工具栏 `运行...` → 打开「运行设置」弹窗。
2. 弹窗右下角显示 `进入任务`（不是 `开始运行`）。
3. 点 `进入任务` → 配置被保存 → task 弹窗打开，**没有发起运行**（无新 attempt，无进度轮询）。
4. task 弹窗能正常渲染（空态步骤预览或「尚未运行」提示），不空白、不报错。
5. 顶部命令栏有 `开始运行` 按钮。
6. 点 `开始运行` → 真正发起运行，进度开始更新。
7. **运行跑到终态后，进度弹窗保持打开并显示终态结果，不被自动关闭**（验证第 0 节同 task 刷新不清理）。

**A2. 切换：已运行 task → 从未运行 task（验证第 0 节）**

1. 先选中一个有历史运行的 task（顶部能看到「最近运行」）。
2. 再切到一个从未运行过的 task。
3. task 弹窗 / 「最近运行」不应再显示上一个 task 的 attempt；`进入任务` 后是干净空态。
4. 若上一个 task 正在运行，切走后旧轮询应停止，不再污染当前 task 的状态。

**B. 已运行过的 task**

1. 选中一个有历史运行的 task → 头部「最近运行」按钮可直接打开 task 弹窗（原有路径不回归）。
2. `运行...` → 「运行设置」→ `进入任务` → task 弹窗显示历史 attempt 进度，**不重新发起运行**。
3. （方案 B）跑过的 task 顶部**仍显示**「开始运行」，可按设置（含范围/单步/全量重跑）再跑；不再有单独的硬编码「重新运行」。
4. 在「运行设置」里选 `run_range` / `run_from_step` 等 → `进入任务` → 顶部「开始运行」按所选 mode 执行（验证 #1 修正）。
5. 「调整设置」（原「从设置重新运行」）仍能回到「运行设置」弹窗。

**C. 运行中**

1. 运行进行中时，工具栏 `运行...`（`727`）按原逻辑禁用。
2. 通过「最近运行」按钮进入 task 弹窗，顶部 `开始运行` 处于禁用状态（`activeRunProgress()`）。

## 风险与回归点

- **（前置，最高优先）task 切换状态清理是本方案成立的前提**。若不做第 0 节的清理，从「有运行 task」切到「从未运行 task」时 `runProgress()` 会残留旧 attempt，空态前提失效，甚至导致顶部 rerun 跨 task 提交。第 0 节必须先落地并单测覆盖。
- **清理必须限定跨 task，否则引入新回归**：`pollRunProgress` terminal 后会 `loadTask(同 taskId)`（`505`），若同 task 刷新也清理/关弹窗，运行完成时弹窗会被关掉且不再重开。务必用 `taskId !== task()?.id` 门控（第 0 节）。
- **`clearRunProgressTimer` 挡不住已在途的请求**，需第 0b 节的 `selectedTaskId()` 身份守卫，覆盖 `pollRunProgress` / `restoreLatestRun` **以及 `loadTask` 自身的 `setDetail` 等写入**，否则切走后旧 task 的在途响应会污染新 task（导致 `task()` 与 `selectedTaskId()` 错位，`enterTask`/`runAnalysis` 操作错 task）。
- **顶部「开始运行」执行的 mode 取决于设置里的 `runMode()`**，不是固定全量；文档/文案不得称其「全新运行」。无 attempt 的非法 rerun 由 4b 节守卫在 saveConfig/请求前拦截。
- task 弹窗渲染条件放宽后，body 内所有 `runProgress()?.xxx` 访问必须容忍 `null`。现有代码大多已用 `?.` 和 `|| []`，但需逐个确认（步骤列表 `955`、命令栏 capabilities `931-935`、summary `942-948`）——空态下这些应为禁用/空，而非崩溃。
- 顶部「开始运行」与运行模式 rerun 选项的组合：无 attempt 时必须按第 4 节守卫拒绝/降级，否则发非法 rerun。
- `enterTask` 与 `runAnalysis` 共用 `saveConfig`，保存语义一致；若 `saveConfig` 失败，应停在「运行设置」弹窗并显示错误，不要进入 task 弹窗（当前实现：异常时不 `setRunProgressOpen(true)`，符合预期）。
- 顶部「开始运行」复用 `runAnalysis()`，其内置的云端 ASR 授权校验（`549-553`）继续生效，不需重复实现。

## 结论

本方案为单文件、低风险前端改造，核心改动点：

0. **（前置，必须先做）** 三件事：
   - `loadTask` **跨 task 时**清理 run-scoped 状态（`runProgress` / `runPlan` / `selectedRunStepId` / `stepQuickWatch` / `stepMenuOpenId` / `runCommandBusy`）+ 轮询 timer + 关弹窗；同 task 刷新不清理（避免运行完成关弹窗）。
   - **所有 await 后写 task-scoped 数据的函数**（`loadTask` / `pollRunProgress` / `restoreLatestRun` / `runAnalysis` / `enterTask` / `saveRunModel` / `generateFinalPrompt` / `loadRunPlan` / `openRunModelDialog` / `loadStepDetail` / 进度命令 `stopRunProgress`/`resumeRunProgress`/`cancelPausePoint`/`setPauseBeforeStep`/`refreshRunProgress`）写入前加 `taskId === selectedTaskId()` 守卫，并用捕获的 `taskId` 调下游 API。建议用 `withTaskGuard(taskId, fn)` 统一包装，避免逐处漏。
   - 全局标志 `busy` / `loading` / `runCommandBusy` / `uploadingVideo` 用 in-flight 计数 / operation token，`finally` 无条件释放，**不得用 taskId 守卫**（否则卡死）。
1. 新增 `enterTask()`。
2. 「运行设置」底部 `开始运行` → `进入任务`。
3. task 弹窗渲染条件放宽到 `task()`。
4. task 弹窗顶部新增 `开始运行`（复用 `runAnalysis`），并对无 attempt 时的 rerun 模式加守卫。
5. 头部用 `task()` 兜底显示。

运行类按钮去重采用**方案 B（顶部单一开始运行）**：顶部「开始运行」始终渲染、永远按设置跑（`runAnalysis()` 沿用配置 mode）；删除冗余的硬编码「重新运行」（`940`）；「从设置重新运行」（`1049`）改名「运行设置」。任何时刻只有一个发起运行入口，且已运行 task 也能按设置跑。

可选：空态步骤预览（须独立只读分支）、背景点击关闭放宽。

> 第 0 节是本方案成立的前提，不是可选项——空态、跨 task 状态、轮询污染都依赖它。
