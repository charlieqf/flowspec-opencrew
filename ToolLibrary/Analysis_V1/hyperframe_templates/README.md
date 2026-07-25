# HyperFrame 直播带货模板集（首版）

4 套面向**直播带货**的标题/字幕动效模板,由 HyperFrame 无头渲染,**全部离线可跑**(纯 CSS 或本地 vendored GSAP,不依赖 CDN)。

| id | 名称 | 带货作用 | 动效 | 需 GSAP |
|---|---|---|---|---|
| `price_card` | 爆款价签 | 价格冲击,主推爆款 | CSS 弹入 | 否 |
| `flash_sale` | 限时秒杀 | 制造紧迫,逼单 | CSS 脉冲 | 否 |
| `selling_points` | 卖点字幕 | 念卖点建立信任 | CSS 逐条蹦出 | 否 |
| `order_cta` | 下单引导 | 引导小黄车成交 | 本地 GSAP 滑入+箭头跳动 | 是 |

完整链路:**看价(price_card)→ 紧迫(flash_sale)→ 信任(selling_points)→ 下单(order_cta)**。

## 预览
`previews/<id>.mp4`(540 宽循环片)与 `previews/<id>.gif`(300 宽)。用于前端"风格画廊"让用户秒选——出厂预渲一次、当静态资产打包,浏览零渲染开销。

## 本地渲染（验证用）
```bash
# 引擎与 Chrome 见 ../node_modules（provisioning 预装,锁版本离线内置)
cd <id> && ../../node_modules/.bin/hyperframes render --output out.mp4
```
画布 1080×1920(竖屏)。

## 重要约束
- **背景渐变是占位**:生产时底层 `<video>` 替换为真实直播/口播视频,模板作为叠加层(配合 ffmpeg 裁剪 → HyperFrame 叠层 → 合成成片)。
- **文案当前写死为示例**:`templates.json` 的 `params` 即参数化目标,产品化时由用户填写或从商品信息/Agent 自动注入(同一模板批量套不同商品)。
- **LAN-only**:`order_cta` 必须引本地 `gsap.min.js`,严禁用 CDN(HyperFrame 原生会从 cdn.jsdelivr.net 内联 GSAP,违反离线约束)。

## 状态
首版提案,色系/文案/位置/调性(克制 vs 夸张)均可调。详见方案 `docs/koubo_video_edit_compose_plan.md` §5.6。
