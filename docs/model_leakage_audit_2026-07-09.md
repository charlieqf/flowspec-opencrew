# 模型供应商信息泄露审计报告

日期：2026-07-09
范围：OpenCrew 生产部署（macmini，公网域名 `opencrew.instmarket.com.au`，经 cloudflared 隧道对外）
目标（原始）：客户（含"研究网页源码 / 抓包 / 查下载文件"的技术型客户）无法得知系统底层使用了哪些真实模型/供应商。
**成功标准（复审后修订，同事 #5）**：鉴于业主决策沿用可部分反推的 `Max {Provider}{Mode}{Ver}` 别名，目标口径调整为——**客户无法拿到任何直接真名、别名↔真名映射、或真实价目；`Max 系别名的可逆性`与 `Google SynthID 像素水印`列为业主知情接受的残留风险**。即：消灭"直接泄露"，两项残留由业主决策承担（硬化路径见治理设计）。
方法：三路只读源码扫描（前端 / 后端 / 生成资产元数据）+ 对运行中生产服务的实测探测（curl 公网域名、本机端口、抽样真实文件元数据）。

---

## 一、总体结论

别名掩码目前是**表层的、不完整的**。前端 UI 上你自定义的别名有效，但真实模型名通过**三条独立通道**仍然泄露，其中两条**无需登录**、一条**普通登录客户即可触发**：

| 通道 | 攻击者门槛 | 是否已现网可复现 | 危害 |
|---|---|---|---|
| **A. 前端 JS bundle 明文模型目录** | 不登录，`curl` 一个 JS 文件 | ✅ 已复现 | 拿到**全量**真实 provider+model+价目表 |
| **B. 生成文件的元数据/水印 + 侧车 JSON** | 客户下载自己生成的图/视频 | ✅ 已复现 | 单个文件即暴露 OpenAI/Google/Veo 等；Google 图片含**无法去除的 SynthID 像素水印** |
| **C. 后端响应/报错/debug 旁路** | 普通登录客户 | ✅ 逻辑确认 | 任务详情、生成报错、`audience=debug` 事件流回传真名 |
| D. 配置类latent风险 | 需部署方式改变才触发 | ⚠️ 非现网 | dev-server 全仓可读、openapi 默认值等 |

好消息：后端**已有**一套别名/掩码框架（`model_policy.py`），真实映射表本身从不整体下发；很多路由已正确套用。问题是它**逐路由手动调用**，有多处遗漏和一个鉴权旁路。修复方向明确。

---

## 二、现网实测（我方主动探测结果）

- 公网前端 `opencrew.instmarket.com.au` 服务的是**已构建的 `dist/`**（root 引用 hash 资源 `index-yrJ1fpon.js`），**不是** vite dev server。因此 `/@fs/`、`/src/`、`.env`、`/openapi.json`、`/docs` 公网访问都只回落到 SPA 首页（HTTP 200 但内容是 `index.html`），**未泄露文件系统**。→ 前端 agent 报告的 F16（`fs.allow:['..']` 全仓可读）与后端 agent 的 H4（openapi/docs）**当前不可现网利用**，属 latent。
- 但公网可直接下载的 bundle `/(assets)/model-config-hzMdkzeC.js`、`index-yrJ1fpon.js` 等**明文包含**：
  ```
  {provider:"openai", providerLabel:"OpenAI", model:"sora-2", ...}
  {provider:"gemini", providerLabel:"Gemini", model:"veo-3.1-generate-preview", ...}
  {provider:"xai",    providerLabel:"xAI",    model:"grok-imagine-image-quality", ...}
  {provider:"wan",    model:"wan2.7-t2v-2026-04-25", ...}
  {provider:"kling",  model:"kling-3.0-turbo", ...}
  ```
  即一张带 USD 单价的完整真实模型价目表。**source map（`.js.map`）未发布**（返回 SPA 回落，非真图）。
- 后端 `/api/*` 受鉴权保护：`/api/metering`、`/api/models`、`/api/model-config`、`/api/pricing`、`/api/config`、`/api/providers` 全部 **401**；仅 `/api/health` 开放。`/api/docs` **401**。Server 头被 cloudflare 掩为 `cloudflare`。→ 后端管理面/配置面**不可匿名访问**。
- 隧道路由（`~/.cloudflared/config.yml`）：`opencrew.instmarket.com.au/api/.*` → `:8011`，其余 → `:18080`。因此后端 openapi（根路径 `/openapi.json`）不在 `/api/` 下，公网不可达。

