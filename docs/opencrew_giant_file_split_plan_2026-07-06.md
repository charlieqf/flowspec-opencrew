# 巨文件拆分规划(v9):按改动频率排序的低风险拆解

- 日期:2026-07-06(v2:吸收同行评审 6 条;v3:再吸收 3 条;v4:新增 G1d 章节;v5:G1d 吸收二轮评审 5 条;v6:三轮 5 条;v7:e2e env 加载方式写死为 .mjs 自加载;v8:补 G1d-1 shell layout 首刀边界;v9:补 G1d-2 routing+metering 拆分计划)
- 依据:技术债报告 P1 第 1 条;所有排序依据 2026-07-06 实测
- 前置:Step 1 完成(拆分工艺、AST 工具、门禁、568 契约测试全部就位);Step 2(db)只碰 `db/`,与本规划零冲突、可并行
- 工艺继承 Step 1:**纯搬移(move-only)与行为改变分开提交;每步契约绿;表面积对账防漂移**

## 0. 排序依据(实测,推翻直觉)

| 文件 | 行数 | 近 30 天提交 | 拆分收益本质 |
|---|---|---|---|
| `frontend/src/App.jsx` | ~4313 | **50** | 日常税最高:每天都有人改,大文件=冲突重灾区 |
| `backend/.../koubo/router.py` | 5291 | 11 | 45 routes / 218 嵌套 def(AST 实数);中等流量 |
| `backend/.../koubo/rebuild_router.py` | 5270 | **2** | 近乎休眠;收益是**去重**(54 函数与 storyboard services 同名)与 dance 竞态残余排查,不是日常税 |
| `frontend/.../OCRebuildModule.jsx` | 3553 | 低 | 第二梯队,G1 套路复制 |

直觉排序(先拆最大/去重红利最大的 rebuild_router)是错的:它一个月只被改 2 次,拆它的收益要很久才能兑现。**改动频率 = 税率**,先拆税率最高的。

## G0 保护网(半天,先行)

1. **route 表面积 snapshot(v2:必须从运行时提取)**:snapshot 不能从源文件静态提取——拆完后 decorator 分散到多个模块,原文件不再代表表面积。改为**实际构建 APIRouter 实例**(契约测试已有用 fake ctx 构建 router 的成熟模式),从 `router.routes` 提取 **(顺序, methods, path, endpoint `__name__`)** 四元组提交为 JSON——**不能用 `__qualname__`**(v3 修正):当前 handler 全部嵌套在 `build_openclip_router` 闭包内,qualname 为 `build_openclip_router.<locals>.<name>`,G2 上移后必然变化,按 qualname 零变化与上移目标自相矛盾;以 `__name__` 比较即同时把「上移不得改 handler 函数名」固化为约束(move-only 本就不改名)。**顺序必须入账**——只比(方法,路径,名)会漏掉路由顺序变化导致的 shadowing。契约测试对账,拆分全程表面零变化。
2. App.jsx 已有保护:role-surface wiring 契约、eslint no-undef、frontend build、e2e。**cache guard 盲区(v2 实测修正)**:`check_koubo_frontend_cache_bump.sh` 只在变更匹配 `^frontend/src/modules/koubo/` 时触发——G1 要改的 `App.jsx`、新增的 `src/lib/`、`src/pages/` **都不会被 guard 拦**,而 `main.tsx` 经 `./App.jsx?v=...` 加载 App,链条失守。G0 必须**先扩展 guard**:App shell 链(index.html / main.tsx / App.jsx / src/lib / src/pages / src/components / src/debug)变更时要求 bump main.tsx→App.jsx 链的 cache string;guard 扩展先于任何 G1 提交落地。**guard 语义边界(v3 明示)**:App.jsx 现存 6 个无 `?v=` 的子模块 import(KouboTaskList、DanceMimicV1 等)——只 bump App 链不一定刷新这些子模块 URL,这是**既有缺口**,归 `?v=` 机制另案,拆分不负责修复但**不得扩大**:G1 新拆出的 lib/pages 模块在 App.jsx 中的 import **一律带 `?v=`** 并纳入 guard 匹配;扩展后的 guard 仍不是完整 cache guard,其承诺范围以带 `?v=` 的链为限。

## G1 App.jsx 拆壳(第一优先,收益最高)

