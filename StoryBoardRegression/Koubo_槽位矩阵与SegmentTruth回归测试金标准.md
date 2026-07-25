# Koubo 槽位矩阵与 Segment Truth 回归测试金标准

版本：v0.1

状态：回归测试汇总稿。本文把 StoryBoard 主界面、Video Plan、Image Plan、Video Only Plan、Segment 拆分、拖拽绑定、执行状态、Shot/Scene/Dialogue 结构编辑相关测试统一归类，并整理出后续做状态修改或代码更改时必须优先跑的最精简回归集。

## 1. 本文依据

已浏览并纳入本文的需求与测试来源：

| 类型 | 文件 |
| --- | --- |
| 状态需求 | `docs/SessionDesign-R2/Koubo_Storyboard_Raw_Final_状态管理需求文档.md` |
| 槽位颜色矩阵 | `docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md` |
| StoryBoard 输出结构与拖拽测试 | `docs/SessionDesign-R2/STORYBOARD_OUTPUT_STRUCTURE.md` |
| StoryBoard 拆分验收 | `docs/故事版口播拆分方案.md`、`docs/故事版口播拆分回归测试报告.md` |
| Video Plan 规划需求 | `docs/SessionDesign-R2/Analysis_V1_05_01_VideoGenerationPlan_工具需求整理.md` |
| Video Plan 执行需求 | `docs/SessionDesign-R2/Analysis_V1_05_02_VideoPlanExecutor_工具需求整理.md` |
| Composer 需求 | `docs/SessionDesign-R2/Analysis_V1_06_01_VideoPlanComposer_工具需求整理.md` |
| 当前自动化读取的槽位表 | `OpenCrew/docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md` |
| Scene 状态与多 Shot 回归 | `OpenCrew/docs/SessionDesign-R2/Koubo_Scene_状态回归测试方案.md`、`OpenCrew/docs/SessionDesign-R2/Koubo_Scene_状态回归测试记录_20260617.md` |
| key / 页面绑定一致性 | `OpenCrew/docs/SessionDesign-R2/Koubo_Storyboard_DialogueKey_统一资源绑定需求与测试案例.md`、`OpenCrew/docs/SessionDesign-R2/Koubo_Storyboard_Plan执行与页面绑定一致性审计.md` |
| 新增结构拖拽回归 | `OpenCrew/docs/SessionDesign-R2/Koubo_Storyboard_新增结构素材拖拽绑定回归案例集.md` |
| 状态合同测试 | `OpenCrew/backend/tests/contracts/test_koubo_storyboard_slot_state_contract.py` |
| key 合同测试 | `OpenCrew/backend/tests/contracts/test_koubo_storyboard_dialogue_asset_key_contract.py` |
| 手动绑定状态测试 | `OpenCrew/backend/tests/contracts/test_koubo_storyboard_manual_asset_status_contract.py` |
| stale edit / 清理归档测试 | `OpenCrew/backend/tests/contracts/test_koubo_storyboard_stale_edit_contract.py` |
| Segment / 执行 / 拼接测试 | `tests/analysis_v1/test_video_generation_plan.py`、`tests/analysis_v1/test_video_plan_executor.py`、`tests/analysis_v1/test_video_plan_composer.py`、`tests/analysis_v1/test_storyboard.py` |

当前 checkout 的真实模块路径：

| 层 | 当前路径 |
| --- | --- |
| 前端 StoryBoard | `OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/` |
| 前端入口 | `OpenCrew/frontend/src/modules/koubo/KouboStoryBoardModule.jsx` |
| 后端 StoryBoard | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/` |
| 状态派生真源 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/slot_state_services.py` |
| Working/绑定读取 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/video_plan_load_services.py` |
| 拖拽绑定/清理/History | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_history_services.py` |

注意：仓库存在根目录 `docs/` 和 `OpenCrew/docs/` 两套文档。当前槽位矩阵脚本与后端合同测试读取的是 `OpenCrew/docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md`；根目录 `docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md` 是更完整的 32 组合设计稿。后续修改金标准或槽位表时，必须明确同步策略，避免人工文档和自动化读取文档分叉。

## 2. 金标准不变量

后续任何状态修改、代码更改、UI 重构，都必须保持以下不变量。

| ID | 不变量 | 失败表现 |
| --- | --- | --- |
| INV-01 | `dialogue_asset_key` 是素材、计划、执行、回写的稳定身份，不得用 `srt_id`、`dialogue_id`、Scene ID 或 Shot ID 替代 | 拖拽后结构变动，素材串到别的 Dialogue |
| INV-02 | Segment 边界只能由 `05_01_VideoPlanGenerator.py` 的统一 Segment Truth 决定；Plan 类型只决定任务状态和按钮，不重新拆 Dialogue | Video Plan / Image Plan / Video Only Plan 同一 Scene 下 Segment 数量不一致 |
| INV-03 | 固定槽位向量为 `[音频, 原图, 新图, 新视频, 终视频]` | 全空输入时 Plan Modal 不渲染槽位，或出现无状态空洞 |
| INV-04 | 绿色完成态必须来自当前 Working 标准槽位文件真实存在，或本轮执行成功且已写入标准 Working 文件 | 只有 execution JSON success，UI 却变绿 |
| INV-05 | 普通槽位完成必须闭环：工具成功 -> Working 标准文件 -> StoryBoard 绑定 -> 统一派生状态 -> UI 绿色 | 文件存在但 JSON 没绑，或 JSON 绑定但文件不存在 |
| INV-06 | 状态优先级固定为 `绿 > 黄 > 红 > 白 > 灰` | 旧 failed/running 覆盖真实文件绿色 |
| INV-07 | Prompt 是特殊产物：Prompt 文件存在即绿色，不随普通槽位删除自动删除 | 删除新图后误删 Prompt，导致无法审计或复用 |
| INV-08 | 删除/替换不级联。用户删哪个槽，只处理哪个槽；更靠近 Final 的下游产物不自动清空 | 删除原图导致 Raw/Final 被误删 |
| INV-09 | Raw 不写入 `working_assets.video`；Final 继续写 `working_assets.video.path` | Raw/Final 混成一个“视频”槽 |
| INV-10 | Final 标准文件存在但未绑定时，显示绿色并提示修复绑定，不要求重跑 Raw/Final | 工具写文件成功但绑定失败后，UI 强迫重跑 |
| INV-11 | `blocked_waiting_input` 和 `skipped_consumed_by_downstream` 必须区分 | 下游已消费的灰色被上游补齐后错误变白 |
| INV-12 | Video Plan 的 Final 需要 Raw + Audio；Video Only Plan 的 Confirm Final 只需要 Raw，不依赖 Audio | Raw 已有但无音频时 Video Only Plan 拷贝被置灰 |
| INV-13 | Shot / Scene / Dialogue 重排、合并、分裂不得改已有 Dialogue 的 `dialogue_asset_key` | 结构编辑后所有绑定失效 |
| INV-14 | Dialogue 删除或合并导致 Dialogue 消失时，消失 Dialogue 的 generated 素材进入 `assets/history/`；upload/original 只解除绑定 | 生成素材静默丢失，或上传素材被误删 |
| INV-15 | Scene / Shot / ShotPlan 拼接必须层级执行：Segment -> Scene -> Shot -> ShotPlan，不能把所有 Segment 直接平铺拼最终片 | Shot/Scene 边界丢失，字幕和状态错位 |

