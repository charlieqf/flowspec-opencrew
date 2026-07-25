# 05 · 文件工作区与产物（Workspace & Artifacts）

> 文件放哪、怎么在步骤间可靠交接、大文件怎么处理。核心立场 [00·P4](./00_Overview.md)：**文件/artifact 是一等公民**（采 Argo 模型），不是外挂在 JSON 传值上的补丁。

## A. 概念层

### A1. 为什么文件要一等公民

目标域（视频、报告、尽调文档、数据集）的产物**天然是文件**，而且**大**。把它们塞进变量/状态 JSON（Airflow XCom、SFN State I/O、BPMN 变量）会撞上体量限制、序列化开销、状态膨胀。规范的选择：**变量只放对文件的引用与元数据；文件本体活在 Workspace，靠 OutputManifest 交接。**

### A2. 三层目录归属

```
Workspace（属于一个 session）
  └── Run 目录（属于一次执行）
        ├── 共享上下文区（Context/Variables）
        ├── 共享输出区（跨步产物汇集）
        └── Step 私有目录 × N（每步独占，互不覆盖）
```

- **session → Workspace**：一个业务实例一个根目录。
- **Run**：一次执行一个子目录，隔离不同业务修订/重跑；单步技术重试再由其下的 Step Attempt 分区隔离。
- **Step 私有目录**：每步独占 `Working/Output/Report/Prompt`，杜绝并发覆盖。

#### A2.1 session、task、Run、Step 的物理归属

物理目录不能机械地为每个逻辑对象各复制一套文件。FlowSpec 的归属规则是：

| 逻辑对象 | 物理归属 | 是否复制文件本体 |
|---|---|---|
| session | 唯一 Workspace 根；本地 OpenCrew 当前由 `sessions.workspace_dir` 解析 | 是文件字节的顶层归属 |
| task | 业务数据库记录，引用同一个 session Workspace 与其 Run；**不另建平行 task 根目录** | 否，避免 session/task 1:1 下双份字节与清理竞态 |
| Run | `tool_use_sessions/<run_id>/`；冻结本次输入、定义、Step 状态与输出 | 只复制/物化本 Run 声明的输入快照 |
| Step | `S{index}_{tool}/`；由 Step ID 显式映射，index 仅帮助阅读 | 不默认复制上游输入；通过 Input/Output Manifest 引用真实字节 |
| Step Attempt | 在现有 `Working/Report/Prompt` 下做 Attempt 分区，输出先写 `Output/.staging/` | 只隔离本 Attempt 的临时物、诊断记录与待发布输出 |

因此，“某个 task 的文件在哪”应回答为：先由 task 找到配对 session，再读取 `sessions.workspace_dir`；而不是拼接一个未经登记的 `tasks/<task_id>/` 路径。“某个 Step 的输入在哪”则由本次输入快照与上游 Artifact locator 回答，不能假设所有输入都被复制到 Step 目录。

### A3. 步骤间文件交接 = OutputManifest 契约

步骤**不靠约定俗成的路径猜测**去拿上游文件。每个产出文件的步骤写一份 **OutputManifest**（"我产了哪些文件、叫什么名、在哪”）；下游步骤通过声明 `consumes` 上游的**产物名**、经编排器解析 Manifest 拿到真实路径。

好处：上游改内部目录结构不影响下游（只要产物名不变）；依赖检查可通过扫描 Manifest 判断"上游产物是否就绪"；产物可被审计与追溯。

#### A3.1 “存在”不等于“可消费”

目标 Runtime 对 `consumes` 的 ready 判定不是裸 `file_exists`，而是 Artifact validity：

```text
valid = manifest_entry_exists
    AND bytes_exist_and_nonzero
    AND checksum_matches
    AND declared_schema_matches
    AND producer Attempt/input snapshot matches
    AND (binding is not required OR declared binding key matches)
```

任一条件不满足分别得到 `missing/corrupt/stale/unbound`，只有 `valid` 才放行下游。`file_bound` 的有用语义由上述 binding/provenance 校验承载，不另造通用文件谓词 DSL；`marker_absent` 之类依赖文件“不存在”的条件具有竞态，只可作为特定实现细节，不能成为核心依赖模型。

#### A3.2 最小充分契约：只管 canonical Artifact

