# DanceMimic_V1 浏览器真实验收用户操作手册

验收日期：2026-06-30

前端地址：`http://127.0.0.1:18080/`

最终验收 run：`1782774862261`

参考视频选择/上传 UX 增量验证 run：`1782780046957`

验收目标：上传或从素材库选择一个舞蹈参考视频，并选择 AI 生成的人物/数字人目标图，让目标人物按参考舞蹈动作生成新视频。

## 验收结论

本轮使用真实浏览器、真实后端、真实 OpenRouter / MaxSR2 `input_references` 视频生成链路完成三条主力 fixture 的完整业务流程验证。所有最终产物均已在 StoryBoard 页面和 DanceMimic run 页面可见，并落盘为 `SessionOutput/storyboard/Working/*_Video_Final.mp4`。

| Fixture | 角色 | Task | Session | 最终视频数 | 状态 | 页面 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `dance_solo_frontal_studio.mp4` | 干净正脸基线(动作小) | 174 | 233 | 1 | completed | `#/koubo-storyboard/tasks/174` |
| `dance_solo_bigmotion_studio.mp4` | 纯白影棚,大动作压力 | 175 | 234 | 2 | completed | `#/koubo-storyboard/tasks/175` |
| `dance1.mp4` | 单人正脸,城市夜景,真实编舞 | 176 | 235 | 2 | completed | `#/koubo-storyboard/tasks/176` |

目标人物图使用 AI 生成的人物/数字人图：`ToolLibrary/DanceMimic_V1/test_fixtures/target_ai_digital_human_avatar.png`。本轮最终验收从素材库选择已上传的目标图，素材库路径为 `/Users/macmini-4/.opencrew/dance_mimic_v1/target_images/1782771892738_target_ai_digital_human_avatar.png`。

## 业务目的

DanceMimic 的业务闭环是：用户给出一个舞蹈参考视频，再选择一个目标人物/商品模特/数字人图片，系统生成“目标人物按参考舞蹈跳舞”的新视频。原始舞蹈视频只作为动作、节奏、姿态参考；最终视频主体应来自目标人物图，不应把原参考视频当成最终视频直接绑定。

## 操作步骤

1. 打开 `http://127.0.0.1:18080/`，进入任务列表页面。
2. 点击 `DanceMimic` 创建按钮。
3. 在创建弹窗填写任务名，并在 `参考舞蹈视频` 区域选择参考视频。默认使用 `素材库`，点击要复刻的舞蹈视频卡片；如需导入本地视频，切换到 `上传` 并选择 `mp4/mov/m4v/webm` 文件；`路径` 只作为高级 fallback 使用。
4. 在目标人物图区域选择 `素材库`，点击 AI 数字人目标图；如果素材库中没有目标图，切换到 `上传` 并上传人物/数字人图片。
5. 分段参数保持默认：目标分段秒数 `8`，最小分段秒数 `4`。
6. 检测 manifest 留空，使用真实 face detector；CI 中仍可使用 fake/fixed bbox fixture。
7. 参考隐私模式选择 `强隐私轮廓`，并勾选 `无人脸时阻断`、`创建后运行`。
8. 点击 `创建 DanceMimic`，进入 run/status 页面。
9. 等待 DanceMimic run 页面显示完成，确认 `Variables.json`、源参考视频、目标人物图、reference media manifest、reference segments manifest、SRT StoryBoard、StoryBoard seed 均存在。
10. 打开 StoryBoard 页面，确认 reference video 只作为参考素材显示，没有被绑定为 `Video_Final`。
11. 点击 `生成计划`，确认视频计划每个 segment 都走 `openrouter`、`MaxSR2`、`input_references`。
12. 点击 `执行生成计划`，等待执行状态 completed。
13. 回到 StoryBoard 页面，确认每个 segment 的 `Video_Final` 可见；再回到 DanceMimic run 页面确认最终 artifacts 可见。

## 参考视频选择/上传 UX 增量更新

本节只记录 2026-06-30 对创建弹窗参考视频入口的增量更新，不替代上方三条主力 fixture 的完整业务流验收结论。

### 可用入口

| 入口 | 用途 | 验证结果 |
| --- | --- | --- |
| `素材库` | 从系统已有参考视频、DanceMimic test fixtures、历史 StoryBoard/reference 视频资产中选择 | 已通过浏览器选择 `dance_solo_frontal_studio.mp4` |
| `上传` | 上传本地参考舞蹈视频 | 已通过浏览器上传 `dance1.mp4`，接口返回 200，并自动选中新上传视频 |
| `路径` | 高级 fallback，用于直接填写服务器本地可访问路径 | 已通过浏览器打开并保留当前选中路径 |

### 用户操作

1. 打开 `DanceMimic 创建` 弹窗后，找到 `参考舞蹈视频` 区域。
2. 默认停留在 `素材库` 标签页，点击视频卡片即可选中参考视频；选中后卡片出现蓝色边框，下方显示已选文件名。
3. 如果参考视频不在素材库，点击 `上传`，选择本地 `mp4/mov/m4v/webm` 文件。上传成功后系统自动回到素材库并选中新上传视频。
4. 如需调试或使用服务器已有绝对路径，点击 `路径`，在 `参考视频路径` 输入框中填写路径。

### 增量截图

![reference video library](acceptance_artifacts/1782780046957/reference_video_ux/02-reference-video-library.png)
![reference video library selected](acceptance_artifacts/1782780046957/reference_video_ux/03-reference-video-library-selected.png)
![reference video upload selected](acceptance_artifacts/1782780046957/reference_video_ux/04-reference-video-upload-selected.png)
![reference video path fallback](acceptance_artifacts/1782780046957/reference_video_ux/05-reference-video-path-fallback.png)