## 3. 全量测试分类

### 3.1 槽位颜色与执行态

覆盖对象：`derive_video_plan_slot_states`、`derive_image_plan_slot_states`、`derive_video_only_plan_slot_states`。

| 类别 | 全量案例 | 当前自动化 |
| --- | --- | --- |
| Video Plan 基础槽位 | `VP-S01` 到 `VP-S32`，穷举 `[A,S,I,R,F]` | `test_video_plan_basic_cases_match_markdown_table` |
| Video Plan Prompt | `VP-P01` 到 `VP-P08` | `test_video_plan_prompt_cases_match_markdown_table` |
| Image Plan 基础槽位 | `IP-S01` 到 `IP-S32` | `test_image_plan_cases_match_markdown_table` |
| Image Plan Prompt | `IP-P01` 到 `IP-P10` | `test_image_plan_cases_match_markdown_table` |
| Video Only Plan 基础槽位 | `VOP-S01` 到 `VOP-S32` | `test_video_only_plan_cases_match_markdown_table` |
| Video Only Plan Prompt | `VOP-P01` 到 `VOP-P10` | `test_video_only_plan_cases_match_markdown_table` |
| Running / Failed | 无文件时黄/红，有文件时绿覆盖 | `test_running_and_failed_never_override_existing_file_green` |
| 灰色原因 | 条件不足 vs 下游已消费 | `test_gray_reasons_distinguish_blocked_from_consumed` |
| Final 未绑定修复态 | Final 文件存在但 `working_assets.video.path` 为空 | `test_final_file_unbound_stays_green_with_repair_reason` |

说明：根目录完整设计稿穷举 32 个基础槽位组合；当前自动化读取的 `OpenCrew/docs` 表是浓缩焦点表。若要把“32 个基础组合”变成自动化硬约束，需要先同步 `OpenCrew/docs` 中的表格，否则合同测试只会锁定当前活跃表中的行。

手工验收时必须确认：三类 Plan Modal 即使 `[0,0,0,0,0]` 也显示固定槽位集合；UI 不允许因为没有 plan item、没有 execution JSON 或没有文件而隐藏槽位。

### 3.2 一个 Scene / 一个 Dialogue 的槽位矢量绑定状态

覆盖对象：一个 Dialogue 的 Audio / Source / Image_New / Video_Raw / Video_Final 标准文件与绑定关系。

| 场景 | 必查点 |
| --- | --- |
| 全空 `[0,0,0,0,0]` | Video Plan：音频白，其余按条件灰；Image Plan：提示词灰、新图灰；Video Only Plan：音频白，其余灰 |
| 只有原图 `[0,1,0,0,0]` | Video Plan 新图白；Image Plan 提示词白、新图灰；Video Only Plan 新图白 |
| 只有新图 `[0,0,1,0,0]` | Video Plan 新图绿、新视频白；Image Plan 新图绿；Video Only Plan 新图绿、提示词白、新视频灰 |
| Raw 存在 `[0,0,0,1,0]` | Video Plan 新视频绿、终视频灰；Video Only Plan 新视频绿、拷贝白 |
| Audio + Raw `[1,0,0,1,0]` | Video Plan 终视频白；Video Only Plan 拷贝白 |
| Final 存在 `[0,0,0,0,1]` | 终视频/拷贝绿，上游灰；Final 未绑定时仍绿但必须有修复提示 |
| Raw + Final `[*,*,*,1,1]` | Raw 绿，Final/拷贝绿；不得因执行态 failed 变红 |

这组案例是单 Dialogue 回归最小矩阵，能覆盖白/灰/绿和 Video Plan vs Video Only Plan 的关键差异。

### 3.3 一个 Scene 拆多个 Segment

覆盖对象：`05_01_VideoPlanGenerator.py`。

| 场景 | 期望 | 当前自动化 |
| --- | --- | --- |
| Scene 内多视觉来源 | 每个视觉锚点开新 Segment；Segment 不跨 Scene | `test_task_target_splits_multi_image_and_overlong_anchor_range`、`test_first_middle_middle_tail_images_split_expected_segments` |
| 每个 Dialogue 都有图 | 每个 Dialogue 一个 Segment；短句仍按模型最小时长计划 | `test_every_dialogue_has_image_creates_one_segment_per_dialogue`、`test_each_short_dialogue_with_image_uses_model_minimum_video_duration` |
| 单图长 Scene | 按 Dialogue 边界切多个 Segment，后续用前段尾帧 | `test_single_image_long_scene_splits_near_max_dialogue_boundary` |
| 单条 Dialogue 超长 | 不切断 Dialogue，标记 `duration_exceeds_limit_unavoidable` | `test_single_overlong_dialogue_is_not_split` |
| 未落位上传素材 | 不能直接作为首帧 | `test_unplaced_upload_asset_cannot_be_first_frame` |
| 已落位新图 | `generated_image` 可直接作为首帧，不再需要 Image Prompt | `test_generated_image_slot_can_be_first_frame_without_image_prompt` |
| 已落位上传图片 | 记录 `materialize_first_frame`，复制到标准 Working 新图槽 | `test_placed_uploaded_image_records_materialize_copy_action` |

