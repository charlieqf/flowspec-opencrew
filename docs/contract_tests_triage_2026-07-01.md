# Contract Tests 分诊报告（Step 0 — 修绿基线）

- 日期：2026-07-01
- 命令：`PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache backend/.venv/bin/python -m unittest discover -s backend/tests/contracts`
- 结果（分诊前）：Ran 550 / **4 failures / 14 errors / 1 skipped**
- 结果（修复后）：Ran 550 / **0 failures / 0 errors / 1 skipped** ✅ 绿色基线达成
- 目的：逐条判定「改测试（契约过期）」还是「修代码（真实回归）」，作为后续重构的绿色基线前置。

> 2026-07-26 更新：当前 CI 与 pre-push 已统一改用
> `backend/.venv/bin/python -m pytest -q backend/tests/contracts`，以同时收集
> `unittest.TestCase` 和函数式 pytest 契约；上面的命令与计数保留为 2026-07-01
> 分诊记录。

## 修复结果（全绿）

18 条全部处理完毕，改动分两类：**测试/契约/夹具更新**（多数）与 **2 处真实代码/数据补全**。

| 桶 | 改了什么 | 类型 |
|---|---|---|
| A | 测试在 `services.opencode_runtime` 也补 patch `OpenCodeSessionClient`（`bc420dd` 改了 client 构造路径，旧 patch 失效导致真连网络） | 测试 hermetic 修复 |
| B | `registry_normalizer.py` 的 `OUTPUT_CONTRACT_DEPENDENCIES` 补 4 个 token（`image_generation_plan_json`→05_03、`video_only_generation_plan_json`→05_05、`video_generation_plan_logic`→05_01、`tts_reference_speed`→03_02/03_03）；测试期望工具列表更新为当前 18 个 | **真实 registry 补全** + 契约更新 |
| C | `<PanelExpandIcon>`→`<StoryboardIcon>`；删除运行菜单重设计后已移除的 9 个旧文案 token | 契约更新 |
| D | 生成 3KB 合成占位 mp4 作 fixture + manifest 标注 `committed_file` 为 SYNTHETIC PLACEHOLDER | 夹具补全 |
| E | 按「保留 legacy key（代码为准）」放宽断言：legacy srt_id/dialogue_id 在索引中、bare dialogue_id 可绑定；重命名测试方法 | 契约更新（含决策） |
| 连带 | `test_02_real_detector...` 增加合成占位跳过守卫（<100KB 跳过，需真实 clip 才跑真人脸检测） | 夹具守卫 |

**未在本轮改动的真实代码隐患（建议单独跟进）**：`adapters/opencode.py:_request` 只捕获 `HTTPError`，连接类 `URLError` 会冒泡成 500。生产环境 OpenCode 可达时不触发，故未阻断绿色基线，但建议补一个 `except URLError -> OpenCodeUnavailable(503)` 的稳健化改动。

### 待办（Step 0 收尾）
将「后端 contract tests + 前端 build」接入门禁。**已完成**：
- CI：`.github/workflows/ci-gate.yml`（backend contract tests + frontend build，push/PR 触发）。
- 本地 pre-push 门禁：`.githooks/pre-push`——main-only 直推前就跑相关检查（按改动路径 scope：动了 backend/ToolLibrary 跑 contract tests，动了 frontend 跑 build；docs-only 快速跳过）。应急/纯文档可 `SKIP_OPENCREW_PREPUSH=1 git push` 绕过。**每台机器 clone 后需执行一次 `npm run install:git-hooks`**（设置 `core.hooksPath=.githooks`）激活。

### CI 首跑暴露的额外非 hermetic 依赖（已修）
本地「绿」其实依赖开发机环境。CI 首跑逐个暴露并修复：
- ffmpeg/ffprobe：vendored 在 gitignore 的 `ToolLibrary/.bin`，CI 缺失。`imageio-ffmpeg` 只带 ffmpeg 不带 ffprobe → CI 里 `apt install ffmpeg` + symlink 进 `.bin` + 设 `OPENCREW_FFMPEG_PATH/FFPROBE_PATH`。
- `test_run_all_...` tempdir 清理竞态（`Directory not empty`）→ tearDown 重试 + 容错 rmtree。

