# STORYBOARD_WORKING_EXECUTION_STATUS_MARKER 需求评审

> 评审对象:`docs/STORYBOARD_WORKING_EXECUTION_STATUS_MARKER_REQUIREMENTS.md`
> 评审日期:2026-06-22
> 评审重点:逻辑断点、页面操作阻碍、用户流程死胡同、与现有前后端实现的衔接风险

整体结论:大方向自洽(以 Working 一级文件为唯一事实源、文件名承载热路径、JSON 承载详情),能解决刷新后状态丢失、不同页面颜色不一致、执行 JSON 与业务文件割裂的问题。但若按现文档直接实现,会在颜色合成核心算法、并发执行、与旧 `execution_state` 衔接、跨段/合并场景上卡住。典型死胡同:

1. 页面状态已恢复可重试,但按钮仍被旧 `execution_state` 全局锁死,用户点不动。
2. Final 视频绿色,但 TailFrame 缺失,下一段无法继承尾帧,且没有补救入口。
3. 同一 `{anchor}_Video_Final.mp4` 被多个 stage 产出,不同 Plan 对同一槽位显示不同颜色;并发执行还会互相覆盖。
4. 删除/替换素材后,后台旧任务仍写回旧产物,让被删素材"复活"。
5. stale 恢复挂在状态刷新接口里,页面轮询触发外部 API 查询和文件写入。

---

## P0 — 必须先解决的断点

### 1. 旧 `execution_state` 仍可能把页面锁死
- §15 要求前端不再按各 Plan 的 execution state 分别染色,只显示后端合成的 `artifact_status.slot_states`。但现有前端按钮大量依赖全局 `executionRunning()` 禁用;后端部分执行入口也只要旧 execution state 是 `queued/running` 就返回 already_running。
- 死胡同链路:后端 Working marker 已把 stale running 转 Failed → 槽位红色、理应可重试 → 但 `*_execution_state.json` 仍是 running → 前端按钮 disabled 或后端拒绝 → 用户只能刷新/重开/手工清文件。
- **建议**:
  1. Working marker 成为槽位事实源后,旧 execution state 只作 job summary。
  2. 后端执行入口判断 running 必须结合本地 job registry + Working marker,不能只看 execution_state。
  3. stale marker 转 Failed 时,同步把对应 execution state 降级为 failed/stale。
  4. 前端按钮禁用改为 stage/slot 级,而非整 Plan 全局锁死。

### 2. 一个业务文件被 4 个 stage 写,slot → stage 映射没定义
- §5 明说 `Video_Final_Copy / LipSync / AudioMix / Video_Final` 都写 `{anchor}_Video_Final.mp4`,但 §9 颜色合成仍按单个 `{anchor}_{stage}` 匹配。现有 UI 又有不同语义(Video Only Plan 的 `copy_final`、Video Plan 的 `sync/final_video`、StoryBoard 的最终视频)。
- 影响:Video Only Plan 的"拷贝成终视频"可能红,Video Plan 的"终视频"仍白/绿;对嘴型失败污染拷贝槽位,或拷贝成功掩盖对嘴型失败;用户不知该在哪个 Plan 重试。
- **建议**:
  1. 明确 UI slot → stage group 映射表:
     - `copy_final` → `Video_Final_Copy`
     - `final_video` → `Video_Final_Copy | Video_Final_LipSync | Video_Final_AudioMix | Video_Final`
     - `tail_frame` → `TailFrame`
  2. 同一业务文件多 stage 的优先级:当前执行 stage 的 Running 优先 → 其次当前失败 stage → 最后业务文件绿色。
  3. 最终槽位已绿但某执行方式失败时,在详情展示历史失败,不应染红最终槽位。
  4. 共享同一业务输出路径的多个 stage 之间存在并发互斥需求,见 #5 的 output 级锁。