结论：**当前唯一"不登录即泄露"的现网面是前端 bundle 的字符串常量（通道 A）与下载文件的元数据（通道 B）。** 通道 C 需登录客户触发。

---

## 三、通道 A —— 前端 bundle 明文模型目录（不登录可得，最高优先）

真实"供应商→真实模型→真实单价"的价目表**硬编码在前端源码**，随主 bundle 下发。别名映射也在客户端完成，等于把解码表一并交给了浏览器。

关键泄露点（file:line）：

1. `frontend/src/lib/meteringFormat.js:5-81` — `MEDIA_PRICE_POINTS` / `LIPSYNC_PRICE_COMPARISON`：逐条列出 OpenAI(`gpt-image-2/1.5`、`sora-2/-pro`)、xAI(`grok-imagine-*`)、Gemini(`gemini-3.1-flash-image`、`veo-3.1-*`、`veo-3.0-*`)、Kling(`kling-3.0-turbo`、`kling-v3-omni`)、Wan(`wan2.7-*`、`happyhorse-1.0-*`)、HeyGen/Sync.so/蝉镜 的真实模型名+单价。
2. `ModelConfig/frontend/src/shared/pricing.ts:24-105` — 与上表**逐字重复**的第二份，外加视频时长表再列一遍真名。
3. `frontend/src/components/ModelPresetCards.jsx:4-19` — 用户可见的 Max/Flash 选择卡片直接映射：`Max→["openai","gpt-5.5"]`、`Flash→["deepseek-v4-flash-free"]`。**别名解码表就在前端。**
4. `frontend/src/components/ModelPresetCards.jsx:39-40` — 读取 `provider_label_real` / `model_label_real` 字段 → 说明后端可能把"真实标签"随配置**下发到浏览器**（字段名自曝，需在后端侧确认，见通道 C）。
5. UI 文案硬露供应商真名（正常点开即见，非源码级）：
   - `ModelConfig/frontend/src/ModelConfigModule.tsx:69-72`、`digital-human/DigitalHumanConfigModal.tsx:8` → "HeyGen 数字人设置"、"Sync.so Lip Sync Key Settings"
   - `frontend/src/shell/SettingsDrawers.jsx:260`、`ModelConfig/.../MediaConfigModalBase.tsx:237` → "Drag OpenAI, Gemini, or xAI image models…"
   - `PromptBuilderModal.jsx:78` "切换到 Grok"、`AgentPanel.jsx:1198` "…没有可用的 Grok 图像模型"
   - `XaiVoiceGuide.tsx:26` 硬编码 xAI console URL，**并泄露真实 xAI team UUID** `b6d215fa-…`
6. 别名派生逻辑（证明真名 ID 存在于前端 config 数据）：`useMediaSettingsController.jsx:167-169`、`MediaConfigModalBase.tsx:144-149`（`.replace(/^gpt-image-/…)` 等），`videoModelCapabilities.js:144-160`（`maxsr2↔bytedance/seedance-2.0`、`maxwr27↔wan2.7`）。
7. 请求参数携带真名（抓包面）：`api.ts` 各接口用裸 `provider`/`model`/`model_id` 字段，取值来自 config 真实 ID；`TalkingHeadV1Module.jsx:168-169` **把 `wan`/`wan2.7-r2v-2026-06-12` 硬编码进请求体**。
8. 各模块默认常量散露 `openai`/`gpt-5.5`/`wan`：`OpenClipModule.jsx:12-13`、`AnalysisV1Module.jsx:68-69`、`OCRebuildModule.jsx:251-257`（并硬编码 `developers.openai.com`/`docs.x.ai`/`ai.google.dev` 文档链接）。
9. TTS provider 判定散落多文件：`OCStoryBoardModule.jsx`、`kouboStoryboardTts.js`、`AnalysisV1TTSBuilder.jsx`（默认 `gemini-3.1-flash-tts-preview`）、`googleTtsScenarioGuide.ts` 等。

干净项：`index.html`/`public/` 无泄露；无 `.env`/`VITE_` 引用；`WorkflowAssistant/frontend` 零命中；source map 未发布。

**修复原则**：前端只持有"别名 + 脱敏聚合金额 + presetKey"。真实 provider/model/价目/文档链接/`*_real` 字段一律留后端，绝不下发。修完需**重新构建**（现有 `frontend/dist/` 已含真名，是当前现网泄露源）。

