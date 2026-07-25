# 模型名称脱敏：问题描述与解决方案

日期：2026-07-13
最后更新：2026-07-14
状态：审核稿
适用范围：OpenCrew 客户可达的前端、后端 API、异步任务、事件流、文件下载与生成媒体
配套资料：

- `docs/model_leakage_audit_2026-07-09.md`
- `docs/model_leakage_channel_a_design_2026-07-09.md`
- `docs/model_leakage_remediation_design_2026-07-09.md`

## 1. 审核结论

当前“模型名称脱敏”不能判定为完成。

已经落地的后端别名、客户响应出口脱敏、部分媒体元数据清洗和 CI 契约测试，显著降低了正常 JSON/SSE 响应直接返回真实 provider/model 的概率；但客户仍可通过以下路径获得真实名称或足以定位供应商的信息：

1. 匿名下载的前端 JavaScript bundle 中仍包含真实 provider、model、价格和部分 alias-to-real 线索。
2. 已退休导航的 `/api/ocrebuild/*` 后端仍对普通用户开放，直接构造真实 catalog、接受真实 provider/model 选择，并完全绕过 `model_policy`。
3. 模型策略缺 surface、缺 `mode` 或 `mode` 拼写错误时，catalog 和 resolver 会 fail-open 到真实模型。
4. 系统异常、供应商异常和工具执行异常会把 provider/model、上游响应或原始 `str(exc)` 带入 HTTP 响应、SSE、任务状态、事件、日志和结果 JSON。
5. execution state/result/run state 等内部 JSON 默认可下载，能够绕过正常 API 响应脱敏。
6. 媒体清洗只覆盖部分 asset-library sink，ToolLibrary 执行器的 Working/Output 等目录尚未纳入统一清洗边界。
7. 存量清洗脚本覆盖范围不足，且尚有带品牌文件名、C2PA/JUMBF、供应商元数据的存量文件。
8. CI 已能扫描实际构建 bundle，但仍有带精确预算的 Phase 3 历史债，且尚未覆盖完整异常矩阵、旧 chunk 可达性和全部发布边界。

因此，当前状态应定义为：

> **后端基础防线已建立，但 P0 级直接泄露仍存在；异常治理、静态 bundle 下沉、内部文件隔离和统一媒体发布边界尚未完成。**

## 2. 安全目标与边界

### 2.1 目标

对普通客户、分享链接访问者以及未登录访问者：

- 不返回真实 provider 名称。
- 不返回真实 model ID、版本号或 API endpoint。
- 不返回 alias 与真实 provider/model 的映射。
- 不返回可直接还原真实模型的单模型价目表。
- 不通过异常、日志、事件、状态文件、文件名、下载响应头或媒体元数据泄露上述信息。
- 客户请求只提交公开 alias，后端内部解析为真实 provider/model。

对管理员：

- 允许通过已鉴权的动态 API 查看真实配置和内部诊断信息。
- 不把真实配置硬编码进任何匿名可下载的前端静态文件。
- 内部异常详情必须与客户可见异常分开存储和授权。

### 2.2 明确接受或需业主决策的残留风险

以下风险无法仅靠字符串脱敏彻底消除：

- 现有 `Max {Provider}{Mode}{Ver}` 别名可能通过首字母和版本号被部分反推。
- Google SynthID 等像素级隐形水印不能通过普通元数据清洗可靠移除。
- 输出风格、语音特征、延迟、分辨率限制、codec、错误行为、计费粒度和能力组合可能形成模型指纹。
- 客户主动上传或输入的文件名、提示词中可能本身包含供应商名称。

这些项目必须区分“直接泄露”和“推断风险”，并由业主书面确认接受范围。

## 3. 当前已经完成的工作

### 3.1 集中式客户出口脱敏

已新增 `CustomerEgressSanitizerMiddleware`，对非 admin 的客户 `/api/*` JSON 和 SSE 响应进行字段删除、provider/model 掩码、域名与模型标识清洗。

当前出口层具备以下能力：

- 未识别角色在出口层默认按 USER 处理。
- 处理正常 JSON 响应及 FastAPI 异常 JSON。
- 处理跨 chunk 的常规 SSE `data:` frame。
- 删除 `provider_result`、`raw_response`、`stdout_tail`、`workspace_dir` 等部分高风险字段。
- 保留 Max/Flash 等公开 alias。
- 对 TTS voice ID 使用不透明公开别名。

### 3.2 媒体和 TTS 公共别名

已对 image、video、TTS 和部分 voice-clone 路径增加公共 alias，并在后端解析真实 provider/model。7 月 10 日至 13 日的后续提交持续修复了：

- 媒体 alias 在计划执行后的保存与恢复。
- TTS 公共 alias 和 voice ID 映射。
- 旧设置、克隆声音和 provider 切换兼容。
- 口播任务模型选择与克隆候选过滤。

最新相关提交为 `83a6b43`，处理云端克隆候选的 provider 归一化和安全选择。

### 3.3 部分媒体清洗与文件过滤

已实现：

- 图片重编码清除 EXIF/XMP/C2PA/JUMBF 等身份类元数据，并保留 ICC。
- 视频/音频通过 ffmpeg 清除容器 metadata 和 chapter。
- asset-library、clean-image、digital-human 等部分落盘路径接入清洗。
- 资产目录 provider sidecar JSON 和 storyboard asset manifest 默认禁止下载和打包。
- 新数字人文件名和 asset label 使用中性名称。
- 存量元数据清洗脚本和数字人文件重命名脚本，默认 dry-run。

### 3.4 自动化检查

已加入：

- 模型泄露路由 inventory 守卫。
- 客户出口脱敏契约测试。
- 媒体清洗、sidecar 下载策略和存量迁移脚本测试。
- CI 中的 `scripts/check_model_leakage_guard.py`。
- Phase 1A-2 新增 `frontend/scripts/model_bundle_leakage_contract.mjs`，在 frontend build 后扫描 HTML/JS/CSS/source map。
- Phase 1A-3 把 bundle 扫描词表与后端策略统一到版本化 `model_leakage_policy.json`，覆盖 provider 品牌、domain、model family、映射字段、固定 locator 和价目特征；新增扫描器 fail-closed 自测，并以逐 pattern 精确上限登记尚未完成的 Phase 3 债务。

2026-07-13 本次复审结果：

- 当前分支 `main` 与 `origin/main` 一致。
- 模型泄露守卫通过：391 条 `/api` 路由，76 条排除，315 条受保护；114 条 koubo-storyboard 路由全部受保护。
- 相关专项契约测试 52 项通过。
- macmini-4 测试环境后端、前端和公网健康检查正常。

上述结果只证明现有测试定义内为绿色，不代表完整安全目标已经满足。

## 4. 问题描述

### 4.1 通道 A：前端静态 bundle 匿名泄露

#### 现象

当前公网正在服务的构建中，有 7 个 JavaScript chunk 命中真实 provider/model 或映射字段，包括但不限于：

- `gpt-image-*`
- `sora-*`
- `gemini-*`
- `veo-*`
- `grok-*`
- `kling-*`
- `wan2.*`
- `seedance-*`
- `provider_label_real`
- `model_label_real`

按明确、完整的 model ID 统计，匿名构建中至少可识别 21 个真实 model ID；本次使用更宽的模型模式扫描得到 35 个唯一命中，其中包含前缀和变体，因此不能把 35 直接等同于独立模型数量。无论采用哪个口径，都已远超“偶发字符串残留”，而是可一次性获得真实模型目录。

真实价格表仍存在于：

- `frontend/src/lib/meteringFormat.js`
- `ModelConfig/frontend/src/shared/pricing.ts`

别名匹配仍读取真实字段：

- `frontend/src/components/ModelPresetCards.jsx`

更严重的是，该文件并非只引用 `provider_label_real` / `model_label_real` 字段名，而是在源码中直接硬编码别名映射：

```javascript
providerValues: ["max", "openai"]
modelValues: ["max", "gpt-5.5"]

providerValues: ["flash", "opencode", "opencode_zen", "opencode zen"]
modelValues: ["flash", "deepseek-v4-flash-free", "deepseek v4 flash free", "deepseek flash"]
```

这不是通过别名、价格或能力进行“部分反推”，而是把 Max/Flash 的 alias-to-real 映射表直接发送给匿名客户。单独这一项就足以使当前 Max/Flash 别名体系失去脱敏意义，风险高于普通价目表字符串残留。

真实名称还存在于常量之外的匹配函数中：

```javascript
function hasFlashModelMatch(values) {
  return values.some((value) => value.includes("deepseek") && value.includes("flash"));
}
```

因此，只机械删除 `providerValues` / `modelValues` 中的真实值并不能完成 P0-0；`hasFlashModelMatch()` 及其两个调用分支也必须删除或改为只匹配公开 alias。

另一方面，对于已经正确接入 `model_policy` 的普通用户 surface，移除这些真实匹配不会影响 alias 路径：

- `model_policy.py::_masked_item()` 对非 admin 且策略为 alias 的 catalog，把 `providerID/providerName/modelID/modelName` 改写为 `Max` 或 `Flash`，并写入 `source: "alias"`。
- 这些受 policy 管理的 surface 中，`MODEL_PRESETS` 保留 `max` / `flash` 即可完成普通用户匹配。
- 但 `/api/ocrebuild/*` 是已确认的例外：它被 `ModelPresetCards` 消费，却完全未接入 `model_policy`，详见 4.6。因此，不能再断言“所有普通用户预设卡片 surface 均采用 alias”，也不能把“普通用户整体无回归”作为 P0-0 的无条件前提。
- admin 由 `mask_prompt_models_for_role()` 原样取得真实 catalog，删除真实匹配后，其预设卡片在 P0-4 恢复前可能显示 `Not connected`。
- 任何现存或未来重新启用的非 alias 客户 surface 都不得靠公共 bundle 内的真实名称匹配；必须先接入 alias/public SKU 契约或拒绝 USER 访问。

`provider_label_real` / `model_label_real` 则是另一种情况：当前 provider catalog API item 只构造 `providerID/providerName/modelID/modelName` 等字段，`_masked_item()` 也不注入 `*_real`。全仓库前端消费点只有 `ModelPresetCards.jsx` 这两处。因此这两个读取属于当前死代码，删除不会造成现有功能降级；同时应加契约测试，防止后端将来新增同名字段。

两份定价文件的静态真实数据也应按文件准确区分：

- `frontend/src/lib/meteringFormat.js`：`MEDIA_PRICE_POINTS`、`LIPSYNC_PRICE_COMPARISON`。
- `ModelConfig/frontend/src/shared/pricing.ts`：`MEDIA_PRICE_POINTS`、`VIDEO_MIN_DURATION_SECONDS`；该文件并不存在 `LIPSYNC_PRICE_COMPARISON`。

同时，`frontend/vite.config.ts` 的 `manualChunks()` 把整个 `/ModelConfig/` 树固定打入 `model-config` chunk。当前首页构建会匿名加载该 chunk，因此 `ModelConfig/frontend/src/shared/pricing.ts` 中本用于 admin 界面的真实价目不是“登录 admin 后才下发”，而是无条件进入匿名可下载的静态资源。

#### 风险

该通道无需登录。一次 `curl` 即可取得真实模型目录、两份价目数据和 alias-to-real 映射。即使真实名称只用于 admin 页面，只要它被编译到静态 chunk，匿名客户就能直接下载。

仅做前端懒加载或权限隐藏不能解决问题，因为静态 chunk 本身仍由 Web 服务器匿名提供。

#### 根因

- 真实价目、真实标签和 alias 匹配逻辑仍在前端源码。
- Max/Flash alias-to-real 映射以常量形式存在于公共组件。
- admin 数据和客户数据没有在构建边界分离。
- `manualChunks` 把 ModelConfig 管理代码和价目集中进匿名可下载 chunk。
- CI 只验证前端能够 build，没有扫描 build 后产物。
- 旧 hashed chunk/CDN 缓存没有纳入发布验收。

### 4.2 通道 C：异常文字泄露

#### 已确认的现象

当前出口正则能够在部分字段中清洗品牌和带版本的模型 ID，但不同字段走不同正则，导致最常用的展示字段反而具有更大的穿透面。

以下示例在 USER 出口实测会原样保留：

- `Google TTS request failed`
- `Gemini request failed`
- `Kling video generation failed`
- `Wan video request failed`
- `Qwen TTS response failed`
- `CosyVoice synthesis failed`
- `MiniMax voice failed`
- `OpenRouter request failed`

`detail`、`error` 等非 free-text 字段走完整的 `MODEL_LEAKAGE_DENY_RE`，其中 `gemini-2.5-flash` 一类带版本 model ID 通常会被清洗；但 `message`、`content`、`text`、`summary`、`description`、`title` 等常用展示字段被归入 `CUSTOMER_EGRESS_FREE_TEXT_KEYS`，只走仅含 8 条品牌规则的 `MODEL_LEAKAGE_BRAND_RE`。

实测结果如下：

| 字段 | 使用的正则 | `gemini-2.5-flash` |
|---|---|---|
| `detail` / `error` | `MODEL_LEAKAGE_DENY_RE` | 被替换 |
| `message` / `content` / `text` | `MODEL_LEAKAGE_BRAND_RE` | 原样保留 |
| `summary` / `description` / `title` | `MODEL_LEAKAGE_BRAND_RE` | 原样保留 |

