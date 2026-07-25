# OpenCrew Phase 0 Secret Store / LLM Gateway 代码审核

## 处理记录（2026-05-28）

本轮已处理审核中 P0/P1 阻塞项：

- Secret store：默认改为 `${OPENCREW_DATA_DIR}/secrets.enc`，Keychain 改为显式 opt-in；新增线程锁与 `fcntl.flock` 文件锁；修复 Keychain 空 stdout 覆盖旧主密钥；`get()` 解密失败时返回 fallback；`set_many()` 批量迁移减少重复读写。
- 启动迁移：legacy `api_key_ciphertext` 迁移失败不再阻断 backend 启动，失败会写入事件流。
- Auth：未配置密码时除 health/auth 初始化接口外不再开放 `/api/*`；首次 setup 仅允许 loopback 或 `OPENCREW_SETUP_TOKEN`；password body 加长度限制与 4KB `Content-Length` 防护；login/setup 密码处理一致。
- Public API：`/api/session-share/*` 与 `/api/sessions/im/send` 放回 public 路径，share smoke 改为无 app cookie 验证。
- CORS：CORS 中间件改为 auth 外层；移除 `* + credentials`，改为显式 origins + nip.io/local regex。
- Proxy：backend provider policy 增加 `sync/grok`；ToolLibrary 代理策略委托 backend 权威实现；direct provider 会清理 sticky proxy；Gemini env-key 分支也应用 mihomo；OpenClip URL direct 判断改为精确后缀 allowlist。

已验证：

- `backend/scripts/opencrew_phase0_stack_smoke.py --json` 通过。
- `backend/scripts/opencrew_p0_stack_smoke.py --json` 通过，含无 app cookie 的 session-share、Caddy Basic Auth。
- 后端 contract tests 30/30 通过。
- `frontend npm run build` 通过（仅 Vite chunk size warning）。
- `git diff --check` 通过。
- 大 auth body 返回 413。

- 审核范围：工作树未提交改动（`git diff HEAD`），相较 `main` 头 commit `5b3830f`。
- 审核方法：9 路并行 finder（5 路 correctness、3 路 cleanup、1 路 altitude）+ 1-vote 验证。
- 总计提出 15 条可执行发现；按严重度排序，最严重者先列。Correctness 一律优先于 cleanup/altitude。
- 审核基础事实：业务模型为转售 Mac mini 一体机、单租户、隐藏批发 LLM key + 统一对客 key，Phase 0 交付方式为 LAN-only，密钥落地为「加密文件 + 设备密钥包装」（user memory 记录）。

## 发现分布

| 集群 | 数量 | 受影响位置 |
| --- | --- | --- |
| LocalSecretStore（启动/解密/写入） | 5 | `backend/opcrew_backend/services/local_secrets.py`、`context.py` |
| 认证 / 中间件 / 会话 | 5 | `backend/opcrew_backend/routes/auth.py`、`app.py`、`routes/sessions.py` |
| Mihomo / 代理策略 / 跨域工具脚本 | 4 | `services/provider_resolver.py`、`OpenClip/.../rebuild_router.py`、`ToolLibrary/opencrew_runtime_secrets.py`、`ToolLibrary/Rebuild_V1/03_02_ShotPlan_GTTSVoiceBuilder.py` |
| `/api/session-share/*` 旁路 | 1 | `backend/opcrew_backend/routes/sessions.py` |

---

## 详细发现（Top 15，按严重度排序）

### 1. 钥匙串读取「成功但 stdout 为空」会清掉旧主密钥，导致无法解密

- 文件：`backend/opcrew_backend/services/local_secrets.py:122`
- 触发条件：任何让 keychain item 存在但内容为空的情形——上一轮 `-U` 中断、MDM 抹除、手动 `security add -U … -w ''`、备份还原。
- 现象：
  - `find-generic-password -w` 返回 `returncode == 0` 且 `stdout == ""`，`if … existing.stdout.strip()` 判定失败 → 走 fallback → 调 `Fernet.generate_key()` + `add-generic-password -U` 直接覆盖原 keychain 条目。
  - 下次 `_read_payload` 解密 `/Library/Application Support/OpenCrew/secrets.enc` 抛 `InvalidToken`，所有批发 provider key 永久丢失，没有回滚路径。
