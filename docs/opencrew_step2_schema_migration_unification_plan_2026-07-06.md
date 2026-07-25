# Step 2 实施方案(v4,决策已拍板):数据库 schema 迁移机制统一

- 日期:2026-07-06(v2:吸收同行评审 6 条 + 自查 4 条;v3:再吸收 4 条——identity 语义入对账、stamp 红线、快照 flags、锁实现约束;v4:再吸收 4 条——恢复口径统一 pg_restore、迁移事务超时、ON_ERROR_STOP、红线入完成判据;决策 D1-D5 不变)
- 范围:`backend/opcrew_backend/db/`(bootstrap.py 180 行 / migrations.py 257 行 / schema.py 570 行、31 张表)、启动链(`context.py:39`)、CI 门禁
- 依据:技术债报告 P0 第 3 项;本方案全部论断基于 2026-07-06 实测
- 前置:Step 0/Step 1 已完成——「单一事实源 + 对账门禁 + 机械迁移」工艺已在 Step 1 验证,本方案复用同一工艺
- **范围外**:宽任务表拆分(attempt/artifact/metadata 拆表,报告 P2 第 4 条)——那是 schema *内容* 演进,本步只统一 *机制*

## 0. 实测现状(与报告的差异)

报告称"三套机制并存"成立,但**自研 migration 系统比报告暗示的成熟**:

1. `migrations.py` 已有 `schema_migrations` 台账表(id/description/applied_at)、12 个编号 migration、已应用跳过、**单事务原子应用**——这是一个可用的版本链,只缺并发保护和回滚手段。
2. 三个 `ensure_*_columns`(4 张表 / **60 列**:openflow_analysis_runs 8、openclip_tasks 12、openclip_prompt_versions 14、oc_rebuild_tasks 26,AST 实数)是 migration 系统建立**之前**的遗留补丁;其全部列**已同时存在于 schema.py 的 Table 定义中**——对新库是死代码,只服务于史前老库。
3. 冗余实锤:`workflow_mode` 同时由 schema.py、migration_0012、`ensure_openclip_columns` 三处表达。
4. **缺 Postgres 级的生产演化路径对账**(v2 措辞修正):契约测试主要用 sqlite `metadata.create_all`;`test_migration_baseline_contract.py` 确实在 sqlite 上回放过 `run_migrations`,但 `initialize_database` 全径(含 ensure_*、pg_insert 种子)是 Postgres 专用、无测试覆盖——「create_all 结果 ≡ 生产演化结果」从未被断言。
5. **部署链没有显式迁移步骤**:`scripts/deploy/40_migrate_db.sh` 是生产→TEST 的克隆器(pg_dump + 路径重写),不是 schema 迁移。schema 演进 100% 依赖"第一个启动的新版本进程"在 `initialize_database` 里隐式完成——多机同时启动即并发 ALTER 竞态。
6. 新表历史上已走 migration(0005 建 local_usage_log)——"新结构走 migration"的实践已部分存在,本方案是把它变成唯一路径。

## 1. 决策已定(2026-07-06 拍板)

**D1 机制选型:保留并收敛自研系统,不引入 Alembic。**
理由:台账/编号/原子应用已存在且经生产验证;团队 main-only 无分支合并需求,Alembic 的核心增益(autogenerate、分支合并)用不上;downgrade 在本团队的现实回滚手段是 pg_dump 快照(40 号脚本已示范该工艺)。自研系统补齐两件事:**并发保护**(`pg_advisory_lock`)与**迁移前自动快照**。

**D2 `ensure_*_columns` 处置:折叠为 `0013_absorb_legacy_ensure_columns` 后删除。**
0013 内容 = 三个 ensure 函数的 **60 列**清单(实现时由脚本从 bootstrap.py AST 生成、与源码对账后写死,禁止手抄——本方案 v1 手数出 53 即是教训),用现成的 `add_column_if_missing` 表达(幂等)。已被 ensure 补过的老库跑 0013 是 no-op 但从此**入账**;新库由 create_all 覆盖。之后从 `initialize_database` 删除三行调用及三个函数。