`label`、`name`、`notes`、`prompt`、`query`、`script` 等字段也使用 free-text 分支。因此问题不只是“裸品牌名漏网”：**完整、带版本号的真实 model ID 只要位于常见展示字段中，也可能直接透传。**

`HeyGen request failed` 会因恰好命中 8 条品牌规则之一而被替换，但不能据此推断其他 provider/model 安全。这进一步证明当前行为依赖 denylist 和字段位置的偶然覆盖，而不是安全的异常契约；异常泄露应按 P0 处理。

#### 当前异常来源

代码中存在大量以下模式：

```python
raise HTTPException(status_code=502, detail=f"Provider request failed: {detail}")
raise HTTPException(status_code=502, detail=str(exc))
state["error"] = str(exc)
event_payload["error"] = stderr or stdout
```

静态扫描得到以下候选数量：

- 196 个 provider/model 相关 `HTTPException` 候选点。
- 174 个原始异常、上游 body 或进程 stdout/stderr 候选点。
- 402 个 ToolLibrary 供应商化 ToolError/RuntimeError 候选点。

这些数字是审计候选，不表示每一处都可被客户直接利用，但证明逐个补词无法形成可靠边界。

#### 异常泄露路径

异常不只通过同步 HTTP 返回，还可能沿以下路径传播：

```text
供应商响应/内部异常
  ├─ HTTPException.detail
  ├─ 未处理异常 str(exc)
  ├─ SSE data/detail/error
  ├─ session event payload
  ├─ execution state/result JSON
  ├─ run_state / one_click_movie_state
  ├─ stdout/stderr/logs
  ├─ raw 文件下载
  └─ files.zip / share download
```

#### 现有出口层的具体缺口

1. **free-text 分支大面积漏网**：常见的 `message/content/text/summary/description/title` 只使用 8 条品牌规则，裸 provider 和完整带版本 model ID 都可能原样透传。
2. **非 JSON `text/plain`**：当前测试明确允许非 JSON plain text 不清洗。
3. **文件路径整体排除**：raw/download 路径在响应类型和状态码确定前就跳过 C0；这些路径返回异常 JSON 时同样不会清洗。
4. **SSE 非 data 字段**：`event:`、`id:`、`retry:` 和注释行不做脱敏；CRLF 和非标准流没有完整契约。
5. **响应头未清洗**：`HTTPException.headers`、`Location`、`Content-Disposition` 等不在当前 body sanitizer 范围。
6. **Pydantic 校验详情**：可能回显非法输入、枚举候选或 schema context。
7. **前端直接展示原文**：多处 `err.message`、`payload.detail`、`response.text()` 会直接展示服务端返回内容。

#### 为什么不能只扩充正则

- 新 provider、新 model 和新错误格式会不断出现。
- 同一 provider 可使用空格、连字符、缩写、本地化名称或上游错误码。
- 上游 body 可能含域名、模型、请求参数、供应商 request ID 和配额文案。
- 过度 scrub 会误伤客户自由文本。
- 如果在前端维护真实供应商 denylist，denylist 本身会进入公开 bundle，形成新的静态泄露。

因此，异常必须“安全构造”，正则只能作为最后兜底。

### 4.3 异步状态、事件和内部文件泄露

#### 现象

后台任务当前会把原始异常写入：

- `video_plan_execution_state.json`
- `video_plan_execution_result.json`
- `image_plan_execution_state.json`
- `video_only_plan_execution_state.json`
- `run_state.json`
- `one_click_movie_state.json`
- ToolLibrary Output/Interactive state
- session events

`SessionFileService` 目前只重点保护资产目录 provider sidecar、少数 manifest 和名称明显敏感的文件。上述 execution state/result 默认分类为：

```text
visibility=public
sensitivity=normal
downloadable=1
```

测试环境中有 532 个 workspace state/result/run JSON 命中 provider/model、域名或相关敏感词。该数字是词法命中数量，不代表全部内容都属于异常，但这些文件默认可下载本身已违反“内部状态不直接公开”的边界。

#### 风险

正常 API JSON 即使经过 C0，客户仍可请求 raw 文件或打包下载原始状态，从而绕过脱敏。

当前风险链路为：

```text
provider exception
  → str(exc)/stderr 写入 execution state
  → state 文件默认 public/downloadable
  → raw 或 files.zip 下载
  → 直接获得原始 provider/model/error body
```

#### 下载判定并未统一

当前四类主要下载方式并不共用一套授权判断：

- raw、signed download、share download 调用 `SessionFileService.resolve_download()`，会读取 `session_files` DB 行，并允许 DB 中的 `visibility`、`sensitivity`、`downloadable` 覆盖路径默认值。
- `files.zip` 调用 `SessionFileService.zip_entries()`，不读取 DB 行，而是对磁盘路径重新执行 `default_file_visibility()` 和敏感词分类。

因此，如果 P0-3 只给 `session_files` 表增加 `artifact_class` / `publish_state` 并只改 `resolve_download()`，可能出现：单文件 raw 返回 403，但同一个文件仍被 `files.zip` 打包下载。

此外，以下文件服务路由完全绕过 `SessionFileService`，直接返回 `FileResponse`：

- `dance_mimic_router.py` 的目标图预览、参考视频预览和 privacy-grid preview。
- `koubo/router.py` 的 voice catalog 音频下载。
- `clean_image_routes.py` 的生成图和参考图预览。

这意味着 `SessionFileService` 目前不是完整的客户文件出口边界。P0-3 必须同时统一 zip 判定和所有直出 `FileResponse` 路由，否则会形成“单文件策略已修复、旁路仍开放”的假边界。

### 4.4 debug audience 鉴权旁路

`GET /api/sessions/{id}/events?audience=debug` 和对应 stream 当前没有 admin 角色校验，普通登录用户可以请求 debug audience。

虽然 C0 会清洗部分结构化字段，但：

- 裸 provider 异常可能漏过正则。
- debug event 可能包含内部路径、工具输出和未知字段。
- 出口 sanitizer 不应替代访问控制。

预期行为应为：非 admin 使用 `audience=debug` 明确返回 403。

### 4.5 角色 fail-open 风险

客户出口中间件在角色未知时按 USER 处理，这是正确的；但 `model_policy.request_role()` 在角色未知时仍回退 ADMIN。

这会导致逐路由 mask/resolve 逻辑在缺失 middleware state、测试调用、未来新入口或错误集成时 fail-open。

此外，模型策略本身存在第二个、可与角色问题叠加的 fail-open：

- `user_model_policy()` 在配置了 `OPENCREW_USER_MODEL_POLICY_PATH` 时直接使用整份外部 JSON，不与默认策略合并。
- `surface_policy()` 在 surface 不存在或配置不是对象时返回 `{}`。
- `policy_mode()` 在缺 `mode` 时返回空字符串。
- `mask_prompt_models_for_role()` 对任何既不是 `hide` 也不是 `alias` 的 mode 原样返回真实 catalog。
- `resolve_prompt_model_for_role()` 对同一情况调用 admin 解析逻辑，允许 USER 提交真实 provider/model，并同时返回真实 catalog。

已对“缺 surface”“surface 缺 mode”和“mode 为未知字符串”三种情况做直接调用验证，三者均返回原始 catalog，真实选择也能解析成功。这意味着新增 surface 忘记写策略、外部策略漏 key 或 mode 拼错时都会静默退化为 raw，且现有测试不会必然失败。

统一原则应为：所有未识别角色默认 USER；非 admin 只允许显式、校验通过的 `alias` 或 `hide`，缺失/未知 mode 一律 fail-closed；只有经过明确鉴权的 admin 才能看到真实名称。

### 4.6 `/api/ocrebuild/*`：退休 UI 后仍存活的 USER 真实 catalog 与执行旁路

`#/ocrebuild` 已列入 `RETIRED_NAV_HASH_PREFIXES`，主 UI 不再提供导航入口，但这只是可发现性缓解，不是安全控制。后端仍存在完整攻击面：

- `build_oc_rebuild_router()` 仍在 `app.py` 注册，当前共有 56 条 `/api/ocrebuild/*` 路由。
- `ADMIN_ONLY_PATH_PREFIXES` 只有 `/api/setup/`、`/api/model-config/`、`/api/local-metering/`，不包含 `/api/ocrebuild/`；持 USER cookie 即可调用。
- `rebuild_router.py::serialize_prompt_models()` 本地构造真实 `providerID/providerName/modelID/modelName`，并优先把 `openai/gpt-5.5` 设为 default。
- `GET /api/ocrebuild/tasks/{id}` 和创建任务响应把该 catalog 放入 `detail["prompt_models"]`。
- `OCRebuildModule.jsx` 读取这些 items，并在 prompt/run 两个对话框直接传给 `ModelPresetCards`。
- `rebuild_router.py::resolve_model()` 是独立 raw resolver，不调用 `resolve_prompt_model_for_role()`；多个生成、refine 和 run 路径可由 USER 提交任意已连接的真实 provider/model。

集中出口层不能把这条路径变成安全 alias 契约。对 USER payload 的直接验证显示：

- denylist 命中的 `openai/gpt-5.5`、`opencode/deepseek-v4-flash-free` 被整对置为空字符串，default 也变空，导致卡片/默认选择契约损坏。
- denylist 未覆盖的 `anthropic/claude-opus-5`、`moonshot/kimi-k2` 等真实名称原样保留，形成直接 catalog 泄露。
- C0 只做删除/替换，不会把 raw catalog 重建为 Max/Flash，也不会限制 raw resolver 的执行授权。

因此，这不是第 10 节中的中性产品决策，而是当前 P0 漏洞。产品决策只决定最终下线该前缀，还是将其完整接入 role-aware policy；在决策和迁移完成前，USER 访问必须立即拒绝。

### 4.7 通道 B：媒体元数据和清洗范围不足

#### 当前数据证据

测试环境扫描发现：

- 379 个媒体文件包含 `C2PA`、`JUMBF`、`claim_generator`、`contentauth`、Google/OpenAI/HeyGen 等身份标记之一。
- 其中 181 个位于当前 B4 脚本扫描范围内。
- 198 个位于当前 B4 范围外。
- 11 个文件修改时间不早于首个脱敏主体提交。
- 13 个存量数字人文件名仍含供应商品牌，待重命名迁移。

当前 B4 只覆盖：

- `SessionOutput/storyboard/assets/images`
- `SessionOutput/storyboard/assets/videos`
- `SessionOutput/storyboard/assets/audios`
- `SessionOutput/storyboard/assets/history`
- `SessionScratch/CleanImageGenerations`

未覆盖的高风险目录包括：

- `S9_05_02_VideoPlanExecutor/Working`
- `S9_05_02_VideoPlanExecutor/Output`
- `SessionOutput/storyboard/Working`
- `SessionContext`
- 其他 ToolLibrary Working/Output/Interactive 目录

2026-07-13 新产生的两个 Raw MP4 在 Working、Output 和 storyboard Working 中形成六份副本，仍包含 C2PA/JUMBF `claim_generator/contentauth` 结构。现有 sanitizer 对临时副本执行后能够移除这些结构，说明问题不是 sanitizer 算法失效，而是执行器产物没有经过该 sanitizer。

#### 根因

- 当前清洗接入点分散在 asset-library、clean-image、digital-human 等服务。
- ToolLibrary 有多条独立下载、复制、重编码和 promote 路径。
- “唯一媒体 sink”假设不成立。
- raw/zip 下载允许访问执行器中间产物。
- 存量脚本按少数固定目录扫描，未按“所有客户可下载媒体”定义范围。

### 4.8 文件名和响应头

除媒体字节外，以下位置也可能泄露名称：

- 生成文件名。
- 资产 label/source/origin。
- raw URL path。
- `Content-Disposition` 下载文件名。
- zip entry 名称。
- 上游重定向 `Location`。

数字人已对新文件做中性命名，但其他生成器仍需审计；同时必须区分系统生成文件名和客户主动上传的原始文件名，避免错误修改客户内容。

### 4.9 CI 与文档状态不一致

当前 CI 守卫可以验证已知路由和样本，但存在以下盲区：

- Phase 1A-3 已把 build 后扫描扩展为共享策略 manifest，并校验 ModelConfig 后端 catalog 中每个 provider 均被策略覆盖；但语义映射、未来未登记的新命名方式和 Phase 3 许可债务仍需治理，不能把正则命中面等同于最终边界。
- 没有验证旧 hashed chunk 不可访问。
- 没有完整异常 provider 矩阵。
- 没有覆盖 plain text、headers、下载路径错误、SSE comment/CRLF。
- 没有审计 state/result/run JSON 的下载策略。
- 没有扫描所有客户可下载媒体目录。
- route count 是固定数字，新路由需要人工同步计数，但不能证明语义安全。
- 没有禁止 customer model surface 自建 raw catalog/resolver，因此路由 inventory 通过时仍漏掉了 `/api/ocrebuild/*` 的语义旁路。
- 没有覆盖外部 model policy 缺 surface、缺 mode 或 mode typo 的 fail-open 负向测试。
- live smoke 需要普通用户 cookie；本次未进行完整已登录现网 smoke。

现有 7 月 9 日设计文档仍标注“设计稿、未改代码”或只记录早期步骤，与当前部分落地、部分未完成的实际状态不一致，也会增加审核和部署误判风险。