- 修复建议：仅在 `returncode != 0` 时生成新密钥；`returncode == 0 && empty stdout` 必须当作不可恢复错误抛出，禁止 `-U` 覆盖。

### 2. `_ensure_fernet` / `_load_or_create_*_key_file` 没有锁

- 文件：`backend/opcrew_backend/services/local_secrets.py:89`、`107`、`146`
- 触发条件：FastAPI 线程池并发首次访问；或 launchd 同时拉起 backend + ModelConfig backend 两个进程。
- 现象：两路都见不到密钥 → 各自 `Fernet.generate_key()` → 各自 `security add -U`（或 `path.write_text`）。最后一个写盘者获胜，前一个进程已经用「孤儿密钥」加密了部分 payload。重启后 keychain/文件返回的是后者的密钥 → 解密失败 → bricked。
- 修复建议：
  1. 进程内：`threading.Lock` 包裹 `_ensure_fernet`。
  2. 进程间：用 `fcntl.flock` 在 `dev_key_file` 上加文件锁，或者把首次创建放到一个显式 init 步骤（migration / 安装脚本），运行期只读取。

### 3. `migrate_legacy_provider_keys` 在 `AppContext.__init__` 里裸跑，任一失败都导致 backend 无法启动

- 文件：`backend/opcrew_backend/context.py:56`、`102`
- 现象：
  - 每次启动无条件 SELECT 两张表 + 对每行调 `secret_store.set()`，没有 `try/except`。
  - `cryptography` 未装、`/Library/Application Support/OpenCrew` 不可写、keychain 锁住、`secrets.enc` 损坏中任意一种都会让 `create_app()` 抛异常，uvicorn 直接退出。
  - 失败时操作员连 `/api/health`、`/api/auth/status` 都打不开，无任何自救入口。
- 修复建议：
  1. 把迁移搬到 `db/migrations.py` 作为一次性数据迁移，并写入 `schema_migrations`。
  2. 若坚持留在 `AppContext.__init__`，必须 `try/except` + `event(level="error", ...)`，让进程仍能启动，把失败暴露在事件流里。

### 4. macOS 默认走 Keychain 与「headless launchd」Phase 0 定位冲突，且默认路径需要管理员权限

- 文件：`backend/opcrew_backend/services/local_secrets.py:20`、`101`
- 现象：
  - `_load_master_key` 在 Darwin 下默认走 `_load_or_create_keychain_key`，而 user memory 明确写着 Phase 0 是「加密文件 + 设备密钥」，原因正是 launchd 无 GUI keychain 会话。
  - `DEFAULT_SECRET_STORE_PATH = /Library/Application Support/OpenCrew/secrets.enc`，非 root 用户无法 `mkdir` 该目录；首次 `_write_payload` 抛 `PermissionError`。
  - 即便 backend 通过 `launchctl asuser` 跑起来，keychain ACL 也会让 ToolLibrary CLI 子进程拿到不同的主密钥派生路径，导致两边对同一 `secrets.enc` 解密结果不一致。
- 修复建议：默认走「加密文件 + 设备密钥（如 `/etc/machine-id` + 操作员口令派生）」路径；Keychain 仅作为可选 opt-in。`DEFAULT_SECRET_STORE_PATH` 改成 `~/Library/Application Support/OpenCrew/secrets.enc` 或 `${OPENCREW_DATA_DIR}/secrets.enc`。

### 5. `_write_payload` 无文件锁，多写者并发会静默丢失刚保存的密钥

- 文件：`backend/opcrew_backend/services/local_secrets.py:172`、`179`
- 现象：
  - 每次 `set()` 做完整的 `_read_payload` → 修改 → `_write_payload` 流程，`tmp_path.replace(self.path)` 为 last-writer-wins。
  - UI 保存 DashScope 新 key 的同时，ToolLibrary worker 调 `store_secret_value('mihomo_subscription_url', …)` 读到的是旧 payload，写回后会把 UI 刚保存的 DashScope 新值覆盖回旧值；前端显示「保存成功」。
  - Starlette 把同步 handler 派到线程池，单进程内也会并发跑 `set()`。
- 修复建议：`_read_payload` + `_write_payload` 之间加 `fcntl.flock` 跨进程锁，再叠一层 `threading.Lock` 跨线程锁；或者一次性接受 `set_many(dict)` 批量更新，减少 read-modify-write 次数。

