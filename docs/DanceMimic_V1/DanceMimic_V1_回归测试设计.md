# DanceMimic_V1 回归测试设计

版本：v0.2  
状态：implementation-ready test plan  
适用范围：DanceMimic_V1 实现、StoryBoard 复用、Analysis_V1 / 口播视频功能回归  

## 1. 测试目标

DanceMimic 的回归测试必须同时证明两件事：

1. DanceMimic 自己的 00-03 创建链路、StoryBoard 适配、05_xx reference-video 执行链符合 v0.6 设计。
2. 现有口播/Analysis_V1/StoryBoard 功能没有被 workflow_mode、runner 抽象、05_xx 扩展和 OpenRouter 路由改动破坏。

本测试设计按阻断优先级组织。P0 用例失败不得合并；P1 用例失败不得发布；P2 用例可作为补充回归或夜间测试。

## 2. 回归边界

### 2.1 需要保护的现有功能

| 现有功能 | 不变要求 |
| --- | --- |
| Analysis_V1 视频分析入口 | 仍进入现有 7 步 run-to-storyboard 流程 |
| 口播 StoryBoard 页面 | 普通任务仍显示口播/故事版既有文案、slot、计划弹窗和执行状态 |
| Analysis_V1 runner | 原 attempt family、surface、step command、日志/轮询行为不变 |
| 05_02 / 05_06 普通视频生成 | 无 DanceMimic reference-video 字段时仍按原 provider/model 路由 |
| 火山 Seedance 旧路径 | 普通 seedance 任务不得被全局改成 OpenRouter |
| Asset Library 视频生成 | 已有 seedance→openrouter 行为不被 05_xx 改造反向影响 |
| StoryBoard `Working/` | 普通最终产物仍写回 `SessionOutput/storyboard/Working/` |

### 2.2 DanceMimic 专属触发条件

DanceMimic 行为只能在以下任一条件成立时触发：

- `workflow_mode = dance_mimic_v1`
- 当前 plan/task segment 同时带 `reference_video_path` 和 `reference_mode = input_references`
- 当前 route/surface 为 `dance_mimic_v1`

任何只带普通 `provider=seedance`、普通 `MaxSR2` alias、普通 StoryBoard task 的口播任务，都不能触发 DanceMimic 分支。

## 3. 必测矩阵

### 3.1 P0: 现有口播/Analysis_V1 不回归

| ID | 层级 | 用例 | 断言 |
| --- | --- | --- | --- |
| DM-R-001 | Backend contract | Analysis_V1 run plan 仍是 7 步 | `/api/openclip/tasks/{id}/analysis-v1/run-to-storyboard` 返回既有步骤，不出现 DanceMimic 00-03 |
| DM-R-002 | Frontend E2E | 点击现有“视频分析”入口 | 仍打开现有视频分析/Prompt Builder/运行设置/7 步任务弹窗 |
| DM-R-003 | Backend contract | Analysis_V1 runner attempt family 不变 | attempt family/surface 仍为 Analysis_V1；DanceMimic attempt 不混入 |
| DM-R-004 | Backend contract | `workflow_mode` migration 兼容历史任务 | 历史 `openclip_tasks` 无值/旧值时按 `analysis_v1` 或原行为读取，列表/详情不报错 |
| DM-R-005 | StoryBoard contract | 普通 StoryBoard lazy normalize 不变 | 普通 `analysis_v1_storyboard` 仍能生成/读取 edit schema，`dialogue_asset_key` 规则不变 |
| DM-R-006 | Frontend E2E | 普通 StoryBoard 页面文案不变 | 普通任务不显示“DanceMimic / 舞蹈复刻”文案 |
| DM-R-007 | Backend contract | 普通 seedance 05_02 路由不变 | 无 `reference_video_path + input_references` 时，seedance provider 仍命中原 `video_seedance` |
| DM-R-008 | Backend contract | 普通 OpenRouter first-frame 行为不变 | 无 reference video 时，OpenRouter 仍只发送原有 first-frame/reference_images 行为 |
| DM-R-009 | Frontend E2E | 现有 StoryBoard 回归套件通过 | `npm --prefix frontend run test:e2e:koubo-storyboard` 通过 |