### 3.4 跨 Shot 槽位矩阵与 TailFrame

覆盖对象：跨 Scene / Shot 的尾帧继承和阻断。

| 场景 | 期望 | 当前自动化 |
| --- | --- | --- |
| 非首 Scene 无视觉来源，有上一段计划尾帧 | 使用 `previous_segment_tail_frame` | `test_zero_image_non_first_scene_uses_previous_planned_tail` |
| 非首 Scene 无视觉来源，缺上一段尾帧 | Scene blocked | `test_zero_image_non_first_scene_blocks_without_previous_tail` |
| Scene scope 依赖真实上一 Scene 尾帧 | 只有物理 TailFrame 存在才可用 | `test_scene_scope_requires_existing_previous_tail_file`、`test_scene_scope_can_use_existing_previous_tail_file` |
| 跨 Shot 继承 | Task scope 可以从上一 Shot 最后一段尾帧继续 | `test_task_scope_carries_tail_frame_across_shots` |
| first Scene 缺视觉 | first Scene skipped，但后续自带视觉 Scene 继续 planned | `test_first_scene_without_visual_source_is_skipped_but_later_visual_scene_runs` |
| blocked 不吞后续视觉 Scene | skipped / blocked 后续如果有自己的视觉锚点仍继续 planned | `test_blocked_scene_does_not_drop_following_visual_scene` |
| 空镜尾帧 | `is_talking_head=false` 的 cutaway 尾帧不可继承 | `test_cutaway_tail_blocks_following_empty_split_segment` |
| 口播尾帧 | talking-head 尾帧可被下一空段继承 | `test_talking_head_tail_allows_following_empty_split_segment` |

### 3.5 绑定视频与 Video Only Plan

覆盖对象：Final/Raw 文件、Video Only Confirm、绑定视频 Segment。

| 场景 | 期望 | 当前自动化 |
| --- | --- | --- |
| Dialogue 已绑定 Final Video | 该 Dialogue 单独形成 `bound_video` Segment；后续 Dialogue 通过尾帧继续，不并入同一 Final Segment | `test_bound_video_dialogue_starts_new_audio_synced_segment` |
| 绑定视频 Segment | 不生成 video prompt，不跑视频模型；需要音频替换/时长同步 | `test_bound_video_segment_materializes_without_video_model` |
| Raw 已有，Final 缺失 | Video Only Plan 拷贝白；Video Plan Final 取决于 Audio | `test_video_only_plan_cases_match_markdown_table` |
| Confirm Final 后 | Final 标准文件写入并绑定；后续 TailFrame 解锁 | 当前需要结合 VideoOnlyPlan 可执行记录和 key 合同测试；建议补一条端到端 Confirm 测试 |
| Raw/Final 删除 | Raw/Final/TailFrame 相关文件进入 history；Prompt 保留；执行步骤清理 | `test_storyboard_video_delete_archives_video_only_raw_tail_and_clears_execution_steps`、`test_storyboard_uploaded_video_delete_archives_matching_video_only_raw_tail` |

注意：Video Only Plan 的 Final / Raw / Prompt 只影响任务状态和槽位颜色，不能影响 Segment 数量、顺序、Dialogue 包含关系或 TailFrame 依赖关系。

### 3.6 拖拽、页面绑定、保存刷新

覆盖对象：StoryBoard 主界面和 `asset_history_services.py`。

| 类别 | 全量案例来源 | 关键断言 |
| --- | --- | --- |
| 上传 | `UP-01` 到 `UP-09` | 图片/视频/声音按类型入库，无效文件不入库，同名不覆盖 |
| 拖拽基础 | `DR-01` 到 `DR-10` | source/image/audio/final_video 各写目标字段；历史素材恢复到 Working |
| 组合 | `CB-01` 到 `CB-12` | 同 Dialogue 多槽互不覆盖，同 Scene 多 Dialogue 互不串写 |
| 替换 | `RP-01` 到 `RP-08` | 旧 generated 进 history，upload/original 只解绑或回素材池 |
| 误拖 | `WT-01` 到 `WT-08` | 类型不匹配不写任何有效字段 |
| 移除 | `RM-01` 到 `RM-10` | generated 进 history，upload/original 不进 history |
| 上传池删除 | `DL-01` 到 `DL-08` | 删除上传素材清空所有引用，不进入 history |
| history 最终删除 | `HD-01` 到 `HD-05` | 只有 history 删除才永久删除文件 |
| 保存刷新 | `SV-01` 到 `SV-12` | Save / 刷新 / 重进 Task 后状态一致；Split/Merge 不改 key |
| 历史素材 | `HS-01` 到 `HS-10` | history 恢复、再次重排、删除 Dialogue 都不产生坏引用 |
| 声音槽 | `TT-01` 到 `TT-13` | `Audio.src`、`dialogue_asset_key`、`working_assets.audio.path` 一致 |

当前已有 Task #31 回归覆盖资产绑定、释放、回收、history 恢复/最终删除、上传池删除联动解绑、Dialogue 删除/合并回收、保存重排保留、视频 raw 分片播放。后续改动时，如果影响拖拽或保存，必须至少复跑该类脚本或等价页面验证。

### 3.7 Shot / Scene / Dialogue 合并分裂

覆盖对象：`kouboStoryboardModel.js`、`DialogueCard.jsx`、`ShotCard.jsx`、保存接口和后端归档。

