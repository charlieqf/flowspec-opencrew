# OpenCrew 页面加载/切换性能审计与修复方案

- 日期:2026-07-08
- 背景:用户普遍反映公网访问(opencrew.instmarket.com.au,Cloudflare Tunnel → 家用 Mac mini origin)页面加载慢、模块切换慢;换自定义域名后无改善。
- 范围:顶层 `frontend/`(vite preview 部署)+ `backend/`(FastAPI)。所有发现均已对照代码与 `frontend/dist` 产物核实,标注 `file:line`。
- 状态标记:✅ 已落地(commit `374207a`)/ 🔴 待修 / 🟡 建议 / ⚪ 知悉即可

---

## 一、已落地的修复(commit `374207a`,审核通过)

| # | 修改 | 位置 | 核实结论 |
|---|------|------|----------|
| 1 | vite preview 对 `/assets/*`、`/hyperframe_templates/previews/*`、`/favicon.svg` 返回 `public, max-age=31536000, immutable`;`/` 与 `/index.html` 保持 no-store | `frontend/vite.config.ts:18-55` | ✅ 属实。路由为 hash 型,HTML 恒走 `/`,no-store 无漏匹配 |
| 2 | 故事板媒体版本号由 `Date.now()` 改为内容稳定哈希(`buildMediaVersion`);后端带非空 `?v=` 且后缀在媒体白名单内时返回 `private, max-age=86400`,否则 no-store | `frontend/src/modules/koubo/KouboStoryBoardModule.jsx:129`、`backend/opcrew_backend/routes/sessions.py:798-805` | ✅ 属实。`private` 仅浏览器缓存,Cloudflare 不缓存,符合描述 |
| 3 | `tts_builder_candidates.json` 由同步阻塞改为先渲染、后台补载 | `KouboStoryBoardModule.jsx:730` | ✅ 属实,带 taskId 守卫 |
| 4 | 任务详情前端内存缓存,切换路由先渲染缓存再后台刷新 | `frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js:79-81` | ✅ 属实,路由切走有守卫 |

---

## 二、审核中发现的缺陷

### 🔴 P0-A:`loadDetail` 后台刷新会覆盖用户编辑(修复 #4 引入)

- 位置:`frontend/src/modules/koubo/KouboStoryBoardModule.jsx:697` `loadDetail()`
- 现象:命中缓存立即渲染后,后台 `kbApi.detail` 返回时只检查路由是否切走,**未检查 `dirty()`**,随后无条件 `setPlan(...)`、`setDirty(false)`,并把选中项重置回第一个 dialogue。
- 触发场景:公网慢链路下刷新窗口长达数秒;用户看到缓存页面后立即开始编辑/选中某条 dialogue → 刷新落地 → **编辑丢失**、选中项跳回开头。
- 参照:同文件 `syncStoryboardDetail()` 已有 `if (dirty() ...) return` 守卫。
- 修复方案:后台刷新分支加同样的 dirty 守卫;dirty 时不覆盖 plan。命中缓存的情况下,刷新落地时不重置 `selectedShotIndex` / `selectedDialogueId` / `scope`(仅数据静默更新)。
- ⚠️ 实施风险:
  1. 仅加 `dirty()` 检查不能消除同任务并发刷新的竞态(快速切走再切回会有多个在途 detail 请求),应引入**每请求 token/版本号**,只应用最新一次请求的结果;
  2. 需明确 dirty 时的语义,且**不能笼统更新整个 `meta`**:meta 不只有资产列表,还含 `storyboard_video_slots`(按后端 plan 计算,`video_plan_load_services.py:231`)、`source_storyboard_sha256`、`video_plan_settings` 等——本地 dirty plan 若已增删/拆分 dialogue,套用全量新 meta 会造成 plan/meta 不一致(slots 对不上本地 dialogue)。建议:dirty 时只合并明确无冲突的 asset/history 子字段(`uploaded_*`、`history_versions` 等),或干脆完全跳过,在实现里写清选择了哪种。
- 工作量:小-中(单函数改动 + 竞态 token)。

### ⚪ 已知残留风险(暂不修,记录约束)

1. `bumpMediaVersion` 仍为 `Date.now()`(`KouboStoryBoardModule.jsx:543`,video-only plan 流程调用 5 处)。这是"文件原地覆盖但元数据不变"的逃生口,合理;代价是每次 bump 当前页所有媒体重新下载一遍。
2. 若某流程原地覆盖媒体文件、但既不刷新 detail 也不 bump,浏览器最长拿 1 天旧图(`max-age=86400`)。**新增媒体生成流程时必须遵守此约束。**
3. preview 中间件对 `/assets/*` 的 404 也会打 immutable 头,Cloudflare 可能缓存 404;正常运行碰不到,发版异常时可能出现"清不掉的 404"。
4. `storyboardDetailCache` 为无上限 Map,单会话内存占用可忽略。

