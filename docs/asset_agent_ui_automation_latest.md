# Asset Agent UI Automation Report

Run ID: `20260613115251`
Task: `116`
URL: http://127.0.0.1:18080/?e2ePersistentAssetAgents=20260613115251#/koubo-asset-library/tasks/116

## 验证范围

- Images-Agent：左侧导航入口、中间 Images 媒体库、右侧 Agent 对话框、模型切换、聊天记录、Agent 产出物回填。
- Videos-Agent：左侧导航入口、中间 Videos 媒体库、右侧 Videos Agent 面板、模型切换、聊天记录、视频产出物回填。
- 持久化：刷新/重新打开 Asset Library 后，自动化测试留下的聊天记录和产出物仍可见。
- 默认不向真实 OpenCode 会话写入 E2E 用户消息；接口 payload 细节由 `test:e2e:asset-agents` 的 mock 用例覆盖，避免污染真实 task 聊天记录。

## 自动化操作说明

1. 打开 Asset Library 页面并进入 `Images-Agent`。
2. 确认右侧是 `Images-Agent` 对话框，中间仍是 Images 媒体库。
3. 创建并登记测试图片产出物：`SessionOutput/storyboard/assets/images/20260613115251_e2e_images_agent_persistent.png`。
4. 通过 Images-Agent 对话框左下角的上传参考图入口一次上传多张参考图：`20260613115251_reference_a.png`、`20260613115251_reference_b.png`，确认多图参考入口在真实页面可用。
5. 跳过真实 Agent 消息发送，仅验证聊天区中的持久化产出物消息。
6. 刷新页面后重新进入 `Images-Agent`，确认媒体库和聊天区仍显示测试产出物。
7. 进入 `Videos-Agent`，确认右侧是 Videos Agent 面板，中间仍是 Videos 媒体库。
8. 创建并登记测试视频产出物：`SessionOutput/storyboard/assets/videos/20260613115251_e2e_videos_agent_persistent.mp4`。
9. 跳过真实 Agent 消息发送，仅验证聊天区中的持久化产出物消息。
10. 刷新页面后重新进入 `Videos-Agent`，确认媒体库和聊天区仍显示测试视频产出物。

## 截图

### 1. Images-Agent 页面：中间媒体库与右侧 Agent 产出消息均显示测试图片

![Images-Agent 页面：中间媒体库与右侧 Agent 产出消息均显示测试图片](../test-results/asset-agent-persistent/20260613115251/01-images-agent-output-visible.png)

### 2. Images-Agent 页面：多张参考图与测试图片产出消息保持可见

![Images-Agent 页面：多张参考图与测试图片产出消息保持可见](../test-results/asset-agent-persistent/20260613115251/02-images-agent-chat-record.png)

### 3. Videos-Agent 页面：中间媒体库与右侧 Agent 产出消息均显示测试视频

![Videos-Agent 页面：中间媒体库与右侧 Agent 产出消息均显示测试视频](../test-results/asset-agent-persistent/20260613115251/03-videos-agent-output-visible.png)

### 4. Videos-Agent 页面：测试视频产出消息保持可见

![Videos-Agent 页面：测试视频产出消息保持可见](../test-results/asset-agent-persistent/20260613115251/04-videos-agent-chat-record.png)

### 5. 重新打开后：Images-Agent 仍显示测试聊天记录和图片产出物

![重新打开后：Images-Agent 仍显示测试聊天记录和图片产出物](../test-results/asset-agent-persistent/20260613115251/05-reopen-images-agent-persistent.png)

### 6. 重新打开后：Videos-Agent 仍显示测试聊天记录和视频产出物

![重新打开后：Videos-Agent 仍显示测试聊天记录和视频产出物](../test-results/asset-agent-persistent/20260613115251/06-reopen-videos-agent-persistent.png)

## 用户复核

打开 http://127.0.0.1:18080/?e2ePersistentAssetAgents=20260613115251#/koubo-asset-library/tasks/116，进入左侧 `Images-Agent` 和 `Videos-Agent`，应能看到本次 Run ID `20260613115251` 对应的聊天区产出物消息和媒体库产出物。