---

## 四、通道 B —— 生成文件元数据 / 水印 / 侧车 JSON（客户下载即得，最高优先）

系统从供应商 API 取回图片/视频后**原样落盘、原样 serve，全代码库无任何元数据清洗**（`exif`/`c2pa`/`piexif`/`-map_metadata`/`strip` 全部 grep 无命中）。实测在真实文件中读到供应商痕迹：

- **图片 C2PA/JUMBF**（抽样 66/167 张 PNG 命中）：
  - Google/Gemini：`claim_generator "Google C2PA Core Generator Library"`、`"Created by Google Generative AI."`、**`"Applied imperceptible SynthID watermark."`**
  - OpenAI gpt-image：9 张含字面 `OpenAI` + `contentauth` C2PA claim
  - Flux/Black Forest Labs：`bfl` 标记；部分素材含 Adobe Firefly C2PA
- **视频 MP4 atom**：`TAG:encoder=Google`（Veo）实测命中多个 `sessions/181,182/**` 的 `direct_video_generation_*.mp4`（部分被后续重编码侥幸覆盖，不可依赖）。
- **明文侧车 JSON**（图片目录 117 个 `*.json`）：明文写 `"provider":"xai"`、`"model":"grok-imagine-image-quality"`，位于被 serve 的 workspace 目录，经 `/api/session-tasks/{id}/raw/…json` 或 `files.zip` 打包即可下载。

落盘/serve 关键点：
- 图片：`provider_services.py:542-581` 取字节 → `clean_image_services.py:262-264` / `asset_routes.py:1955-1956` `write_bytes` 原样落盘（未经 PIL 重存）。
- 视频：`asset_video_generation_services.py:584-614` `shutil.copyfileobj` 原始流拷贝。
- serve：`routes/sessions.py:797/842/977` `FileResponse` 直通，含**公网 share token** 下载路径 `:977`。
- 文件名/URL：image/video 干净（`时间戳_agent_generated_<hex>`）；**但 digital-human 例外——文件名直接嵌 `heygen`**（`asset_digital_human_services.py:847/1025`，见复审补充）。未发现 CDN 直链入库/回前端。

**修复原则**：在两个转存入口各加强制清洗——
- 图片：Pillow 无损重存去除身份类 chunk（不传 pnginfo/exif；**保留 icc_profile** 以防色差）或专用 C2PA/JUMBF 剥离。统一口径：**允许 ICC，禁止 EXIF/XMP/tEXt/iTXt/C2PA/JUMBF 及供应商字符串**（详见 remediation §1 B0/§1.5）；
- 视频/音频：`ffmpeg -i in -map_metadata -1 -map_chapters -1 -c copy out`；
- serve/zip **排除 provider/model 侧车 JSON**，或把 provider/model 移出 workspace 存 DB。

⚠️ **重要且无法用代码解决的一项**：Google（Gemini/Imagen 图片、Veo 视频）带 **SynthID —— 像素级隐形水印，剥掉元数据也去不掉**，用 Google 的 SynthID 检测器可判定"由 Google AI 生成"。这一条属选型/合规层面，需业主单独决策（是否接受、是否对高敏客户改用无隐形水印的供应商、是否重编码/再生成以削弱——效果有限）。

---

## 五、通道 C —— 后端 HTTP 响应泄露（登录客户可触发）

后端有 `model_policy.py` 掩码框架且真实映射表不整体下发，但以下路径**绕过或未套用**掩码：