| 操作 | 必查点 |
| --- | --- |
| Add Dialogue | 新增 Dialogue 必须生成独立 `dialogue_id` 和 `dialogue_asset_key`，不能从 Scene ID 派生固定 key |
| Split Scene | 原 Dialogue 的 `dialogue_asset_key` 不变，素材绑定跟随原 Dialogue |
| Merge Scene | 仍存在的 Dialogue 素材不变，不生成 history |
| Split Shot | 所有受影响 Dialogue 的 `dialogue_asset_key` 不变且不重复 |
| Merge Shot | 素材路径不因 Shot 合并丢失或覆盖 |
| Merge Dialogue | 保留前一个 Dialogue 素材；消失 Dialogue 的 generated 素材进入 history |
| Delete Dialogue | generated 进 history；upload/original 只解除绑定；当前 StoryBoard 不再引用 |
| 拖拽后结构编辑 | 拖拽绑定后的 Split/Merge/Move 不得改变绑定归属 |

当前自动化锚点：

| 测试 | 作用 |
| --- | --- |
| `test_frontend_new_dialogue_generates_independent_edit_and_asset_keys` | 新 Dialogue 独立生成 `dlg_*` 与 `dak_*` |
| `test_storyboard_groups_srt_under_scene_without_merging_dialogue_records` | Scene 聚合 SRT，但 Dialogue 仍独立 |
| `tests/storyboard/fixed_reorganize_timing_boundaries.mjs` | 固定拆分时间边界 |
| `tests/storyboard/fixed_reorganize_task18_regression.mjs` | 合并/新增 Dialogue 后固定重排仍保留行 |

### 3.8 执行与拼接

覆盖对象：`05_02_VideoPlanExecutor.py`、`06_01_VideoPlanComposer.py`。

| 类别 | 关键断言 | 当前自动化 |
| --- | --- | --- |
| 05-02 执行闭环 | Prompt、Audio、Image、Raw、Final、TailFrame 落盘；StoryBoard 绑定回写 | `test_executes_segment_outputs_prompt_audio_image_video_lipsync` |
| 空镜执行 | product-only，不出现 talking-head / lip-sync 指令 | `test_cutaway_prompts_are_product_only_without_host_or_talking_face` |
| 绑定视频 | 不跑视频模型，执行音频替换/时长同步 | `test_bound_video_segment_materializes_without_video_model` |
| Raw/Final 覆盖备份 | `--force` 清工具目录但不删 StoryBoard Working；覆盖前进 history | `test_force_cleans_tool_dir_but_backs_up_storyboard_outputs` |
| Provider 防线 | duration、代理、API key、payload、模板、参考视频 | `test_wan_rtv_*`、`test_xai_*`、`test_gemini_*`、`test_openai_*`、`test_sync_lipsync_*` |
| Scene Compose | Scene 输出写回 Working 和 StoryBoard | `test_scene_compose_outputs_to_working_and_writes_storyboard` |
| Shot Compose | 先拼 Scene，再拼 Shot | `test_shot_target_composes_scenes_before_shot` |
| Task Compose | 先拼 Shot，再拼 ShotPlan | `test_task_target_composes_shots_before_shot_plan` |
| 缺 Segment Final | Scene blocked，不平铺跳过 | `test_missing_segment_video_blocks_scene` |

### 3.9 口播 / 空镜专项覆盖

覆盖对象：`05_01` 的 `need_lipsync` 规划、`05_02` 的最终视频执行路径、Prompt 模板选择、尾帧是否可继承。

| 类型 | 业务规则 | 当前自动化 | 结论 |
| --- | --- | --- | --- |
| 口播规划 | 默认可见人脸段按口播处理：`need_lipsync=true`、`need_sync=true`、`sync_mode=lipsync`、`lipsync_reason=visible_face` | `test_video_outputs_use_first_dialogue_key_and_audio_is_dialogue_level` | 已覆盖 |
| 口播 Prompt | talking-head 视频 Prompt 必须使用 `VIDEO_GROK_*_TALKING_HEAD` 模板，包含中文口播 speech 段，不混入 cutaway pitfalls | `test_grok_talking_head_prompt_splits_speech_storyboard_and_scopes_pitfalls` | 已覆盖 |
| 口播尾帧 | `is_talking_head=true` 或兼容默认口播时，当前 Segment 尾帧允许作为后续空段首帧来源 | `test_talking_head_tail_allows_following_empty_split_segment` | 已覆盖 |
| 空镜规划 | `video_plan.is_talking_head=false` 或用户标记 cutaway 时，`need_lipsync=false`、`need_audio_video_sync=true`、`sync_mode=audio_replace_retime` | `test_dialogue_cutaway_flag_disables_lipsync_for_segment` | 已覆盖 |
| 空镜执行 | 空镜不调用 lipsync；执行 audio/video sync，最终 `Video_Final` 来自音频替换和时长同步后的 sync video | `test_need_lipsync_false_retimes_video_and_replaces_audio` | 已覆盖 |
| 空镜绑定视频 | 已绑定视频段不跑视频模型、不跑 lipsync；只物化视频并做音频替换/时长同步，再抽 TailFrame | `test_bound_video_segment_materializes_without_video_model` | 已覆盖 |
| 空镜 Prompt | product-only cutaway 不使用 HOST_REFERENCE，不生成主播、人脸、嘴型、说话主体，不出现 talking-head 正向描述 | `test_cutaway_prompts_are_product_only_without_host_or_talking_face` | 已覆盖 |
| 空镜尾帧 | `is_talking_head=false` 的 cutaway/product-only 尾帧不得被后续空段继承 | `test_cutaway_tail_blocks_following_empty_split_segment` | 已覆盖 |

仍建议补的页面级验证：