### 3.2 P0: DanceMimic 入口与 runner 隔离

| ID | 层级 | 用例 | 断言 |
| --- | --- | --- | --- |
| DM-R-010 | Frontend E2E | `任务列表（口播）` 顶部 DanceMimic 入口 | 点击后进入 DanceMimic 创建/运行入口，不调用 Analysis_V1 创建接口 |
| DM-R-011 | Frontend E2E | DanceMimic run plan UI | 只展示 00/01/02/03 四步，不展示 Analysis_V1 7 步 |
| DM-R-012 | Backend contract | DanceMimic runner target | run API target 为 `dance_mimic_v1`，attempt family/surface 与 Analysis_V1 分离 |
| DM-R-013 | Backend contract | DanceMimic stop/log polling | stop、poll、step logs 使用 DanceMimic route，不读取 Analysis_V1 attempt |
| DM-R-014 | Backend contract | DanceMimic 创建 task | 写入 `openclip_tasks.workflow_mode = dance_mimic_v1`，保留 `reference_video_path` 作为任务级字段 |

### 3.3 P0: 00-03 工具合同

| ID | 层级 | 用例 | 断言 |
| --- | --- | --- | --- |
| DM-R-020 | Tool fixture | 00 写变量 | `Variables.json` 只要求 `source_video_path`；不要求工具读取 `Variables.json.reference_video_path` |
| DM-R-021 | Tool fixture | 01 音画拆分正常 | 生成 `reference_media_manifest.json`、silent video、mixed audio；demucs 缺失时 vocal warning 而非 blocked |
| DM-R-021A | Tool fixture | 02 缺 01 manifest 阻断 | 缺 `SessionOutput/reference/reference_media_manifest.json` 时 blocked `reference_media_manifest_missing`，不继续切分 |
| DM-R-022 | Tool fixture | 02 分段可行性校验 | `ceil(D/target) > floor(D/minimum)` 时 blocked `segment_constraints_infeasible` |
| DM-R-022A | Unit/Tool fixture | 02 分段正向边界 | `D=34,target=8,min=4` 得到 `7,7,7,7,6`；`D=14,target=8,min=4` 得到 `8,6`；尾段不得低于 minimum，近似均分需稳定 |
| DM-R-023 | Tool fixture | 02 默认检测器合同 | 默认 `insightface_scrfd`，manifest / `FaceTrack.json` / Result 中一致 |
| DM-R-023A | Tool fixture | 02 遮脸有效性自动 QA | 对 `FaceMasked.mp4` 的 expanded bbox 区域断言 `grid_black_black_pixel_ratio >= 0.60`、`masked_region_diff_mean >= 15.0`；post-mask re-detect 有脸时必须写 warning 或 blocked |
| DM-R-024 | Tool fixture | 03 单 segment 成功 | 生成 `srt_storyboard.json`、`storyboard_seed.json`、1 个 reference asset、Result |
| DM-R-025 | Tool fixture | 03 多 segment 成功 | N segment 生成 N dialogue，`dialogue_asset_key` 唯一稳定，seed 长度为 N |
| DM-R-026 | Tool fixture | 03 不伪造最终视频 | `working_assets.video.path` 初始为空，不把 face-masked reference 写成 `Video_Final` |
| DM-R-027 | Tool fixture | 03 缺 face-masked 视频失败 | 失败码 `missing_face_masked_reference_video`，不写半成品 StoryBoard |
| DM-R-028 | Tool fixture | 03 已有 StoryBoard 保护 | 未传 `--force` 时失败 `storyboard_existing_requires_force`，不覆盖用户文件 |
| DM-R-029 | Tool fixture | 03 force 归档 | 旧 `srt_storyboard/edit/seed/Working/reference assets` 进入 `_archive/{timestamp}` 后再重建 |
| DM-R-029A | Backend/tool contract | M1 级联 stale | 02 force 成功后，03、`storyboard_seed.json`、StoryBoard reference assets、Video Plan 标记 stale；不得误标普通 Analysis_V1 task |
| DM-R-029B | Backend/tool contract | 共享 SessionOutput force 例外 | 01/02 force 只清理本工具声明路径和可追踪派生产物，不删除用户上传素材或无归属 StoryBoard 文件 |