- **H1 口播任务详情回传真名** — `koubo/task_list_router.py:326-342` 硬编码 `wan`/`wan2.7-r2v-2026-06-12`/`heygen` 写入 task_meta，经 `serialize_task(detail=True):530`、`GET /api/koubo-tasks/{id}`、`GET /api/talking-head-v1/tasks/{id}:1110`（原样回传整份 `meta`）返回。**该文件全程未调用掩码。** ← 当前你未提交的改动正在这个文件里，修复可一并进行。
- **H2 `audience=debug` 鉴权旁路** — `routes/sessions.py:738-769`：`GET /api/sessions/{id}/events?audience=debug` 对**任意登录用户**放行全部内部事件，其中 `provider_call.completed`（`asset_video_generation_services.py:1543`）含 provider/model/供应商 CDN 直链/endpoint。`redact_payload` 只脱敏密钥，不脱敏 provider/model。**一次请求拿到全量真名。** 修复：`audience=debug` 必须校验 admin。
- **H3 上游报错原样透传** — `asset_video_generation_services.py`（多处 1371-1509）、`rebuild_router.py`、`router.py:2306-2382`、`asset_digital_human_services.py` 把 `HTTPException(detail=f"Gemini/Wan/Seedance/OpenAI/xAI/HeyGen/DashScope … {上游原始JSON}")` 直接回浏览器，含供应商名+域名（`dashscope.aliyuncs.com`、`generativelanguage.googleapis.com`）。生成失败是常见路径。修复：统一异常包装为通用文案。
- **M1 `/api/ocrebuild/*` 零掩码** — `koubo/rebuild_router.py` 全文件无 `mask_*`/`request_role`，成功响应回传 `provider`/`model`/`endpoint`（`api.openai.com`/`api.x.ai`/`generativelanguage.googleapis.com`）/`docs_url`。需确认是否客户可达：若是→套掩码并删 endpoint/docs_url；若仅 admin→加入 `ADMIN_ONLY_PATH_PREFIXES`。
- **M2 兜底异常** — `app.py:131-134` 返回 `{"detail": str(exc)}`；生产应改通用 500。
- **M3 掩码仅对 `role=="user"` 生效，且 `request_role` 取不到角色默认 ADMIN**（`model_policy.py:191-193`、`auth.py:324-325`）。生产须 `OPENCREW_AUTH_REQUIRED=1`、客户仅发 USER 口令；建议把默认回退改为 USER（最小权限）。
- **M4 CORS** — `app.py:142-149` 允许任意 `*.nip.io`/localhost 且 `allow_credentials=True`；收敛为业主前端域名白名单。

已正确掩码的路由（整改参照）：`openflow_analysis.py`、`koubo/router.py`、`agent_chat_routes.py`、`asset_routes.py`、`host_product_*`、`task_routes.py`、`tts_routes.py`。客户/分享受众事件过滤正确（`services/session_events.py:95-106`），漏洞仅在 H2 的 debug 旁路。

---

## 六、通道 D —— latent 配置风险（当前不可现网利用，但应加固）

- **F16 vite `fs.allow:['..']`**（`frontend/vite.config.ts:81`）：若将来在公网隧道上跑 `vite dev`（而非当前的 dist 静态服务），`/@fs/<绝对路径>` 将可读**整个仓库含后端源码与 `.env` 密钥**。→ 生产永远只用 dist 静态托管/`vite preview`；若必须 dev，设 `fs.strict:true` 并收窄 allow。
- **H4 openapi/docs**（`app.py:110` 未设 `docs_url=None`；`auth.py:322` 只拦 `/api/`）：当前隧道不把根路径转给后端，故公网不可达；但属深度防御，生产建议 `docs_url=None, redoc_url=None, openapi_url=None`，并清理 `koubo/schemas.py` 里 `voice_provider="heygen"`/`model="gemini-3.1-flash-tts-preview"` 等真实默认值。
- `vite.config.ts:8` `allowedHosts` 硬编码部署域名（低危，改环境变量注入）。

---

## 七、修复优先级

**P0 —— 不登录即泄露 / 客户下载即得，先做：**
1. 通道 B：图片 Pillow 去 chunk + 视频 `ffmpeg -map_metadata -1` + serve/zip 排除侧车 JSON。（对已生成的 167 张图/81 视频还需补一次批量清洗）
2. 通道 A：把价目表/别名解码/真名默认值/`*_real` 字段全部下沉后端，UI 文案改中性别名，重构建 `dist/`。

**P1 —— 登录客户可触发：**
3. H2：`audience=debug` 加 admin 校验（一处代码，收益最大）。
4. H1：`task_list_router` 对 talking_head 字段套掩码/剔除（可与当前未提交改动一起做）。
5. H3：统一上游异常包装，禁传 provider/域名/上游 body。

**P2 —— 加固：**
6. M1 ocrebuild 定性（掩码 or admin-only）；M3 `request_role` 默认改 USER + 强制 `OPENCREW_AUTH_REQUIRED=1`；M4 CORS 收敛；D 类 latent 配置。

**根本架构原则**：把"别名 ↔ 真名"这条边界**收敛到后端一处**。前端、下发的 JSON、生成文件、错误消息——任何离开后端进程、可能到达客户的字节，都只允许出现别名/脱敏值。真名只存在于后端内存与出站到供应商的请求里。