---

## 三、其余性能问题(按影响排序)

### 🔴 P0-1:完全没有代码分割 —— 全站一个 1.4MB JS + 566KB CSS

- 位置:`frontend/src/shell/OpenCrewShellView.jsx:2-17`(静态 import 全部模块);`frontend/vite.config.ts`(无 `build` 配置);全 `frontend/src` 零处 `lazy()`/动态 `import()`。
- 产物实测:`dist/assets/` 仅两个文件 —— `index-*.js` 约 1.40MB(gzip 后约 389KB)、`index-*.css` 约 566KB(gzip 后约 89KB),CSS 为单一渲染阻塞样式表。(文件名哈希与精确字节数随每次构建漂移,以上为 2026-07-08 某次构建的示例值,勿据此核对当前 dist;量级结论不受影响。)
- 影响:任何用户首屏都要下载+解析+执行整个应用,不论只用哪个模块。
- 修复方案:
  1. shell 中每个导航模块改为 Solid `lazy(() => import("..."))` + `<Suspense>`;
  2. `vite.config.ts` 增加 `build.rollupOptions.output.manualChunks` 拆 vendor(solid-js 等);
  3. 模块级 CSS 随动态 import 自动拆分。
- 预期收益:首屏 JS 降至数百 KB 量级;后续模块按需加载且命中 immutable 缓存。
- ⚠️ 实施风险:
  1. **副作用导入延迟**:模块目前通过 import 副作用注册 CSS(如 `KouboStoryBoardModule.jsx:27` 导入 `styles/index.css`,多个模块导入共享 `styles.css`)、可能还有调试钩子/全局监听。lazy 化后这些副作用推迟到首次访问才发生——需逐模块检查路由、侧栏、全局事件、默认兜底页是否依赖"导入即生效",并接受首次进入模块时的样式加载瞬间(配 `<Suspense>` fallback);
  2. **manualChunks 从简**:只拆 vendor(solid-js 等),不要过度切分——错误的 chunk 边界会导致共享依赖重复打包、请求数暴涨。拆完用构建分析器或至少人工检查 `dist/assets` 验证无重复。

### 🔴 P0-2:源站零压缩 —— vite preview 与 FastAPI 均不发 gzip/brotli

- 位置:`backend/opcrew_backend/app.py:81-89`(仅 Auth + CORS 中间件,无 `GZipMiddleware`);`vite preview` 本身不压缩。
- 影响:origin→Cloudflare 边缘这一腿走**家庭上行带宽**。静态资源现已可被边缘缓存,但**所有 API JSON 每次都未压缩跨隧道传输**;故事板 detail 这类大 payload(见 P1-2)是"切页慢"的直接放大器。1.4MB JS 可压至 388KB(3.6×)。
- 修复方案:
  1. 后端:`app.add_middleware(GZipMiddleware, minimum_size=1024)`(一行);
  2. 静态:构建时预压缩(`vite-plugin-compression`)并换用支持 `.gz`/`.br` 的静态服务,或最低限度接受 Cloudflare 边缘压缩仅覆盖已缓存资源的现状。
- 预期收益:全部 API 响应体积降 3-10×,性价比最高的单项改动。
- ⚠️ 实施风险:GZipMiddleware 是全局的,会波及非 JSON 响应——**SSE 流**(`sessions.py:743-769`,`text/event-stream`)可能被压缩缓冲导致事件延迟,**zip 流下载**(`sessions.py:787`)与缩略图/媒体 `FileResponse` 被二次压缩纯浪费 CPU。落地时需排除这些路径(路由级豁免或按 content-type 跳过),上线前实测 SSE 事件到达延迟、下载与缩略图正常。
- 工作量:后端小(含豁免逻辑);静态侧小-中。

### 🔴 P0-3:静态 import 上的 `?v=` 查询串导致重复打包 + 模块状态分裂