### 3.4 P0: StoryBoard 复用与 workflow 文案

| ID | 层级 | 用例 | 断言 |
| --- | --- | --- | --- |
| DM-R-030 | Backend contract | DanceMimic lazy normalize | explicit `dialogue_asset_key` 被保留，`dance_mimic.reference_video_path` 不丢失 |
| DM-R-031 | Backend contract | DanceMimic task detail metadata | detail 返回 `workflow_mode=dance_mimic_v1`、`source_type=dance_mimic_v1_storyboard` |
| DM-R-032 | Frontend E2E | DanceMimic 打开 StoryBoard | 页面能打开同一个 `openclip_tasks.id`，标题/标签为 DanceMimic 或中性 StoryBoard 文案 |
| DM-R-033 | Frontend E2E | DanceMimic 页面不出现口播误导 | 不显示“台词生成”“口播视频复刻”等仅口播语义文案 |
| DM-R-034 | Backend contract | `Working/` 边界 | 03 只写 reference assets；最终视频仍由 05_xx 写 `SessionOutput/storyboard/Working/` |

### 3.5 P0: 05_xx reference-video 执行链

| ID | 层级 | 用例 | 断言 |
| --- | --- | --- | --- |
| DM-R-040 | Backend contract | 05_01 读取 `storyboard_seed.json` | video plan segment 写入 `provider=openrouter`、`model=bytedance/seedance-2.0`、`reference_mode=input_references`、`reference_video_path` |
| DM-R-041 | Backend contract | 05_05 video-only 读取 seed | video-only task 同样带 DanceMimic reference-video 字段 |
| DM-R-042 | Backend contract | 05_02 函数签名扩展 | `generate_video_with_provider(..., reference_videos=[...])` 写入 `context["reference_videos"]` |
| DM-R-043 | Backend contract | 05_06 调用点传参考视频 | video-only executor 从 task/plan 解析 reference video 并传入 `generate_video_with_provider` |
| DM-R-044 | Backend contract | DanceMimic MaxSR2 命中 OpenRouter | 最终 module 为 `video_openrouter`，不走 `video_seedance` |
| DM-R-045 | Backend contract | OpenRouter payload 含视频 reference | mock `video_openrouter.generate()` 捕获 `context.reference_videos`；payload 含 video `input_references` |
| DM-R-046 | Backend contract | provider mismatch 阻断 | DanceMimic reference-video 场景若最终 module 不是 OpenRouter，失败 `dance_mimic_video_provider_mismatch` |
| DM-R-047 | Backend contract | model call 审计 | model_call 记录 reference video 路径/count，且不落真实 API key |

### 3.6 P1: 端到端 smoke

| ID | 层级 | 用例 | 断言 |
| --- | --- | --- | --- |
| DM-R-050 | E2E smoke | 创建 DanceMimic task -> 跑 00-03 -> 打开 StoryBoard | 全链路成功，同一 task id 打开 StoryBoard |
| DM-R-051 | E2E smoke | DanceMimic StoryBoard 生成一段视频 | mock provider 下 05_02 写 Raw/Final 到 `Working/`，reference video 不被当成 Final |
| DM-R-052 | E2E smoke | 创建 DanceMimic 后再跑普通视频分析 | 普通视频分析仍能进入 7 步并生成普通 StoryBoard |
| DM-R-053 | E2E smoke | 普通 StoryBoard 后再打开 DanceMimic StoryBoard | 两类任务文案、metadata、计划弹窗互不污染 |