## 5. 根因分析

问题不是单一正则缺词，而是边界不统一：

1. **内部对象和公共对象混用**：同一 payload 同时承载真实 provider/model 和客户显示字段。
2. **异常没有公共契约**：原始异常被当作可展示文案使用。
3. **内部状态和客户文件共用 workspace**：只靠文件名敏感词判断下载权限。
4. **媒体落盘 sink 分散**：没有统一的“客户发布”阶段。
5. **静态前端承担真实配置逻辑**：把 admin 数据编译进匿名可下载资源。
6. **防护以 blacklist 为主**：新增字段、格式和 provider 容易绕过。
7. **访问控制与脱敏混淆**：debug 数据依赖 C0 清洗，而非先做 admin 授权。

## 6. 解决方案总纲

采用四层边界：

```text
内部真实域
  provider/model/key/upstream body/internal exception
        │
        ├─ 仅内部日志、internal DB、meta/provider_private
        │
        └─ 明确转换
             ↓
公共数据域
  alias/public code/public message/public asset
             ↓
集中出口兜底
  JSON/SSE/text/error/header validation
             ↓
客户浏览器与下载文件
```

原则：

- 公共响应使用 allowlist 重建，不从内部对象“删几个字段后直接返回”。
- 异常安全由结构化错误契约保证，正则只是最后防线。
- 文件能否公开按数据类型和发布状态决定，不按名字是否看起来敏感决定。
- 所有客户可下载媒体必须经过同一个 publish/sanitize boundary。
- 静态前端永远不持有真实映射；admin 数据也通过鉴权 API 动态加载。
- 未识别角色一律 USER。

## 7. 详细解决方案

### 7.1 建立统一公共异常契约（Phase 2A / P0-1）

#### 公共错误结构

所有客户可见错误统一为：

```json
{
  "error": {
    "code": "UPSTREAM_GENERATION_FAILED",
    "message": "生成失败，请稍后重试。",
    "retryable": true,
    "request_id": "req_public_xxx"
  }
}
```

公共结构不得包含：

- provider/model。
- endpoint/domain。
- 上游 HTTP body。
- 上游 request ID。
- API key、headers、请求 payload。
- `str(exc)`、repr、traceback、stdout/stderr。
- 内部绝对路径和工具脚本名。

#### 内部错误结构

内部使用独立异常类型，例如：

```python
ProviderFailure(
    public_code="UPSTREAM_GENERATION_FAILED",
    public_message="生成失败，请稍后重试。",
    retryable=True,
    provider=real_provider,
    model=real_model,
    upstream_status=status,
    internal_detail=raw_detail,
    internal_request_id=upstream_request_id,
)
```

真实字段只写：

- 服务端受控日志。
- admin-only 诊断记录。
- `meta/provider_private` 或等价内部存储。

不得直接序列化到客户 state、event 或 HTTP body。

#### 异常映射规则

| 内部异常 | 客户 code | 客户 message | HTTP |
|---|---|---|---:|
| provider 4xx/5xx | `UPSTREAM_GENERATION_FAILED` | 生成失败，请稍后重试 | 502 |
| provider timeout | `UPSTREAM_TIMEOUT` | 生成仍在处理中或暂时超时，请稍后重试 | 504 |
| provider quota | `SERVICE_CAPACITY_UNAVAILABLE` | 当前服务额度不足，请联系管理员 | 503 |
| provider content policy | `CONTENT_NOT_ACCEPTED` | 当前内容未通过生成服务检查，请调整后重试 | 422 |
| alias 无效 | `PUBLIC_MODEL_INVALID` | 请选择有效的模型选项 | 400 |
| 配置缺失 | `SERVICE_NOT_CONFIGURED` | 当前服务暂不可用，请联系管理员 | 503 |
| 未知 500 | `INTERNAL_ERROR` | 系统暂时不可用，请稍后重试 | 500 |

不要把供应商原始错误码直接返回；如前端确实需要分支行为，只返回系统自有的稳定公共 code。

#### FastAPI 处理要求

- 已知 `PublicError` 按公共结构返回。
- `ProviderFailure` 记录内部详情，客户只拿公共字段。
- 未知异常对 USER 永远返回通用 500，不使用 `str(exc)`。
- admin 如需详情，使用独立 admin-only 诊断 API，不在同一响应按 role 塞入任意内部对象。
- `RequestValidationError` 删除 `input`、`ctx` 和枚举内部候选，只返回字段位置与公共校验 code。
- 响应 headers 使用 allowlist；不得透传上游 headers。

#### 前端兼容迁移要求

公共错误结构不能在未迁移消费端的情况下直接替换现有 `detail` / `message` / 字符串 `error` 契约。当前多个前端调用点会把未知 payload 回退为原始 JSON；如果直接返回嵌套对象，虽然多数情况下不会抛出运行时异常，但可能显示 `[object Object]`、整段 JSON 或完全失去可读错误提示。

P0-1 必须满足：

- 前端先提供唯一的 `publicErrorMessage(payload)` 适配器，在迁移窗口内同时识别新 `error.code/message` 和旧 `detail/message/error` 形态。
- 旧兼容字段只能由服务端使用公共 code/message 安全构造；不得为了兼容继续填入 `str(exc)`、上游 body 或原始 payload。
- UI 和 `new Error()` 只能接收适配器返回的字符串，禁止把未知 error 对象直接交给渲染层或异常构造器。
- HTTP handler、前端适配器和客户文案必须同批发布并可整批回滚；不能只改后端结构后依赖各页面的 JSON fallback。
- 契约测试覆盖字符串 error、结构化 error、validation error、plain text、空响应和未知 500，并验证最终展示文本不含原始 JSON 或内部名称。

### 7.2 后台任务与持久化错误分层（Phase 2B / P0-3）

后台任务不得写：

```python
state["error"] = str(exc)
```

P0 第一阶段不得机械地把现有 `error` 键全量改名为 `public_error`。该键目前同时参与 backend、ToolLibrary 独立执行进程、断点续跑和前端 API 消费；`video_only_plan_routes.py` 还读取其中的 `sensitive_output` 文本执行 failed→completed 自愈。只改任一侧不会必然崩溃，却会静默打断自愈和跨进程状态协议。

兼容阶段建议写为：

```json
{
  "schema_version": 2,
  "status": "failed",
  "error": "生成失败，请稍后重试。",
  "public_error": {
    "code": "UPSTREAM_GENERATION_FAILED",
    "message": "生成失败，请稍后重试。",
    "retryable": true,
    "request_id": "req_public_xxx"
  },
  "internal_error_ref": "req_public_xxx"
}
```

其中兼容 `error` 只能保存 public-safe 字符串，不得保存原始 provider 异常。API response mapper 在迁移期继续向旧前端输出安全的 `error` 字符串；新前端读取 `public_error`。原始细节仍按下述内部路径保存。

内部详情另存：

```text
meta/provider_private/errors/<request_id>.json
```

或写入受控数据库表，至少包含：

- request_id。
- provider/model。
- 上游状态和原始 body。
- traceback。
- 关联 task/session/attempt。
- 创建时间和保留期限。

客户事件只引用公共 error；admin 诊断按 request_id 查询内部记录。

跨进程迁移必须遵守：

- backend 与 ToolLibrary 的所有顶层和嵌套 `error` 读写点同批盘点；不能只修改已知的正常完成路径，还必须覆盖异常分支、`segment_state`、`step_payload` 和 previous-state 读取。
- `sensitive_output` 自愈不得继续依赖公共错误文本包含某个供应商字符串；应在清洗原文前转换为稳定的内部 failure code，并由自愈逻辑读取该 code。
- 如最终删除兼容 `error`，必须先完成 schema version、至少一个迁移窗口的双读/双写以及旧状态文件迁移；禁止单边改名。
- 回归测试必须覆盖历史状态文件加载、执行器断点续跑、failed→completed 自愈、backend/ToolLibrary 跨进程往返和前端旧版 API 消费。

### 7.3 加固集中出口层（Phase 2A / P0-1）

C0 保留为最后一道防线，并做以下调整：

1. 先等待 `http.response.start`，再根据 role、status、content-type 决定是否清洗。
2. 文件路径不能在入口阶段无条件排除：
   - 成功的二进制 2xx 可直通。
   - 文件路由返回的 JSON、problem+json、plain-text 错误必须走错误清洗。
3. 非 admin 的 API plain text：
   - 原则上禁止；统一改 JSON 公共错误。
   - 无法立即迁移的 plain text 至少替换为通用错误，不返回原文。
4. SSE：
   - 支持 `\n\n` 和 `\r\n\r\n`。
   - 清洗 `data:`，并禁止 `event:`、`id:`、`retry:`、comment 携带内部文本。
   - 非标准 frame fail-closed。
5. 支持或明确禁止 NDJSON、JSON-seq、multipart streaming 和未来 WebSocket。
6. 对客户响应 headers 使用 allowlist，重点处理 `Location`、`Content-Disposition` 和自定义 `X-*`。
7. 设置最大可缓冲 JSON 响应，避免大响应导致内存风险。

### 7.4 完成通道 A：前端 alias-only

#### P0-0：立即静态止血（Phase 1A）

把无需后端契约迁移即可移除的匿名静态泄露拆成独立 P0-0，并作为第一实施项：

- 在 `frontend/src/lib/meteringFormat.js` 保留原有 named export，把 `MEDIA_PRICE_POINTS` 和 `LIPSYNC_PRICE_COMPARISON` 中性化为空数组 `[]`。
- 在 `ModelConfig/frontend/src/shared/pricing.ts` 保留原有 named export，把 `MEDIA_PRICE_POINTS` 中性化为 `[]`、把以真实 model ID 为 key 的 `VIDEO_MIN_DURATION_SECONDS` 中性化为空对象 `{}`；不要误按另一文件的导出名操作。
- 在 `ModelPresetCards.jsx` 的 `providerValues` / `modelValues` 中只保留 `max` / `flash` 公开 alias，删除全部真实 provider/model 值和 Max/Flash alias-to-real 映射。
- 把删除 `hasFlashModelMatch()` 函数定义、删除其两个调用分支视为一个不可拆分的原子修改；禁止只删定义或只删部分调用。只删函数会在 Flash 精确匹配失败后进入残留调用，在 `createMemo` 渲染期触发确定性 `ReferenceError`。
- 删除对 `provider_label_real` / `model_label_real` 的读取；这是当前 API 契约下的死代码，不产生功能降级。
- 重新 build，扫描所有 chunk，删除旧 dist 文件并处理 CDN/browser cache。

P0-0 不得通过删除 named export 来完成中性化。虽然完整 Vite/Rollup build 通常会对残留 import 响亮失败，但未经过完整构建的 dev/手工发布路径可能在浏览器 import 阶段白屏；保留空数组/空对象既关闭静态泄露，也保持现有 import 和消费者的安全降级契约。

该步骤不应等待 admin 动态配置 API 和完整 alias-only 契约。其功能影响必须按 surface 区分，不能再概括为“普通用户全部无回归”：

- 已接入 policy 的普通用户 surface：收到 `Max/Flash` alias catalog，保留 `max` / `flash` 后，自动匹配和选择不应降级。
- `/api/ocrebuild/*`：当前 USER catalog 已因 C0 部分置空、部分泄露，预设卡片本就处于不安全且半损坏状态；P0-0 既不会修复它，也不得用它证明普通用户 alias 契约成立。该前缀必须由 P0-2 单独阻断或迁移。
- admin：后端原样返回真实 catalog；删除真实匹配后，Max/Flash 卡片可能暂时显示 `Not connected`。
- admin：`MediaConfigModalBase.tsx` 和 `useMediaSettingsController.jsx` 直接消费静态价目，价格排序、最低时长价格和比较展示需要暂时隐藏。
- 死代码：删除 `provider_label_real` / `model_label_real` 读取没有功能影响。

P0-0 可以接受暂时隐藏 admin 价格排名和禁用 admin 真实值驱动的预设匹配；目标是先关闭匿名全表泄露。验收必须包含受 policy 管理的 USER surface 的 Max/Flash 自动匹配无回归、`/api/ocrebuild/*` 不被误判为安全、admin 已知降级符合预期，以及前端 build、lint 和核心页面 smoke。提交前至少执行 `npm --prefix frontend run lint`、对应 frontend build 和 `ModelPresetCards` 渲染 smoke；并以 `rg`/bundle scan 确认 `hasFlashModelMatch`、`deepseek` 与真实映射零残留。仓库现有 `.github/workflows/ci-gate.yml` 已执行 frontend lint，因此要求该 job 在 P0-0 合并时实际运行且为 required check；不能误写为“CI 没有 lint”，也不能让只执行 build 的手工发布绕过它。

必须如实区分自动与人工 gate：当前 CI 中的 frontend lint 可以自动拦截 `ModelPresetCards.jsx` 的未定义调用，frontend build 也已自动执行；但仓库现有 `test:e2e:*` 脚本均未接入 `.github/workflows/ci-gate.yml`，`ModelPresetCards` 渲染 smoke 当前只是人工发布步骤。现有 `shell-nav-smoke.mjs` 只验证导航、计费和角色限制，并未打开任何渲染 `ModelPresetCards` 的对话框，不能单独作为该回归的自动证明。只有新增带 mock catalog 的确定性组件测试，或扩展 e2e 实际打开预设卡片并验证 Max/Flash，且将其接入 CI 后，文档才能把“渲染 smoke”称为自动合并 gate。在此之前，发布记录必须保存人工 smoke 结果。P0-0 不应被完整 admin 动态化工程拖延，但也不能用于关闭 ocrebuild 的 P0 状态。