- **G1a 纯函数搬家(零风险,当天可完成)**:顶部 20+ 个格式化/构造 helper(`formatMicrosUsd`、`meteringStatusLabel`、`createDefaultOpenFlowInput` 等)按域搬到 `frontend/src/lib/`(metering 格式化一个文件、openflow 输入构造一个文件),App.jsx import 回来。move-only、逐文件提交。
- **G1b OpenFlow 死代码处置(行为敏感,单独提交)**:App.jsx 内 149 行 OpenFlow 相关代码 + eslint 白名单里 20 个未定义引用,先判死活。**可达性判据(v2 收紧)——role-surface 契约只断言 nav token 不回潮,不能单独作为死亡证明**(弹窗、task detail、hash 路由、历史 session 都可能是入口),判死必须三件套齐全:① 源码引用清单(谁引用这些标识符/组件,含 hash 路由与事件绑定);② 浏览器/e2e 实际路径确认不可达;③ 删除后 `appLegacyOpenFlowGlobals` 白名单**归零**且 eslint 全绿。三证齐全 → 删代码+删白名单,两笔债一次销;任一不齐 → 视为活代码,修未定义引用(潜伏的运行时 ReferenceError)。判据与结论入提交信息。
- **G1c 页面区块下沉(主体)**:App.jsx 不是松散页面集合而是**强闭包状态容器**(约 112 个 createSignal + 约 46 个 createMemo,实测于 2026-07-06,日常漂移属正常;约 1100 行起大量区块直接读写这些信号)——**每个区块动手前先跑信号读写扫描,生成该区块的 props/state/action 清单**(读哪些信号→props、写哪些→回调、私有的→随块下沉),清单入提交;没有清单的下沉极难保持 move-only。区块逐个搬到 `frontend/src/pages/`,App.jsx 收敛为 shell(目标 **< 800 行**)。一区块一提交 + e2e;role-surface 契约 token 随迁移改指向新文件(Step 1 已有成熟模式)。
- **多机协调(重要)**:App.jsx 是月 50 commits 的热区,G1c 的大 diff 与并行改动相撞最痛——**开拆前在群里锁窗口,选安静时段集中完成**。先痛一次,换今后所有人的改动都落在小文件里。
- 每次提交记得 bump `?v=` 链(G0 扩展后的 guard 会强制;扩展落地前靠手工纪律,这正是 G0 先行的原因)。

## G1d useOpenCrewAppController 域拆分(G1 后追加,插在 G2 前)

> 背景:G1c 一步到位后 App.jsx 仅 7 行,但状态整体落入 `shell/useOpenCrewAppController.jsx`(约 1454 行、63 signal、28 memo、~235 返回 key,实测于 2026-07-06;精确数随日常提交漂移,开工时以 G1d-0 重测为准)——App 的月 50 commits 改动税由它继承,若不继续拆,巨文件只是换了名字。插队理由:前端税率(50/月)仍高于 router.py(11/月),G 序列在此插入 G1d,G2 顺延。

### 域划分(与实测引用密度吻合)

| controller | 职责 | 实测引用密度 |
|---|---|---|
| `useAuthController` | 登录/authState/capabilities(**从提案的 connection 域独立出来**:小而被 routing/metering 广泛消费,不与基建混装) | (auth+connection 合计 177) |
| `useConnectionController` | OpenCode/publish/tunnel/npc/wecom/setup、connection tests | 同上大头 |
| `useShellRoutingController` | hash/routeHash/activeNav/role route/retired route/守卫 | 42 |
| `useMeteringController` | metering state/actions | 52 |
| `useMediaSettingsController` | ASR/media/mihomo/价格排行/agent aliases | 48 |
| `useShellLayoutController` | **仅 shell layout owner**:右侧栏 resize、navCollapsed、右侧媒体 sidebar selection(`analysisV1MediaItem`/`danceMimicMediaItem`/`kouboStoryBoardSidebar`) | ~5(薄;首刀不与 sessions 合并) |
| sessions/events 归属(v6 升级为 G1d-0 硬输出项) | selectedSessionId/session 事件流——**归属决定是 G1d-0 报告的必交付项**,不定就开工会把 event stream 状态散落 layout/routing 两边 | 6 |

**接口线索与权威契约分开(v6 修正)**:子组件 props 解构只是**域内划分的线索**——权威契约是前置 4 的 return-surface snapshot + ShellView/ShellDialogs/DebugConsole/AuthGate 的顶层消费清单;两者在 G1d-0 一并产出。

