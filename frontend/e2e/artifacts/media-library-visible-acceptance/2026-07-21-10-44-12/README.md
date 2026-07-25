# 素材库综合 UI 验收证据

最终报告：`visible-acceptance-report.json`，`ok: true`。产品流程截图 20 张，另有桌面与手机 HTML 手册验证截图 2 张。

- 浏览器：Playwright WebKit（macOS HEVC 解码路径）。
- 主素材：`mla_1784591754592_d1c20d9832d6`，199.552 秒中文真人多场景口播。
- 编辑器：156 个片段；选区 151217–159217ms；解码帧 1200×2670、readyState 4。
- 中文内部检索：`mls_1784594660573_cf8501d6911f`，1 个命中，未降级。
- 外部检索：`mls_1784594665696_09f9934d6474`，26 个结果，无 source error。
- 派生片段：`mlc_1784594230033_42040d460917`，8 秒，4,429,459 bytes，真实 FFmpeg 完成。
- StoryBoard：Task #308 / Session #380；5 个视频素材卡片，fresh-read persistence 通过。
- 移动端：390×844；素材卡片、分析片段、目标选择器、播放器、语义检索与时间轴均通过可见尺寸断言。
- 手册：28/28 张 Base64 内嵌图在 WebKit 桌面与 Chromium 手机均成功解码；无横向溢出。

最终报告没有 HTTP 5xx、console error 或 page error。片段在前一次浏览器断言尝试中创建并导入，本次最终流程显式复用同一 ID 验证了任务重启后的可查询性、UI 可见性和 StoryBoard 留存，没有伪造或替换业务数据。
