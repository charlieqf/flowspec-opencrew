# 中文长口播上传与分析验收证据

- 主素材：`video(26).mp4`，199.552 秒，1200×2670，HEVC + AAC。
- 素材库：`mla_1784591754592_d1c20d9832d6`，Task #14 / Session #381。
- 当前发布结果：对白 123、画面 22、综合 11 个片段。
- 当前 ready runs：
  - dialogue `mlar_dialogue_1784591756626_9d60e1c5b8d5`
  - visual structure `mlar_visual_structure_1784592530028_cb54453afd9a`
  - visual semantic `mlar_visual_semantic_1784592556244_7d854cadc77c`
  - composite `mlar_composite_1784593125701_f3cc1e96929e`

本目录保留了真实调试过程，因此文件时间跨越多个浏览器尝试。`12-composite-ready-for-run.png` 与 `99-failure.png` 是旧 `composite_prompt_v1` 因引用闭包不完整而 fail-closed 的现场；修复后的 `composite_prompt_v2` 真实运行结果见 `13-composite-analysis-ready.png`。最终状态与 run ID 以 `long-chinese-talking-head-report.json` 为准，其 `ok` 为 `true`，且没有 API 5xx、console error 或 page error。

这些记录没有删除业务数据。素材、Task/Session、分析 run 和发布片段仍保留在测试环境页面中。