### 6. 认证「bootstrap window」可被 LAN 内攻击者抢占管理员

- 文件：`backend/opcrew_backend/routes/auth.py:147`、`183`
- 现象：
  - 中间件第 183 行：`if not auth_configured(ctx): return await call_next(request)`，意思是「没设密码时所有 `/api/*` 全开」。
  - `setup` 第 151 行：只在 `auth_configured` 已为真且 cookie 无效时返回 409。
  - 一台出厂机在 owner 第一次登录前，攻击者只要先 `POST /api/auth/setup` 提交自己的密码，就拿到管理员 session cookie；owner 之后再点登录会得到 `409 Application password is already configured.`，且 owner 没有任何「reset 密码」入口。
  - 在该窗口内攻击者也可以任意改 `/api/setup/media-models/*`、`/api/setup/mihomo/*`。
- 修复建议：
  - 安装/出厂流程预置 `OPENCREW_APP_PASSWORD_HASH`（或在首次 launchd 启动前由 install 脚本写入 setting），让 `auth_configured` 一开始就是 True。
  - 至少应当对 `/api/auth/setup` 的来源做约束（仅允许 127.0.0.1 / Unix socket / 安装期一次性 token）。

### 7. `LocalSecretStore.get()` 让 `InvalidToken` 直接冒到 500，绕过 legacy ciphertext 回退

- 文件：`backend/opcrew_backend/services/local_secrets.py:61`、`167`
- 现象：
  - `get(ref, default="")` 签名暗示「找不到/出错时返回 default」。
  - 实际 `_read_payload` 在 `Fernet.decrypt` 抛 `InvalidToken` 时立刻 `raise LocalSecretStoreError(...) from exc`；`get()` 不 catch。
  - `media_model_config.load_stored_key` / `asr_config.load_stored_key` 设计上是「先 `secret_store.get`，没读到再回退到 `api_key_ciphertext` 列」。一旦 `secret_store.get` 抛错，根本走不到 legacy 回退分支——而 legacy 回退正是迁移期赖以「不中断」的关键。
- 修复建议：在 `get()` 里 `try/except LocalSecretStoreError`，记录事件后返回 `default`；同时让 `load_stored_key` 显式区分「找不到」与「解密失败」两种语义。

### 8. `ToolLibrary` 的 `apply_provider_proxy` 是 sticky 的：abroad 之后跟 CN 调用，CN 走 mihomo

- 文件：`ToolLibrary/opencrew_runtime_secrets.py:66`、`72`
- 现象：
  - `mihomo` 分支会 `os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"`，`direct` 分支什么都不做。
  - 一旦同一进程内跑过任意 abroad provider（openai/xai/gemini），后续 Qwen TTS / Wan video / DashScope 全部继承代理环境变量。
  - 后果：CN provider 通过 mihomo（境外出口）打 `dashscope.aliyuncs.com`，要么超时、要么被 dashscope 当作异常境外流量限流；可观测面看不到任何告警。
- 修复建议：
  1. `direct` 分支显式 `os.environ.pop("HTTP_PROXY", None)` / `"HTTPS_PROXY"` / 小写版本。
  2. 更彻底的做法：删掉 env 变更，改为「按调用注入 transport」（urllib `ProxyHandler` / requests `proxies=`）。

### 9. `proxy_policy_for_url` 用 `in` 子串匹配 hostname，既能被子域名欺骗，又会把 CN provider 在非 aliyun CDN 的回包打到 mihomo

- 文件：`OpenClip/backend/openclip_backend/rebuild_router.py:1416`
- 现象：
  - `if "aliyuncs.com" in host or "aliyun.com" in host: return "direct"`。
  - 攻击/幻觉 URL 形如 `api.openai.com.evil-aliyun.com` → 被判为 direct，绕过 mihomo 隔离。
  - Minimax / 其它 CN provider 返回的 OSS 直链常常托管在 `cos.ap-shanghai.myqcloud.com`、`cdn.minimaxi.com` 等不含 aliyun 的域名 → 被判为 mihomo，100MB+ 视频下载经境外代理回传，慢且容易 TLS 失败，浪费代理流量。
