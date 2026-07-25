# Step 1 实施方案（v5，决策已定）：Koubo Storyboard `globals()` 注入 → 显式 Context

- 日期：2026-07-01（v5，五个决策点已拍板，可开工 Phase 0）
- 范围：`backend/opcrew_backend/koubo/koubo_storyboard/`（**28 个 `*services*.py` + 13 个 `*routes*.py`**）
- 目标：把模块级全局注入的隐式依赖，改为**显式、类型化、可注入**的 `StoryboardContext`，提升可测性、消除"共享可变模块全局"这一竞态/隔离债的根源。
- **行为边界（重要）**：**单 router / 单实例的正常路径 = 零行为 diff**（纯重构不变式）。而**多 router / 多实例互不污染**是本次**刻意修复**的隔离缺陷——严格说这是有意的行为改变（从"共享/被覆盖"变为"隔离"），**不算回归**，正是 Step 1 的目的。"零 diff" 只约束单实例路径。
- **交付定义**：走到 Phase F —— service 函数**上移为 module-level、显式接收 `sc`**、跨模块调用走**直接 import**、**零共享可变全局**、隔离 contract 测试转绿。`_sc` 只是单模块 PR 内机械中间态，不作为交付终点。
- 前置：Step 0 已完成（contract tests 绿基线 + CI 门禁 + pre-push）。

> v3 变更（相对 v2）：① 明确**嵌套 service 函数（`_export_services(ns, locals())`）的上移方案**；② 双模式签名改为**尾部 keyword-only** `*, sc=None`（避免位置参数错位）；③ 终态 `StoryboardContext` **只装依赖、不装函数**（去 service-locator），跨模块调用走直接 import；④ 隔离测试用 **expectedFailure** 而非 skip；⑤ route 数字澄清（13 模块 / 12 注入点，`asset_digital_human_routes` 已显式）。
>
> v4 变更（相对 v3）：① Phase S 明确用**显式 `SERVICE_EXPORTS` 白名单**导出上移后的函数（禁 `globals()+callable` 过滤，避免扫入 imported 符号污染 ns）；② 补**行为边界**（单实例零 diff / 多实例隔离是有意行为改变）；③ Phase 0 补 **context 可变性约束**（过渡期须 mutable/非 slots/非 frozen）；④ **只 context-dependent 函数加 `*, sc`**，纯 helper 不加，由 AST 门禁判定；⑤ Phase S 覆盖**全部 4 处 `globals().get(...)`**（含 2 处动态函数查找）；⑥ 版本号/日期更正。
>
> v5 变更（相对 v4）：五个决策点拍板（见 §0）；据此补三点：① AST 门禁的"注入函数名集合"**由 `SERVICE_EXPORTS` 并集增量建成**，sc 终判锁 Phase F，禁用运行时反射；② dance flaky `skip` 会使该路径脱离门禁，ticket 须写明重启并尽量保留一条更轻的确定性断言；③ 断环优先级改为**归位/依赖反转 > 抽公共 helper > 函数内 lazy import**，并加 CI 轻量 import-cycle 检测。

## 0. 决策已定（2026-07-01）