| 待补 UI 案例 | 原因 |
| --- | --- |
| 在 StoryBoard 页面把某个 Dialogue 从口播切为空镜，再打开 Video Plan / Video Only Plan | 当前自动化覆盖后端计划和执行，但页面切换入口、保存、刷新后的可见状态需要截图证据 |
| 执行中切换口播 / 空镜入口 disabled | 需求里要求 `05_02` 正在运行时不允许改 Dialogue 口播状态，当前金标准应保留页面验收 |
| 空镜恢复口播后重跑 Plan | 需要确认旧 `need_lipsync=false` / execution state 不会污染新口播计划 |

## 4. 最精简高覆盖回归集

下面这套是后续改状态或改代码时的“最短路径”。它不是替代全量用例，而是每次改动最少要跑、且能覆盖主要代码分支的集合。

### 4.1 必跑命令

```bash
python3 scripts/koubo_video_plan_slot_state_check.py --compare
```

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/contracts/test_koubo_storyboard_slot_state_contract.py \
  backend/tests/contracts/test_koubo_storyboard_dialogue_asset_key_contract.py \
  backend/tests/contracts/test_koubo_storyboard_manual_asset_status_contract.py \
  backend/tests/contracts/test_koubo_storyboard_stale_edit_contract.py \
  backend/tests/contracts/test_koubo_non_single_scene_plan_state_contract.py \
  backend/tests/contracts/test_analysis_v1_image_plan_tools_contract.py \
  backend/tests/contracts/test_analysis_v1_video_only_plan_tools_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_executor_resilience_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_composer_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_settings_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_image_gemini_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_image_reference_provider_contract.py
```

真实 UI runner 是默认门禁的一部分：

```bash
npm --prefix frontend run test:e2e:koubo-storyboard
```

这个 Playwright 入口打开真实 Koubo StoryBoard 前端，使用确定性 API mock 跑 8 个单一职责 UI 测试。精简的定义是“每个测试只证明一个业务不变量”，不是把所有断言塞进一条长流程：

| 测试 | 覆盖 |
| --- | --- |
| `frontend/e2e/koubo-storyboard/slot-identity-rendering.mjs` | 固定槽位渲染、`dialogue_asset_key` 优先、毒化 `by_dialogue_id` 不被误用 |
| `frontend/e2e/koubo-storyboard/upload-asset-pool.mjs` | 上传入口、multipart 请求、上传后素材池卡片出现、tab 保持在上传素材 |
| `frontend/e2e/koubo-storyboard/binding-save-reload.mjs` | 真实 pointer 拖拽绑定原图/新图/Raw/Final、保存 payload、刷新后可见状态 |
| `frontend/e2e/koubo-storyboard/final-unbound-confirm.mjs` | Final 文件存在但未绑定时显示确认入口，点击后写回 Final 绑定 |
| `frontend/e2e/koubo-storyboard/talking-head-toggle.mjs` | 口播/空镜右键入口、保存 payload、刷新前可见状态切换 |
| `frontend/e2e/koubo-storyboard/dialogue-merge-archive-boundary.mjs` | Merge Dialogue 后保留 Dialogue 的 key/绑定稳定，消失 Dialogue 的 generated Working 引用不再进入保存 payload，为后端 history 归档提供正确边界 |
| `frontend/e2e/koubo-storyboard/plan-modal-status.mjs` | Video Plan / Image Plan / Video Only Plan 三个真实 modal、Raw done、Image done、Final disabled、copy-final pending、移动宽度 |
| `frontend/e2e/koubo-storyboard/structure-clear-isolation.mjs` | Split/Merge 后保存 key 稳定、清空新图不级联 Raw/Final |

默认入口 `frontend/e2e/koubo-storyboard-regression.mjs` 只负责编排这 8 个测试；也可以用 `npm --prefix frontend run test:e2e:koubo-storyboard:identity|upload|binding|final|talking-head|merge|plans|structure` 单跑。脚本不会启动 Vite；运行前需要 `OPENCREW_E2E_FRONTEND_URL` 或默认 `http://127.0.0.1:18080` 可访问，否则会写入 `ok=false` 的 `result.json` 并提示启动 `npm --prefix frontend run dev -- --host 127.0.0.1 --port 18080`。每个测试都会单独截图并写进 `result.json`，便于定位失败。

该 runner 的 key 断言不是只看保存 payload：fixture 会故意让 `by_dialogue_id` 返回错误 Raw 状态，页面必须通过 `dialogue_asset_key` 的 `by_asset_key` 状态显示 Raw；Video Plan / Video Only Plan 还会断言 Raw done、Final disabled / copy-final pending，以及输出路径没有退回 `srt_id`、`dialogue_id`、Scene ID 或 Shot ID。它不替代依赖真实 workspace 文件的人工验收；真实 Working 文件存在性、history 归档、媒体播放、真实上传文件和长拖拽边界仍要补 4.3 的浏览器页面验证。

归档脚本说明：

- `StoryBoardRegression/script/analysis-v1/test_*.py` 已适配当前 repo root，最新针对性运行 `61 passed, 1 skipped`；它们是可复跑归档 fixture，但默认门禁仍以 4.1 的当前仓库 contract 命令为准。
- `StoryBoardRegression/script/storyboard-node/*.mjs` 的前端 import 已指向当前 `frontend/src/modules/koubo/OCStoryBoard/...`；`fixed_reorganize_task18_regression.mjs` 缺少可选 `STORYBOARD_PLAN` 时会跳过并写报告。
- `StoryBoardRegression/script/tools/koubo_video_plan_slot_state_check.py` 已可从当前仓库根定位文档，但默认门禁仍使用 `scripts/koubo_video_plan_slot_state_check.py`。

### 4.2 必跑案例清单