## 路由与合规断言

- `target` 固定为 `dance_mimic_v1`。
- DanceMimic 工具链不复用 Analysis_V1 七步任务链。
- `Variables.json` 工具级输入使用 `source_video_path`。
- 02 使用真实 detector，并保留 fake/fixed bbox CI fixture 主路径。
- 02 QA 不合格、无脸且开启阻断、bbox 越界/为空、输出空文件时必须 blocked。
- 03 产出的 reference video assets 不能当作 `Video_Final`。
- 后续视频生成走 OpenRouter / MaxSR2 `input_references` 主路径。
- 本轮 5 个 provider 调用均记录 `generate_audio=false`、`send_input_references=true`、`input_reference_count=2`。

## 最终产物

| Fixture | 最终视频 | 时长 | 分辨率 | 大小 |
| --- | --- | ---: | --- | ---: |
| frontal | `/Users/macmini-4/.opencrew/sessions/233/workspace/SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4` | 7.16s | 720x1280 | 999998 bytes |
| bigmotion | `/Users/macmini-4/.opencrew/sessions/234/workspace/SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4` | 5.96s | 720x1280 | 1147809 bytes |
| bigmotion | `/Users/macmini-4/.opencrew/sessions/234/workspace/SessionOutput/storyboard/Working/dak_0002_Video_Final.mp4` | 5.92s | 720x1280 | 1104846 bytes |
| dance1 | `/Users/macmini-4/.opencrew/sessions/235/workspace/SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4` | 7.96s | 720x1280 | 1456085 bytes |
| dance1 | `/Users/macmini-4/.opencrew/sessions/235/workspace/SessionOutput/storyboard/Working/dak_0002_Video_Final.mp4` | 5.96s | 720x1280 | 999717 bytes |

这些视频与原始参考视频的区别：原始视频是真人舞蹈素材；最终视频是模型根据目标 AI 数字人图片和参考动作重新生成的新视频。参考视频在送入 provider 前已遮脸并转换为强隐私轮廓，仅用于动作、节奏和姿态参考。

## 页面入口

- frontal DanceMimic：`http://127.0.0.1:18080/#/dance-mimic/tasks/174`
- frontal StoryBoard：`http://127.0.0.1:18080/#/koubo-storyboard/tasks/174`
- bigmotion DanceMimic：`http://127.0.0.1:18080/#/dance-mimic/tasks/175`
- bigmotion StoryBoard：`http://127.0.0.1:18080/#/koubo-storyboard/tasks/175`
- dance1 DanceMimic：`http://127.0.0.1:18080/#/dance-mimic/tasks/176`
- dance1 StoryBoard：`http://127.0.0.1:18080/#/koubo-storyboard/tasks/176`

## 截图证据

### frontal

![frontal task list](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-01-task-list-before-create.png)
![frontal create modal](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-02-create-modal-filled-real-detector-target-image.png)
![frontal run page](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-03-run-page-started.png)
![frontal DanceMimic artifacts](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-04-dance-mimic-completed-artifacts.png)
![frontal StoryBoard opened](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-05-storyboard-opened-target-image-reference-visible.png)
![frontal video plan](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-06-video-plan-generated-openrouter-reference-target.png)
![frontal before execute](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-07-before-execute-video-plan.png)
![frontal executing](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-08-execution-queued-running.png)
![frontal execution completed](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-09-execution-completed-modal.png)
![frontal final video visible](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-10-storyboard-final-video-visible.png)
![frontal final artifacts](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/frontal-11-dance-mimic-final-artifacts.png)

### bigmotion

![bigmotion task list](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-01-task-list-before-create.png)
![bigmotion create modal](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-02-create-modal-filled-real-detector-target-image.png)
![bigmotion run page](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-03-run-page-started.png)
![bigmotion DanceMimic artifacts](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-04-dance-mimic-completed-artifacts.png)
![bigmotion StoryBoard opened](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-05-storyboard-opened-target-image-reference-visible.png)
![bigmotion video plan](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-06-video-plan-generated-openrouter-reference-target.png)
![bigmotion before execute](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-07-before-execute-video-plan.png)
![bigmotion executing](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-08-execution-queued-running.png)
![bigmotion execution completed](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-09-execution-completed-modal.png)
![bigmotion final video visible](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-10-storyboard-final-video-visible.png)
![bigmotion final artifacts](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/bigmotion-11-dance-mimic-final-artifacts.png)

### dance1

![dance1 task list](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-01-task-list-before-create.png)
![dance1 create modal](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-02-create-modal-filled-real-detector-target-image.png)
![dance1 run page](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-03-run-page-started.png)
![dance1 DanceMimic artifacts](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-04-dance-mimic-completed-artifacts.png)
![dance1 StoryBoard opened](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-05-storyboard-opened-target-image-reference-visible.png)
![dance1 video plan](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-06-video-plan-generated-openrouter-reference-target.png)
![dance1 before execute](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-07-before-execute-video-plan.png)
![dance1 executing](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-08-execution-queued-running.png)
![dance1 execution completed](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-09-execution-completed-modal.png)
![dance1 final video visible](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-10-storyboard-final-video-visible.png)
![dance1 final artifacts](acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/dance1-11-dance-mimic-final-artifacts.png)

## 附件

- 验收 summary：`acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/summary.json`
- 原始 Playwright summary：`acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/summary_raw.json`
- 目标人物图：`acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/target_ai_digital_human_avatar.png`
- 截图目录：`acceptance_artifacts/business_loop_20260630_ai_digital_human_1782774862261/screenshots/`