身份与哈希不是所有文件的普遍税。FlowSpec 只对**跨 Step 交接、跨 Run 复用或正式对外发布**的 canonical Artifact 施加以下契约：

| 信息 | 谁提供/计算 | 何时需要 | 是否在每个 Artifact 重复 |
|---|---|---|---|
| `artifact_id`（本次发布版本身份） | Runtime 生成 | 每次正式发布 | 是；新版本必须新 ID |
| `binding`（业务上“它是谁”） | 流程作者只声明必要 `binding_keys`，Runtime 取值 | 只有需要按业务对象 join/re-bind/跨 Run 对齐时 | 按 Artifact 保存；作用域是 `session/task + artifact contract`，不是全局“永不变” |
| `sha256 + size`（输出字节完整性） | Runtime 在 finalize/上传时流式计算 | 每个 canonical Artifact | 是；不要求 Tool 自己算，也不在每次读取时重算 |
| `input_snapshot_hash`（派生摘要） | Runtime 从声明的 `reads/consumes`、上游 Artifact hash、fanout item key 与冻结 Step/Tool/Profile 契约计算 | 每个生产 Attempt | **否**；只存一次在 producer Attempt，Artifact 以 `attempt_id` 引用；便携导出可选择内联 |
| Schema/media/classification | Artifact contract 声明，Runtime 校验 | 被消费或发布的正式产物 | 是 |

以下对象**不进入上述业务 Artifact 身份/派生契约**：`Working/` 临时文件、`Report/` 日志、`Prompt/` 证据、`Output/.staging/` 待发布字节、Checkpoint 内部状态和可重建 `SessionOutput` 投影。它们仍可因完整性、传输或证据包需要记录 checksum，但不强制业务 binding，也不能被当作可复用正式产物。

同一输入摘要不承诺 LLM/外部服务会产生相同输出；它只证明“这一版用了哪些声明输入与冻结执行契约”。能否复用还必须由显式 reuse policy、副作用类别、Schema/内容完整性与业务 binding 共同决定。

### A4. 大文件按引用，不按值

跨步传递大文件时，Context 里存的是**引用**（workspace 相对路径 / 内容哈希 / 外部存储 URI），不是文件内容。真正的字节留在 Workspace 或外部对象存储（S3/GCS/MinIO）。这与 Argo 的 artifact-repository 模型一致。

### A5. 相对路径与可搬迁

Workspace 内部一律用**相对路径**，但契约必须明确相对基准，不能只写一个含糊的 `path`。Artifact locator 使用 `workspace`、`run`、`step`、`step_attempt` 或 `external` 五种 base；Artifact 的默认 base 是 **Run root**，例如 `S4_risk_score/Output/CreditReport.json`，跨 Run/业务级引用才使用 `workspace` base，外部对象使用带版本的 URI。完整 StorageIndex 还可用 `run_bundle/database/platform` 登记证据包、数据库事实和平台日志，但这些不是 Artifact 文件路径。绝对路径只由部署层的 storage resolver 解析，不能进入 Process、Event、Manifest、API 或可搬迁证据包；解析结果必须仍位于声明根目录内。这样 Workspace 可整体搬迁、归档、复制到另一环境重放。

> **OpenCrew 现状精度**：通用 runner 的 `OutputFileEntry.path` 由 `result.result_paths` 归一后与 `self.paths.root` 拼接（`runner.py:_output_manifest_for_result`），因此当前清单路径实际相对 **Tool Use Session/Run root**，不是 session workspace 根。`result_sync` 在对外同步时才同时计算 session-root 与 workspace-root 相对路径。目标契约把 path base 显式化，避免两个“相对路径”语义混用。

> **部署约束 `[proposed]`**：持久 Workspace 必须位于由 storage resolver 登记的数据根，不能放在可能被镜像、替换或清理的源码/部署目录内。具体绝对根路径属于环境配置，不进入 vendor-neutral Process、Manifest 或本规范。

---

## B. 可实现绑定

### B1. 目录布局（OpenCrew 兼容基线）

（OpenCrew 参考：`paths.py:ensure_tool_session_layout` + `koubo_storyboard/constants.py`）