**D3 启动行为:空库快路径 + 非空库单一迁移路径,非空库禁 create_all。**
- 空库(无 `schema_migrations` 表且无业务表):`metadata.create_all` + **全量 stamp**——新环境秒建。**stamp 红线(v3)**:快路径只允许跳过「空库上无副作用」的 migration;现有数据回填型 migration(如 0012 按 sessions join 回填 workflow_mode)在空库上恰为 no-op,故当前 stamp 安全,但**今后任何「空库也必须执行的数据初始化」禁止写进 migration**——必须放在种子逻辑(initialize_database 的 seed 段)或单独声明并测试,否则会被 stamp 永久跳过。此红线写入 migrations.py 顶部注释。
- 非空库:advisory lock(**专用连接持有、try/finally 释放、`lock_timeout` 有界等待**——严禁用连接池连接持锁,否则锁被提前归还或另一进程卡在快照阶段时,启动会退化为难排查的无限等待;固定 lock id) → 有待应用 migration 时先 `pg_dump -Fc`(custom 格式)快照到 `data_dir/backups/`(恢复用 `pg_restore --clean --if-exists`——plain SQL 无 --clean 在半迁移库上不可靠;保留最近 5 份;**快照失败即阻断迁移**——没有回滚手段就不动结构;快照仅在有待应用项时触发,运行环境须有 pg_dump 与凭据)→ `run_migrations`(**v4:迁移事务连接同样设置 `lock_timeout`/`statement_timeout`**——DDL 在 engine.begin() 单事务内执行,可能被表锁/索引锁无限阻塞;超时即事务回滚、快照保留、启动失败并报因)→ **preflight:断言 metadata 中所有表已存在**,缺表即硬失败并给出修复指引 → 释放锁。**不再调用 create_all**——新表若只写在 schema.py 而没写 migration,将在运行时响亮失败并被 D4 门禁在 CI 提前抓住。这是"单一事实源"的强制执行点。
- **兼容性下限(v2 明示)**:历史 migration 大多只补列、不建基础表,基础表历来靠 create_all——因此新路径的**支持下限 = baseline schema**。比 baseline 更老/残缺的库(缺基础表)不再被启动自动治愈:preflight 硬失败,修复途径 = 从 baseline_schema.sql 重放或人工补表。两台在役库均为当前结构,实际暴露面≈0,但此变化必须写进 runbook。
- 环境变量 `OPENCREW_MIGRATE=off` 可禁用启动自动迁移(供未来正式部署管道显式控制);默认 on,保留两台开发机直启的现有体验。
- 单例运行时行的种子写入(pg_insert on_conflict)保持在启动逻辑——那是数据播种不是 schema。

**D4 对账门禁(本方案的核心交付,Step 1 snapshot 思想的移植):**
- 交付 `backend/opcrew_backend/db/baseline_schema.sql`:从当前生产库 `pg_dump --schema-only` 采集、提交进 repo 的基线 DDL(顺带成为"不依赖 Python 也能重建库"的文档)。
- 新契约测试(Postgres-only):库 A = `create_all` + 全量 stamp;库 B = `psql -v ON_ERROR_STOP=1 -f baseline_schema.sql`(否则 psql 默认在错误后继续执行,测试失败原因会被冲散)→ **显式 stamp 基线涵盖的 0001-0013**(pg_dump --schema-only 不带台账数据行,不 stamp 则 CLI status/启动路径会误判未应用)→ 回放 0014+。
- **比较面(v2 收紧,防假绿)**:① 表/列/类型/可空性/默认值(information_schema),**含 identity 语义**——21 个主键用 `Identity()`,Postgres 的 identity 不等于 column_default,须显式比较 `is_identity`/`identity_generation` 及序列归属;只忽略自动生成的名字,**不忽略生成语义**;② **索引必须查 `pg_indexes`**——0010 建的是裸 UNIQUE INDEX,information_schema 根本看不到;③ 约束(PK/FK/UNIQUE/CHECK,pg_catalog);④ **两库 migration 台账一致性**。全部做**规范化比较**:按结构签名对齐,忽略自动生成的约束/索引/序列名(SQLAlchemy 与 pg_dump 命名规则不同,按名比较必假红)。
- 抓两个方向的漂移:只改 schema.py 忘写 migration(A 有 B 无)、migration 与 schema.py 不同步(B 有 A 无)。
- **运行环境(v2 消除矛盾)**:`CI=true` 时 Postgres 不可达 = **测试失败**——核心门禁不允许静默失效;仅本地开发环境无 Postgres 时允许 skip 并打印醒目警告。ci-gate backend job 加 `postgres:16` service container;本地默认复用 5433 开发实例建临时库(跑完即 drop)。