1. **终态形式**：module-level 函数 + **尾部 keyword-only 显式 `sc`** + 跨模块**直接 import**。`StoryboardContext` 只装依赖不装函数（去 service-locator）。
2. **阶段策略**：四段式 **Phase 0 → Phase R → Phase S → Phase F**；Phase S 每模块用**显式 `SERVICE_EXPORTS` 白名单**导出上移后的函数。
3. **AST 门禁**：**Phase 0 就写** `scripts/check_storyboard_context_migration.py` 并挂 CI。它一物三用：漏改红线 + 覆盖 `globals().get(...)` + 驱动"哪些函数加 sc"。**注意**：其注入函数名集合由 `SERVICE_EXPORTS` 并集**增量建成**，故"该不该加 sc"的最终判定**锁在 Phase F**（并集完整时）；Phase S 每模块只当"本模块无 dep 裸引用残留"用；**不得用运行时 `_export` 反射建集合**（要静态可复现）。
   > 实施注记（2026-07-05）：Phase 0 落地时以**提交进 repo 的运行时 snapshot**（`scripts/storyboard_context_injected_names.json`，由 `storyboard_step1_inventory.py --write` 生成）作为门禁的名字全集来源——Phase S 前全库没有任何静态 `SERVICE_EXPORTS` 可用，snapshot 是唯一能提供第一天全覆盖的来源，且提交后静态可复现。为封堵「Phase S 导出污染被 `--write` 重新固化」的口子：① `--write` 对**新增**注入名一律拒绝，除非用 `--allow-new` 显式列名（污染绊线，新增成为受审动作）；② verify 模式交叉校验静态 `SERVICE_EXPORTS` 声明必须存在于 snapshot；③ Phase F 终态收紧为 `dynamic == 全部 SERVICE_EXPORTS 并集` 的严格相等。Phase S 起，静态声明是意图之源，snapshot 退为对账副本。同日二次收紧：context **字段**（read_json/write_json/safe_workspace_rel/analysis_tool_env/redact_payload/redact_secret_text 等 24 个）即使存在同名模块级 import，也必须经 `_sc.`/`deps.` 访问——旧 `globals().update` 会覆盖 import 绑定使 ns 覆盖生效，门禁对字段名不再把 import 计为绑定（动态函数名不受此限，Phase F 的跨模块直接 import 仍是终态）。
4. **dance flaky**：**先 `skip` + 挂 ticket**，不带病推进。区分清楚：**稳定必红的隔离测试用 `expectedFailure`**；**~60% 会过的 flaky 用 `skip`**（用 expectedFailure 会频繁报 unexpected success）。**注意**：`skip` 会使 dance 路径完全脱离门禁——ticket 须写"隔离工作完成后重启"，可行的话保留一条更轻的确定性断言别一并 skip。
5. **断环策略**：优先级 **归位/依赖反转（把函数移到天然属主模块）> 抽公共 helper 模块 > 函数内 lazy import（兜底，加注释说明为断环）**。lazy import 是藏环非解环，仅在调用少、抽模块 churn 大时用。另给 CI 加**轻量 import-cycle 检测**，Phase F 起防新增环。

---

## 1. 现状架构（实测）

装配入口（每个 app 实例调一次）——`koubo_storyboard/router.py:25`：`build_koubo_storyboard_services(ctx, repo)` 造 `ns`，注入各模块 globals；13 个 `register_*_routes(router, deps)`。

注入机制（`services.py:83-133`）：
- `ns = SimpleNamespace(ctx, repo, 7×asyncio.Lock, 4×job dict, runtime, 十几个辅助/工具函数)`。
- **关键结构事实**：多数 service 模块的服务函数是**定义在 `register_*_services()` 内部的嵌套闭包**，通过 `_export_services(ns, locals())` 挂到 `ns`（`asset_core/agent_chat/asset_reference/asset_pool/asset_history/builder_state/composer/host_product/asset_video_generation` 等 10+ 个模块）。只有 `asset_search_services` 用 `_export_services(ns)`（顶层函数、读 globals）。
- `_sync_service_globals(ns)` 把**含全部跨模块函数的完整 ns** 分发到 25 个模块 globals → 任一模块可裸名调任一模块函数。
- 12 个 route 模块 `globals().update(vars(deps))`；**`asset_digital_human_routes.py` 已是显式 `deps.xxx`（无注入）——终态样板已存在**。

**规模（更正）**：28 services + 13 routes；**~40 个显式注入 / 动态 global-access 点**；**数百个裸依赖读取点**（排期按此）；12 个 contract 测试文件直接依赖这套装配。

## 2. 核心缺陷

依赖被注入到模块级全局（进程内单例）：① 每次 build `globals().update()` 互相覆盖 → **无法隔离**；② 依赖不可见；③ 后台 worker + 请求并发下共享全局瞬时损坏 → **竞态**；④ 裸名（含跨模块函数、嵌套闭包）难穷举 → 漏改即运行时 `NameError`。

## 3. 两个关键结构约束

**(A) 跨模块调用是全连接 web**：`_export_services` + `_sync` 使任一模块可裸调任一模块函数 → 显式 `sc` 迁移非原子、无法纯叶子优先（图内可能有环）。故 `_sc`（持依赖 + 过渡期跨模块可达）是必要机械桥，非终点。