```
<workspace_dir>/                                      # 1 session；DB 的 workspace_dir 是解析入口
├── inbox/                                             # [recommended] 原始上传/外部输入，不可原地改写
├── SessionContext/                                    # [implemented/domain] session 级归一输入与变量
├── SessionOutput/                                     # [implemented/domain] session-latest 对外投影；不是产物权威源
├── SessionReport/                                     # [implemented/domain] 领域运行报告/兼容日志
├── tool_use_sessions/<run_id>/                        # [implemented] 一次 Tool Session / Run
│   ├── 0_SessionContext/                              # [implemented] 本 Run 冻结输入
│   │   ├── Variables.json
│   │   ├── InputManifest.json
│   │   └── <declared input copies...>
│   ├── S{index}_{tool}/                               # [implemented] 1 Step 私有根
│   │   ├── State.json
│   │   ├── Working/
│   │   │   └── Attempts/A{no}_{attempt_id}/           # [proposed/additive] 临时工作区，可清理
│   │   ├── Report/
│   │   │   └── Attempts/A{no}_{attempt_id}/           # [proposed/additive] 诊断日志与错误
│   │   │       ├── diagnostic.ndjson
│   │   │       ├── stdout.log
│   │   │       ├── stderr.log
│   │   │       └── error.json
│   │   ├── Prompt/
│   │   │   └── Attempts/A{no}_{attempt_id}/           # [proposed/additive] 实际 prompt / tool transcript
│   │   └── Output/                                    # canonical 发布位置保持不变
│   │       ├── .staging/A{no}_{attempt_id}/           # [proposed/additive] finalize 前隔离
│   │       ├── OutputManifest.json
│   │       └── <published artifacts...>
│   ├── SessionReport/                                 # [implemented] Run 汇总；目标补 Runtime.ndjson/索引
│   │   └── SessionRunSummary.json
│   └── SessionOutput/                                 # [implemented] 对外汇集/兼容投影
│       └── {manifests,media,reports,json,...}/
└── SessionScratch/                                    # session 级可清临时区，不得放唯一副本
```

这是一项**增量约定，不是目录迁移**：`0_SessionContext`、Step 根和四个既有子目录、`OutputManifest.json`、`SessionReport`、`SessionOutput` 均保持原位；旧 Tool 仍可运行。支持并发/崩溃恢复的新 Adapter 才使用 `Attempts/` 与 `.staging/`。不得为了“目录更整齐”把既有 Step Artifact 集中搬到新的 `<run>/artifacts/`，否则会破坏 OpenCrew 的升级兼容性。

> **本地 OpenCrew 现状 `[implemented]`**：默认 Workspace 是 `$OPENCREW_DATA_DIR/sessions/<session_ref>/workspace`，`OPENCREW_DATA_DIR` 默认 `~/.opencrew`；真正入口是数据库 `sessions.workspace_dir`，不能在业务代码中重复硬编码该默认值。通用 prepare 已把声明输入复制到 `<run>/0_SessionContext/` 并写 `InputManifest.json`；`session_files` 只索引 workspace 相对路径与元数据，文件字节仍在磁盘。

### B1.1 输入与输出的定位规则

| 对象 | 规范位置/解析方式 | 权威性与约束 |
|---|---|---|
| session 原始输入 | 优先 `<workspace>/inbox/<safe-name>`；历史/领域路径可由 `session_files.path` 兼容 | 原始字节不可原地覆盖；修订产生新路径/哈希 |
| Run 输入 | `<run>/0_SessionContext/InputManifest.json` + 其中的声明副本 | 本 Run 的冻结输入快照；恢复与重放以清单/hash 为准 |
| Step 输入 | Run 输入清单 + 上游 `OutputManifest` 解析出的 locator | 默认**引用而不复制**；若 Adapter 需本地 staging，只能放本 Attempt 的 `Working/`，且 staging 副本不是权威 |
| Step 中间过程 | `<step>/Working/Attempts/A.../` | 可清理、不可被下游直接消费、不得是唯一业务产物 |
| Step 诊断与 prompt | `<step>/Report/Attempts/A.../`、`<step>/Prompt/Attempts/A.../` | 按可见性/敏感度授权和保留；不等于业务 Artifact |
| Step 待发布输出 | `<step>/Output/.staging/A.../` | 仅当前 Attempt 可写；失败、过期或失去 fencing 的 Attempt 不得发布 |
| Step 正式输出 | `<step>/Output/OutputManifest.json` + Artifact 字节，或清单中的外部 URI | finalize 后不可变；这是跨步消费权威 |
| Run/session 对外输出 | Run 内或 workspace 根的 `SessionOutput/` | 面向用户的目录投影/目录册；必须反向指到 canonical Artifact，不可成为第二权威源 |
| task 输入/输出 | task DB 记录引用 session、Run、Artifact ID | 不复制文件，不设独立 task 文件根 |