| ID | 最小案例 | 覆盖分支 |
| --- | --- | --- |
| M-01 | `slot_state_contract` 全部用例 | VP/IP/VOP 32 矩阵、Prompt、黄红、Final 未绑定、灰色 reason |
| M-02 | `dialogue_asset_key_contract` 全部用例 | key 只用 `dialogue_asset_key`；新增 Dialogue 独立 `dlg_*`/`dak_*` |
| M-03 | `manual_asset_status_contract` 全部用例 | 手动 key 覆盖 plan key；三类 Plan 状态读取当前 StoryBoard 绑定 |
| M-04 | `test_task_target_splits_multi_image_and_overlong_anchor_range` | 一个 Scene 多 Segment、长范围切段 |
| M-05 | `test_single_image_long_scene_splits_near_max_dialogue_boundary` | 单图长 Scene 按 Dialogue 边界切段 |
| M-06 | `test_generated_image_slot_can_be_first_frame_without_image_prompt` | 已有新图不再需要 Image Prompt |
| M-07 | `test_placed_uploaded_image_records_materialize_copy_action` | 上传图落位后物化到标准 Working |
| M-08 | `test_bound_video_dialogue_starts_new_audio_synced_segment` | 绑定 Final Video 单独成段，后续尾帧继续 |
| M-09 | `test_cutaway_tail_blocks_following_empty_split_segment` + `test_talking_head_tail_allows_following_empty_split_segment` | 空镜尾帧阻断 vs 口播尾帧继承 |
| M-10 | `test_task_scope_carries_tail_frame_across_shots` | 跨 Shot 尾帧矩阵 |
| M-11 | `test_blocked_scene_does_not_drop_following_visual_scene` | skipped / blocked 不吞后续可执行 Scene |
| M-12 | `test_video_outputs_use_first_dialogue_key_and_audio_is_dialogue_level` | Segment 输出用首 Dialogue key；Audio 仍是 Dialogue 级 |
| M-13 | `test_executes_segment_outputs_prompt_audio_image_video_lipsync` | 05-02 执行闭环和绑定回写 |
| M-14 | `test_video_outputs_use_first_dialogue_key_and_audio_is_dialogue_level` + `test_grok_talking_head_prompt_splits_speech_storyboard_and_scopes_pitfalls` | 口播 lipsync 规划与 talking-head Prompt |
| M-15 | `test_dialogue_cutaway_flag_disables_lipsync_for_segment` + `test_need_lipsync_false_retimes_video_and_replaces_audio` | 空镜规划、音频替换和时长同步 |
| M-16 | `test_cutaway_prompts_are_product_only_without_host_or_talking_face` | 空镜 product-only Prompt 不混入口播 |
| M-17 | `test_bound_video_segment_materializes_without_video_model` | 绑定视频物化，不跑视频模型 |
| M-18 | `test_force_cleans_tool_dir_but_backs_up_storyboard_outputs` | 覆盖前 history 备份 |
| M-19 | `test_storyboard_video_delete_archives_video_only_raw_tail_and_clears_execution_steps` | 删除 Final 时 Raw/TailFrame 归档，Prompt 保留，执行态清理 |
| M-20 | `test_clear_new_image_archives_standard_image_slot_orphans` | 清空新图时归档孤儿标准槽位文件 |
| M-21 | `test_scene_compose_outputs_to_working_and_writes_storyboard` + `test_task_target_composes_shots_before_shot_plan` | 层级拼接和写回 |
| M-22 | `npm --prefix frontend run test:e2e:koubo-storyboard` | 8 个单一职责真实 UI 测试：key 优先槽位、上传素材池、真实拖拽绑定、Final 确认、口播/空镜、Merge Dialogue 消失引用、三类 Plan modal、结构编辑和清槽隔离 |
| M-23 | 两个 Node fixed reorganize 测试 | Shot/Scene/Dialogue 合并新增后的固定重排；当前为归档脚本，路径适配后再纳入门禁 |

### 4.3 手工页面最小回归

自动化跑完后，如果本次改动碰到前端/UI/绑定接口，先跑 `frontend/e2e/koubo-storyboard-regression.mjs`。如果改动超出该 smoke 覆盖范围，再至少执行以下 8 个页面案例：

| ID | 操作 | 必查 |
| --- | --- | --- |
| UI-01 | 上传图片，拖到同一 Dialogue 的原图槽 | 写入 `{asset_key}_Image_Source.*`，不是 `Image_New` |
| UI-02 | 上传图片，拖到同一 Dialogue 的新图槽 | 写入 `{asset_key}_Image_New.*`，Image Plan/Video Plan 新图绿 |
| UI-03 | 上传视频，分别拖到新视频和终视频槽 | Raw 只物化文件；Final 同时写 `working_assets.video.path` |
| UI-04 | Raw 存在、Final 不存在、Audio 不存在 | Video Plan 终视频灰；Video Only Plan 拷贝白 |
| UI-05 | 删除新图 | 只清新图并归档 `Image_New`，Raw/Final/Prompt 不动 |
| UI-06 | 拖拽后 Split Scene / Merge Scene / Split Shot / Merge Shot | `dialogue_asset_key` 不变，素材不串 |
| UI-07 | 合并两个 Dialogue | 消失 Dialogue 的 generated 素材进 history，保留 Dialogue 素材不变 |
| UI-08 | Save -> 刷新 -> 重进 Task | UI、JSON、Working 文件三者一致；素材池 tab 不跳回原视频素材 |

证据要求：`Koubo_Scene_状态回归测试记录_20260617.md` 里已经标记过一批 PNG 不是目标 OpenCrew 界面的有效浏览器截图，不能作为 UI 通过证据。后续页面回归必须重新截图，并同时保留接口 JSON、Working 文件存在性和浏览器可见状态。

## 5. 代码实现踩坑清单

这部分是后续维护时最容易踩的坑，也是测试失败时优先排查的方向。