### 3. 热路径"只看文件名"判不了绿/黄/红优先级,且 current signature 本身就不轻量
- §6.1 声称高频路径只扫文件名、查业务文件、读文件名里的 `Running/Failed/sig`。但 §9.5/9.7、§8.4.6 的颜色规则需要判断"Running 是否在覆盖既有文件""Failed attempt 是否晚于当前绑定""业务文件是否优先绿"——这些需要 `failed_at`、binding 时间、`overwrites_existing_output`(JSON line 207),**都不在文件名上**。同输入重跑时 `signature12` 可能不变,文件名只有 `state + sig + uid`,无法只靠文件名分辨"旧绿优先"还是"新重跑黄/红优先"。
- **额外断点**:§9.3 要"计算当前 stage 的 signature",而 §7 输入含 `input_file_fingerprints`。若 fingerprint 是内容 hash,每次刷新就要对所有 stage 的所有输入做 sha256 —— current signature 这一步本身就不是轻操作,§6.1 与验收标准 15 站不住。
- 影响:重跑后已有文件继续绿、看不到正在重跑;重跑失败但旧文件还在则继续绿,用户不知新 attempt 失败;反之旧失败错误覆盖已完成文件。
- **建议**:
  1. 文件名热路径只用于"是否存在候选 marker"的初筛。
  2. 业务文件与当前 signature marker 并存时,必须允许读当前匹配 marker JSON。
  3. marker JSON 增加可比较字段:`started_at`、`failed_at`、`completed_at`、`attempt_sequence`、`output_binding_fingerprint_at_start`、`binding_revision_at_start`。但这些只描述 attempt **自己**,不足以判"当前业务文件/绑定是否比失败更新"。
  4. **业务侧必须提供权威 revision,且要区分文件版本与绑定版本**(发布文件 ≠ StoryBoard 绑定,例如 Final 文件存在但未绑定、TailFrame 无 StoryBoard binding):
     - `asset_revision`:业务文件内容版本。
     - `binding_revision`:StoryBoard 指向/绑定版本。
     - `slot_revision`(composite):颜色合成用的综合版本,由前两者派生。
     §9.7 红绿优先级**比 revision,不比 mtime/时间戳**:`marker.binding_revision_at_start` 与当前 slot 的 revision 比较,失败 attempt 基于旧 revision 则不染红已更新的绿。
     - **TailFrame 通常没有 StoryBoard binding,需单独定义派生规则**:`TailFrame.slot_revision = TailFrame.asset_revision + 来源 Final 的 (asset_revision, binding_revision)`。这样下游跨段继承(#10)有明确依据——来源 Final 内容或绑定一变,TailFrame 的 slot_revision 即变,继承它的下游 `Image_New` signature 随之失效。
  5. `input_file_fingerprints` 不要用裸 `mtime + size`(覆盖可能保留旧 mtime,不同内容同尺寸会撞)。优先级:
     - 首选**显式 revision**——Working 文件 / asset binding 维护 revision 计数,signature 用 revision,最稳且不依赖文件系统 stat 语义。
     - fs 兜底用 `size + mtime_ns + ctime_ns`(本文档全程 tmp + 原子 rename 发布,每次发布 ctime_ns 必变,可作"是否变过"信号;但 inode 每次发布都变,不能当稳定标识)。
     - 内容 hash 只在写 marker 时算一次或异步计算并缓存,绝不在页面刷新现算。
  6. **revision 必须有唯一写入口 + manifest 基线**:所有 Working 业务文件写入/覆盖/绑定变更(上传、手工替换、历史恢复、TailFrame 物化、执行器发布)必须走统一 publish/bind 服务并自增 revision。统一服务写一份 `WorkingAssetManifest.json`(或等价索引),记录上次发布的 `size / mtime_ns / ctime_ns / hash / ref / asset_revision / binding_revision`,作为旁路检测的基线。fs 层无法硬禁旁路写,故扫描时若检测到"文件与 manifest 基线不符但 revision 未动",**标记 `revision_unknown / stale_binding`,不要静默自增成可信绿**;由用户或修复流程确认后再纳入可信 revision。

### 4. 清理 marker ≠ 取消在飞的外部任务 → 孤儿产出污染绿色
- §8.6 删除/替换素材时清理下游 Running/Failed marker;§8.1.1 又强调外部任务已创建并记在 marker 里。
- 死胡同:用户在 `Video_Raw` 运行中替换 `Image_New` → §8.6.4 清掉 Video_Raw 的 Running marker → 但外部任务仍在跑,完成后把旧输入的 `Video_Raw.mp4` 写进 Working → 无 marker + 业务文件存在 → §9 直接判绿并进素材池。被删链路"复活",用户无从察觉。
- **建议**:
  1. 删除/替换素材时,相关 Running marker 进入 `canceled_by_asset_change`;有本地 job 尝试取消,外部 API 支持取消则记录并调用。
  2. **所有执行器发布业务文件前必须重新读取当前 Working marker,确认 `marker_uid + signature` 仍是当前 attempt**;若 marker 已清理或 signature 已变,不得发布,只能归档为 obsolete/canceled。
  3. 这条同时是 §9"旧失败不污染新输入"在"运行中替换"场景下成立的前提。

### 5. 多 Session / 多 Plan 并发点击同一 stage,无执行锁
- §6 rule 2 规定"同一 `anchor+stage` 同一时刻最多一个当前 Running",但 §8.1 没有任何获取锁步骤,直接"算 uid → 写 tmp → 改名"。
- 两个浏览器(或从 Video Plan 与 Video Only Plan)同时点执行:各生成不同 `marker_uid` → 两个 Running 并存(违反 rule 2),且两次都打外部 API → **重复任务、重复计费、同一输出被并发写坏**。§6.1.3 的"事后按 started_at 清理"无法挽回已发出的 API 调用。严重度不低于 #4,故列 P0。
- **锁机制必须正确**(这三点是关键,做错等于没防):
  1. **不能用 `*_Running_*.json` 的 O_EXCL 当锁**——Running 文件名带随机 `marker_uid`,两个并发执行创建的是两个不同文件名,O_EXCL 永远不碰撞。
  2. **锁的粒度是 output group,不是单个 output path,更不是 `anchor+stage`**——`Video_Final_Copy / LipSync / AudioMix` 是不同 stage,却同时写 `{anchor}_Video_Final.mp4` **和** `{anchor}_TailFrame.png`,而独立的 TailFrame stage 也碰同一张 `TailFrame.png`。只锁单 path,Final 路径与 TailFrame 修复动作仍会互相覆盖。
  3. **锁不能是进程级 flock**——flock 随持锁进程消亡而释放,但外部 API 任务此时可能仍在跑,锁一释放第二个执行就进来,又造成双提交。锁的生命周期必须与外部任务绑定,而非与进程绑定。
- **建议**:
  1. 用**确定性的 output group 锁**,key 由 stage 声明的输出集合定义,例如 `final_bundle = {Video_Final, TailFrame}`;共享任一业务输出路径的 stage(含独立 TailFrame stage)走同一把锁。
  2. 锁必须是**持久 lease 文件**,内容含 `state / marker_uid / job_id / stage / output_slot / heartbeat_at / external_task_ids`;获取时与对应 Running marker 做 **CAS** 校验防止 stale lease 抢占;lease 靠 `heartbeat_at + TTL` 判活,而不是靠"持锁进程是否存活"。flock 只用作同机同进程的临界区辅助。
  3. **lease 与 Running marker 的创建必须写成两阶段协议 + 显式 state 机**,固定顺序消除歧义,并覆盖"lease 建了但 marker 没建"的中间态:
     1. 先生成 `marker_uid / attempt_id`。
     2. 原子创建 output group lease,`state=initializing`,lease 内写入该 `marker_uid`。
     3. 再创建同 `marker_uid` 的 Running marker;成功后把 lease 置 `state=running`。
     4. Running marker 创建失败则回滚释放 lease。
     5. **崩溃在第 2 与第 3 步之间**会留下无 marker 的 `initializing` lease:回收规则——`state=initializing` 且超 TTL 且 `external_task_ids` 为空时,可直接释放(尚未提交外部任务,无孤儿风险)。
     6. lease state 取值 `initializing | running | recovering | failed`;后续心跳、外部 task 更新、业务发布前都必须校验 lease 与 marker 的 `marker_uid` 一致(CAS),不一致即放弃本次操作。
  4. **外部 API 提交要堵住"已提交未落盘"孤儿窗口**:若进程在"provider 已接受任务"与"本地已把 `external_task_id` 写入 marker/lease"之间崩溃,会留下不可恢复的孤儿任务。约定:
     1. 外部请求必须带 `marker_uid / attempt_id` 作为 idempotency / client request id。
     2. 拿到 `external_task_id` 后更新 marker + lease。**注意两个 JSON 文件的"原子更新"不是真原子**,必有单边成功的中间态:约定 **lease 为锁主记录、marker 为展示记录**,两者不一致时按 `marker_uid / attempt_id / updated_at` reconcile(以 lease 为准),或引入小型 write-ahead event 先写意图再落两文件。
     3. provider 支持按 request id 查询时,stale recovery 用 `marker_uid` 反查上游,即使本地未落盘也能找回任务。
     4. **不支持 idempotent client request id / 不支持按 request id 查询的 provider 必须降级**:提交前把 lease 标 `non_recoverable_submit_window`;若提交后 `external_task_id` 未落盘即崩溃,该窗口只能转**人工核对 / 费用审计**,系统不承诺自动恢复,也不得在该窗口直接放新任务进来。
  5. 在 §8.1「创建 Running marker / 调用外部 API」**之前**获取 lease;获取失败说明已有执行在跑,拒绝本次执行并提示"该 stage 正在运行",而不是再建一个 Running。
  6. **锁回收边界要硬**:lease 过期 ≠ 可直接释放。若 `external_task_ids` 对应上游任务仍在运行,**不能**释放锁让新任务开始,应由 stale recovery(#7)接管 lease、维持黄态或补拉结果;**只有确认上游 failed / canceled / not_found / 不可恢复,才释放 output lock 并转 Failed**。
  7. 这是验收标准 5(多 Session 一致)与 #2 多 stage 共写同一文件的共同前提。lease 生命周期与 recovery(#7)是同一套状态机。

---

## P1 — 页面操作阻碍 / 死胡同

### 6. TailFrame 是下游凭证,但没有形成独立可操作的状态闭环
- §10.5 要求 Final 存在但 TailFrame 缺失时,`TailFrame` stage 显示白色可执行或红色失败。但现有状态模型基础 slot input 没有 tail 字段,Video Only 的确认 Final 主要看 `final_bound`。
- 死胡同:当前段 Final 完成 → 下一段需继承上一段尾帧 → `{anchor}_TailFrame.png` 不存在 → 下一段卡住,用户不知要回上一段补尾帧,且可能没有补救按钮。
- **建议**:
  1. 把 `TailFrame` 加入所有相关 `slot_states`。
  2. Final 绿色判定区分 `final_video_file_green` / `tail_frame_green` / `downstream_consumable_green`;只有 Final + 绑定 + TailFrame 三者有效才算可继承。
  3. 文档明确"TailFrame 是可独立触发的 stage",UI 必须提供"提取尾帧 / 重新确认 Final"等修复动作,而不只规定颜色。

### 7. stale recovery 不应直接挂在高频状态查询里
- §11.4 stale 时若上游成功就"尝试下载/绑定输出"。而触发 stale 扫描的最自然时机就是生成 Plan payload(§9),与 §8"生成/刷新 Plan 轻量"冲突。
- 副作用:页面轮询触发外部 API 查询;多浏览器并发恢复同一任务;状态接口变成长耗时写接口;用户只是打开页面就触发下载/覆盖/归档。
- **建议**:
  1. 状态接口只做轻量判断,返回 `stale_recovery_needed` / `recoverable_external_task`。
  2. 恢复动作由带锁后台任务执行,或由用户点击"恢复任务 / 标记失败"触发。
  3. 恢复必须**接管 #5 的 output lease**(而非新建一把锁):lease 过期但 `external_task_ids` 仍在跑时,recovery 接管 lease、维持黄态或补拉结果;只有上游确认 failed/canceled/not_found 才释放 lease 并转 Failed。lease 生命周期与本节是同一套状态机。
  4. 恢复写文件前再次校验当前 `step_signature` 和 StoryBoard 绑定,并经统一 publish 服务自增 revision(见 #3)。

### 8. Segment 合并后旧 anchor 文件变孤儿,且 anchor schema 未承载 aliases
- §4.2.3 要求合并时记录被合并 anchors 为 aliases,但 §4.2 示例 `StoryBoardSegmentAnchors.json` 没有 `aliases` / `retired_anchors` / history 字段。§9.1 合成只解析出一个 `segment_anchor` 去扫文件,旧前缀(如 `srt_0001_02_*`)的业务文件与 marker 全部扫不到。
- 影响:合并后已完成素材凭空"消失";运行中黄色丢失;外部任务恢复时找不到新 Segment;拆分/合并后状态可能串到错误 Segment。
- **建议**:
  1. schema 增加 `aliases`、`retired_anchor_of`、`superseded_by`、`anchor_history`。
  2. 合并时定义旧 marker 迁移策略(保留历史 / 迁移热路径 / 仅详情追溯);要么物理重命名旧前缀文件,要么 §9.1 合成按 anchor + aliases 一起扫描。
  3. 拆分时定义非代表 Dialogue 是否继承任何业务文件或 marker。
  4. 测试覆盖合并后旧 anchor 的 Failed/Running 不污染新 Segment。

### 9. marker 文件名解析规则不够严格(多下划线 stage)
- 文件名格式 `{anchor}_{stage}_{Running/Failed}_{signature12}_{marker_uid}.json`,但 anchor 和 stage 都含 `_`,如 `srt_0001_01_Video_Final_Copy_Running_a1b2c3d4e5f6_mk9t2d7e.json`。简单按 `_` split 会错拆 anchor 和 stage。
- **建议**:
  1. 从右向左解析 `uid`、`signature12`、`state`。
  2. 剩余部分用 stage 枚举做最长后缀匹配,匹配后前缀才是 `segment_anchor`。
  3. 非法文件名忽略,不参与颜色合成。
  4. 新增契约测试覆盖 `Video_Final_Copy`、`Video_Raw_TailFrame`、`SegmentAudio_Final` 等多下划线 stage。

### 10. 跨 Segment 尾帧拷贝的 signature 未纳入上游指纹(跨段继承正确性)
- §7.2 `Image_New` 输入只写本 anchor 的"原图或尾帧来源"。当 Image_New 从上游 Segment 的 `TailFrame` 拷来时(§1 链路"尾帧拷贝新图"、§10.6 跨段继承),**上游尾帧重生成后本段 signature 不变 → 本段一直绿、实际内容已过期** → 用户拿着错素材继续往下做甚至导出。
- 这不是规则空洞,而是跨段继承的核心正确性 bug;跨段继承是 Video Plan 的核心能力,故列 P1 并入第一批签名修订。
- **建议**:`Image_New` 的 `step_signature` 必须纳入上游来源文件(TailFrame)的指纹 / revision;上游尾帧变化时本段失绿、可重新执行。

### 11. "业务文件存在但未绑定 / 绑定不一致" 没有定义状态(高频可操作态)
- §9.5 绿色要求绑定一致,但 §9.2 算 base 白/绿/灰时,"文件在、绑定不一致"算什么没说,§9.9 兜底"保持 base"而 base 本身未定义该情况,颜色不确定。
- 这不是边缘规则空洞:Final 已生成但尚未确认绑定是常见可操作态(配合 #3 的 `asset_revision` 有、`binding_revision` 缺),直接影响用户下一步,故列 P1。
- **建议**:slot state 明确一个 `file_exists_unbound`(白色 pending)状态——`asset_revision` 存在但 `binding_revision` 缺失或不一致时,显示为可确认/可绑定 pending,而不是误判绿色或退回空白;UI 提供"确认绑定 / 重新确认 Final"动作。

---

## P2 — 待补的规则空洞

12. **§8.1.4 + §8.7.10 冲突未解**:预执行要"先归档旧 marker 再建新 Running",但归档移动失败时"不许删除"。那此时能不能建新 Running?不能则用户卡住无法重跑,能则出现双 marker。需补"预归档失败 → 中止本次执行并报错"。

---

## 与现有实现的关键衔接风险

### 后端
1. `slot_state_services.py` 当前基于布尔文件存在 + 传入 execution 状态推导颜色,不读 Working marker。
2. Image / Video / Video Only Plan 的 artifact status 仍各自构造自己的 slot state。
3. Video Plan 执行入口仍受旧 `video_plan_execution_state.json` 的 running 影响。
4. Video Only 的 confirm-final 会生成 TailFrame,但状态模型没把 TailFrame 作为独立可见 stage。

### 前端
1. Image Plan Modal 会用 execution state 的当前 step 覆盖 slot state。
2. Video Only / Video Plan Modal 的执行按钮依赖全局 `executionRunning()`。
3. 若后端只新增 Working marker 合成、前端仍按 execution state 禁用按钮,用户仍会卡住。

> 备注:以上衔接点来自对仓库现状的判断,接入前应再核对 `slot_state_services.py`、各 Plan artifact status 服务、三个 Modal 的当前实现是否仍如所述。

---

## 建议补充到需求文档的条款

1. UI slot → stage group 映射表。
2. `TailFrame` 独立 slot + 下游继承 gate + 修复入口。
3. marker 与业务文件并存时的 JSON 读取规则与可比较字段。
4. 旧 execution state 与 Working marker 的降级/同步规则。
5. stale recovery 后台化 + 幂等锁。
6. 删除/替换素材时 job cancel + 执行器发布前 signature/marker 校验。
7. anchor `aliases` / `retired` / history schema 与合并迁移策略。
8. marker 文件名"从右向左 + stage 枚举最长后缀"解析算法。
9. **output group 持久 lease 执行锁**:key 为输出组(如 `final_bundle = {Video_Final, TailFrame}`),lease 文件含 `marker_uid/job_id/external_task_ids/heartbeat_at` 并与 Running marker 做 CAS,在创建 Running marker / 调外部 API 前获取;明确不能用带随机 uid 的 Running 文件当锁、不能只锁单 path、不能用进程级 flock 当主锁。
10. **lease/marker 两阶段创建协议 + state 机**:uid → 原子建 lease(`state=initializing`,写入 uid)→ 建同 uid Running marker → 置 `state=running` → marker 失败回滚 lease;`state ∈ {initializing, running, recovering, failed}`;`initializing` 超 TTL 且无 external task 可直接释放;后续操作 CAS 校验 uid。
11. **外部 API idempotency 窗口 + 两文件 reconcile + 不可恢复降级**:请求带 `marker_uid/attempt_id` 作 client request id,拿到 task id 后更新 marker+lease;两 JSON 非真原子,**lease 为锁主记录、marker 为展示记录**,不一致按 `marker_uid/attempt_id/updated_at` 以 lease 为准 reconcile(或 WAL);不支持 idempotency / 按 request id 查询的 provider 标 `non_recoverable_submit_window`,未落盘崩溃只转人工核对/费用审计。
12. **lease 回收边界**:上游任务仍在跑时不释放,交 stale recovery 接管;仅上游确认 failed/canceled/not_found 才释放并转 Failed。
13. `input_file_fingerprints` 定义:revision 优先,fs 兜底 `size+mtime_ns+ctime_ns`,内容 hash 异步缓存。
14. **三层 revision + 唯一写入口 + manifest 基线**:`asset_revision`(文件)/ `binding_revision`(绑定)/ `slot_revision`(合成用 composite);经统一 publish/bind 服务写入并自增,写 `WorkingAssetManifest.json` 作基线;颜色合成比 revision 而非时间戳;旁路写检出后标 `revision_unknown/stale_binding`,不静默转绿。`TailFrame.slot_revision = TailFrame.asset_revision + 来源 Final 的 (asset_revision, binding_revision)`。
15. `Image_New` signature 纳入上游 TailFrame 来源指纹/revision。
16. slot state 增加 `file_exists_unbound`(白色 pending)态 + "确认绑定 / 重新确认 Final" 动作。
17. **第一批必须含最小 recovery**:lease/marker 判活、拒绝误释放"外部仍 running"的 lease、可转人工/Failed;完整补拉结果可第二批。

---

## 建议新增验收标准

1. 旧 `*_execution_state.json` 保持 running、但 Working marker 已 stale→Failed 时,页面按钮必须可重试。
2. Final 存在但 TailFrame 缺失时,下游继承不得显示可用,上游段落提供可执行的 TailFrame 修复入口。
3. `Video_Final_Copy` Running 在 Video Only Plan 与 Video Plan 对同一最终视频槽位表现一致。
4. 对嘴型失败不得把已成功绑定的拷贝 Final 错染失败,除非当前用户正在执行对嘴型覆盖路径。
5. 删除 Image_New 后,旧外部 Video_Raw 任务完成也不能写回 Working。
6. 状态刷新接口不得在无锁情况下下载外部结果或发布业务文件。
7. 多下划线 stage 文件名解析稳定,不会误拆 anchor/stage。
8. 同一输出组并发两次点击执行(含 Copy 与 LipSync、或 Final 路径与独立 TailFrame 同时触发),最终只产生一个 Running、只触发一次外部 API、不发生并发写。
9. 上游 TailFrame 重生成后,由其拷贝得到的下游 Image_New 失绿可重执行,不残留过期绿色。
10. 持锁进程崩溃但外部任务仍在跑时,lease 不被释放、不放新任务进入;上游确认死亡后才转 Failed。
11. 绕过统一 publish 服务直接改写业务文件时,扫描比对 manifest 基线能检出 revision 未自增,标记 `revision_unknown/stale_binding`,不产生稳定错误绿色。
12. 进程在"外部已接受任务"与"task id 落盘"之间崩溃后,支持 idempotency 的 provider 能按 `marker_uid/attempt_id` 反查并接管;不支持的 provider 标 `non_recoverable_submit_window` 并转人工核对,不产生静默孤儿。
13. Final 文件已生成但未绑定时,槽位显示 `file_exists_unbound` 白色 pending 且提供确认绑定动作,不误判绿色。
14. 进程在"建 lease"与"建 Running marker"之间崩溃留下 `initializing` lease 时,超 TTL 且无 external task 能被安全释放,不永久占锁。
15. marker 与 lease 因单边写失败不一致时,以 lease 为锁主记录 reconcile,不出现"有 marker 无锁"或"有锁无 marker 却放新任务"。

---

## 落地优先级

**第一批(全部 P0 + 三项关键 P1)**
1. **output group 持久 lease 执行锁**:两阶段创建协议(uid→lease→marker)+ CAS + heartbeat/TTL + 外部 API idempotency 窗口 + 回收边界,先于创建 Running marker / 调外部 API(#5);lease 生命周期与 recovery 同一套状态机。
2. **统一 publish/bind 服务 + 三层 revision + manifest 基线**(`asset/binding/slot_revision`;支撑颜色优先级、fingerprint、跨段签名、旁路检测,是 #3/#10/#11 的共同前提)。
3. slot → stage group 映射(#2)。
4. execution state 不再全局锁死页面(#1)。
5. 颜色合成:marker 与业务文件并存时读 marker JSON,比 revision 判新旧(#3)。
6. 执行器发布前 signature/marker 校验 + 在飞任务取消(#4)。
7. TailFrame 独立状态 + 修复入口(#6)。
8. 签名规则修订:`Image_New` 纳入上游 TailFrame 来源指纹/revision(#10)。
9. `file_exists_unbound` 白色 pending 态 + 确认绑定动作(#11)。
10. **最小 recovery**(lease 状态机的必需部分,不能推迟):识别 lease/marker stale、拒绝误释放"外部仍 running"的 lease、能转人工/Failed。完整补拉上游结果可放第二批,但"判活 + 不误释放"必须随 lease 一起落地。

**第二批(P1)**
1. stale recovery 完整化:后台补拉上游结果 + 自动下载发布(#7,在第一批最小 recovery 之上扩展)。
2. anchor aliases 生命周期与合并迁移(#8)。
3. 文件名解析契约测试(#9)。

**第三批(P2 规则空洞)**:#12 预归档失败处理。

---

## 小结

修订主线是四类规则,其中前两类各自靠一个单一权威机制收口:

1. **唯一 publish/bind 服务 + 三层 revision(asset/binding/slot)+ manifest 基线**:current signature/fingerprint 比 revision 而非时间戳;颜色红绿优先级、`file_exists_unbound` 态、跨段尾帧来源签名、旁路写检测都建立在它之上。
2. **output group 持久 lease**:两阶段创建(uid→lease→marker)、外部 API idempotency、执行锁、在飞任务取消、stale recovery、发布前签名校验共用同一套 lease 状态机——锁与外部任务绑定而非与进程绑定,上游确认死亡才释放(防双提交、防并发写、防孤儿产出)。
3. 业务文件 ↔ 多 stage 的反向匹配、优先级与 UI slot 映射。
4. 旧 execution_state 降级,使页面禁用粒度下沉到 stage/slot。