#### P0-0B：匿名 bundle 残余静态止血（Phase 1A-2）

Phase 1A 关闭整张价目表和 alias-to-real 映射后，实际重建产物仍暴露了一批分散在默认值、兼容映射、能力判断和管理端帮助链接中的真实 model ID/account locator。Phase 1A-2 是 P0-0 的受控扩展，只删除前端自己携带且已有 alias、catalog 元数据或服务端默认可替代的静态知识，不扩大到后端/API 契约工程。

本阶段实施范围：

- `AnalysisV1Module.jsx` 不再硬编码首选 run provider/model；USER 优先使用公开 `max` preset，admin 在无公开 preset 时回落到服务端 `default_model`。
- `MediaConfigModalBase.tsx` 删除真实 model rename 表；管理端自动创建 agent alias 时使用 `Image Model` / `Video Model` 等中性名称和序号，不再从真实 model label 解析供应商或系列名。
- `useMediaSettingsController.jsx` 同步使用中性 alias 生成规则，避免主应用与 ModelConfig 两套实现再次分叉。
- 视频能力判断只使用公开 alias 或 catalog/alias capability 元数据；删除“无 alias 时按真实 provider/model 字符串补丁”的分支。catalog 已提供的顶层 `reference_mode` 或 `reference_images.mode` 继续生效。
- StoryBoard TTS 默认选择只读取 active/configured provider/model 和已保存/公开 voice selection；删除按真实 TTS model ID 排序的 fallback。Talking Head 缺省值交给现有后端解析/默认逻辑，不在匿名前端重复保存真名。
- Qwen Voice Guide 使用 `supports_prompt_builder`、`voice_modes` 和 voice `mode` 判断是否支持指令场景，不按真实 model ID substring 判断；帮助文字删除具体 model ID。
- 删除前端静态 team/account URL，保留供应商控制台通用入口。

本阶段明确排除：

- 不改后端 model catalog、resolver、model policy、角色、请求或响应 schema；`/api/ocrebuild/*` 仍由 Phase 1B 阻断或后续迁移。
- 不改公共异常、execution state、ToolLibrary、DB、artifact 下载、raw/zip/share 或媒体 publish boundary。
- 不宣称普通 provider 品牌、provider docs URL 和管理端 provider label 已清零；这些属于 Phase 3 的完整通用组件/admin 动态化范围。
- 当前未被入口引用的退休/源代码模块即使仍含真实默认值，也不能据此关闭源码治理；它们必须保持不可达，并由 Phase 1B/Phase 3 及每次真实 build 扫描约束，重新进入 bundle 时必须阻断发布。

相容性边界与已知降级：

- USER 的 Max/Flash preset 与公开 voice alias 不变；请求字段名和 API payload 不变。
- admin 的旧 image model rename 兼容会停止，新增 agent alias 的默认显示名变为中性编号；管理员仍可手工编辑 alias。
- 未配置公开 alias、且 catalog 又没有 capability 元数据的 admin 视频模型，不再获得按真实名称猜测的参考素材模式；这是有意 fail-closed，必须在 admin smoke 中确认提示和降级可接受。
- TTS 没有 active/configured model 时不再由浏览器猜具体供应商；admin 生成前沿用现有显式“请先设置可用 Provider / Model / Voice”校验，USER 继续依赖保存选择或公开 voice alias。

Phase 1A-2 的发布 gate：

1. 对实际 `frontend/dist` 的所有 HTML/JS/CSS/动态 chunk 扫描本次发现的真实 model ID、model-family prefix 和 team/account locator，必须零命中；`test:model-bundle-contract` 必须在 CI 中紧随 build 自动运行。删除旧 dist 后重建，不能只扫源码。
2. frontend lint/build 必须通过；由于 ModelConfig TS/TSX 当前仍不在 lint/typecheck 范围，必须额外完成动态 chunk 浏览器加载检查和管理弹窗人工交互 smoke。后者必须标记为人工发布 gate，不能用 build/动态 import 成功冒充完整类型检查。
3. 对 Analysis V1 默认模型、Koubo StoryBoard TTS、视频 reference capability、MediaConfig alias 新增和 Qwen/Xai guide 至少各完成一条成功/空配置 smoke；检查浏览器 `pageerror`。
4. 回滚物也不得恢复本阶段删除的真名。功能回归优先通过 catalog capability、公开 alias 或后端配置修复前滚；禁止把真实字符串重新写回匿名 bundle。

截至 2026-07-14，本工作区已实施上述源码修改。本地验证结果：frontend lint 为 0 error（770 条既有 warning）、完整生产 build 成功（170 modules）、视频 capability 与 TTS configured/empty 纯逻辑 smoke 通过、Chromium 成功加载 Analysis V1/Koubo StoryBoard/Upload Asset Library/ModelConfig 四个动态 chunk且无 `pageerror`，现有模型泄露守卫保持通过。Phase 1A-3 已进一步完成真实 admin/user 配置的交互式页面 smoke，详见下节；但 macmini-1 生产发布、旧公网 chunk URL 和 CDN/browser cache 验收仍未完成，因此不能标记“已发布/已关闭”。

#### P0-0C：审核发现的 provider 品牌止血与 bundle gate 收紧（Phase 1A-3）

本阶段处理 Phase 1A-2 之后新发现的两个缺口，作为独立补丁审核和发布：

- 普通用户人物口播、数字人和声音选择界面的供应商专名改为“云端服务/云端克隆声音”等中性文案；删除前端按 `heygen-*`、`minimax-*`、`cosyvoice-*` 推断 voice provider 的逻辑，继续提交已有不透明 `tts_voice_*` alias，由后端统一解析。
- 删除 ModelConfig 和主应用中的 `chanjing` 双凭证静态特判，改为 admin-only media config API 返回通用 `credential_fields` schema；前端按 schema 渲染、校验和序列化单字段或成组字段，不再携带该品牌分支。
- 删除 TTS/voice-clone 前端硬编码 provider 顺序表；显示顺序由已鉴权 catalog 决定。
- 新增版本化共享策略 `backend/opcrew_backend/model_leakage_policy.json`。bundle 使用完整 `provider_brands`；客户 free-text 出口使用更窄的高置信 `egress_provider_brands`，避免把用户提示词中的普通 “Google style”等文本误删。
- bundle gate 校验所有后端 media catalog provider 均已被策略覆盖；`.map` 无条件失败；未知临时许可、超过精确 match 上限、四个本次审核品牌、真实 model family 或 `*_real` 映射字段均失败。
- 新增扫描器自测，主动证明安全样本通过，`sora-*`、`deepseek-*`、provider 品牌、映射字段、超预算和 source map 均会使 gate 失败。

本阶段不是完整 Phase 3：共享 manifest 中仍有逐 pattern、带 owner/expiry 的 Phase 3 临时许可。许可只能保持或下降，任何新增命中都会失败；它不能作为“匿名 bundle 已零真实 provider/model”的完成证明。

截至 2026-07-14 的验证记录：

- `heygen`、`chanjing`、`minimax`、`cosyvoice` 在新构建的全部 JavaScript chunk 中均为 0 命中；31 个构建资产由 73 条策略扫描通过，并明确打印 13 组 Phase 3 历史债。
- bundle 策略自测、Phase 1A runtime contract、共享策略契约、101 个相关后端契约、frontend lint/build、后端 lint和模型泄露路由守卫通过。
- macmini-4 测试栈已重启，命名隧道 ingress 校验、connector 和公网 `/api/health` 正常。
- 真实普通用户从任务列表打开“人物口播 → 人物声音”，看到“云端克隆声音”；clone-list 返回 200，四个审核品牌在 UI 与响应均为 0，`pageerror`、console error、真实失败请求和 5xx 均为 0。
- 真实管理员只读打开 Video Model Settings，admin-only API 返回两字段 credential schema，页面正确渲染两个凭证字段；未点击保存，页面和控制台无错误。切换历史视频页面时出现的媒体 `ERR_ABORTED` 属浏览器导航取消，未计为服务失败。
- 后端 757 项契约全跑暴露一个既有异步测试竞态：两次全跑分别在不同 Analysis V1 runner 用例返回瞬时 404，两个失败用例单独重跑均通过；这不影响本阶段专项契约结论，但全套稳定性债务不能被隐去。

生产环境未更新，本阶段尚未关闭。

#### P0-4：完整 alias-only 契约（Phase 3）

P0-0/Phase 1A-2 只负责立即移除公开静态真名；随后 P0-4 完成长期契约和管理端能力恢复。

##### ModelConfig 检查覆盖前置

P0-4 会修改 `ModelConfig/frontend/src/shared/MediaConfigModalBase.tsx` 等 TS/TSX 文件，但当前自动检查没有覆盖这棵源码树：

- `frontend/eslint.config.js` 只配置 `frontend/src/**/*.js|jsx`，且显式忽略 `src/**/*.ts|tsx`；`npm --prefix frontend run lint` 的 glob 也只位于 `frontend/src`。
- `frontend/tsconfig.json` 的 `include` 只有 `frontend/src`。当前 `tsc --listFilesOnly` 只包含 `frontend/src/main.tsx`、`lib/api.ts` 和 `shared/tts/googleTtsScenarioGuide.ts`，没有任何 `ModelConfig/frontend/src/*.ts(x)`。
- ModelConfig 由 `.jsx` 文件跨目录导入并交给 Vite/esbuild 转译；Vite build 成功不等于这些 TSX 已执行 TypeScript typecheck，也不能作为未定义符号防线。

因此，在 P0-4 修改 ModelConfig 前，必须先完成：

1. 建立独立的 `typecheck:modelconfig`（或等价 project reference），以 `tsc --noEmit` 覆盖 `ModelConfig/frontend/src/**/*.ts|tsx`，并正确解析 Solid JSX 和 frontend 依赖。
2. 将该 typecheck 接入 `.github/workflows/ci-gate.yml` 并设为 required check；P0-4 不得在该 gate 缺失时合并。
3. 如需 ESLint 覆盖 TSX，必须配置 TypeScript parser/plugin 和明确的 ModelConfig glob，不能只扩大现有 JS/JSX 命令后假定生效。
4. 增加至少一个 ModelConfig admin 动态化渲染测试，覆盖空价目、鉴权 API 成功/失败和变量/函数删除后的页面加载。

##### 前端删除项

- 删除两份 `MEDIA_PRICE_POINTS`、`LIPSYNC_PRICE_COMPARISON` 和 `VIDEO_MIN_DURATION_SECONDS` 中的真实价目/model ID key。
- 删除 `MODEL_PRESETS` 中真实 provider/model 数组。
- 删除 `provider_label_real`、`model_label_real` 消费逻辑。
- 删除 alias↔real 能力特判。
- 删除客户 bundle 中供应商品牌、真实 docs URL、team/account ID 和真实默认 model。
- 所有生成请求只提交 alias/public voice ID。

##### admin 页面

admin 前端组件也必须是通用组件，不应硬编码真实目录。登录后通过 admin-only API 动态获取：

- provider/model 列表。
- 价目。
- docs URL。
- 连接测试信息。

##### 定价 join 键的发布前决策

把价目移到 admin-only 动态 API，只能解决 admin 获取真实数据的传输问题，不能自动解决客户侧定价。当前两个消费点都使用真实 model ID 关联 catalog 与价目：

```javascript
availableModels.has(item.model)
```

该 join 分别存在于 `useMediaSettingsController.jsx` 和 `MediaConfigModalBase.tsx`。因此，第 10 节决策 3 是 P0-4 客户定价前端开工前的阻塞性设计输入，必须二选一：

1. 客户只看 alias 聚合成本：从客户 bundle 和客户 API 中彻底删除逐模型价目与 join；真实逐模型价目仅由 admin-only API 返回。
2. 客户保留逐模型价格：先定义稳定、中性且不可直接反推出真实 model ID 的 `public_sku`，让客户 catalog 和客户价目统一以 `public_sku` 关联；`public_sku -> real provider/model` 映射只保留在后端。

不能让客户侧继续用真实 model ID 作 join，也不能仅把真实表从静态常量搬到客户可调用的动态 API。alias 若不能唯一表示价格档位，则必须使用独立 `public_sku`，不能强行复用 Max/Flash。

##### 发布要求

- 前后端 alias-only 契约同批发布，USER 不保留真实字段兼容窗口。
- build 后扫描所有 JS/CSS/HTML。
- 确认不发布 source map。
- 删除旧 dist chunk。
- 清理或等待 CDN/browser cache 失效。
- 逐个验证历史敏感 chunk URL 已不可访问，不能只验证新首页引用。

### 7.5 内部文件与客户文件分层（Phase 2B / P0-3）

#### 文件分类原则

默认 deny，只有明确公共类型才能下载：

- public media：经过 publish/sanitize 的媒体。
- public document：经过公共内容检查的客户文档。
- public state：由 public schema 重建的有限状态。
- internal state/result/log/provider sidecar：不可下载。

#### 立即加入敏感策略的文件