| 坑 | 表现 | 防线 |
| --- | --- | --- |
| 旧路径引用 | 文档或脚本还指向旧 `OpenClip` 路径 | 以当前 checkout 为准：前端 `OpenCrew/frontend/src/modules/koubo/KouboStoryBoard`，后端 `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard` |
| docs 双目录分叉 | 根目录 `docs` 与 `OpenCrew/docs` 同名文件内容不同，脚本读取到的不是人工刚改的那份 | 自动化读取的槽位表在 `OpenCrew/docs`；新增金标准文档需同步到两边或明确单一来源 |
| key 混用 | 使用 `srt_id`、`dialogue_id`、Scene ID 做绑定或输出名 | 跑 `test_koubo_storyboard_dialogue_asset_key_contract.py` |
| Raw/Final 混槽 | Raw 写进 `working_assets.video`，或 Final 只写文件不写绑定 | 检查 `asset_history_services.py` 的 `raw_video` / `final_video` 分支和 Final 绑定 |
| execution JSON 造绿 | 旧 success/result 让不存在的槽位显示绿色 | 绿色只认 Working 标准文件；跑 `slot_state_contract` |
| running/failed 覆盖文件 | 文件已存在但旧 running/failed 让 UI 黄/红 | 状态优先级必须是绿最高 |
| 灰色 reason 不分型 | 上游补齐后，下游已消费任务也变白 | 必须输出 `blocked_waiting_input` 或 `skipped_consumed_by_downstream` |
| 删除前置级联下游 | 删除原图/新图时误删 Raw/Final | 删除不级联；只处理目标槽位 |
| 清槽只清 JSON | UI 空槽但 Working 旧文件仍让派生状态变绿 | 清槽必须同步移动/归档标准 Working 文件 |
| Prompt 被误删 | 删除新图/Raw 时 ImagePrompt/VideoPrompt 丢失 | Prompt 是例外，不随普通槽位清除 |
| Final 文件未绑定误判失败 | 工具中断后 Final 文件存在但 JSON 空，UI 要求重跑 | 显示绿色 + 修复绑定提示 |
| bound video 合并后续 Dialogue | 已绑定 Final 的 Dialogue 覆盖后续无视觉 Dialogue | bound video 单独成段，后续依赖尾帧继续 |
| 空镜尾帧误继承 | cutaway/product-only 尾帧被下一段当口播首帧 | `is_talking_head=false` 必须阻断继承 |
| 口播 Prompt 被空镜模板污染 | talking-head 段使用 cutaway 模板或缺少 Speech / 口播段 | 跑 `test_grok_talking_head_prompt_splits_speech_storyboard_and_scopes_pitfalls` |
| 空镜 Prompt 混入口播 | product-only 场景出现嘴型、眨眼、talking head | 跑 `test_cutaway_prompts_are_product_only_without_host_or_talking_face` |
| Scene scope 引用未来尾帧 | 单 Scene 规划引用本次不会生成的未来产物 | Scene scope 只能用真实存在的上一 Scene TailFrame |
| `tailframe` target 误入图片分支 | `asset-clear target_kind=tailframe` 没被拒绝，落入默认 image 分支并清掉 `Image_New` | 清理接口必须显式白名单 target；尾帧不通过普通槽位 target 清 |
| Image_Source 被当新图 | 原图绑定后 Video Plan 新图直接绿 | 原图只能触发生成新图，不能作为视频首帧完成态 |
| 上传图片未物化 | 上传图路径直接作为 Working 新图，后续计划找不到标准文件 | 拖到新图槽必须物化成 `{asset_key}_Image_New.*` |
| 手动 asset key 被 plan key 覆盖 | 手动 `dialogue_asset_key` 的素材状态被旧 `asset_key` 隐藏 | 跑 `manual_asset_status_contract` |
| `koubo_storyboard_edit.json` 与 `srt_storyboard.json` 不同步 | 生成图片/视频后页面仍显示未绑定 | 执行工具发布时两个 JSON 都要更新或标 warning/failed |
| stale edit 覆盖新源 | 04-02 重新生成后旧编辑稿覆盖新 StoryBoard | stale edit 必须拒绝保存或归档 |
| 旧 plan / ui_cache 复用 | 主界面已清空图片/视频，但 Generation Plan 继续显示上一轮绿色 | 媒体绑定变化必须让 plan cache 失效，并重置旧 execution state/result |
| history 变大 JSON | 所有历史写进单一 JSON/JSONL，难追溯 | History 是文件夹和 manifest，不做大合并日志 |
| 上传素材误进 history | 删除 upload/original 绑定时复制进 history | upload/original 只解除绑定或回素材池 |
| 原视频素材池被清空 | 解除绑定误当删除素材本体 | 原视频素材是只读参考池，不能删除本体 |
| 素材池 tab 跳回 | 拖拽/上传/删除后右侧 tab 回到原视频素材 | tab 状态提升到编辑器层，必要时 URL/localStorage 兜底 |
| 音频播放用临时 ID | 听得到声音但 Dialogue 音频槽没绑定 | 验证 `Audio.src`、`dialogue_asset_key`、`working_assets.audio.path` 三者一致 |
| 删除音频后旧缓存播放 | 清槽后浏览器继续请求旧 `Audio_Final` 404 | 清空槽位时同步清播放缓存，复用前先校验文件存在 |
| 05-02 只写文件不绑 JSON | Working 有新图/Final，但 UI 和 05-01 仍认为缺失 | 发布产物后校验文件、JSON 路径、JSON 指向同一文件 |
| 图片画幅漂移 | 生成 2:3 图进入 9:16 视频链路 | 发布前校验并归一化 9:16 |
| 覆盖前无备份 | `--force` 或重跑直接覆盖用户可见 Working 文件 | 覆盖前进入 `assets/history/batch_*` |
| 拼接平铺 Segment | Task 输出直接 concat 所有 Segment | 必须按 Segment -> Scene -> Shot -> ShotPlan 层级拼 |
| 前端输入失焦 | 每次输入触发 `renumberPlan(copy(plan))` 导致组件重挂载 | 文本/名称编辑用局部状态，blur 时再提交 normalize |
| 伪 UI 证据 | 截图不是目标页面或不可审计，却被写成“通过” | 页面回归必须重新截图，并保留接口 JSON 与文件存在性证据 |

## 6. 为什么这套回归能证明产出一致