### 六项前置(评审拍板,缺一不开工;v5 由四项扩为六项)

1. **G1d-0 信号依赖报告(半天)**:范围**必须含**(v5 收紧):① effect/memo 的直接信号读;② **经 helper 的传递式 tracked read**(`navAllowed()`/`syncActiveNavFromHash()` 这类被 effect 调用的 helper 参与依赖收集——b21a1ec 修的 bug 正是此类);③ 现有 `untrack` 边界;④ 事件 handler 内的非响应式读(它们不订阅,拆分时容易被误当依赖)。实测跨域行仅 ~8 行,集中在 routing+auth+metering 三角。报告直接决定 controller 参数面。
2. **角色门控三角显式化 + 单向依赖**:auth → routing → metering;routing 拥有 `activeNav/routeHash/守卫`,以 accessor 参数注入 `canManageConnection/canViewMetering`。**禁止 controller 间互相 import**——组合根注入。**回调边界显式定义(v5)**:hashchange 解析 `#/metering` 时会写 metering 状态并触发 `loadMeteringTaskReport`(实测 controller 278/286 行)——routing 不得 import metering,由组合根注入 `onMeteringRoute(route)`、`goToBusinessHome` 等回调;此边界清单在 G1d-0 报告中列全,否则实现时必然重生隐式耦合。
3. **导航冒烟固化为 `frontend/e2e/shell-nav-smoke.mjs`**:登录 → Connection 首击 → 五导航切换验 hash → 直接 `#/metering` → 零 pageerror。**认证与角色(v6 纠正)**:现有 e2e 消费的是 `OPENCREW_E2E_APP_PASSWORD`(10 处),`.opencrew-e2e-auth.env` 实际同时定义 ADMIN/USER/APP 三个变量——双角色 smoke 用 `OPENCREW_E2E_ADMIN_PASSWORD` + `OPENCREW_E2E_USER_PASSWORD`(env 已备),与现有单角色脚本的 APP_PASSWORD 约定并存;**加载方式写死(v7):smoke 脚本自加载**——照 `scripts/dance_mimic_v1_real_browser_acceptance.mjs:423` 的现成模式在 .mjs 内 `readEnvFile(REPO_ROOT/.opencrew-e2e-auth.env)`,且 `process.env` 已有值时优先(CI 注入不被覆盖);这样 `node e2e/shell-nav-smoke.mjs` 直跑与 npm script 两种方式都闭合,不依赖 shell source。任一所需变量缺失时**报错退出**而非静默跳过;**覆盖双角色预期**——admin 直入 `#/metering` 应停留,受限用户(无 can_view_metering)应被弹回业务首页(这是 b21a1ec 保留的认证后角色管控,属正确行为,要断言而非回避)。拆分最易复活的就是 effect 自触发类 bug,先落 e2e 再动手。
4. **controller 返回面契约(v5 新增)**:`useOpenCrewAppController()` 返回 **~235 个 key**(实测),而"子组件 props 即合同"只覆盖其中一部分——ShellView 自身、ShellDialogs、DebugConsole、`renderAuthGate` 都直接消费顶层 key。G1d-0 一并产出 **return-surface snapshot**(key 集合入库 + 契约测试对账,Step 1 snapshot 模式第三次复用):拆分期间 key 名零漂移;命名空间化重构(如按域分组返回)是拆完后的独立提交。
5. **AuthGate UI 先出 controller(v5 新增,G1d-0.5,move-only)**:`renderAuthGate` 是 controller 内的 JSX helper(1201 行)——先抽为 `shell/AuthGate.jsx` 组件,auth controller 只供 state/actions;否则"组合根 150-200 行纯组装"的目标从第一天就被 UI 混装破坏。
6. **cache guard 链映射同步扩展(v6 写清链式语义)**:改 `shell/controllers/X.jsx` 时,guard 须要求 ① `useOpenCrewAppController.jsx` 中对 X 的带版本 import 有 bump,且 ② `App.jsx → useOpenCrewAppController.jsx` 这级 import 同步 bump(完整链:index.html → main.tsx → App.jsx → controller 组合根 → 域 controller)。bump 脚本的重写范围同步覆盖。缺链级语义就会重演「间接模块没有 App.jsx 逐行 import」的 guard 盲区。