**须业主决策（非技术可解）**：Google SynthID 像素水印无法通过元数据清洗去除——若有客户会主动用 SynthID 检测器验证，需在供应商选型层面评估。

---

## 复审补充（2026-07-09，同事 review 后新确认）

初版审计之外，复审又坐实两处泄露，已并入设计文档：

- **通道 A 咽喉点更广**：客户可达的运行时 config 端点直发真名，除 image/video 外，**TTS 也漏**：`asset_routes.py:2670` `tts-model-config` → `asset_library_tts_model_config()`（:1256）返回 `active_provider` + `providers[].provider/model`（仅剥 api_key）；前端 TTS 请求 `AnalysisV1TTSBuilder.jsx:1498`、`AnalysisV1Module.jsx:1533` 也发真名。voice-clone/digital-human 同理。→ 治理范围**全覆盖**（详见 channel_a 设计）。
- **文件出口过滤落点**：普通下载 `/api/sessions/{id}/files/{file_id}` 与 **share 下载**走 `SessionFileService.resolve_download`（`services/session_files.py:101`），JSON 默认 `default_file_visibility`（:58）判为可下载。侧车/清单过滤必须下沉到 `is_sensitive_path`/`SENSITIVE_PARTS`（:49），落在 `routes/sessions.py` 会漏 `files/{file_id}` 和 share。

- **digital-human 文件名/asset 泄露 HeyGen（初版漏判）**：初版"文件名干净"只对 image/video 成立。digital-human 生成文件名直接是 `{batch}_heygen_digital_human_…mp4`（`asset_digital_human_services.py:847/1025`），asset `source="heygen_digital_human"`/`label="HeyGen digital human video"`/`origin`（:889/1117）与 sidecar 也带真名，经 `/raw` URL、files.zip、列表/详情返回体暴露。→ 已列入 remediation §1 B5（文件名去品牌 + asset/manifest/事件掩码 + 存量重命名）。voice-clone 同类需一并核。
- **digital-human agent 记录返回体（五轮复审）**：客户路由 `digital-human/agents/{id}`（`asset_digital_human_routes.py:178`）与 `/stop`（:190）返回 `_write_video_agent_record`（`asset_digital_human_services.py:781`），含 `provider:"heygen"`/`model`/`provider_result`/`agent_snapshot`。登录客户可读 → 通道 C 掩码/白名单。
- **clean-image 在 SessionScratch（五轮复审）**：clean-image 生成物在 `SessionScratch/CleanImageGenerations`（`constants.py:14`，非 assets/ 下），图片原样落盘 + manifest 真名（`clean_image_services.py:264/282`），客户经 `/clean-image/{generations,{id}/image,generate,promote}`（`clean_image_routes.py:45-123`）可达。→ live 走 B1 图片清洗、响应走通道 C 掩码、存量 B4 已加 `CLEAN_IMAGE_REL`。

**别名可逆性（残留风险）**：现用 `Max {Provider}{Mode}{Ver}`（如 `MaxWR2.7`）把供应商首字母+真实版本号编入别名，技术型客户可部分反推。业主决策沿用现有体系、接受此残留（与 SynthID 同列知情接受项）；日后可选去版本号/首字母硬化。

**已定决策**：范围全覆盖 / 别名沿用 Max 系 / USER 首版即 alias-only 无泄露窗口。详见 `docs/model_leakage_remediation_design_2026-07-09.md` §7 与 `docs/model_leakage_channel_a_design_2026-07-09.md`。

**实施状态补记（2026-07-09 C0/B0/B1/B2/B3/B5-new）**：已落地集中式客户出口掩码 `CustomerEgressSanitizerMiddleware`，覆盖非 admin `/api/*` JSON/SSE/可解析 text 响应；CI 已加入 `scripts/check_model_leakage_guard.py`，当前基线为 `/api` route entries 385 条、guarded 309 条、`koubo-storyboard` guarded 113 条。同步已补三类复审漏点：`SessionFileService` 排除媒体 sidecar/manifest 的下载与 zip；新生成 digital-human/video-agent 文件名与 asset `source/label` 去 `heygen`；新生成图片/视频/音频在落盘 sink 做字节级元数据清理。该状态降低"新增端点漏掩码"、"新生成数字人文件名泄露"和"新生成媒体元数据泄露"风险，但不替代通道 A 前端真名下沉、B4 存量媒体清洗/存量 digital-human 重命名、H2 debug 鉴权和 H3 异常包装。Google SynthID 像素水印仍是业主知情接受的残留风险。