### 未决：dance_mimic 间歇性测试隔离 bug（P0 债的具体化）
- 现象：全量套件里 `test_auto_run_invokes_dance_mimic_tools_with_source_video_path` 偶发 `IndexError: tuple index out of range`（`repositories/base.py:row_to_dict` 的 `dict(row._mapping)`）。
- 已排除：单测隔离通过；fresh DB 直连查询通过；固定 `PYTHONHASHSEED` 0–7 均不复现；无重复列、无全局 Table 变异。
- 判定：**后台 run worker 存活到测试 tearDown 之后**（伴随 `session_events: no such table`、`Directory not empty` 噪声），与 dance 测试并发时竞态污染共享的 SQLAlchemy 语句/列缓存。约 5 次 CI 1 次复现,非确定性,难确定性重放。
- 本质：评审 P0「globals 注入 + 未 join 的后台线程 → 测试无法隔离」的具体实例。
- 选项：(A) 真正修 worker 生命周期（tests 在 tearDown 前 stop/join workers，Step 1 级别）；(B) CI step 失败重试一次作为过渡 + 列为 Step 1 首个目标；(C) 暂记为已知问题。

---


## 结论一览

18 条失败聚成 5 个桶。**其中 16 条是测试/契约/夹具问题（非代码回归），只有 1 条是真正需要产品决策的行为冲突，1 条待查。**

| 桶 | 数量 | 根因 | 判定 | 动作 | 谁改 |
|---|---|---|---|---|---|
| A. 非 hermetic：真连 OpenCode 网络 | 11 err | app 在请求路径里发真实 HTTP | 测试基础设施缺陷 | mock/注入 adapter 或加离线兜底 | 改测试+加兜底 |
| B. 注册表随功能增长，strict 契约未同步 | 3 err | registry 加了 6 个新工具，测试仍期望旧 12 工具 | 契约过期（含待验证的依赖 token） | 更新期望 + 补依赖声明 | 改测试+查 registry |
| C. 前端文案/结构漂移 | 2 fail | 断言前端源码子串，UI 已改 | 契约过期 | 更新期望字符串 | 改测试 |
| D. Dance 夹具缺失 | 1 fail | 期望的 mp4 不在 test_fixtures/ | 夹具缺失 | 恢复夹具或改断言 | 改夹具/测试 |
| E. dialogue asset key 索引冲突 | 1 fail | 契约要求仅按 asset_key 索引，代码仍按 srt_id | **真实行为冲突** | 需产品决策 | 待决策 |

---

## 桶 A：11 个 error —— 测试真的去连 OpenCode（非 hermetic）

**测试**（全在 `test_analysis_v1_runner_executable_contract.py`）：
`test_cloud_asr_rejects_local_default_provider_before_attempt_created`、`test_free_rewrite_selected_steps_explains_missing_02_02_output`、`test_pause_before_step_and_resume_same_attempt`、`test_run_all_on_fresh_workspace_waits_for_02_01_reference_audio`、`test_run_from_step_writes_run_state_and_recovers_from_file`、`test_run_only_builder_g_tts_builder_accepts_uploaded_audio_without_reference_video`、`test_run_only_quick_adv_tts_builder_uses_03_03_outputs`、`test_run_only_quick_tts_builder_accepts_uploaded_audio_without_reference_video`、`test_run_only_quick_tts_builder_requires_reference_audio`、`test_run_only_step_records_diagnostic_scope_and_bounded_log_tail`、`test_stop_after_current_marks_remaining_cancelled`

**堆栈**：
```
router.py:4789 start_analysis_v1_run_to_storyboard
  → router.py:787 resolve_model
  → router.py:740 serialize_prompt_models
  → adapters/opencode.py:231 providers()
  → adapters/opencode.py:214 _request → urlopen
  → URLError: [Errno 8] nodename nor servname provided, or not known
```

**根因**：`serialize_prompt_models` 在请求处理路径里同步调用 `OpenCode.providers()` 发真实 `GET /provider`。离线/未配 OpenCode 的环境下 DNS 解析失败。且 `adapters/opencode.py:213-222` 的 `_request` 只 `except HTTPError`（处理 401），**不捕获 `URLError`（连接类失败）**，异常直穿到 handler。

**判定**：测试 hermeticity 缺陷（契约测试不应依赖活的 OpenCode 服务）。同时暴露两个代码异味：
1. 请求路径里做同步网络拉取，无离线兜底；
2. 连接失败未被捕获（只处理了 401）。
3. 可疑关联：最近提交 `bc420dd Handle OpenCode auth refresh for Koubo prompts` 可能把这个网络调用挪到了更早的路径。

**动作**（一处修复清 11 条）：
- 测试侧：给 runner/router 注入 fake OpenCode adapter（返回固定 provider 列表），断掉真实网络。
- 代码侧（顺带修真 bug）：`_request` 捕获 `URLError`，转成明确的 503/`OpenCodeUnavailableError`，别让 DNS 错误冒泡成 500。

---

## 桶 B：3 个 error —— 注册表长大了，strict 契约没跟上

**测试**（`test_analysis_v1_framework_bridge_contract.py`）：
`test_analysis_v1_registry_normalizes_without_manual_overrides`、`test_framework_bridge_invokes_video_plan_composer`、`test_framework_bridge_returns_tool_result_and_manifest_for_prepare_and_probe`