### G1d-1 首刀:`useShellLayoutController`(move-only)

目标是先抽低耦合 shell layout 状态,不给后续 auth/routing/metering/media 拆分制造新 owner 争议。新增 `frontend/src/shell/controllers/useShellLayoutController.jsx`,由组合根 `useOpenCrewAppController.jsx` 调用并继续按旧 key 平铺返回;return-surface snapshot key 数与 key 名零漂移。

**纳入 G1d-1 的唯一范围**:

- `rightSidebarWidth` / `setRightSidebarWidth`
- `rightResizeState` / `setRightResizeState`
- `startRightResize` 与对应 window mousemove/mouseup `createEffect`
- `navCollapsed` / `setNavCollapsed`
- `analysisV1MediaItem` / `setAnalysisV1MediaItem`
- `danceMimicMediaItem` / `setDanceMimicMediaItem`
- `kouboStoryBoardSidebar` / `setKouboStoryBoardSidebar`

**明确不纳入 G1d-1**:

- `envDialog` / `runDialog`:归 connection/NPC controller,因为对应 open/save/reconnect action 会读写业务状态。
- `asrDialog` / `mediaDialog` / `mediaPriceListOpen` / `mediaUnitPriceOpen`:归 media settings controller,因为 dialog kind、loading/saving、provider/model 选择与 media 配置强耦合。
- `publishGuideOpen`:归 publish/connection controller,不属于 shell layout。
- `mediaAgentDrag`、`setMediaAgentDrag`、pointer handlers:归 media settings controller,属于 agent aliases 交互。
- `selectedSessionId` / session event stream:按 G1d-0 dependency report 归 `useSessionEventsController`,不可混入 layout/routing。

**执行步骤**:

1. 搬移上述 signal 与 resize effect 到 `useShellLayoutController`;新文件只 import Solid primitives,不 import 其他 domain controller。
2. `useOpenCrewAppController` 以带版本串 import 新 controller,解构后按原 return key 返回;ShellView/ShellDialogs/DebugConsole/AuthGate 的 prop 名不变。
3. 同步 bump cache 链:`index.html → main.tsx → App.jsx → useOpenCrewAppController.jsx → useShellLayoutController.jsx`。
4. 只做 move-only;若发现 dialog owner 或 media drag owner 需要调整,另开后续 G1d media/connection 提交处理。

**G1d-1 验收**:

- `npm --prefix frontend run test:g1d-controller-contract` 通过,且 return-surface snapshot 无变化。
- `scripts/check_koubo_frontend_cache_bump.sh` 通过。
- `npm --prefix frontend run build` 与 `npm --prefix frontend run lint` 通过。
- `npm --prefix frontend run test:e2e:shell-nav` 通过;若只在 production preview 常绿而 dev server 有既有 Solid owner 问题,提交说明必须写明实测 URL 与原因。
- 单独提交,建议提交信息:`Extract shell layout controller`。

### G1d-2:`useMeteringController` + `useShellRoutingController`(高收益收口)

目标:拿掉当前组合根里风险最高的 routing/metering 副作用三角,但不一次性重写 auth/bootstrap。当前 `useOpenCrewAppController.jsx` 仍直接拥有 `activeNav`、`routeHash`、hashchange listener、role guard、metering state/actions 与 `#/metering/task/<id>` 深链加载;这正是后续最容易复活 b21a1ec 类问题的区域。G1d-2 只拆 owner 与注入边界,保持顶层 return key 零漂移。

**执行顺序必须先 metering 后 routing**:

1. 先抽 `frontend/src/shell/controllers/useMeteringController.jsx`。原因:把 metering 内部 signal/action 收口后,routing 才能只依赖一个 callback,避免 `useShellRoutingController` 一诞生就携带 metering setter 清单。
2. 再补 metering route adapter。原因:当前 hashchange 对 `#/metering` 的处理既写 tab/task state,又可能触发 task report 加载;这些细节应归 metering owner。
3. 最后抽 `frontend/src/shell/controllers/useShellRoutingController.jsx`。原因:routing 需要消费 auth accessors 与 metering callback;等 metering 边界稳定后再搬 hash listener,风险最小。

**G1d-2a:抽 `useMeteringController`(move-only)**:

纳入 metering owner 的唯一范围:

- `initialMeteringRoute`
- `meteringReport` / `setMeteringReport`
- `meteringTab` / `setMeteringTab`
- `meteringTaskId` / `setMeteringTaskId`
- `meteringAttemptScope` / `setMeteringAttemptScope`
- `meteringTaskReport` / `setMeteringTaskReport`
- `meteringDays` / `setMeteringDays`
- `meteringBusy` / `setMeteringBusy`
- `meteringTaskBusy` / `setMeteringTaskBusy`
- `meteringError` / `setMeteringError`
- `loadMeteringReport`
- `loadMeteringTaskReport`
- `selectMeteringTask`

边界要求:

- 新 controller 可 import `api` 与 `parseMeteringHash`;不得 import routing/auth/其他 domain controller。
- `selectMeteringTask(taskId)` 仍负责写 metering state、静默替换 hash、同步 routeHash 镜像并加载 task report;这是现有行为,本提交不改交互。由于 `window.history.replaceState(null, "", nextHash)` 不触发 `hashchange`,`useMeteringController` 必须接收 `onMeteringHashReplaced(nextHash)` 回调,由组合根传入 routing 返回的 routeHash 同步函数;不得直接 import routing controller。
- `useOpenCrewAppController` 只解构并平铺返回旧 key;ShellView/MeteringPage props 名不变。
- 同步 bump cache 链:`index.html -> main.tsx -> App.jsx -> useOpenCrewAppController.jsx -> useMeteringController.jsx`。

`useMeteringController` 参数面:

```js
useMeteringController({
  onMeteringHashReplaced,
});
```

回调语义:

- `onMeteringHashReplaced(nextHash)`:只在 metering 主动 `replaceState` 后调用,用于同步 routing owner 的 `routeHash` signal;不得触发额外导航或重新解析 metering route。
- 推荐组合根传入 routing 暴露的 `syncRouteHashFromExternalReplace(nextHash)` 或同等语义函数,不要传裸 `setRouteHash` 给 metering,避免 metering 获得 routing 内部 setter 所有权。

**G1d-2b:补 metering route adapter(小提交,可与 G1d-2a 合并但不与 routing 合并)**:

在 `useMeteringController` 中提供明确的 routing 回调面:

```js
const applyMeteringRoute = (route, options = {}) => {
  const loadTask = Boolean(options.loadTask);
  // route = parseMeteringHash(hash)
};
```

语义:

- `route.tab` 存在时设置 `meteringTab`。
- `route.taskId` 存在时设置 `meteringTaskId` 并把 `meteringAttemptScope` 重置为 `"all"`。
- `loadTask === true && route.taskId` 时调用 `loadMeteringTaskReport(String(route.taskId), "all")`。
- route 为空时不写 state,由 routing owner 决定是否跳转。

保留旧 key:

- `setMeteringTab`、`setMeteringTaskId` 等 setter 仍按旧 return surface 暴露;命名空间化或隐藏 setter 是 G1d 完成后的独立行为改变,不得混入本次拆分。

**G1d-2c:抽 `useShellRoutingController`(move-only + 注入 callback)**:

纳入 routing owner 的唯一范围:

- `activeNav` / `setActiveNav`
- `routeHash` / `setRouteHash`
- `canManageConnection`
- `canViewMetering`
- `roleAccess`
- `navAllowed`
- `statusCanManageConnection`
- `statusCanViewMetering`
- `isRetiredNavHash`
- `goToBusinessHome`
- `applyRoleRoute`
- `syncActiveNavFromHash`
- hashchange listener 与依赖 `routeHash()` 的防回弹 `createEffect`

明确不纳入 G1d-2c:

- `authState` / `submitAuth` / `logout` / `api.authStatus()` bootstrap:暂留组合根,避免 auth+routing+metering 三域同提交互相放大风险。
- `loadInitialData(hashTaskId, canManageConnection)` bootstrap:暂留组合根;只通过 routing helper 得到 role guard 结果。
- metering state/action 内部细节:只通过 `onMeteringRoute(route, options)` 注入。
- session selection:只通过 `onClearSessionSelection()` 注入,不得让 routing import `useSessionEventsController`。

`useShellRoutingController` 参数面:

```js
useShellRoutingController({
  authState,
  authReady,
  canManageConnection,
  canViewMetering,
  onMeteringRoute,
  onClearSessionSelection,
});
```