Step 目录名中的 index 不是身份。Run 快照/State 必须保留 `step_id → physical_dir` 映射；重排展示顺序后，消费者仍按 Step ID 与 Manifest 找产物。

### B1.2 路径引用最小信封 `[proposed]`

```jsonc
{
  "base": "run",                         // workspace | run | step | step_attempt | external
  "path": "S4_risk_score/Output/CreditReport.json",
  "storage_root_id": "opencrew-primary", // 部署层解析；可搬迁契约不含绝对路径
  "sha256": "sha256:...",
  "size": 18244
}
// external 时使用 {"base":"external","uri":"s3://...","version_id":"...",...}
```

这个最小信封定义的是 **Artifact/File locator**，所以 base 只有 `workspace|run|step|step_attempt|external`。完整 [`StorageIndex`](./schema/proposed/StorageIndex.schema.json) 是位置与权威索引，另允许 `run_bundle`（便携证据包）、`database`（表/记录事实）和 `platform`（Workspace 外的服务/进程日志 sink）；三者不能写入 Artifact 的 `path_base`。

路径进入 resolver 前必须拒绝绝对路径、`..`、NUL、根目录逃逸与未经允许的 symlink；下载/API 返回相对 locator 或 Artifact ID，不能泄露服务器绝对路径。

### B2. OutputManifest Schema `[implemented]`（真实模型 `schemas/models.py:78`）

> ⚠️ 真实模型是 **`files[]`**，键为 `path/kind/schema_name/sha256/size/visibility/sensitivity/downloadable`——**没有** `name/media_type/role`。v0.1 的 `artifacts[{name,role}]` 是臆造，已更正。下游 `consumes` 按**上游 tool 有 completed manifest** 或**指定 path 存在**解析（见 B3），**不按 `name`**。

```jsonc
// <run>/S{index}_{tool}/Output/OutputManifest.json
{
  "schema_version": "1.0",
  "tool_use_session_id": "tus_...",
  "step_id": "S2_credit_pull",
  "tool_id": "credit_bureau_pull",
  "status": "completed",
  "files": [
    {
      "path": "S2_credit_pull/Output/CreditReport.json", // 当前：相对 Tool Use Session/Run root
      "kind": "artifact",                                // artifact | ...
      "schema_name": "",                                 // 结构化产物的 schema 名
      "sha256": "…", "size": 18244,
      "visibility": "internal", "sensitivity": "normal", "downloadable": 0
    }
  ]
}
```
> `[proposed]` 目标扩展：为 canonical Artifact 的跨步"按名索取"引入稳定 `name`、`role/media_type`、`artifact_id`、producer Attempt 引用、按需 `binding_keys`、**显式 path base** 与 provenance 语义（见 [07·3.B](./07_ImplementationBinding.md)）。Run 级 `input_revision_hash` 不复制到每条 Artifact；更精确的派生摘要只存于 producer Attempt 的 `input_snapshot_hash`。已发布 Manifest 应视为不可变；重生成发布新 Manifest/Artifact 版本，而不是原地修改并递增 revision。`created_at/writer_id` 用于 provenance，不能替代内容哈希或并发控制。

### B3. 消费上游产物（依赖解析）

**真实行为 `[implemented]`**（`runner.py:546-562`）：`consumes_outputs` 每项要么给 `path`（检查文件存在），要么给 `tool_id`（检查该上游 tool **有 completed 的 OutputManifest**）。**不按产物 `name`、不校验哈希**。缺失 → `DependencyCheckResult(status="blocked")`（**无 `waiting`**）。

```python
# [implemented] 真实解析
for item in tool["consumes_outputs"]:
    if item.get("path"):
        ok = (root / item["path"]).exists()
    else:
        ok = has_completed_output_manifest(item["tool_id"])   # 只看"上游有完成清单"
    if not ok:
        missing.append(MissingDependency(kind="tool_output", ...))   # → blocked（非 waiting）
```
> `[proposed]` 目标：按产物 `name` + `sha256` + producer Attempt 的输入 provenance 精确解析；只有 Artifact contract 声明了 `binding_keys` 时才校验 binding。缺失必需 binding 返回 `artifact_validity=unbound`/`needs_repair`；未声明 binding 的一次性正式产物不因此失败。依赖未就绪进入 `waiting(artifact)`。