## 4. 建议落地到现有测试文件

### 4.1 新增后端合同测试

建议新增：

```text
backend/tests/contracts/test_dance_mimic_task_and_runner_contract.py
backend/tests/contracts/test_dance_mimic_reference_face_mask_contract.py
backend/tests/contracts/test_dance_mimic_storyboard_build_contract.py
backend/tests/contracts/test_dance_mimic_storyboard_workflow_contract.py
backend/tests/contracts/test_dance_mimic_video_reference_execution_contract.py
```

分别覆盖：

- DanceMimic API/runner/attempt 隔离。
- 02 分段逻辑、H2 manifest blocked、fake detector、FaceTrack、遮脸自动 QA、force 级联 stale。
- 03 工具输入输出、幂等、force、失败码。
- StoryBoard workflow-aware metadata 和 lazy normalize。
- 05_01/05_05/05_02/05_06 reference video 路由与 OpenRouter payload。

### 4.2 复用/扩展现有合同测试

| 现有测试 | 增补方向 |
| --- | --- |
| `test_analysis_v1_runner_executable_contract.py` | 增加 Analysis_V1 runner family/surface 不被 DanceMimic 改动的断言 |
| `test_analysis_v1_run_to_storyboard_tts_mode_contract.py` | 增加 7 步 plan 不受 DanceMimic route 影响的断言 |
| `test_koubo_storyboard_dialogue_asset_key_contract.py` | 增加 DanceMimic explicit dak lazy normalize 保持测试 |
| `test_analysis_v1_openrouter_video_contract.py` | 复用 `input_references` payload 测试，补 DanceMimic reference video 上下文 |
| `test_analysis_v1_seedance_video_contract.py` | 增加普通 seedance 仍走 `video_seedance` 的负向保护 |
| `test_analysis_v1_video_plan_executor_resilience_contract.py` | 增加 `reference_videos` 传参、model_call 审计、Working 边界 |
| `test_koubo_asset_video_provider_retry_contract.py` | 保持 Asset Library 的 seedance→openrouter 合同，防止 05_xx 改造反向影响 |

### 4.3 新增前端 E2E

建议新增：

```text
frontend/e2e/dance-mimic-entry-regression.mjs
frontend/e2e/dance-mimic-storyboard-regression.mjs
```

复用现有 `frontend/e2e/koubo-storyboard/fixture.mjs` 的启动、登录、mock API 能力。

需要继续跑现有套件：

```bash
npm --prefix frontend run test:e2e:koubo-storyboard
```

如果环境有 Analysis_V1 TTS fixture，再跑：

```bash
node frontend/e2e/analysis-v1-tts-quick-adv.mjs
```

## 5. 测试数据与 mock 策略

### 5.1 Fixture workspace

建议准备最小 DanceMimic workspace：

```text
SessionContext/
  Variables.json
  Video_Reference_Source.mp4
SessionOutput/reference/
  reference_media_manifest.json
  Audio_Reference_Mixed.wav
  segments/
    reference_segments_manifest.json
    segment_0001/face_masked_reference.mp4
    segment_0002/face_masked_reference.mp4
```

视频文件可用极短 synthetic mp4，合同测试只校验文件存在、大小、路径和传参，不需要真实舞蹈内容。

02 遮脸 QA fixture 需要额外准备：

```text
SessionOutput/reference/segments/
  segment_0001/reference_silent.mp4
  segment_0001/face_detections_fixture.json
  segment_0001/FaceTrack.json
  segment_0001/face_masked_reference.mp4
```

`face_detections_fixture.json` 使用固定 bbox，覆盖稳定可计算的 expanded bbox 区域。测试不依赖真实人脸图片，只需要合成视频中有可对比的像素区域。

### 5.2 模型调用 mock

P0/P1 合同测试不得依赖真实模型或网络：