**(B) 多数服务函数是 register 内嵌套闭包**：不能直接 import，也不是 module-level 符号。终态要求**把它们上移为 module-level 函数**，否则删 `_export_services` 会丢函数。上移后：捕获的 `ns.xxx` → 形参 `sc.xxx`；`_export_services(ns, locals())` → 先转 `_export_services(ns)`（globals 形态）过渡，Phase F 整体删除。`asset_digital_human_*` 已是"module-level 函数 + 直接 import + `deps.xxx`"，作为**目标样板**。

## 4. 目标架构（终态）

**`StoryboardContext` 只装依赖，不装函数**（去 service-locator）：

```python
@dataclass
class StoryboardContext:
    ctx: AppContext
    repo: OpenClipRepository
    video_plan_lock: asyncio.Lock            # 7 lock + 4 job dict：每 router/实例一份，生命周期严格绑 router
    ...                                       # runtime 辅助 + 工具函数（read_json/workspace_for 等）
    # 不含任何 service 函数

# service 函数：module-level，依赖经尾部 keyword-only sc
def load_asset_search_settings(task, *, sc: StoryboardContext):
    return normalize(read_json(sc.workspace_for(task) / ASSET_SEARCH_SETTINGS_REL))

# 跨模块调用：直接 import 目标函数，显式传 sc（不再经 ns/sc 转发）
from .storyboard_plan_services import load_storyboard
def generate_asset_video(task, *, sc: StoryboardContext):
    plan = load_storyboard(task, sc=sc)

# route：闭包捕获 sc，显式传给 service
def register_asset_search_routes(router, sc):
    @router.get(...)
    async def _h(...):  return list_asset_search_runs(..., sc=sc)
```

**终态形式抉择（推荐：module-level 函数 + 显式 `sc` 参数 + 直接 import）**
- 依赖 = `sc`（纯上下文）；函数 = module-level，跨模块靠 import。类型化收益完整，无 service-locator。
- **不推荐** ContextVar（不自动传播到后台 `threading.Thread` worker，`copy_context()` 才行，正踩 flaky 涉及的 worker）。
- **不推荐**把函数塞回 `sc`/单独 `StoryboardServices` 注册表（仍是 locator，类型化打折）。

**行为不变红线**：7 lock + 4 job 字典严格"每 router/实例一份、不被 request-dep/测试 helper 重建、不跨 loop 复用再竞争、不下沉到请求级"。

## 5. 阶段计划（绞杀式，每步 contract 绿 + 纯重构零 diff）

### Phase 0 — 地基
1. `SimpleNamespace ns` → 类型化 `@dataclass StoryboardContext`（字段与现状一致，**目标字段只依赖不含函数**；Phase 0 只改承载依赖的容器类型）；保留注入基建，旧码零改动。
   - **可变性约束**：过渡期 `_export_services(ns, locals())` 仍会 `setattr` 服务函数到该对象上、再被 `_sync` 分发，因此**过渡期 context 必须是 mutable、非 `slots`、非 `frozen` 的 dataclass**，否则旧注册机制直接断（`AttributeError`/`FrozenInstanceError`）。
   - 替代方案：引入 sidecar registry 承载过渡期函数，context 从一开始就纯依赖、可 frozen。（更干净但多一个对象，推荐仅在 context 想尽早 frozen 时采用。）
   - Phase F 函数上移后，可选给 context 加 `slots=True`/`frozen=True` 锁死不可变。
2. **两个隔离 contract test，用 `@unittest.expectedFailure`（非 skip）**：
   - (a) 同一 router 多请求**共享同一** lock/jobs 对象；
   - (b) 两个 router/context **互不污染**。
   - 现状会红 → `expectedFailure` 让它们"预期失败"通过；Phase F 达成隔离后**移除标记**，意外提前转绿也会被 `expectedFailure` 报"unexpected success"提示。
3. 修正规模数字；搭 AST 门禁脚手架（§6）。

### Phase R — route 层去全局（提前，低风险早收益）
- 12 个仍注入的 route 模块：删 `globals().update(vars(deps))`，handler 闭包捕获 `deps`（=sc），改 `deps.xxx`；**不碰 service 签名**。
- `asset_digital_human_routes.py` 已完成，作参照，无需改（**13 模块 / 12 注入点，差异保留在案**）。
- 每模块一次提交，contract 绿 + 零 diff。