### B4. 大文件/外部存储引用

```jsonc
// 变量里只存引用（A4）
{ "source_video_ref": {
    "kind": "workspace", "path": "SessionOutput/media/source.mp4",
    "sha256": "…", "bytes": 734003200 } }
// 或外部对象存储
{ "dataset_ref": { "kind": "s3", "uri": "s3://bucket/loan_00123/txns.parquet",
    "sha256": "…" } }
```

### B5. 产物生命周期与清理

- **保留策略 `[proposed]`**：产物 `role` 语义（`primary/sidecar/report`）本身是 [proposed]（现行 OutputManifest 只有 `kind`，见 [B2](#b2-outputmanifest-schema-implemented真实模型-schemasmodelspy78)）；"primary 随 Run 归档、Working 可清、Report/Prompt 保留"是目标策略，无统一 file:line 支撑。
- **版本与覆盖 `[implemented, 媒体库范围]`**：覆盖已有媒体产物须变更内容哈希/版本标识——见媒体库 `?v={content_sha256}`（`media_library_upload/service.py:257,261`）。**这是媒体库的实现约束，非通用 runner 全局保证**。
- **幂等重跑 `[部分实现]`**：force-rerun 前物理重置本步目录（`runner.py:_reset_tool_dir`，[09·L4.5]）；"原子改名"仅口播 `io_utils`（[09·L3.1]），通用 runner 仍 `write_text`。
- **Attempt 隔离 `[proposed/additive]`**：同一 Step 的不同 Attempt 不应并发写同一最终路径。在四个现有子目录内使用 `Attempts/A{no}_{attempt_id}/`，待发布字节写 `Output/.staging/A.../`；只有 finalize 获得当前 fencing/revision 后才原子发布到既有 `Output/`。失败/过期 Attempt 的迟到结果必须被拒绝。该约定不改变旧 Tool 看到的 canonical `Output/`。
- **清理次序**：先依据 Manifest/Storage Index 判定是否仍被 Run 修订链、审计保留或用户下载引用，再清 `Working`/`.staging`，最后按 retention policy 清诊断日志；不得通过扫描“看起来像临时目录”的名字直接删除。
- **投影可重建**：`SessionOutput`/媒体库索引应能由 canonical Manifest 重建；删除投影不能删除仍受保留策略保护的 Artifact 本体。

### B6. 检查清单

- [ ] 每个 session 一个 Workspace 根、每次 Run 一个子目录、每步一个私有目录？
- [ ] 步骤只往自己私有 `Output/` 写、并登记 OutputManifest？
- [ ] 下游靠**产物名 + Manifest** 拿文件，而非硬编码路径？
- [ ] 下游是否只消费 `artifact_validity=valid`，并区分 missing/stale/unbound/corrupt？
- [ ] 身份/hash 契约是否只施加于 canonical Artifact，而没有把 Working、日志、Prompt、staging 或投影升级成业务产物？
- [ ] Tool 是否只声明 output/binding；Runtime 是否在 finalize 自动生成 Artifact ID、内容 hash，并在 producer Attempt 只保存一次输入摘要？
- [ ] `binding_keys` 是否确有 join/re-bind/跨 Run 对齐用途，且作用域写清，而不是给每个临时文件强造全局 ID？
- [ ] 大文件按引用进变量，字节留 Workspace/对象存储？
- [ ] Workspace 内部全用相对路径，且每个 path 明确相对 Run root、workspace root 还是外部 URI？
- [ ] task 是否只引用配对 session Workspace，而没有制造第二套 task 文件树？
- [ ] Run 输入是否冻结在 `0_SessionContext/InputManifest.json`；Step 输入是否靠 locator 解析而非无意义复制？
- [ ] 不同 Step Attempt 是否隔离写入，并只允许持有当前 fencing/revision 的 Attempt 发布 canonical Artifact？
- [ ] `SessionOutput` 是否只是可重建投影，并能反向关联 canonical Artifact/Run/Attempt？
- [ ] 覆盖产物时 bump 了版本/哈希？临时物有清理策略？

下一步：[06 · 运行时与可观测性](./06_Runtime_Observability.md)。