**D5 运维接口:提供 `python -m opcrew_backend.db.migrate` CLI**(`status` / `--dry-run` / `upgrade`),供手动操作、故障排查和未来部署管道使用;不新建部署脚本步骤(现阶段无正式管道,启动自动迁移 + advisory lock 已覆盖两机现实)。

## 2. 分阶段计划(每步 contract 绿,可停在任意稳定态)

- **S2-0 基线采集与演练(风险关键步)**:用 40 号脚本克隆生产→TEST 库;`pg_dump --schema-only --no-owner --no-privileges` 生成 baseline_schema.sql(去除属主/权限差异,避免 CI 与本机角色不同导致 restore 噪声);在 TEST 副本上演练「baseline 重放 + stamp + 对账」,确认 A/B 结构一致后才提交基线。若发现 create_all 与生产结构漂移,**逐项分诊**(v2 修正,不做单向对齐):合法遗漏 → 补进 schema.py;确认的历史垃圾(手工 DDL 残留)→ 记录在案、保留在基线中、视情况出清理 migration——不让「向生产对齐」把垃圾合法化。
- **S2-1 机制收敛**:migration 0013 折叠 ensure_*;重构 `initialize_database` 为 D3 行为(空库/非空库分路 + advisory lock + 快照);删除三个 ensure 函数。
- **S2-2 CLI**:`db/migrate.py` 入口(status/dry-run/upgrade),复用 run_migrations。
- **S2-3 对账门禁**:baseline_schema.sql 入库;新契约测试;ci-gate 加 postgres service。
- **S2-4 收尾**:部署 README 增补 runbook 段落(含快照位置、OPENCREW_MIGRATE 说明);技术债报告标注 P0 第 3 项完成。

工期估计:S2-0 半天(含演练),S2-1~S2-4 合计 1 天;总计 **1.5-2 天**。

## 3. 行为红线(纯机制重构不变式)

- 已应用过全部 migration 的库:启动后结构零变化(0013 对其为 no-op)。
- sqlite 契约测试路径(`metadata.create_all`)完全不受影响。
- 迁移执行语义不变:仍单事务、仍按序、仍记台账;仅新增锁与快照包裹。

## 4. 风险登记

| 风险 | 缓解 |
|---|---|
| baseline 采集时生产与 schema.py 已有隐性漂移 | S2-0 在 TEST 副本上先对账,漂移逐项分诊(合法补码/垃圾记录);基线只在对账通过后固化 |
| 非空库禁 create_all 后,漏写建表 migration | 正是 D4 门禁的抓捕对象;运行时也是响亮失败(表不存在)而非静默 |
| 两机同时启动并发迁移 | `pg_advisory_lock` 串行化;后到者取锁后发现已应用,直接跳过 |
| 迁移把库改坏 | 迁移前自动 pg_dump -Fc 快照(仅在有待应用项时),恢复 = `pg_restore --clean --if-exists`;快照保留 5 份;快照失败阻断迁移;迁移事务失败自动回滚且快照保留 |
| 比 baseline 更老的残缺库无法自愈 | preflight 硬失败 + runbook 修复指引;支持下限明示为 baseline(在役库均为当前结构,暴露面≈0)|
| 数据初始化型 migration 被空库 stamp 跳过 | stamp 红线:此类初始化禁入 migration,归种子逻辑;红线注释写在 migrations.py 顶部 |
| CI 无 Postgres | ci-gate 加 service container(GitHub Actions 标准用法);本地缺 Postgres 时 skip 有警告 |
| stamp 语义错误(空库快路径与回放不等价) | 等价性本身就是 D4 门禁的断言对象,每次 CI 验证 |

## 5. 完成判据

1. `initialize_database` 中 create_all / migrations / ensure_* 三径合一:空库快路径 + 非空库单一 migration 路径;`ensure_*` 函数删除。
2. 对账契约测试在 CI 常绿(A≡B)。
3. TEST 库演练记录:baseline 重放 + 新 migration 追加 + 快照恢复(pg_restore)各一次成功。
4. stamp 红线注释已写入 migrations.py 顶部(数据初始化禁入 migration)。
5. 技术债报告 P0 第 3 项可标注完成。