- 修复建议：
  1. 改为 `host.endswith(".aliyuncs.com") or host.endswith(".aliyun.com")` 等精确后缀匹配，并维护一份 CN CDN allowlist。
  2. 更好：彻底放弃「按 URL 推断」，由调用方把 provider proxy_policy 透传进 `open_provider_request`。

### 10. 新的认证中间件挡住 `/api/session-share/{token}/*` 和 `/api/sessions/im/send`

- 文件：`backend/opcrew_backend/routes/auth.py:181`、`backend/opcrew_backend/routes/sessions.py:650`、`748-771`
- 现象：
  - 中间件白名单只放行 `/api/health` 与 `/api/auth/*`。
  - `/api/session-share/{token}`、`/events`、`/files`、`/files/{file_id}` 五条本来是外部协作者凭 share token 访问的。设置 `OPENCREW_APP_PASSWORD` 后，分享页 `/session/share/{token}` 仍能加载，但内嵌 fetch 全部 401，外部分享流彻底失效。
  - `/api/sessions/im/send`（`/Task` ChatOps 入口、Simulator）同样 401。
- 修复建议：
  1. 在中间件白名单里增加 `/api/session-share/` 前缀和 IM 入口；后两者通过自身 token / 来源校验保护。
  2. 长期方案：每个 router 以依赖（`Depends`）方式显式声明 public / private，避免中央白名单。

### 11. 预认证接口接受无界 body + 240k PBKDF2，LAN 内一键打爆

- 文件：`backend/opcrew_backend/routes/auth.py:23`、`147`、`159`
- 现象：
  - `PasswordPayload.password: str` 无 `max_length`，FastAPI body 也无大小限制。
  - 接到 100MB 的 `password` 字段后，`verify_password`/`hash_password` 会跑 240k 次 PBKDF2 over 100MB。
  - 50 并发即可让单进程 backend 内存 + CPU 双爆，OOM 之前所有合法请求 timeout。
- 修复建议：`PasswordPayload.password: str = Field(..., min_length=1, max_length=256)`，加 `slowapi` 之类的 per-IP 限速，必要时在 reverse proxy 上限制 `client_max_body_size`。

### 12. `03_02_ShotPlan_GTTSVoiceBuilder.py` 走 env-key 捷径时跳过了 `apply_provider_proxy('gemini')`

- 文件：`ToolLibrary/Rebuild_V1/03_02_ShotPlan_GTTSVoiceBuilder.py:304`
- 现象：当操作员设置 `OPENCREW_TTS_API_KEY=AIza…` 临时跑 CLI 时，`load_google_tts_key` 立即返回 env_key，没经过 `apply_provider_proxy('gemini')`。后续 `urllib.request.urlopen("https://generativelanguage.googleapis.com/…")` 没有 `HTTP_PROXY`，直连境外，CN 机器必然超时。
- 修复建议：env-key 分支也必须 `apply_provider_proxy('gemini')`；并在类似的 03_02_QTTSVoiceBuilder.py 中保持一致（虽然 Qwen 当前是 CN provider，写法应统一以防未来改类目）。

### 13. CORS 配置 `allow_origins=["*"]` + `allow_credentials=True` 不合规，且中间件注册顺序让 auth 包在 CORS 外层

- 文件：`backend/opcrew_backend/app.py:37`、`45`
- 现象：
  - Starlette `add_middleware` 与 `app.middleware("http")` 都把中间件插入 `user_middleware[0]`；后注册的反而成为最外层。所以执行顺序是 `auth → CORS → routes`。
  - auth 中间件返回的 401 不会经过 CORSMiddleware，浏览器看到的是「没有 Access-Control-Allow-Origin 的 401」。
  - 即便顺序修好，`Allow-Origin: *` + `Allow-Credentials: true` 也会被浏览器规范拒绝；要让 cookie 跨域生效必须显式列出 origin。
  - 当前 same-origin（Caddy 反代）部署掩盖了问题；将来发布 nip.io / subdomain 的方案一上线就炸。
- 修复建议：把 CORS 改为最后注册（成为最外层），允许的 origin 显式列出。

### 14. `provider_resolver.ABROAD_PROVIDERS` 与 ToolLibrary 副本都漏掉 `sync`，且 `xai` vs `grok` 命名也不一致