- 所有 `*_execution_state.json`。
- 所有 `*_execution_result.json`。
- `run_state.json`。
- `one_click_movie_state.json`。
- ToolLibrary Output/Interactive state 和 provider polling state。
- stdout/stderr/log/trace/debug 文件。
- 包含内部 prompt package、provider response、model calls 的结果文件。

不能只依赖文件名。建议在 `session_files` 数据中引入明确的 `artifact_class` 和 `publish_state`：

```text
artifact_class = public_media | public_document | public_state | internal_state | internal_log | provider_private
publish_state  = draft | sanitized | published | blocked
```

#### 统一授权判定要求

只增加 DB 字段不构成安全边界。必须新增一个同时支持 DB 行和路径 fallback 的统一判定函数，例如：

```python
authorize_customer_artifact(
    root,
    relative_path,
    row,
    audience,
    operation,  # preview | raw | download | zip | share
)
```

并满足：

- raw、signed download、share download 继续读取 DB artifact 分类。
- `zip_entries()` 必须为每个文件读取对应 DB 行或权威 artifact manifest，不得只按路径重新分类。
- 文件未登记、分类未知或 DB/磁盘状态不一致时 fail-closed。
- zip、单文件下载、预览和 share 对同一个 artifact 得出相同授权结论。
- `artifact_class` / `publish_state` 必须参与 `visible_file_rows()`、`resolve_download()`、`zip_entries()` 和签名链接生成。

#### 统一 artifact 写入边界

下载授权只能阻止已经正确分类的数据，不能修复错误 DB 行。当前 `resolve_download()` 优先采用 `session_files` 行中的 `downloadable`，而 `downloadable`/`visibility` 并非只有一个写入点：除 `tool_sessions/runner.py` 和 `koubo/router.py` 外，`openflow_analysis.py`、`routes/sessions.py`、ToolLibrary `framework_bridge.py`、`tool_sessions/result_sync.py` 以及 repository 通用 upsert 均可能写入或合并该状态。因此，P0-3 不得只修改 `default_file_visibility()`，也不得把“修了两个已知 upsert”作为边界完成。

必须新增唯一的 artifact 注册/更新入口，例如：

```python
register_artifact(
    session_id,
    relative_path,
    artifact_class,
    publish_state,
    requested_visibility,
    requested_downloadable,
)
```

并强制以下不变量：

- `internal_state`、`internal_log`、`provider_private` 或 `blocked/draft` 无论调用方传什么，都不能持久化为 customer-visible 或 `downloadable=1`。
- manifest/caller 提供的宽松值不能覆盖中央分类得出的更严格值；安全属性按“最严格者优先”合并。
- 所有 backend、ToolLibrary 和同步/恢复任务的 upsert 迁移到统一入口，repository 层再次断言不变量，禁止未来调用方旁路。
- 对现有 `session_files` 执行迁移并生成差异报告；只保护新增行不能关闭存量泄露。
- 下载时仍调用 `authorize_customer_artifact()` 二次校验，不能因为写入已统一就移除出口 fail-closed。

以下当前绕过 `SessionFileService` 的直出路由必须迁移到统一授权函数或只允许返回 `published` artifact：

- DanceMimic 目标图、参考视频和 privacy-grid preview。
- Analysis V1 voice catalog 音频。
- clean-image 生成图和参考图 preview。
- 后续扫描发现的其他直接 `FileResponse` / streaming file 路由。

验收时不能只测 `SessionFileService` 单元测试，必须对每一条实际文件路由执行 raw、download、zip、preview、signed 和 share 的一致性测试。

#### raw JSON 兼容边界

`/api/session-tasks/{sid}/raw/{path}` 同时承担媒体预览和前端业务 JSON 读取。AnalysisV1/Storyboard 等模块会读取 `SessionContext/Variables.json`、`SessionOutput/storyboard/srt_storyboard.json` 等文件；现有读取器遇到 403 等非 404 响应会直接抛错。因此，不能用 `SessionOutput/storyboard/*.json` 或整个目录前缀作为 internal 规则，否则会把安全修复变成业务加载故障。

落地分两步：

1. P0 兼容补丁可对已知 execution/result/run state 使用精确文件名或受控模式拒绝，同时列出必须继续工作的业务 JSON 正向清单。
2. 长期以 `artifact_class`、`publish_state` 和 public DTO 为权威；只有明确登记并经过公共 schema 构造的 JSON 才能下载，未知 JSON 默认 internal。精确文件名 denylist 只能作为紧急止血，不能成为最终安全边界。

`SessionContext/Variables.json` 等当前业务依赖文件也必须重新审查字段，不能因为前端正在读取就整体认定为公开；如同时包含内部变量，应改为单独的 public DTO。HTTP 验收必须同时证明内部 state/result 返回 403、已批准的业务 JSON 返回 200 且前端模块可以完成加载。

### 7.6 建立统一媒体发布边界（Phase 4 / P0-5）

新增唯一公共发布函数，例如：

```python
publish_customer_media(
    source_path,
    target_path,
    media_kind,
    public_filename,
    provenance,
)
```

该函数负责：

- 图片身份元数据清洗。
- 视频/音频 metadata、chapter、C2PA/JUMBF 清洗。
- 中性文件名生成。
- hash/完整性验证。
- 写入 public artifact classification。
- 失败时 fail-closed，不发布原文件。
- 内部 provenance 写入 provider_private，而非公共 sidecar。

所有客户可下载路径必须从该 publish 函数产生，包括：

- asset-library。
- clean-image。
- digital-human。
- ToolLibrary Working→Output。
- Output→SessionOutput/storyboard/Working。
- composer/promote/copy/history。
- talking-head、DanceMimic、Analysis V1、VideoOnlyPlan。

执行器内部可以保留原始文件，但必须位于不可下载的 internal 区域；客户看到的必须是独立 sanitized copy。

### 7.7 扩展存量迁移（Phase 5 / P1-1）

存量清洗分开执行：

1. 元数据清洗。
2. 品牌文件名重命名及引用更新。
3. 内部 state/result/log 文件重新分类。
4. 必要时生成新的 public state，而非直接修改内部原始记录。

B4 扫描范围应从固定五个目录扩展为：

> 所有当前可被 raw/files.zip/signed/share download 解析到的媒体文件。

执行纪律：

- 首次只 dry-run。
- 输出按 session、目录、媒体类型、风险类型汇总。
- write 前做 DB、manifest 和媒体快照。
- 先灰度少量 session。
- 校验播放、缩略图、hash、manifest、DB path 和历史引用。
- 数字人重命名单独执行和回滚。

### 7.8 访问控制修复（Phase 1B / P0-2）

- 非 admin 请求 `audience=debug` 返回 403，普通和 stream 路由一致。
- `request_role()` 未识别角色时回退 USER。
- `policy_mode()` 只接受 `alias` / `hide`；缺 surface、缺 mode、未知 mode、非法配置一律 fail-closed，禁止 fall through 到 raw catalog 或 admin resolver。
- 外部 policy 在启动时校验所有已注册 customer model surface；alias 必须有合法 options，hide 必须有合法 defaults。校验失败应阻止启动或使对应 USER surface 返回通用 503，不得继续返回真实数据。
- 运行时对非 admin 再做兜底：未知 policy 的 catalog 返回空/隐藏结果，真实模型选择返回 403；不能仅依赖启动校验。
- `/api/ocrebuild/*` 立即加入 USER 拒绝边界：若功能已退休，则注销路由或对 USER 返回 404/410/403；若最终保留为客户功能，在重新开放前必须删除本地 raw catalog/resolver，接入 `request_role()`、显式 `SURFACE_*`、`mask_prompt_models_for_role()` 和 `resolve_prompt_model_for_role()`。
- ocrebuild 的 catalog、task detail/create 响应以及所有调用本地 `resolve_model()` 的生成、refine、run 路径必须一并处理；不能只修 GET detail。
- admin-only path 的异常仍返回安全、通用的 401/403，不透传内部详情。
- 关闭或限制根路径 docs/openapi，避免未来隧道路由变化后暴露 schema 默认值。

### 7.9 前端错误处理（Phase 2A / P0-1）

前端通过唯一兼容适配器解析完整 payload，并最终只根据公共 code/message 展示字符串：

```javascript
showError(publicErrorMessage(payload))
```

适配器在迁移期可以读取结构化 `error/public_error` 和安全的旧 `detail/message/error`，但不得把未知对象 `JSON.stringify` 后展示；无法识别时统一返回本地通用文案。

禁止：

- 直接展示 `response.text()`。
- 直接展示未知 `payload.detail`。
- 对 provider 请求直接展示 `err.message`。
- `JSON.stringify(payload)` 后展示整个错误对象。
- 在前端维护真实 provider/model denylist。

客户本地校验错误可以保留具体字段提示，但远端/工具/provider 错误必须使用公共 code。

### 7.10 CI 和安全测试（跨阶段 gate）

#### 静态检查

- 禁止 `HTTPException(detail=str(exc))`。
- 禁止公共 handler 拼接 `exc.read()`、`response.text`、上游 body。
- 禁止公共 state/event 写入 `str(exc)`、stdout、stderr。
- 禁止前端静态源码出现真实价目、真实 model ID、alias-to-real 映射字段。
- 新增客户 model catalog/selection 路由必须声明 `SURFACE_*` 并调用统一 mask/resolve；出现本地 `serialize_prompt_models()` / raw `resolve_model()` 时 CI fail。
- `OPENCREW_USER_MODEL_POLICY_PATH` 缺 surface、缺 mode、未知 mode、alias options 非法或 hide defaults 非法时，契约测试必须证明 USER fail-closed。
- P0-0 必须证明 `hasFlashModelMatch()` 定义和两个调用分支同时消失；对 `.jsx` 执行 ESLint `no-undef`，不能以 `tsc -b` 或 Vite build 代替。
- 现有 frontend lint CI job 必须实际执行并作为合并 gate；手工部署也必须运行 lint，不能假定 build 会检查 `.jsx` 未定义变量。
- 价目/时长常量必须保留原 named export 并中性化为类型兼容的 `[]` / `{}`，契约测试验证所有现有 import 仍可解析。
- Phase 1A-2 禁止新增按真实 model ID/provider substring 推断默认选择、alias 或 capability 的前端分支；允许公开 alias 和服务端 catalog capability。实际 build 扫描必须覆盖本阶段基线 token，不能只扫描变更文件。
- P0-4 开工前新增 `typecheck:modelconfig`，确认 `tsc --listFilesOnly` 实际列出 `ModelConfig/frontend/src` 下的 TS/TSX；将命令接入 CI required checks。仅有 Vite build 或 frontend JS/JSX lint 不得判定 ModelConfig 检查通过。
- 如为 ModelConfig 增加 lint，CI 必须打印实际匹配文件或采用可审计的明确 glob，防止配置存在但 TSX 零文件命中的假绿。

#### CI gate 现状与自动化目标

截至本审核稿最后更新日，`.github/workflows/ci-gate.yml` 自动运行 frontend lint、G1d contract、backend contract tests、frontend build 和 Phase 1A-2 定向 bundle contract，没有运行任何 `test:e2e:*`。`frontend/package.json` 当前定义了 14 个 `test:e2e:*` 脚本，但“脚本存在”不等于“CI 已覆盖”。

- P0-0 发布前的 `ModelPresetCards` 渲染 smoke 当前标记为**人工发布 gate**，结果必须记录；不得在审核或验收报告中写成自动 CI gate。
- Phase 1A-2 的定向 bundle token 扫描已是自动 CI gate；ModelConfig/voice guide 的真实管理弹窗交互 smoke 仍是**人工发布 gate**。在独立 typecheck 和专项渲染测试接入 CI 前，frontend build 和动态 chunk import 只证明可打包/加载，不证明所有 TS/TSX 运行分支正确。
- `shell-nav-smoke.mjs` 当前不渲染 `ModelPresetCards`，且依赖运行中的前后端、Playwright 和 admin/user 凭据；即使接入 CI，也只能证明其实际导航覆盖，不能替代预设卡片专项测试。
- 自动化目标应优先增加无外部服务依赖的组件/契约测试，以 mock catalog 覆盖 Max/Flash 匹配、空 catalog 和卡片渲染；如选择 e2e，则必须在 CI 中启动可重复的前后端 fixture，打开实际预设卡片对话框并捕获 `pageerror`。
- 只有专项测试已经在 PR 上实际运行并设为 required check，才可以把该 smoke 从“人工发布 gate”升级为“自动合并 gate”。

#### build 产物检查

在 `npm run build` 后扫描：

- 所有 `.js`、`.css`、`.html`。
- source map。
- 动态 import chunk。
- 旧 dist 残留。

#### 异常契约矩阵

对每个 provider adapter 注入以下异常：

- 裸 provider 名称。
- 完整 model ID。
- endpoint/domain。
- 上游 JSON body。
- provider request ID。
- quota/safety/content policy 文案。
- Unicode、空格、连字符、大小写变体。

分别验证：

- HTTP JSON 4xx/5xx。
- validation error。
- plain text。
- SSE JSON、非 JSON、跨 chunk、CRLF、comment/event/id。
- background state/event。
- raw/file/download 路由错误。
- response headers。
- frontend 最终显示文本。
- 新旧前端错误解析：字符串 `error`、结构化 `public_error`、`detail/message` 兼容输入均只能得到公共字符串，不显示对象或整段 JSON。
- 历史 execution state、backend↔ToolLibrary 状态往返、断点续跑和 `sensitive_output` 自愈；禁止把仅“任务仍能失败”误当成状态迁移通过。