回调边界:

- `onMeteringRoute(route, { loadTask: false })`:初始 hash 同步时只设置 metering tab/task state,不额外加载。
- `onMeteringRoute(route, { loadTask: true })`:hashchange 进入 `#/metering/task/<id>` 时触发 task report 加载。
- `onClearSessionSelection()`:处理空 hash 时清理 session selection;组合根传入 `() => setSelectedSessionId(null)`。
- `syncRouteHashFromExternalReplace(nextHash)`:routing 返回给组合根、再注入 metering 的函数;仅同步 `routeHash` signal,服务 `selectMeteringTask` 的静默 `replaceState` 场景。
- routing 不得 import metering/session/auth controller;组合根是唯一 wiring 点。

核心 bootstrap 交接:

- `submitAuth` 暂留组合根,但登录/创建密码成功后必须继续调用 routing 返回的 `applyRoleRoute(status)`;这是登录后角色 bounce 的保护点,不得因搬移 routing helper 而遗漏。
- `onMount` 的 `api.authStatus()` 暂留组合根,但得到 status 后必须继续调用 routing 返回的 `applyRoleRoute(status, initialHash)`;这是初始深链/受限用户路由应用的保护点。
- `loadInitialData(hashTaskId, statusCanManageConnection(status))` 暂留组合根,其中 `statusCanManageConnection` 必须来自 routing 返回面或保留在组合根直到 auth 拆分,不得复制出第二套角色判断。
- 构造顺序必须保证 routing controller 在 `submitAuth` 与 `onMount` 定义前完成解构,使核心调用点使用的是 routing owner 返回的函数,而不是遗留本地副本。

必须保留的防回弹语义:

- `routeHash()` 是 hashchange 的响应式镜像;`""` 是 Connection 页面的真实状态,不能被当作 falsy 退回 hash-derived nav。
- 防止 Connection 首击被旧 hash 弹回的 effect 必须继续只 track `authReady + routeHash`,读取 `activeNav` 时继续使用 `untrack(activeNav)` 或等价非响应式读取。`authReady` 由组合根现有 memo 直接注入 routing controller,不要在 routing 内从 `authState` 重算第二份派生逻辑。

**G1d-2 验收用例(缺一不合格)**:

- admin 直开 `#/metering` 后停留在 Metering,不被业务首页覆盖。
- admin 直开 `#/metering/task/<id>` 后 `meteringTab === "task"`,task id 写入,并加载 task report。
- 无 `can_view_metering` 的用户直开 `#/metering` 或 `#/metering/task/<id>` 被送回 `#/analysis-v1/tasks`。
- 从 Metering task 列表点击 task 行后 hash 仍变为 `#/metering/task/<id>`,且 task report 加载。
- 从 Metering task 列表点击 task 行后,即使使用 `history.replaceState` 静默更新 hash,`routeHash()` 镜像也同步为 `#/metering/task/<id>`。
- Connection 首击不被旧 hash 弹回。
- 登录/创建密码成功后仍执行角色路由管控,受限用户不会停留在 `#/metering`。
- 初始 auth status 返回后仍执行 `applyRoleRoute(status, initialHash)`,受限用户深链不会停留在 `#/metering/task/<id>`。
- retired route(`#/sessions`、`#/openflow` 等)仍跳回业务首页。
- return-surface snapshot key 名与 key 数零漂移;新增子 controller surface 被 `test:g1d-controller-contract` 捕获。
- dependency report 的 Current Boundary State 更新为 metering/routing 已拆,不得继续写“routing/metering 仍在根 controller”。

**G1d-2 验证命令**:

- `npm --prefix frontend run inventory:g1d-controller`
- `npm --prefix frontend run test:g1d-controller-contract`
- `scripts/check_koubo_frontend_cache_bump.sh`
- `npm --prefix frontend run build`
- `npm --prefix frontend run lint`
- `npm --prefix frontend run test:e2e:shell-nav`(若本机 e2e 环境不可用,提交说明必须写明未跑原因;不能静默跳过)

**建议提交切分**:

1. `Extract shell metering controller`
2. `Add metering route adapter`
3. `Extract shell routing controller`

每个提交必须独立可 revert;若 G1d-2b 与 G1d-2a 合并,提交信息必须明确 adapter 只是 routing callback 面,不是行为改变。

### G1d 阶段完成判据(较提案收紧)