- 文件：`backend/opcrew_backend/services/provider_resolver.py:9`、`ToolLibrary/opencrew_runtime_secrets.py:59`、`OpenClip/.../rebuild_router.py:1416`、`588`
- 现象：
  - 三处独立维护「哪些 provider 走 mihomo」的判断（按 provider 名 / 按 URL host / 又一份按 provider 名），互相不同步：ToolLibrary 副本里有 `grok`、backend 没有；三处都没把 `sync`（sync.so lipsync）当 abroad。
  - 后果之一：ToolLibrary 跑 `12_01_Shot_PlanD_TTSDrivenLipSyncVideoGenerate.py` 时 `apply_provider_proxy('sync')` 是 no-op，`requests.post("https://api.sync.so/v2/generate", …)` 从 CN 直连，TLS reset / 超时。
  - 后果之二：`record_local_usage(provider="sync", …)` 通过 `resolve_endpoint("sync", …)` 算出来的 `proxy_policy` 是 `direct`，但 backend `open_provider_request` 实际走了 mihomo。`local_usage_log` 的合规审计字段与实际不一致。
- 修复建议：合并为一个 `proxy_policy_for_provider` 的单一权威实现（含 `sync`），三处共用；删除 URL-host 启发式。

### 15. `/api/auth/login` 不 `.strip()` 密码，但 `/api/auth/setup` 会 strip

- 文件：`backend/opcrew_backend/routes/auth.py:147-156`、`158-165`
- 现象：
  - `setup`：`password = payload.password.strip()` 之后 hash。
  - `login`：直接 `verify_password(payload.password, encoded)`，不 strip。
  - 用 curl/kubectl 一类工具用同一个含尾部空格/换行的 JSON 体跑 setup → login，第二步必 401。
  - 前端 `submitAuth` 调 `authPassword().trim()` 才掩盖了问题。
- 修复建议：选一种语义：要么两端都 strip，要么两端都不 strip（推荐后者，强制操作员对密码格式负责）。

---

## 二级建议（cleanup / altitude，未占用 15 条名额）

> 这些不直接造成 bug，但是后续维护代价高。除非与功能开发同步落地，否则迟早会演化成上一节的 bug。

1. **三套并行的「provider proxy 政策」**：`provider_resolver.ABROAD_PROVIDERS`（按 provider 名）、`rebuild_router.proxy_policy_for_url`（按 URL host）、`opencrew_runtime_secrets.proxy_policy_for_provider`（再一份按 provider 名）。建议合并到 `provider_resolver` 单点，删掉另外两份。
2. **legacy ciphertext → secret_store 的迁移逻辑被复制了 5 处**：`context.migrate_legacy_provider_keys`（eager）、`media_model_config.load_stored_key`、`asr_config.load_stored_key`、`rebuild_router.provider_key_from_row`、`opencrew_runtime_secrets.resolve_secret_value`。建议把迁移做成一次性数据 migration 写入 `schema_migrations`，然后删掉所有 lazy 回退；`api_key_ciphertext` 列也可以接着 drop。
3. **`routes/auth.py` 重新发明了 `itsdangerous` / `SessionMiddleware`**：base64(JSON)+HMAC、自管 `session_secret` 设置项。FastAPI 生态已经自带；改造前先评估能否直接用 `SessionMiddleware`。
4. **`services/local_usage.py` 用裸 SQL + `CAST(:units_json AS JSONB)`**：`db/schema.py:141` 已经把 `local_usage_log` 表声明了 `JSON` 列，用 `local_usage_log.insert().values(...)` 即可方言中立、避免列名两处定义漂移。
5. **`migrate_legacy_provider_keys` 每行做一次完整的 read-encrypt-write-fsync**：N 行就是 N 次完整文件改写，全部在同一个 DB 事务里。改为先 `_read_payload` 一次、内存里改完、再 `_write_payload` 一次。
6. **`LocalSecretStore.get/has` 每次都解密整张表**：单请求里查 5 个 `api_key_ref` 就解密 5 次。加内存缓存（set/delete 时失效），可把 `has` 退化到 O(1)。
7. **`auth.py` 的 `configured_password_hash` + `session_secret` 每次请求最多 4 次 SELECT**：可在 `ctx` 上缓存解析结果，登录/setup 时显式失效。
8. **`mihomo.py` 的 enable/save 完全不操作 mihomo 进程**：UI 给操作员的是「Enabled · localhost proxy reachable」这种看起来像开关的呈现，但保存只改一个 setting + 一个 secret，既不启动也不重载。要么补齐进程托管（与 launchd 集成），要么把 UI 改成「检测到 / 未检测到」纯只读。
9. **`auth` 中间件硬编码白名单**：新增任何 public 接口都得改这个集合，且新接口的所有者多半不在 `routes/auth.py` 里。改为路由侧 `Depends`/marker 自描述。
10. **`backend/opcrew_backend/routes/{media_model_config,asr_config}.py` 现已是 `from … import *` 的薄壳**，但 `rebuild_router.py` 还保留 `try: from opcrew_model_config … except ModuleNotFoundError: from opcrew_backend.routes …` 的双路径 import。两条路径并存意味着将来如果两边模块身份不同（contract test、部分 wheel），`isinstance` 会失败。选一条留下，另一条删干净。
11. **前端 auth gate 放在 `<ModelConfigProvider>` 里面**：provider 的 `onMount` effect 在 auth 通过前就执行，可能预热出错误的状态。要么 App 在 `authReady` 之前根本不渲染 provider 树，要么抽 `<AuthGate>` 单独包裹。
12. **`loadInitialData` 7 个独立请求顺序 `await`**：互不依赖，应 `Promise.allSettled` 并行，减少登陆后白屏时间。
13. **两个 smoke 脚本 (`opencrew_p0_stack_smoke.py` / `opencrew_phase0_stack_smoke.py`)** 覆盖不同维度但共享 ~80 行模板（`HttpResult`、`request`、`login_cookie`、`require`）。抽 `_smoke_common.py`。