#### ocrebuild 与 policy 回归

- USER 调用 `/api/ocrebuild/*`：退休方案必须稳定拒绝；客户方案必须只返回 alias，并拒绝任何真实 provider/model selection。
- 覆盖 task create/detail、prompt generation、refine、run 等每一个当前调用本地 resolver 的路径。
- 验证 C0 置空不被当成 alias 成功：catalog item 不允许出现“provider/model 全空但仍可选”的半损坏对象。
- 模拟新增未知 surface、外部 policy 漏 key 和 mode typo，catalog 不得原样返回，resolver 不得采用 admin/raw 分支。
- 直接单测 `request_role()` 的缺失/未知角色回退 USER；同时验证正常 Request 和 auth-disabled 中间件显式 ADMIN 行为不变。

#### 文件与媒体测试

- state/result/run JSON 不可 raw/zip/share 下载。
- DB 标记为 internal/blocked 的同一文件，在 raw、signed、share 和 files.zip 中都不可获得。
- `zip_entries()` 不得忽略 DB `artifact_class` / `publish_state`。
- 使用真实 DB row 验证中央写入不变量：internal/blocked artifact 即使调用方请求 `downloadable=1`，落库后仍不可下载；覆盖所有 backend、ToolLibrary、manifest sync 和恢复写入路径。
- 对存量迁移后的 DB 行执行真实 HTTP 请求，而不只测试 `classify()`/`authorize_customer_artifact()`；raw、signed、share、files.zip、preview 对同一文件必须得出一致结果。
- raw 兼容矩阵同时验证已知内部 state/result 为 403、`srt_storyboard.json` 等批准的公共业务 JSON 为 200，并完成 AnalysisV1/Storyboard 页面加载 smoke。
- DanceMimic preview、voice catalog、clean-image preview 等直出路由必须执行统一 artifact 授权。
- public media 扫描无 EXIF/XMP/C2PA/JUMBF/provider strings。
- 文件名、zip entry、Content-Disposition 中无系统生成的 provider/model。
- 所有新增媒体目录自动纳入 publish boundary 测试。

#### live smoke

在测试环境使用普通用户 session：

- 主动制造每类 provider failure。
- 扫描真实客户响应、SSE、任务状态、事件、下载文件。
- 匿名扫描首页及全部 chunk。
- admin 回归验证运营功能仍可使用。

## 8. 实施计划

`P0-0`、`P0-1` 等编号表示安全事项和优先级，不等于发布先后顺序。实际实施与发布必须使用以下 `Phase` 标签；同一紧急时间窗口也不代表可以合并提交、部署单元或回滚边界。

### 8.1 实施阶段与发布边界

| 实施阶段 | 对应事项 | 正向范围 | 明确排除 | 发布与回滚边界 |
|---|---|---|---|---|
| **Phase 1A：相对安全的静态止血** | P0-0 | 价目/时长 export 中性化为 `[]`/`{}`；删除 alias-to-real、Flash 真实特判及两个调用、`*_real` 死代码；lint/build、人工预设卡片 smoke、bundle 扫描、旧 chunk/cache 清理 | 不改 API/model policy/角色/ocrebuild；不改 error/state schema；不改 DB/download/raw/zip；不做 admin 动态 API；不做媒体 publish boundary | 必须独立提交、独立构建、独立发布。回滚不得恢复真实常量或旧泄露 chunk；失败时使用已脱敏构建修复前滚，或回到已验证的脱敏 artifact |
| **Phase 1A-2：受控的 bundle 残余静态止血** | P0-0B | 删除前端真实默认 run/TTS model、真实 model rename、按真名能力特判和 team/account URL；改用公开 preset、active/configured catalog、capability 元数据及中性 alias | 不改后端/API/policy/role/ocrebuild；不改 error/state/ToolLibrary/DB/download/media；不完成 provider 品牌/docs 的全量通用化 | 独立于 1A/1B 提交、构建和发布；允许明确的 admin 显示/兼容降级，回滚不得恢复真名，只能以前滚补 metadata/alias/config 修复 |
| **Phase 1A-3：审核品牌止血与 bundle gate 收紧** | P0-0C | 中性化本次四个 provider 品牌的客户文案；删除 voice prefix 推断和 admin 静态凭证特判；凭证字段改由 admin-only API schema 驱动；共享泄露 manifest、扫描器 fail-closed 自测和精确 Phase 3 债务预算 | 不处理其余已登记 Phase 3 品牌/docs/model 债；不改 customer model policy/ocrebuild、异常/state/artifact/download/media publish | 独立补丁发布；API schema 为 additive。回滚前端时不得恢复品牌分支；后端 schema 可暂时保留，优先以前滚修复通用渲染器 |
| **Phase 1B：紧急访问控制止血** | P0-2 | `request_role()` fail-closed；debug 403；`/api/ocrebuild/*` 对 USER 阻断或注销；policy 缺失/未知 mode fail-closed；文件路由错误响应清洗 | 不迁移公共异常结构；不改 execution state/error 键；不做 artifact DB 迁移或媒体复制 | 可与 1A/1A-2 同一紧急窗口，但必须不同提交/部署单元和回滚开关。回滚不得重新开放 raw catalog/resolver；必要时维持路由关闭 |
| **Phase 2A：公共异常边界** | P0-1 | 前端兼容适配器、`PublicError`/`ProviderFailure`、HTTP/SSE/validation/header 出口安全构造、provider adapter 包装 | 不机械改持久化 `error` 键；不改 artifact 可下载性 | 前后端兼容字段同批发布；保留 public-safe 旧字段作为回滚窗口，禁止回退到 `str(exc)` |
| **Phase 2B：异步状态与文件边界** | P0-3 | 状态 schema version/双读写、内部 failure code、自愈和续跑迁移；统一 artifact 写入、存量 DB 迁移、raw/zip/signed/share/preview 授权与 public JSON DTO | 不进行完整 admin alias-only 动态化；不纳入未经独立评估的媒体 publish copy | 状态协议、ToolLibrary 和 backend 同批；artifact 按类型/路由灰度。允许回滚读取器但不得重新公开 internal artifact |
| **Phase 3：alias-only 与管理能力恢复** | P0-4 | 先建立 ModelConfig typecheck/TSX 检查 gate；确定聚合成本或 `public_sku`；完成客户 alias-only、admin 鉴权动态 API 和管理能力恢复 | 不在决策 3 或 ModelConfig gate 未完成时开工；不把真实 join key 临时下发客户 | 前后端整批发布和回滚；客户 bundle 始终保持零真实目录，回滚不得恢复静态真名 |
| **Phase 4：统一媒体发布边界** | P0-5 | ToolLibrary/Working/Output 等媒体 sanitize/publish、metadata 和公共副本边界 | 不与前述阶段共享未经验证的回滚假设；存量重命名另行执行 | 完成独立运行时/性能评审后单独灰度；保留 internal 原件，回滚 public copy，不直接公开原件 |
| **Phase 5：存量迁移与治理收尾** | P1-1、P1-2、P2 | 存量媒体和文件重新分类、扩大自动扫描、docs/openapi/CORS/side-channel 与治理 | 不用于替代任何未完成的 P0 边界 | dry-run、快照、小批灰度和引用回滚；自动化 gate 按其保护的前序 Phase 提前交付 |

#### Phase 1A 完成定义

Phase 1A 是本文唯一明确标记为“相对安全”的第一实施阶段。只有同时满足以下条件才能关闭：

- 变更集仅包含上表正向范围；出现后端/API/policy/state/DB/download/media 变更时必须移出 1A 单独审核。
- 两份价目文件保持原 named export，值为类型兼容的空数组/空对象。
- `hasFlashModelMatch()` 定义和两个调用分支原子删除；真实 provider/model、alias-to-real 和真实定价在全部构建 chunk 零命中。
- 现有 frontend lint/build 自动 checks 通过，Max/Flash 卡片人工渲染 smoke 留痕；专项测试接入 CI 后才升级为自动 gate。
- admin 已知降级与批准范围一致；受 policy 管理的 USER alias 匹配无回归。
- 发布物和回滚物均不包含已知真实常量；禁止用恢复泄露数据的方式回滚功能降级。

#### Phase 1A-2 完成定义

Phase 1A-2 仍是低耦合前端止血，但比 1A 多触及默认选择和能力分支，不能把它描述为“纯常量删除”或“零功能风险”。只有同时满足以下条件才能关闭：

- 变更集不包含后端/API/policy/role/state/ToolLibrary/DB/download/media 改动；所有默认选择均来自公开 alias、当前 catalog/config 或服务端既有默认。
- 本次列出的真实 run/TTS/image/video model ID、model-family prefix 和 team/account locator 在实际新构建的全部 chunk 零命中。
- USER Max/Flash、公开 voice alias 和请求 payload 契约无回归；admin 的中性 alias、旧 rename 停止和 capability fail-closed 降级已人工确认。
- frontend lint/build 和自动 `test:model-bundle-contract` 通过；ModelConfig 动态 chunk 和本节列出的功能 smoke 无 `pageerror`。在独立 ModelConfig typecheck/专项交互测试落地前，审核记录必须明确真实管理弹窗交互仍是人工 gate。
- 发布物及回滚物均保持脱敏；发现 metadata 缺口时以前滚补 catalog capability 或 alias 解决，不把真名判断恢复到客户端。

#### Phase 1A-3 完成定义

- 四个本次审核品牌在匿名构建的全部 JavaScript chunk 中为 0；普通用户相关页面和 catalog 响应同样为 0。
- 客户 voice 选择只使用不透明 voice alias；前端不存在按真实 voice model prefix 推断 provider 的分支。
- 多字段凭证完全由 admin-only API 的通用 schema 驱动；前端源码不存在该 provider 的静态特判，真实管理员只读渲染 smoke 通过且不保存凭证。
- 共享 manifest 覆盖后端 media catalog 的全部 provider；扫描器自测证明 model family、provider 品牌、映射字段、超预算和 source map 会 fail-closed。
- 临时许可逐 pattern 具备正数上限、owner 和 expiry，实际命中不得高于上限。存在任何许可时只能标记“带 Phase 3 债务通过”，不能声明全局零泄露。
- 测试环境自动检查和真实 USER/admin smoke 留痕；生产旧 chunk、CDN/browser cache 未核验前不得关闭。

### 8.2 安全事项清单

| 安全事项 | 内容 | 优先级 | 主要风险 | 回滚 |
|---|---|---:|---|---|
| P0-0 | 保留空 export；原子删除两文件中的真实定价/时长值、alias-to-real 映射、Flash 函数及两个调用分支和 `*_real` 死代码；自动 lint/build + 当前人工专项渲染 smoke 后重建并清旧 chunk | 第一（同窗） | 漏删任一 Flash 调用会在渲染期确定性崩溃；现有 CI 没有专项渲染测试；不能误把 ocrebuild 当作 alias-safe | 暂时隐藏 admin 展示；不得回退到重新公开真名 |
| P0-0B | 清理实际 bundle 中分散的真实默认 model、rename/能力判断、模型名派生 alias 和 team/account locator；改用 alias/catalog/config/capability | 第一（紧随 1A） | admin 旧 rename 与无 metadata 的能力推断降级；ModelConfig 尚无自动 typecheck；TTS 空配置必须保持显式失败而非猜真名 | 前滚补 catalog metadata/配置；不得恢复客户端真名 fallback |
| P0-0C | 清理新发现的四个 provider 品牌、voice prefix 推断和 admin 凭证特判；共享完整 bundle policy 并锁定 Phase 3 债务预算 | 第一（紧随 1A-2） | 通用 credential schema 的前端运行时、策略过宽误伤 free-text、历史债被错误当成零泄露 | additive schema 保留；前滚修复渲染器/策略分层，不恢复客户端品牌常量 |
| P0-1 | 公共异常契约、前端兼容适配器、未知异常通用化、provider adapter 包装 | 最高 | 前端依赖旧 detail/error；结构硬切换会静默降级为对象/原始 JSON 展示 | 保留安全构造的旧字段和旧内部日志，前后端整批回滚 |
| P0-2 | debug 403、角色默认 USER、policy mode fail-closed、立即阻断或迁移 `/api/ocrebuild/*`、下载路径错误清洗 | 第一（同窗） | 退休 ocrebuild 的旧 USER 调用失败；错误 policy 会阻止启动/返回 503 | ocrebuild 仅可在接入 alias policy 后重新开放；不得回退 raw |
| P0-3 | state/result/log 内外分层；保持状态 schema 兼容；统一 artifact 写入、存量迁移及 raw/zip/signed/share/preview 授权 | 最高 | 跨进程 error 改名会静默破坏自愈/续跑；错误 DB 行可覆盖路径分类；粗粒度 JSON 规则会误封业务读取 | 双读/双写、有限 public state API、按 artifact/路由灰度 |
| P0-4 | 先建立 ModelConfig 独立 typecheck/可选 TSX lint CI gate，再实施完整 alias-only 契约、admin 动态配置 API、public SKU/聚合定价契约和管理端能力恢复 | 最高 | 当前 ModelConfig TS/TSX 不在 lint/typecheck 范围；决策 3 未定、前后端 join/契约不一致 | 检查 gate 独立先行；业务改造前后端整批回滚 |
| P0-5 | ToolLibrary/Working/Output 统一媒体 publish boundary | 最高 | 尚未完成独立运行时风险核验；fail-closed 会新增“生成成功但发布失败”，另有媒体复制和性能回归 | 保留 internal 原件，回滚 public copy；独立评审后发布 |
| P1-1 | 扩展存量媒体清洗与数字人重命名 | 高 | 存量断链 | 快照、灰度、引用回滚 |
| P1-2 | CI 异常矩阵、bundle scan、文件策略测试 | 高 | CI 时间增加 | 分 job 并行 |
| P2 | docs/openapi、CORS、side-channel 和治理文档收尾 | 中 | 兼容性 | 环境 flag |