### Phase S — service 模块逐个（机械桥 `_sc` + 上移嵌套函数）
按拓扑近似（叶子→核心，仅降单步 diff）。每模块：
- a. `register_X_services` 的 `globals().update(vars(ns))` → 绑单个 `_sc`。
- b. **上移嵌套函数为 module-level**；生成**显式 `SERVICE_EXPORTS = (...)` 白名单**（只列原 `locals()` 导出的服务函数名），`_export_services(ns, locals())` → 按 `SERVICE_EXPORTS` 白名单导出（跨模块过渡期仍可达）。**禁止用 `globals()` + `callable` 过滤上移后的 module-level 作用域**——那会把 imported 类/函数也扫进 `ns`、被 `_sync` 分发，造成命名污染/误覆盖。`asset_search_services._export_services(ns)` 的硬编码名单即此白名单形态样板。
- c. 函数依赖名（含跨模块函数）→ `_sc.xxx`；**所有 `globals().get(...)` → `_sc.xxx` 或 `getattr(_sc, name, None)`**。全库仅 4 处，注意其中 2 处是**动态函数查找**（不只 `ctx`）：`asset_search_services.py:276,298`（`ctx`）、`agent_chat_services.py:232`（动态查 `image_plan_artifact_status`）、`asset_video_generation_services.py:243`（动态查 `read_or_create_videos_agent_settings`）——门禁需覆盖 `globals().get(...)` 形态。
- d. **AST 门禁**（§6）断言本模块无残留注入名裸引用。
- e. contract 绿 + 纯重构零 diff。
- 注：此阶段末仍是模块全局 `_sc`，隔离未根治（中间态）。

### Phase F — 去 `_sc`，收口到显式 `sc` + 直接 import（**独立估算，全图 sweep**）
1. **只给 context-dependent 的 service 函数**加**尾部 keyword-only 过渡签名**：`def foo(task, payload, *, sc: StoryboardContext | None = None): sc = sc or _sc`（**sc 放尾部 keyword-only，绝不插到位置参数首位**，否则旧 `foo(task, payload)` 会把 task 错绑成 sc）。
   - **纯 helper 不加 sc**：不引用任何注入名的纯函数（如 `default_asset_search_settings()`、`normalize_asset_search_settings()`、`asset_search_provider_query_variants()`）保持原签名——加 sc 既无必要，也会破坏已有直接调它们的测试。
   - **由谁决定**：AST 门禁（§6）判定某函数体是否引用注入名，即决定它要不要 sc——机器判定，避免主观漏判/误加。
2. 跨模块调用改**直接 import 目标函数** + `sc=sc`；route 调用改 `sc=sc`。逐步传齐。
3. 全图传齐后：删默认值、删 `_sc`、删 `_export_services`/`_sync_service_globals`/所有 `globals().update()/.get()`。
4. **AST 门禁全库红线**：无注入名裸引用、无 `_sc`、无 `globals().update/get`、无 `__dict__.update`。
5. 迁移 12 个 contract 测试脚手架：构造 `StoryboardContext(fakes)` 传入。
6. Phase 0 两个隔离测试**移除 `expectedFailure` 标记**并转绿。

## 6. 安全网

- **每步**：全量 contract tests 绿（pre-push + CI）。
- **纯重构步**：关键路由请求/响应黄金样本零 diff。
- **逐模块 AST 门禁**（可执行项 `scripts/check_storyboard_context_migration.py`）：注入名集合 = `StoryboardContext` 字段 + `_export_services` 导出的函数名 + `{_sc}`（Phase F 起）。AST 遍历"已迁移清单"内模块，标记任何匹配注入名却非本地绑定（赋值/形参/import/闭包）的 `Name(Load)`，**并覆盖 `globals().get("<注入名>")` 动态形态**。未迁移模块豁免（避免 F821 全开炸包）；Phase F 后清单 = 全库红线。
  - **副产物驱动 sc 决策**：门禁对每个函数产出"是否引用任何注入名"的判定 → 直接决定该函数 Phase F 是否加 `*, sc`（引用→加；纯函数→不加），机器判定避免主观漏/误。