- 位置:`frontend/src/main.tsx:3`、`App.jsx:1-2`、`shell/*.jsx`、`KouboStoryBoardModule.jsx` 等,当前源码共约 **100 处**静态 import 带 `?v=` 查询串。**注意:串值随每次前端改动被 bump**(例如曾为 `task126-route-cache`,当前值会持续变化,勿以文档中的示例为准),核查/清理时**不要按具体串值 grep,否则会误判已清理**;也不要用 `import.*\?v=`(会漏掉多行 import 的 `} from "...?v=..."` 行,实测只匹配 92/102 处),应使用 `grep -rE "from [\"'][^\"']+\?v=|import [\"'][^\"']+\?v="`(实测匹配全部 102 处),或直接用 AST/codemod 清理所有 import source 上的 query。其中至少 6 个文件同时以带/不带 `?v=` 两种路径被导入:`kouboStoryboardApi.js`、`kouboAgentChat.js`、`debugAdapter.js`、`digitalHumanModel.js`、`FloatingAssetMenu.jsx`、`StoryboardIcon.jsx`。
- 实测证据:`kouboStoryboardApi.js` 独有字符串("上传请求连接失败")在产物 bundle 中出现 **2 次**(串值 bump 后复测仍为 2 次)—— Rollup 将 `x.js?v=...` 与 `x.js` 视为两个模块,双份打包。
- 正确性地雷:因此存在**两个独立的 `storyboardDetailCache` Map**(带 `?v=` 实例与裸导入实例,后者被 `AssetThumb.jsx:2`、`DialogueCard.jsx:2`、`ImagePreview.jsx:2` 使用)。当前读写恰好集中在同一实例上未出错,但任何一处改动都可能踩雷。
- 修复方案:**删除所有静态 import 上的 `?v=` 查询串**(不限于某个具体串值);退役 `scripts/check_koubo_frontend_cache_bump.sh`。生产资源文件名本身带内容哈希 + 现已配 immutable 头,查询串已无存在意义。
- ⚠️ 实施风险(硬约束):**清理范围仅限静态 import specifier,绝不能动运行时媒体 URL 上的 `?v=`**——前端现有 43 处 `?v=${...}` 模板 URL(`rawFileUrl(...)?v=${mediaVersion}`、`?v=${Date.now()}` 等),而后端 raw 路由(`sessions.py:804-805`)正是**以 `?v=` 存在为缓存开关**:删掉它们不只是可能看到旧媒体,而是直接退回 no-store,废掉第一节修复 #2。按"import/from 语句内"为界做替换,替换后 grep 确认 43 处运行时 URL 原样保留。
- 工作量:小(机械替换 + 回归确认)。

### 🔴 P0-4:任务列表接口在事件循环内做同步文件 I/O + 全文 SHA-256

- 位置:`backend/opcrew_backend/koubo/koubo_storyboard/task_routes.py:29-55`;`backend/opcrew_backend/koubo/koubo_storyboard/storyboard_plan_services.py:80-104`(`latest_analysis_storyboard_source` 读取工作区内**每个**候选故事板 JSON 并整篇哈希);`backend/opcrew_backend/workflow_modes.py:38-62`(注意:在 `opcrew_backend` 根目录下,不在 koubo 子目录)。
- 影响:`async def` 内阻塞磁盘 I/O + 哈希 → **卡住整个 FastAPI 事件循环**,列表页一挂载,缩略图/detail/轮询全部排队。耗时随任务数线性增长。
- 修复方案:
  1. 签名按 `(path, mtime, size)` 缓存,文件未变不重算哈希;
  2. 端点改 `def`(FastAPI 自动走线程池)或阻塞段 `run_in_executor`。
- 工作量:中。

### 🟡 P1-1:模块切换 = 完整卸载重建 + 全量重取

- 位置:`frontend/src/shell/OpenCrewShellView.jsx:321`(按 `activeNav()` 的大三元,切走即销毁)。
- 影响:AnalysisV1 每次返回都重拉任务列表 + detail + 8 个 workspace JSON(`AnalysisV1Module.jsx:1028-1070`,其中 `restoreLatestRun`、`restoreLatestOneClickMovie` 还是串行 await);故事板/素材库虽有内存缓存先渲染,但后台仍打重接口(P1-2)。
- 修复方案:已访问模块保持挂载、`display:none` 切换;或给其余模块补 shell 级状态缓存;AnalysisV1 的两个串行 restore 改 `Promise.all`。
- ⚠️ 实施风险:keep-alive 会把"卸载即停止"的隐式清理全部失效——当前 2.5s 运行轮询(`TalkingHeadV1Module.jsx:263`、`DanceMimicV1Module.jsx:347`)、音频播放、SSE 监听、resize/键盘/window 监听都靠 `onCleanup` 随卸载停止。隐藏后若继续运行,后台开销可能**比重挂载更糟**,还会产生重复事件。每个模块必须有显式的 active/visible 暂停门控(隐藏时停轮询、暂停播放、解绑全局监听),没做门控之前宁可先只做 shell 级状态缓存方案。
- 工作量:中-大(keep-alive 需逐模块做暂停门控;状态缓存方案风险更低)。

### 🟡 P1-2:故事板 detail payload 构建重且体积翻倍