### 8.3 推荐顺序

1. **Phase 1A、Phase 1A-2 与 Phase 1B 可以进入同一个紧急日历窗口，但必须是独立提交、独立部署单元和独立回滚边界**。顺序为 1A → 1A-2 → 1B：1A 关闭匿名 bundle 整表，1A-2 清除重建后发现的分散静态真名，1B 关闭已登录 USER 的 raw catalog/访问控制旁路；任何一方失败不得拖入另一方的变更，也不得通过恢复真实数据回滚。
2. Phase 2A 和 Phase 2B 可并行开发，但公共错误兼容与持久化状态迁移分别验收；禁止用 HTTP 契约测试替代自愈/续跑/文件下载测试。
3. Phase 3 先建立覆盖 `ModelConfig/frontend/src` 的独立 typecheck CI gate 和 admin 动态化渲染测试，再完成长期 alias-only 契约和 admin 动态化；如保留 ocrebuild 客户功能，也在此之前完成统一 policy 迁移。
4. Phase 4 完成独立风险核验后单独灰度。Phase 5 的自动化部分按需要前置为各 Phase 的 gate，存量 write 和治理收尾仍独立审批。

P0-0/P0-0B 和 P0-4 必须分开管理：前两者是可独立发布的紧急暴露面收缩，后者是需要前后端同批发布和完整回滚预案的架构工程。不能因为 P0-4 较大而继续保留当前匿名静态泄露。

## 9. 验收标准

只有全部满足以下条件，才能把“模型名称脱敏”标记为完成：

### 匿名前端（Phase 1A / Phase 1A-2 / Phase 1A-3 / Phase 3）

- 公网首页引用的所有 chunk 扫描零命中真实 provider/model、真实价目和映射字段。
- 动态 chunk 同样零命中。
- 无 source map。
- 已知历史敏感 chunk URL 不再返回旧内容。
- 所有受 policy 管理的普通用户 catalog 只含 Max/Flash 或其他公开 alias，预设卡片仍能自动匹配、选择并提交 alias。
- P0-0 阶段 admin 预设卡片和价目展示的临时降级与批准范围一致；P0-4 后由鉴权 API 恢复。
- 若客户只看聚合成本，客户响应和 bundle 不存在逐模型价目；若保留逐模型价格，catalog 与价目只以 `public_sku` 关联。
- P0-0 价目/时长 named export 仍存在且值为类型兼容的空数组/空对象；所有现有 import 可解析，核心设置页空态正常。
- `hasFlashModelMatch()` 定义和两个调用分支全部删除，frontend 自动 lint/build 和当前人工 Max/Flash 卡片渲染 smoke 均通过；人工结果进入发布记录。只删除函数导致的残留调用不得进入发布包。
- `.github/workflows/ci-gate.yml` 的 frontend lint 在该提交上实际运行并通过，且发布记录证明手工构建未绕过 lint。
- Phase 1A-2 列出的真实默认 model、rename/能力特判、model-family prefix 和 team/account locator 在实际构建 chunk 零命中，且 `test:model-bundle-contract` 在 build 后自动通过；Analysis V1、Koubo TTS、视频 capability、MediaConfig alias 和 voice guide 的成功/空配置 smoke 无 `pageerror`。
- Phase 1A-2 不以恢复真实字符串维持 admin 兼容；缺 capability 时按文档 fail-closed，默认 alias 保持中性且可由管理员编辑。
- Phase 1A-3 的四个审核品牌在构建 chunk、真实 USER 文案和 USER catalog 响应中均为 0；admin 多凭证字段由鉴权 API schema 驱动，前端无品牌特判。
- `test:model-bundle-policy-contract` 主动证明完整词表、预算上限和 source-map 拒绝 fail-closed；`test:model-bundle-contract` 对共享 manifest 中每条命中执行精确预算。任何临时许可仍存在时不得把本项解释为最终“所有 chunk 零命中”。
- P0-4 开工前，`typecheck:modelconfig` 已覆盖并实际列出 ModelConfig TS/TSX、接入 CI required checks；admin 动态化渲染测试覆盖成功、失败和空数据路径。

### USER API（Phase 1B / Phase 2A / Phase 3）

- 正常 JSON、所有 4xx/5xx、validation、plain text、SSE 中无真实 provider/model/domain。
- 生成请求只包含 alias/public voice ID。
- 未识别角色按 USER。
- `audience=debug` 返回 403。
- `/api/ocrebuild/*` 对 USER 全前缀拒绝；如果批准保留为客户功能，则 create/detail/catalog/generate/refine/run 全部只接受和返回 alias，不存在本地 raw resolver。
- 外部 model policy 缺 surface、缺 mode、mode typo 或 schema 非法时，USER catalog fail-closed，真实 provider/model selection 被拒绝。
- C0 清洗后的 catalog 不存在空 provider/model 的可选项；出口置空不能被当作 alias 契约通过。

### 异步与文件（Phase 2B）

- 客户 state 只含公共 error code/message。
- 兼容 `error` 字段只含 public-safe 字符串；新 `public_error`、旧 error 消费和 API mapper 同时通过契约测试，前端不显示对象或原始 JSON。
- 历史状态加载、backend/ToolLibrary 跨进程状态往返、断点续跑和 sensitive-output 自愈无回归；若删除旧键，已证明 schema version 和双读/双写迁移完成。
- internal state/result/log/provider sidecar 不可 raw、zip、signed 或 share 下载。
- 同一 internal artifact 在 raw、signed、share、files.zip、preview 等全部出口得到一致拒绝结果。
- `files.zip` 使用 DB/权威 artifact 分类，不以路径敏感词重新推翻 DB 决策。
- 全部 artifact DB 写入路径使用统一注册入口和 repository 不变量；存量 `session_files` 已迁移，internal/blocked 行不存在 `downloadable=1` 的有效覆盖。
- 已批准的公共业务 JSON 继续返回 200 并通过 AnalysisV1/Storyboard 页面 smoke；内部 state/result 返回 403，未知 JSON 默认 internal。
- DanceMimic、voice catalog、clean-image 等直接文件路由已接入统一授权边界。
- Content-Disposition 和系统生成文件名中无 provider/model。

### 媒体（Phase 4 / Phase 5）

- 所有新生成 public media 经过统一 publish boundary。
- 图片、视频、音频抽样和自动扫描无身份 metadata/provider string。
- 存量迁移报告中的目标文件已处理或明确列为接受风险。
- SynthID 风险已有书面决策。

### admin 与运营（Phase 3）

- admin 仍能通过鉴权 API 查看真实配置和内部诊断。
- 客户 bundle 不因 admin 功能重新包含真实目录。
- 连接测试、生成、计量和错误排查流程可用。

### 自动化（跨阶段 gate）

- bundle scan、异常矩阵、文件策略、媒体扫描和 authenticated live smoke 全绿。
- 新路由、新 provider、新媒体目录缺少公共契约时 CI fail-closed。
- 验收报告逐项标明“自动 CI”“人工发布 gate”或“尚未自动化”，不得把 `frontend/package.json` 中存在但 CI 未调用的 e2e 脚本计为自动覆盖。
- `ModelPresetCards` 专项渲染测试未接入 CI 前，必须保留人工发布 gate；接入后需保存 PR job 证据，方可升级为自动合并 gate。

## 10. 待审核决策

1. 是否继续接受 Max 系别名的可逆性风险，还是迁移为无供应商/版本线索的 SKU。
2. SynthID 是否接受；高敏客户是否改用其他供应商或明确披露。
3. 客户是否需要查看单模型价格；这是 P0-4 客户定价前端的阻塞性决策。默认建议仅显示 alias 聚合成本并删除逐模型 join；若必须保留，则先批准 `public_sku` 方案，禁止使用真实 model ID 作客户侧 join。
4. admin 是否允许在原业务响应中看到内部错误；建议只通过独立诊断接口查看。
5. 内部错误详情保留多久，谁可以访问，是否进入审计日志。
6. 客户自由文本中主动输入的 provider 名称是否保留；建议保留客户原文，但系统生成的异常和状态必须中性化。
7. 存量迁移的生产时间窗、快照保留期和抽样 session 范围。
8. 是否将输出 codec、能力、价格和延迟等指纹纳入本阶段安全目标。

`/api/ocrebuild/*` 不再列为安全定性待决项：其当前 USER 可达 raw catalog/resolver 已按 P0 处理。业主仍需决定最终下线还是迁移为客户 alias 功能，但在该产品决策完成前，安全默认动作是拒绝 USER 访问。

## 11. 环境与证据说明

本文件中的运行数据来自 2026-07-13 macmini-4 测试环境和 `opencrew.instmarket.com.au` 当前测试入口：

- 只进行了只读扫描和迁移脚本 dry-run。
- 没有执行存量清洗、文件重命名、数据库修改或部署。
- 没有使用普通用户 cookie 完成全量已登录 live smoke。
- 没有核验 macmini-1 生产环境是否运行相同代码和构建。
- 数量型结果是安全审计候选或词法命中，实施前需通过结构化 dry-run 报告再次确认。

因此，“测试环境已修复”不能自动推导为“生产暴露面已关闭”。生产发布前必须新增强制 gate：

- 在 macmini-1 核对实际部署 commit、进程启动时间和前端 build hash。
- 从生产公网入口重新抓取首页、静态 chunk、动态 chunk 和响应 headers。
- 用本次已知敏感 chunk URL 验证旧资源已不可访问，而不是只检查新首页引用。
- 核对 Cloudflare/CDN cache rule、缓存命中和清理结果。
- 在生产以普通用户执行已登录异常、SSE、状态、下载和 `/api/ocrebuild/*` 全前缀访问 smoke。
- 核对生产实际使用的 `OPENCREW_USER_MODEL_POLICY_PATH` 内容和 hash，并注入缺 surface/mode 的负向测试确认 fail-closed。
- 保存生产验收报告和构建 hash，才能关闭 P0-0/P0-0B/P0-2/P0-4。
- Phase 1A-2 还必须保存其 bundle token 基线、零命中扫描结果及人工动态 chunk smoke 记录，才能在生产标记关闭。

## 12. 审核建议

本方案建议按以下结论审核：

1. 同意新增 P0-0，立即删除匿名 bundle 中的真实价目、alias-to-real 映射和真实字段，不等待完整 admin 动态化。
2. 同意把异常泄露从原 P1 提升为 P0。
3. 同意采用“公共错误契约 + 内部错误记录”替代原始异常 scrub。
4. 同意 state/result/log 默认 internal，公共状态由 allowlist DTO 重建。
5. 同意 artifact 授权必须统一覆盖 raw、zip、signed、share、preview 和所有直接 FileResponse 路由。
6. 同意前端静态资源零真实名称，admin 数据也改为鉴权后动态加载。
7. 同意以“客户可下载”为边界重构媒体清洗，而不是继续逐 sink 补丁。
8. 同意把 bundle、异常、内部文件和媒体扫描纳入强制 CI/发布验收。
9. 同意将 `/api/ocrebuild/*` 当前状态定为 P0，在下线或完整接入 role-aware policy 前立即拒绝 USER 访问。
10. 同意 model policy 缺失/未知 mode 必须 fail-closed，并在启动校验和运行时各设一道防线。
11. 同意在 macmini-1 生产验证完成前，不关闭 P0-0/P0-0B/P0-2/P0-4，也不对外宣称完成。
12. 同意 P0-0 保留 named export 并中性化为空值，`hasFlashModelMatch()` 定义与两个调用分支必须原子删除；lint/build 是现有自动 CI checks（是否为 required 仍须由仓库设置确认），专项渲染 smoke 在接入 CI 前是必须留痕的人工发布 gate。
13. 同意后台状态先保持旧 `error` 的 public-safe 兼容，采用 schema version 和双读/双写迁移；不得单边改名破坏 ToolLibrary、断点续跑或自愈逻辑。
14. 同意 artifact 安全边界同时覆盖全部 DB 写入源、存量行迁移和全部下载出口；精确文件名 denylist 只作为 P0 止血，长期使用 public allowlist/DTO。
15. 同意 P0-5 在独立运行时风险核验完成前不进入生产发布批次。
16. 同意 P0-4 修改 ModelConfig 前，先建立覆盖其 TS/TSX 的独立 typecheck required check；现有 frontend lint/build 不得替代该前置。
17. 同意不得把未被 workflow 调用的 e2e 脚本计为自动覆盖；现有 shell-nav smoke 不能替代 `ModelPresetCards` 专项渲染测试。
18. 同意采用 Phase 1A→1A-2→1B→2A/2B→3→4→5 的发布标记；P0 编号只表示安全事项，不表示发布批次。Phase 1A、1A-2 与 1B 即使同窗也必须独立提交、部署和回滚。
19. 同意新增 Phase 1A-2 / P0-0B，紧随 1A 清理实际重建后发现的真实默认值、能力特判、model rename 和 team/account locator；其回滚不得恢复客户端真名，且 ModelConfig smoke 在自动覆盖建立前必须按人工 gate 留痕。