- **提交纪律**：纯重构与行为变化分开提交；纯重构标 `refactor(no-behavior-change)`。

## 7. 风险登记与缓解

| 风险 | 说明 | 缓解 |
|---|---|---|
| 面大、churn 高 | ~40 注入点 + 数百裸读取点 + 嵌套函数上移 | 逐模块、每步可验证、可停在任意绿色稳定态 |
| 裸名漏改（含跨模块函数、嵌套闭包） | import 不覆盖未执行分支；contract 不覆盖全路径 | 逐模块 AST 门禁；已迁移模块红线 |
| **嵌套函数上移丢函数** | 多数模块函数是 register 内闭包，删 `_export` 前须上移 | Phase S 显式含"上移为 module-level + `_export_services(ns)` 过渡"步；样板 `asset_digital_human_*` |
| 位置参数错位 | sc 插到首位会把旧 `foo(task,...)` 的 task 绑成 sc | **尾部 keyword-only** `*, sc=None`，调用方 `sc=sc` |
| 显式 sc 迁移非原子 | 跨模块 web | 过渡期 `sc or _sc` 回落，全图传齐后统一删默认值 |
| **上移 + 直接 import 触发循环 import** | 现在靠全局注入正是**绕开了模块间 import**；上移后直接 import 可能成环 | 断环优先级 **归位/依赖反转 > 抽公共 helper > 函数内 lazy import（兜底，加注释）**；CI 加轻量 import-cycle 检测；`asset_digital_human` 证明该模式在本库可行 |
| **lock/job 语义被无意改动** | 进程级单例负责互斥 | 行为红线 + 两个隔离 contract test 守护 |
| asyncio.Lock 生命周期 | "只按 router 生命周期构造一次，不被 request-dep/测试 helper 重建，不跨 loop 复用再竞争" | 隔离测试 (a)(b) 直接断言 |
| 安全网有洞 | 测试脚手架依赖注入；dance flaky 时绿时红 | 先隔离 flaky（独立 ticket，skip）；脚手架 Phase F 同步迁移 |
| 并发直推 main | main-only 多机 | 每步小、独立可提交；优先不活跃模块；pre-push 兜底 |

## 8. 工期与排序（粗估）

- Phase 0：0.5–1 天（类型化 + 两个 expectedFailure 隔离测试 + 数字更正 + AST 门禁脚手架）。
- Phase R：12 route 模块，每个 0.2–0.5 小时，低风险。
- Phase S：25 service 模块，每个 0.5–1.5 小时（**含嵌套函数上移**，大模块 `asset_search`/`asset_video_generation`/`agent_chat` 更久），含 AST 门禁 + 零 diff。
- **Phase F（单列）**：全图 keyword-only sweep + 跨模块直接 import（含断环）+ 删注入基建 + 12 测试脚手架迁移 + 隔离测试转绿——独立大步，估 2–4 天，风险集中于此（含潜在 import 环）。
- 总体：中等偏大；任意一步可停在绿色稳定态。

## 9. 回滚

每步独立提交，`git revert` 单步。Phase 0 兼容层 + Phase S/F 的 `sc or _sc` 默认值让新旧访问过渡期并存。

## 10. dance flaky（假设 + 独立 ticket）

**假设（非结论）**：dance_mimic 间歇 `IndexError` 可能由"共享可变全局在后台 worker + 请求并发竞态"引起；本重构**可能**顺带缓解，**不作 Step 1 承诺目标**。行动：拆独立 ticket；迁移前先 `skip` 该测试（挂 ticket 号）稳住安全网。

## 11. 建议的第一刀

Phase 0 后，先做 **Phase R 的 `clean_image_routes.py`**（小）走通"闭包捕获 deps → deps.xxx → contract 绿 → 零 diff → 提交"，固化 golden-diff/AST 脚本；再做 **Phase S 的 `value_services.py`**（叶子）走通"绑 _sc + 上移嵌套函数 + AST 门禁"，套路固化后批量推进。

---

**决策点状态**：五个决策点已于 2026-07-01 全部拍板，见 **§0 决策已定**。本方案可开工 Phase 0。