- 位置:`backend/opcrew_backend/koubo/koubo_storyboard/video_plan_load_services.py:192-233`(每次请求重哈希源文件、扫描资产池、stat 每条 dialogue 的音视频文件,均为 async 内同步 I/O);`:224-227`(`meta.manual_assets` = 三个 uploaded 数组之拼接,但三个数组又单独输出 → **每个资产序列化两次**);`asset_pool_services.py:82`(每条 agent 音频内嵌完整 TTS 会话 JSON)。
- 修复方案:去掉 `manual_assets` 重复数组(前端改为自行拼接或引用);`tts_agent_session` 裁剪至 UI 实际使用的字段;slot 状态 stat 结果按 mtime 缓存。
- ⚠️ 实施风险:`manual_assets` 不是死字段,而是**活的 fallback**——`UploadAssetLibraryPage.jsx:31-34` 在 `uploaded_images/audios/videos` 数组缺失时会从 `manual_assets` 过滤兜底(`KouboStoryBoardModule.jsx:116/280` 亦有消费)。删除前需确认后端在所有路径下都稳定输出三个 `uploaded_*` 数组(含空数组而非缺省),并同步排查素材库页、故事板页、agent chat payload 三处消费方;否则应反向操作(保留 `manual_assets`、去掉三个重复数组)并改造 fallback。
- 工作量:中。

### 🟡 P1-3:缩略图接口同步跑 cv2 解码/缩放

- 位置:`backend/opcrew_backend/routes/sessions.py:812-825` → `services/media_thumbnails.py`;`async def` 内同步 `imread`/视频抽帧/`imwrite`。
- 影响:新素材网格首次打开并发几十个请求,每个未缓存的视频抽帧都阻塞全后端;落盘有缓存,故为"首次查看很卡"。
- 修复方案:端点改 `def` 走线程池;可选:上传完成时预生成缩略图。
- ⚠️ 实施风险:线程池是**转移压力而非消除**——几十个并发 cv2 视频解码会打满 Mac mini 的 CPU 和 AnyIO 线程池(默认 40 线程),反过来拖慢其他线程池请求。应配一个生成并发上限(如 semaphore 限 2-4 个并发解码),中期以上传时预生成为主、请求时生成为兜底。P0-4 的 `def` 化同理受线程池容量约束,签名缓存才是根治。
- 工作量:小-中。
- (前端侧无问题:网格已用 thumbnail 端点 + `loading="lazy"`,`AssetThumb.jsx:15-17`。)

### ⚪ P2(低影响,顺手修)

| 项 | 位置 | 问题 | 修法 |
|----|------|------|------|
| CPU 采样阻塞 | `backend/opcrew_backend/routes/sessions.py:158-170` | `psutil.cpu_percent(interval=0.05)` 每次 `/api/session-tasks` 白阻塞事件循环 50ms | 改 `interval=None` |
| 执行轮询过密 | `KouboVideoPlanModal.jsx:378-386`、`KouboImagePlanModal.jsx:334`(1s)、`KouboVideoOnlyPlanModal.jsx:238`(1.6s) | 生成期间每秒打一次重接口,与 P0-4/P1-2/P1-3 的事件循环阻塞互相放大 | 放宽至 3-5s,或改用已有 SSE 流(`sessions.py:743`) |
| 外部汇率直连 | `frontend/src/shell/controllers/useMediaSettingsController.jsx:64` | 客户端直连 `open.er-api.com`(懒加载、有兜底,不在关键路径) | 走后端代理并缓存 |

---

## 四、体感归因与实施顺序

用户体感的完整解释:**冷启动 = 2MB 未压缩静态资源过家庭上行隧道(P0-1 + P0-2);切页 = 全量重挂载后重取未压缩、双倍序列化的大 JSON(P1-1 + P1-2 + P0-2),而这些接口又在互相阻塞后端事件循环(P0-4 + P1-3),请求彼此排队。**

建议批次:

| 批次 | 内容 | 理由 |
|------|------|------|
| 第 1 批 | P0-A(dirty 守卫 bug)+ P0-2 后端 GZip + P2 CPU 采样 | 均为小改动,P0-A 是正确性问题。**注意收益边界:后端 GZipMiddleware 只压缩 API/JSON 响应(改善切页/detail),不影响 vite preview 发出的 JS/CSS——当前 dist 无 `.gz`/`.br` 预压缩文件,首屏静态冷启动要到第 2 批(P0-1)或静态预压缩服务落地才改善** |
| 第 2 批 | P0-3(删 `?v=`)→ P0-1(lazy + 拆包) | 有依赖关系,须同批;做完首屏冷启动问题基本解决 |
| 第 3 批 | P0-4(列表签名缓存)+ P1-3(缩略图线程池) | 后端事件循环解堵 |
| 第 4 批 | P1-2(detail 瘦身)、P1-1(keep-alive)、P2 轮询/SSE | 改动面大,收益在前三批之后仍显著再做 |

每批完成后用公网域名实测:首屏 TTFB/LCP、切页请求瀑布(DevTools Network,勾选 Disable cache 对照)、生成任务期间其他接口的排队延迟。