在以上 P0 完成并通过普通用户端到端验收前，不建议对外宣称模型名称已经完成脱敏。

## 13. 审核意见处理结论

### 13.1 第一轮审核

| 审核意见 | 核验结论 | 文档处理 |
|---|---|---|
| free-text key 只走 8 条品牌正则，完整 model ID 也会穿透 | 成立；已实测 `message/content/text/summary/description/title` 原样保留 `gemini-2.5-flash` | 已上调 4.2 严重性并加入字段分支表 |
| P0-3 只增加 DB artifact 字段会漏 files.zip | 成立；`resolve_download()` 读取 DB 覆盖值，`zip_entries()` 只按路径重算 | 已在 4.3、7.5、8、9 节要求统一授权函数和 zip DB 判定 |
| 多个 FileResponse 路由绕过 SessionFileService | 成立；DanceMimic、voice catalog、clean-image 均有确认实例 | 已加入 7.5 迁移清单和路由级验收 |
| ModelPresetCards 直接下发 Max/Flash alias-to-real 映射 | 成立；风险高于普通价目字符串 | 已在 4.1 单独列为致命项 |
| ModelConfig 价目只在 admin 页面使用 | 不成立；`manualChunks` 形成匿名 `model-config` chunk，当前首页可下载 | 已在 4.1 明确打包机制 |
| P0-4 应拆成 P0-0 和完整 P0-4 | 成立 | 已重排第 8 节，P0-0 为第一实施项 |
| 删除常量风险接近零 | 经后续核验需进一步收窄；受 policy 管理的 USER alias 匹配不依赖真实值，ocrebuild 则是独立的既有 P0 | 已由 13.2/13.3 的细化结论和 7.4 精确降级范围取代 |
| 当前结论不能自动外推到生产 | 成立 | 已在第 11 节新增 macmini-1 强制发布 gate |

### 13.2 P0-0/P0-4 补充审核

| 审核意见 | 核验结论 | 文档处理 |
|---|---|---|
| P0-0 删除真实预设值不会让普通用户降级 | 仅对已接入 `model_policy` 的 alias surface 成立；作为普通用户全局断言不成立，`/api/ocrebuild/*` 是反例 | 已在后续 13.3 撤回全局前提；7.4、8、9 节改为按 surface 验收，并把 ocrebuild 交由 P0-2 处置 |
| `hasFlashModelMatch()` 单独残留 `deepseek` | 成立；该函数及两个调用分支不属于常量数组 | 已加入 4.1 证据和 P0-0 独立删除项 |
| `provider_label_real` / `model_label_real` 是死代码 | 成立；当前 catalog item 和 `_masked_item()` 均不注入，前端只有两处无效读取 | 已标注为零功能影响，并要求契约测试防止未来引入 |
| `pricing.ts` 还有 `VIDEO_MIN_DURATION_SECONDS` | 成立；该文件包含 `MEDIA_PRICE_POINTS` 和第三张真实映射表，不包含 `LIPSYNC_PRICE_COMPARISON` | 已按文件精确改写 P0-0 删除清单 |
| P0-4 客户定价需要先决定公开 join 键 | 成立；两个消费点均用 `availableModels.has(item.model)` 按真实 model ID join | 已将决策 3 设为阻塞输入：聚合成本直接删除逐模型 join，保留逐模型价格则先定义 `public_sku` |

### 13.3 ocrebuild 与 policy fail-open 补充审核

| 审核意见 | 核验结论 | 文档处理 |
|---|---|---|
| “普通用户预设卡片 surface 均采用 alias”不成立 | 成立；`/api/ocrebuild/*` 对 USER 可达，catalog、默认模型和 resolver 均为本地 raw 实现，未引用任何 model policy API | 已更正 4.1，并在 4.6 单列为 P0 catalog 与执行旁路 |
| 退休导航足以缓解 ocrebuild | 不成立；前端 hash 已退休，但 56 条后端路由仍注册，且该前缀不在 admin-only 列表 | 已要求 P0-2 立即全前缀拒绝 USER；是否下线或迁移只影响后续方案 |
| C0 能兜住 ocrebuild catalog | 不成立；命中 denylist 的 pair 被置空，未覆盖的真实 catalog 原样保留，且 raw resolver 不受 C0 限制 | 已加入 4.6 直接验证结果、空卡片禁止项和路由级验收 |
| policy 缺 surface/mode 时 fail-open | 成立；缺 surface、缺 mode、未知 mode 三种实测均原样返回 catalog，并允许真实选择走 admin/raw resolver | 已补充 4.5；7.8 要求启动校验与运行时 fail-closed，7.10 加入负向契约测试 |

### 13.4 运行时崩溃与静默失效补充审核

| 审核意见 | 核验结论 | 文档处理 |
|---|---|---|
| 删除 `hasFlashModelMatch()` 但残留调用会导致必崩 | 成立；函数有两个调用点，正常 Flash 匹配会在 `createMemo` 渲染期进入该分支并触发 `ReferenceError`。这是“不完整实施必崩”，不是正确实施后的固有风险 | 7.4 改为定义与两个调用分支原子删除；7.10、8、9 节加入 lint、零残留和渲染 smoke gate |
| CI 抓不到 `ModelPresetCards.jsx` 未定义变量 | 不成立；该文件位于现有 frontend JS/JSX lint 范围，ESLint `no-undef` 可以拦截。但本地 `npm run build` 不等价于 lint，且该结论不能外推到 ModelConfig TS/TSX | 文档明确现有 CI 的准确作用域，要求 lint job 为 required/实际执行，并把 lint 纳入手工发布 gate |
| 现有 lint/typecheck 覆盖 ModelConfig | 不成立；lint 只匹配 `frontend/src/**/*.{js,jsx}` 并忽略 TS/TSX，`frontend/tsconfig.json` 也只 include `frontend/src`。`tsc --listFilesOnly` 实测没有任何 `ModelConfig/frontend/src` 文件 | 7.4 新增 P0-4 检查覆盖前置；7.10、8、9、12 节要求独立 `typecheck:modelconfig` 进入 CI required checks，TSX lint 需正确 parser/config |
| 文档中的 `ModelPresetCards` 渲染 smoke 已是自动 CI gate | 不成立；当前 CI 不调用任何 `test:e2e:*`，package 虽有 14 个 e2e 脚本但均未接入。现有 shell-nav smoke 也没有打开预设卡片对话框 | 7.4、7.10、8、9、12 节明确其当前为人工发布 gate；只有专项组件测试或实际渲染 e2e 接入 CI 后才能升级为自动合并 gate |
| 价目常量应置空而不是删除 export | 成立；现有消费者对空数组/空对象安全降级，保留 export 可以维持 import 契约；删 export 可能在 build 或浏览器 import 阶段响亮失败 | 7.4 按文件和类型明确 `[]`/`{}`；7.10、9 节加入 import/空态验收 |
| `error`→`public_error` 会打断自愈和跨进程契约 | 成立，且影响范围大于列出的写入点；除顶层写入外还有异常分支、嵌套 state、previous-state 读取和前端 API 消费。即使保留键名，若把原文直接替换为通用文本，依赖 `sensitive_output` 文本的自愈仍会失效 | 重写 7.2：旧 `error` 仅作 public-safe 兼容，增加结构化 `public_error`/内部 failure code，要求 schema version、双读/双写、同批迁移和自愈/续跑测试 |
| 只改 `session_files.py` 或两个 upsert 会让下载修复静默失效 | 成立；DB `downloadable` 优先于路径 fallback，实际还有 openflow、sessions、framework bridge、result sync、repository 等多处写入/合并来源 | 7.5 新增统一 artifact 写入边界、repository 不变量和存量迁移；7.10、9 节要求用真实 DB 行和 HTTP 端点验收 |
| raw 端点按目录封禁会破坏正常业务 JSON | 成立；多个业务模块读取 `Variables.json`、`srt_storyboard.json` 等，非 404 的 403 会抛错并中断加载 | 7.5 增加 raw JSON 兼容边界：P0 精确止血并保留正向清单，长期迁移到 public allowlist/DTO；验收同时覆盖内部 403 和业务 200 |
| 精确文件名黑名单是最终安全边界 | 不成立；它能降低 P0 误伤，却会漏掉未来新增或改名的内部 JSON | 明确只允许作为紧急兼容补丁，长期未知 JSON 默认 internal，公共数据由 artifact class/public DTO 明确发布 |
| `request_role()` fallback 从 ADMIN 改 USER 会造成运行时回归 | 当前风险极低；现有 handler 均传真实 Request，auth 关闭时中间件也显式赋 ADMIN。仍不应称为数学意义上的零风险 | 保留 7.8 fail-closed 修改，要求小型角色默认值单元测试，不将其列为阶段阻断风险 |
| P0-1 新错误结构完全不会崩溃 | 方向上多数表现为可读性静默降级，但不能作为无条件断言；未知对象可能进入 UI 文本或异常构造器 | 7.1、7.9、7.10 增加统一兼容适配器和最终展示契约测试 |
| P0-5 publish boundary 可与前述 P0 同风险结论直接发布 | 尚无充分证据；fail-closed 会新增“生成成功但发布失败”，还涉及媒体复制、性能和引用一致性 | 8、12 节标记为独立评审和发布 gate，未完成运行时核验前不进入生产批次 |

### 13.5 Phase 1A-2 实施发现

| 实施发现 | 核验结论 | 文档与代码处理 |
|---|---|---|
| Phase 1A 清空整表后，实际 bundle 仍含分散真名 | 成立；来源包括 Analysis V1 默认 run model、媒体 model rename、按真实 model 字符串判断 capability、TTS fallback 和 voice guide 文案 | 新增 Phase 1A-2/P0-0B；逐项改为 preset/catalog/config/capability 驱动，并把定向实际构建扫描接入 frontend-build CI job |
| admin 自动 alias 从真实 model label 派生会继续泄露系列名 | 成立；主应用和 ModelConfig 各有一套 prefix replacement | 两处统一改为中性 kind label + 序号；保留管理员手工编辑能力 |
| 删除 TTS 真实 fallback 会导致普通用户直接崩溃 | 不成立；USER 只强制公开 voice，已保存 selection/public voice alias 可由后端解析；admin 缺完整配置已有显式校验。仍需成功/空配置 smoke 防止静默功能回归 | 浏览器不再猜供应商/model；默认选择来自 active/configured catalog，Talking Head 的最终缺省交给既有后端处理 |
| 视频 capability 可以继续按真实 model 名兼容 | 不应继续；这会把 alias-to-real 知识重新固化在匿名 bundle | 公共 alias 分支保留，raw 真名分支删除；catalog/alias 的 `reference_mode` 与引用限制成为通用来源，无 metadata 时按已声明 admin 降级 fail-closed |
| 通用控制台链接可以包含固定 team/account locator | 不成立；固定 locator 无需鉴权即可从 bundle 读取，且不属于功能所需 | 删除固定 team/account path，保留控制台根入口；provider 品牌/docs 的完整通用化仍留 Phase 3 |

### 13.6 Phase 1A-3 审核发现

| 审核意见 | 核验结论 | 文档与代码处理 |
|---|---|---|
| 四个真实 provider 品牌仍在匿名 bundle，且部分直接显示给 USER | 成立；修复前新构建分别命中 `heygen` 36、`chanjing` 18、`minimax` 10、`cosyvoice` 6，人物口播还直接显示供应商文案 | USER 文案中性化；删除 voice model prefix 推断；管理端凭证特判迁到 admin-only schema；修复后四项在全部 JS chunk 为 0 |
| 原 bundle contract 只是本次回滚 pin，不是完整 guard | 成立；缺少多组 provider、model family 和映射字段，重新加入其他价目/目录仍可能绿灯 | 建立共享版本化 manifest；覆盖后端 media catalog provider；添加精确 Phase 3 债务预算、未知许可失败、source map 无条件失败和扫描器自测，并接入 CI |
| 完整 provider 词表可直接复用于 free-text 出口 | 不成立；会误删用户正常提示词中的常用品牌或普通词，本次测试实际捕获 “Google style” 被替换 | 同一 manifest 分离完整 `provider_brands` 与高置信 `egress_provider_brands`；bundle 严扫，free-text 保持更窄规则，结构化 provider/model 字段仍由完整 deny 规则处理 |
| UI 中存在 provider prefix 映射证明 USER API 仍返回真名 | 前半成立、后半不成立；静态映射本身会泄露，但真实 USER clone-list 实测已返回不透明 voice alias/掩码字段 | 删除静态映射；真实 USER 页面和 API 双重 smoke，既验证 UI 契约也验证响应无四个品牌 |
| 只把四项加进手写 denylist 就能长期收敛 | 不成立；接入新 provider 时会静默漏更新 | Python 契约和 Node gate 均从共享 manifest 读取，且 Node gate反向检查后端 catalog provider 覆盖；新增 catalog provider 未登记时 CI 失败 |