这套回归不是用“代码行覆盖率”来证明，而是用“业务状态分支 -> 可观察产物 -> 断言”的闭环来证明。只要修改后的代码仍然通过这些断言，就说明同一输入、同一绑定状态、同一执行状态下，外部可见结果没有漂移。

证明链路如下：

1. **输入状态被枚举**：Video Plan / Image Plan / Video Only Plan 的绑定态、执行态、文件存在态、上游依赖态已经落在槽位颜色案例表和合同测试里；口播、空镜、bound video、Final 未绑定但文件存在、running/failed 不覆盖绿色等特殊态也被单独列入。
2. **唯一标识被锁定**：测试要求绑定只能跟 `dialogue_asset_key` 走，不能退回 `srt_id`、`dialogue_id`、Scene ID。这样 Shot / Scene / Dialogue 合并、分裂、新增、拖拽后，只要 key 不变，同一素材就仍然绑定在同一个语义槽位上。
3. **Segment Truth 被锁定**：一个 Scene 拆多个 Segment、跨 Shot 的槽位矩阵、口播尾帧续接、空镜尾帧阻断，都是从同一套 Segment 结构派生；因此 Image Plan、Video Plan、Video Only Plan 不允许各自生成一套互相矛盾的 Segment。
4. **执行产物被锁定**：回归不只看 JSON success，还检查 Working 标准文件、StoryBoard JSON、计划 JSON、执行摘要和页面槽位状态。这样可以挡住“执行 JSON 造绿但文件不存在”“文件存在但绑定没写回”“旧缓存让页面误绿”等分支。
5. **破坏性操作被锁定**：删除、清槽、force 重跑、Scene/Shot/Dialogue 结构变化，都要求 generated 素材进入 history，upload/original 不误删，Prompt 不被误清，Final/Raw 不串槽。
6. **页面结果被人工复核**：自动化覆盖服务和工具分支；UI-01 到 UI-08 负责补齐浏览器拖拽、页面绑定、刷新后重进、tab 状态等自动化没有完全覆盖的交互分支。

因此，严格的证明口径是：

> 修改前，这组案例定义了“当前已经验证过的外部行为”。修改后，如果同一组自动化测试和命中的页面回归全部通过，并且关键输出文件、JSON、页面槽位三者一致，则可以认为修改没有改变这些被覆盖分支的产出结果。

这个证明不是数学意义上覆盖所有未知输入；它保证的是金标准案例集列出的业务等价类不漂移。未列入的新增业务态，需要先补案例，再谈回归保障。

### 6.1 当前实跑证据

2026-06-26 在当前工作区实跑结果如下：

| 验证项 | 命令/范围 | 结果 | 结论 |
| --- | --- | --- | --- |
| 槽位颜色案例表一致性 | `python3 scripts/koubo_video_plan_slot_state_check.py --compare` | `differences=0` | 文档表与脚本派生规则一致 |
| 真实 UI runner | `npm --prefix frontend run test:e2e:koubo-storyboard` | 通过；`run_id=20260626071732`，8 个测试截图在 `test-results/koubo-storyboard-regression/20260626071732/` | 覆盖真实 StoryBoard 前端、key 优先槽位、上传素材池、真实拖拽绑定、保存刷新、Final 确认、口播/空镜状态、Merge Dialogue 消失引用、三类 Plan modal、结构编辑和清槽隔离 |
| 后端合同测试 | `backend/.venv/bin/python -m pytest -q --tb=no backend/tests/contracts` | 通过；`485 passed, 1 warning, 92 subtests passed` | 合同门禁当前全绿 |
| 归档 pytest | `backend/.venv/bin/python -m pytest -q --tb=short StoryBoardRegression/script/analysis-v1/test_*.py` | 通过；`61 passed, 1 skipped` | 归档 fixture 已可在当前 checkout 复跑，但仍不替代 4.1 当前仓库门禁 |

当前可以说的是：覆盖矩阵已经建立；槽位表一致性、后端合同、归档 pytest 和真实 UI runner 已在当前工作区通过。仍不能把确定性 UI mock 说成真实 workspace 文件验收；真实 Working 文件、history 归档和媒体播放仍按 4.3 留手工证据。

### 6.2 当前未通过案例索引

本节记录 2026-06-26 实跑时未通过、或暂时不能作为强门禁的案例。当前后端合同失败项已清零；后续如果出现新的失败，再按“失败原因 / 预期结果”补回索引。

#### 6.2.1 当前后端合同测试未通过项

当前无。

#### 6.2.2 测试启动环境注意事项

| 现象 | 原因 | 正确处理 |
| --- | --- | --- |
| 系统 `python3` 收集测试时报 `int | None` 类型错误 | 系统 Python 是 3.9，不满足项目 `>=3.10,<3.14` 语法要求 | 使用项目根 `.venv` 的 Python |
| 后端 contract 直接跑时报 VCR cassette 路径错误 | 根目录 `pytest-recording` fixture 误套到 `OpenCrew/backend/tests/contracts` | 临时关闭 recording 插件后再看业务失败，例如加 `-p no:recording` |

## 7. 回归结论口径

一次状态修改或代码更改只有同时满足以下条件，才可以认为通过金标准回归：

1. 槽位颜色脚本 `--compare` 无差异。
2. 合同测试和 Analysis_V1 核心测试通过。
3. 本次改动命中的 UI 操作完成页面最小回归。
4. 任一槽位的 UI、StoryBoard JSON、Working 标准文件三者一致。
5. Segment 数量、顺序、Dialogue 包含关系在 Video Plan / Image Plan / Video Only Plan 之间保持同一套 Segment Truth。
6. Shot / Scene / Dialogue 合并、分裂、新增、删除后，仍存在的 Dialogue 保留 `dialogue_asset_key` 和绑定；消失 Dialogue 的 generated 素材进入 history。
7. 发现失败时先判定是 `key`、`Working 文件`、`绑定 JSON`、`execution JSON`、`Segment Truth`、`history 归档`、`UI 缓存` 哪一层漂移，再修代码；不要靠改测试表绕过。