- `video_openrouter.generate()` 用 fake module 捕获 `context`，写一个假 mp4 到 output。
- OpenRouter payload 测试只构造本地小图片/音频/视频，断言 `input_references`。
- 真实付费 smoke 只作为人工/预发验收，不作为默认 CI 阻断。

02 Tool fixture 也不得依赖真实 insightface/onnxruntime/model cache：

- 02 实现必须支持测试注入检测结果，例如 `--face-detections-manifest`、detector interface fake，或等价的 test-only fixture hook。
- CI 用固定 bbox + synthetic mp4 跑分段、FaceTrack、遮盖写出、black-pixel-ratio、diff mean、post-mask re-detect warning 断言。
- 真实 `insightface_scrfd` 只放可选集成测试或预发环境，用来验证依赖安装和模型缓存，不作为默认 P0 CI 阻断。
- post-mask re-detect 在 CI 可用 fake detector 注入“遮后无脸”和“遮后仍有脸”两种结果，分别断言 passed 与 warning/blocked。

### 5.3 DB migration fixture

至少覆盖三种历史数据：

1. 新 DanceMimic task：`workflow_mode=dance_mimic_v1`
2. 现有 Analysis_V1 task：`workflow_mode=analysis_v1`
3. 历史旧 task：`workflow_mode IS NULL`

断言旧 task 读取行为与当前口播/Analysis_V1 一致。

## 6. 合并前阻断命令建议

实现完成后，合并前至少跑。新 DanceMimic 测试文件创建前，先跑现有回归组；新文件落地后再跑 DanceMimic 组，避免 pytest 因文件不存在 collection error。

```bash
python -m pytest \
  backend/tests/contracts/test_analysis_v1_runner_executable_contract.py \
  backend/tests/contracts/test_analysis_v1_run_to_storyboard_tts_mode_contract.py \
  backend/tests/contracts/test_koubo_storyboard_dialogue_asset_key_contract.py \
  backend/tests/contracts/test_analysis_v1_seedance_video_contract.py \
  backend/tests/contracts/test_analysis_v1_openrouter_video_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_executor_resilience_contract.py \
  backend/tests/contracts/test_koubo_asset_video_provider_retry_contract.py
```

DanceMimic 测试文件创建后追加：

```bash
python -m pytest \
  backend/tests/contracts/test_dance_mimic_task_and_runner_contract.py \
  backend/tests/contracts/test_dance_mimic_reference_face_mask_contract.py \
  backend/tests/contracts/test_dance_mimic_storyboard_build_contract.py \
  backend/tests/contracts/test_dance_mimic_storyboard_workflow_contract.py \
  backend/tests/contracts/test_dance_mimic_video_reference_execution_contract.py
```

前端 StoryBoard 回归仍需运行：

```bash
npm --prefix frontend run test:e2e:koubo-storyboard
```

如果 DanceMimic 前端已接入，再追加：

```bash
node frontend/e2e/dance-mimic-entry-regression.mjs
node frontend/e2e/dance-mimic-storyboard-regression.mjs
```

## 7. 发布前验收门

发布前至少满足：

1. P0 全部通过。
2. P1 smoke 至少通过 mock provider 链路。
3. 普通 Analysis_V1 视频分析入口仍打开 7 步任务。
4. 普通 StoryBoard E2E 套件通过。
5. DanceMimic StoryBoard 能打开并保持 workflow-aware 文案。
6. DanceMimic 05_02/05_06 mock 调用确认命中 `video_openrouter` 且 payload 含 video `input_references`。
7. 普通 seedance 任务仍能命中 `video_seedance`，没有被全局重路由。
8. 02 遮脸自动 QA 通过，并抽查 QA sheet / 样例视频，人工确认脸不漏、遮脸参考送云前不泄露原始身份。
9. 至少抽查 1 条 DanceMimic 生成结果，人工确认动作/节奏未明显漂移，且没有遮脸网格残留成为最终画面主体。