**报错**：`RegistryNormalizationError: Unresolved registry dependency tokens: 04_01_free:tts_reference_speed, 05_03:video_generation_plan_logic, 05_04:image_generation_plan_json, 05_05:video_generation_plan_logic, 05_06:video_only_generation_plan_json`

**证据**：
- 测试期望工具集：`["00","01","02_01","02_02","03_01","03_02","04_01","04_02","04_03","05_01","05_02","06_01"]`（12 个）
- 实际 `ToolLibrary/Analysis_V1/tool_registry.json`：`00,01,02_01,02_02,03_01,03_02,03_03,04_01,04_01_free,04_02,04_03,05_01,05_02,05_03,05_04,05_05,05_06,06_01`（18 个）
- 新增工具来自功能提交：`e24e78f 高级快速 TTS`、`9252dba free SRT rewrite`、`6391e16 video-only plan`、`7e9d1ac image plan`——**功能合入时没更新 strict 契约测试**。

**判定**：契约过期为主。但 strict 模式报的「unresolved dependency tokens」需逐个确认：是这些新工具声明了依赖但注册表里没有对应的 producer（真实 registry 数据缺陷），还是只是测试没同步。

**动作**：
- 更新测试期望的工具 id 列表到当前 18 个。
- 对 5 个 unresolved token 逐个核实：有 producer 的 → 补进 registry；确实可选的 → 加 manual override / 标记 optional。**这一步顺带体检 registry 数据完整性。**

---

## 桶 C：2 个 fail —— 前端文案/结构漂移

- `test_frontend_button_uses_free_rewrite_selected_steps`：期望 `'<PanelExpandIcon /><span>故事板</span>'` 出现在前端源码，未找到。
- `test_frontend_contract_exposes_chinese_mvp_controls`：期望 `'运行...'` 出现，未找到。

**判定**：契约过期——测试断言前端源码子串，UI 已改。低风险。
**动作**：更新期望字符串到当前 UI；长远看这类「grep 前端源码」的脆断言应换成更稳的契约（如导出常量/data-testid）。

---

## 桶 D：1 个 fail —— Dance 夹具缺失

- `test_reference_video_library_lists_fixtures_assets_and_uploads`：期望列表含 `ToolLibrary/DanceMimic_V1/test_fixtures/dance_solo_frontal_studio.mp4`，实际只返回会话内 `dance_reference_clip.mp4`。
- **证据**：`ToolLibrary/DanceMimic_V1/test_fixtures/` 目录里**根本没有** `dance_solo_frontal_studio.mp4`（只有 manifest.json、README、两个 PNG、一个 JSON）。mp4 可能因 .gitignore 媒体规则未入库或被改名。

**判定**：测试夹具缺失，非逻辑 bug。
**动作**：恢复该 fixture mp4（或改用现存 fixture），或更新断言到实际存在的夹具。

---

## 桶 E：1 个 fail —— dialogue asset key 索引冲突（唯一需要决策的）

- `test_executor_indexes_and_binds_by_asset_key_only`（`test_koubo_storyboard_dialogue_asset_key_contract.py:56`）
- 契约明确要求：`VPE.flatten_dialogues` 只能按 `dialogue_asset_key`（`dak_target`）建索引，**不得**含 `srt_id`（`srt_0004`）或 `dialogue_id`。
- 实际：索引里同时含 `srt_0004`、`scene_001_dialogue_004`、`dak_target`。
- **冲突源**：最近提交 `c813392 fix(koubo): preserve legacy dialogue audio keys` ——「保留旧版对白音频 key」正是把 srt_id/dialogue_id 重新加回索引的行为，与本契约直接对立。

**判定**：真实行为冲突。二选一：
1. 「保留 legacy key」是有意的（为兼容旧数据）→ 该契约过时，需放宽/更新；
2. 「只按 asset_key 索引」是硬规则 → `c813392` 引入了回归，需修代码。

**动作**：需你或 koubo owner 拍板意图。这是 18 条里唯一不能由测试维护者单方决定的。

---

## 建议执行顺序（把红修绿）

1. **桶 A**（清 11 条，收益最大）：加 fake OpenCode adapter + `_request` 捕获 URLError。
2. **桶 E**（先决策）：确定 asset_key-only 还是保留 legacy key，再改测试或改代码。
3. **桶 B**：更新工具 id 列表 + 核实/补 registry 依赖 token。
4. **桶 C / D**：更新前端断言、恢复 dance 夹具。
5. 全绿后，把「后端 contract tests + 前端 build」设为 CI 必过门禁（Step 0 收尾）。

> 关键：桶 A、C、D、B 大多是「测试没跟上功能」，改动风险低；桶 E 是唯一可能改动运行时行为的，必须先决策再动。