**G1d-2 阶段完成**:

- `metering` 与 `routing` owner 已从组合根拆出;组合根仍可保留 auth/bootstrap、connection/publish/npc 等未拆逻辑。
- `useOpenCrewAppController.jsx` 在 G1d-2 结束时不要求达到 150-200 行;该数字是整个 G1d 完成态目标,不是 G1d-2 的验收门槛。
- routing/metering controller 间零直接 import;双向协作只通过组合根注入的 `onMeteringRoute` 与 `onMeteringHashReplaced`/`syncRouteHashFromExternalReplace` 完成。
- return-surface snapshot、dependency report、cache guard、build、lint、shell-nav-smoke 均按 G1d-2 验证命令通过。

**整个 G1d 完成态**:

- 组合根 `useOpenCrewAppController.jsx` **~150-200 行**(提案 <500 太松:1454 是各域之和,纯组装根若到 400+ 说明有逻辑没归位),只做调用各 controller + merge 返回。
- 每个域 controller ≤400 行;controller 间零直接 import。
- shell-nav-smoke e2e 常绿;契约/build/eslint/guard 全绿;move-only 与行为改变分开提交照旧。

## G2 router.py 拆分(第二优先)

- **G2-0(v2 新增,先行)**:hoist **dry-run + 捕获变量报告**——扫描 `build_openclip_router` 闭包内 **218 个嵌套 def**(AST 实数;v1 的 208 是 4 空格缩进 grep 的漏数)各自捕获哪些闭包变量(ctx/repo/兄弟函数/局部状态),报告决定切割边界与显式参数面。工具已入库:`scripts/step1_tools/`(phase_r/s/f 三件,v2 时从会话临时目录抢救提交——评审指出仓库里原本没有它们),改造后复用。
- 结构照抄 koubo_storyboard 终态:`koubo/openclip/` 包,按 URL 域切 routes 模块(45 routes 按前缀自然分组),嵌套 def 上移 module-level + 显式参数。
- route snapshot 对账保证 URL 零变化;role-surface 契约(SURFACE_ANALYSIS_V1 tokens 断言 router.py 源码)随迁移改指向新文件。
- 5 个与 storyboard 同名的函数(`resolve_model`、`opencode_client_for` 等):先 diff,**实现一致才替换为复用**,分叉的记录在案不强合。

## G3 rebuild_router.py(机会性,最后)

- 休眠文件(月 2 commits),不占用主线排期;两个非日常收益单独立项:
  1. **去重**:54 个同名函数逐一 diff,与 storyboard services 一致的替换为 import(带 sc),分叉的记录清单——预期能消掉数百行重复;
  2. **dance 竞态残余排查**:拆分时顺带盘点闭包内共享可变状态(module 级 dict/lock),若 issue #1 烧验期间再现 flake,这里是头号嫌疑现场。
- 工艺与 G2 完全相同。

## G4 前端第二梯队

路径均在 `frontend/src/modules/koubo/`:`OCRebuildModule.jsx`(3553)、`OpenClipModule.jsx`(1554)、`OCStoryBoardModule.jsx`(**1474,v1 漏列**)、`KouboStoryBoardModule.jsx`(**1426**,v1 写 1408 已过期):G1 套路复制,排在 G1 完成之后(同域改动错峰)。

## 明确不做

- 搬移提交里混入任何行为改变(G1b 的死代码删除是唯一例外,且单独提交、判据入档)。
- 强合分叉的重复实现(diff 不一致就保留两份并记录)。
- `?v=` 机制移除、repository 层重构、路由 URL 调整——各是另案。

## 排期与并行

```
本周   G0 保护网(含 cache guard 扩展 + runtime route snapshot)+ G1a 纯函数搬家(1 天)
       G1b OpenFlow 判死活与处置(半天)
下周   G1c 区块下沉(锁窗口集中做,2-3 天)—— 与 Step 2 S2-0/S2-1 并行无冲突
之后   G2 router.py(2-3 天,工具复用)
机会性 G3 rebuild_router(去重+竞态排查)、G4 前端第二梯队
```

## 完成判据

- App.jsx < 800 行,eslint 白名单删除,role-surface 契约常绿;
- router.py 拆为 ≤500 行/文件的 routes 包,route snapshot 对账常绿;
- 全程 main 无红灯,每步可独立 revert。