---

## 修复优先级建议

| 阶段 | 修复项 | 依据 |
| --- | --- | --- |
| P0（出货前必须） | 发现 1、2、3、4、5、6、7、10、11 | 关乎数据丢失、启动失败、未授权接管、外部分享回归 |
| P1（上线 nip.io / 公网测试前） | 发现 8、9、12、13、14 | 关乎代理路由正确性与跨域 cookie |
| P2（功能补完） | 发现 15 + 二级建议 1-6 | 提升一致性、消除重复维护 |
| P3（产品打磨） | 二级建议 7-13 | 性能、可维护性 |

## 验证证据

- 9 路 finder agent 全部跑完；每条 finding 至少有一路提出，关键项（1、2、5、7、8、9、10、13）由 2 路以上独立提出，verifier 走 1-vote 全部为 CONFIRMED / PLAUSIBLE。
- 与现状 grep 交叉确认：
  - `grep '"/api/session-share' backend/opcrew_backend/routes/sessions.py` 列出 5 条路由，证实发现 10。
  - `frontend/src/lib/api.{js,ts}` 的 `fetch()` 默认 `credentials: 'same-origin'`，证实发现 13 仅在跨域部署触发。
  - `frontend/src/debug/DebugConsole.jsx:113` 的 `EventSource` 没有 `withCredentials`，same-origin 下不影响；列入二级备忘，不占用 15 条。
  - `local_secrets.py` 一字一字核对 `find -w` / `add -U` 分支，确认发现 1 的子句。
- 与既有 user memory 对照：
  - 「Phase 0 key storage 是加密文件 + 设备密钥包装，不是 Keychain（headless launchd）」与现实代码 `_load_master_key` 默认走 Keychain 直接冲突 → 发现 4 是架构层硬伤，不是细节 bug。
  - 「Phase 0 网络是 LAN-only，公网 nip.io 只用于测试」意味着发现 11 的 DoS、发现 6 的 bootstrap window 在「LAN 多人」与「测试期公网」场景下仍然需要修。

## 不再追究的几条「看起来像 bug 但已澄清为安全」

- `provider_urlopen` 在 `proxy_url` 为空时退回 `urllib.request.urlopen`：刻意的兜底，已在 user memory 中默认 `127.0.0.1:7890`，不视作 bug。
- `tmp_path.replace(self.path)` 后的二次 `os.chmod`：保留 0600 权限，行为正确；冗余但不致错。
- `int(payload.get("exp") or 0)` token expiry：缺失 exp 时立即过期，是「安全失败」方向，行为符合预期。
- `provider_key_from_row` 内联在 `build_oc_rebuild_router` 里，调用方读完 `mapping` 后不再依赖 `api_key_ciphertext`，目前并无数据竞争。
