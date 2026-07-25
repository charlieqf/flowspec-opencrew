# OpenCrew Phase 0 Secret Store / LLM Gateway 复审（Round 2）

- 审核范围：第一轮 review 后已合入修复的工作树（`git diff HEAD`，相较 `main` 头 commit `5b3830f`）。
- 审核方法：对照 Round 1 的 15 条发现逐条 verify 修复证据；对修复涉及的新代码路径再过一遍 5 路 finder + 1-vote。
- 结论速览：**Round 1 的 15 条全部得到处理（13 修复 + 2 部分修复）**；**新引入 2 处 P0、1 处 P1**；**3 处 P2 carry-over**。

---

## A. Round 1 发现处理状态

| # | Round 1 标题 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | Keychain 空 stdout 覆盖旧主密钥 | ✅ 已修 | `local_secrets.py:138-142`：`returncode == 0` 且 `key_value` 为空时直接 `raise LocalSecretStoreError("macOS Keychain master key exists but is empty")`，不再走 `add -U`。 |
| 2 | `_ensure_fernet` / dev-key 文件无锁 | ✅ 已修 | `local_secrets.py:50`/`104`/`118`/`167`：进程内 `threading.RLock`，进程间 `fcntl.flock` 包住 keychain 与 dev-key-file 创建。 |
| 3 | `migrate_legacy_provider_keys` 启动失败让 backend 进不去 | ✅ 已修 | `context.py:56-59`：`try/except`，失败写 `error` 事件，进程继续启动。 |
| 4 | macOS 默认走 Keychain + `/Library` 默认路径 | ✅ 已修 | `local_secrets.py:47`：默认路径改为 `${OPENCREW_DATA_DIR}/secrets.enc`；`116`：Keychain 仅在 `OPENCREW_SECRET_STORE_KEYCHAIN=1` 且 Darwin 时启用，否则 `local_key_file`。 |
| 5 | `_write_payload` 无文件锁，多写者丢密钥 | ✅ 已修 | `local_secrets.py:202-214`：`_read_payload`/`_write_payload` 均加 `threading.RLock` + `fcntl.flock`；`set_many` 在同一锁内做 read-modify-write。 |
| 6 | bootstrap window：攻击者抢占 admin | ⚠️ 已修但有新漏洞（见 B-1） | `auth.py:216-217`：未配置密码时除 health/auth-meta 接口外一律 401；`177` 走 `setup_request_allowed`（loopback 或 `OPENCREW_SETUP_TOKEN`）。**但 `request_peer_host` 信任 `X-Forwarded-For`，参见 B-1**。 |
| 7 | `LocalSecretStore.get()` 让 `InvalidToken` 冒到 500 | ✅ 已修 | `local_secrets.py:64-71`：`get` 用 `try/except Exception` 兜底返回 `default`。 |
| 8 | `apply_provider_proxy` sticky env，CN 走 mihomo | ✅ 已修但有过度行为（见 B-3） | `opencrew_runtime_secrets.py:76-79`：direct 分支显式 `pop` 4 个变量。 |
| 9 | `proxy_policy_for_url` 子串匹配 + 漏 CN CDN | ✅ 已修 | `rebuild_router.py:1416-1436`：CN allowlist 改为带前导点的精确后缀（`.aliyuncs.com`、`.myqcloud.com`、`.minimaxi.com` 等），用 `host_matches_suffix`。 |
| 10 | `/api/session-share/*` 与 `/api/sessions/im/send` 被中间件挡掉 | ✅ 已修 | `auth.py:218-219`：显式放行 `path.startswith("/api/session-share/")` 与 `/api/sessions/im/send`；smoke 脚本在 `opencrew_p0_stack_smoke.py:174/196/205` 用不带 cookie 的请求验证 share 路径。 |
| 11 | 预认证接口无 body cap → PBKDF2 DoS | ⚠️ 部分修复（见 B-2） | `auth.py:24` 加 `Field(..., max_length=256)`；`209-213` 在中间件里读 `Content-Length`，>4096 返回 413。**但 chunked transfer-encoding 仍可绕过**。 |
| 12 | `03_02_ShotPlan_GTTSVoiceBuilder.py` env-key 跳过代理 | ✅ 已修 | `03_02_ShotPlan_GTTSVoiceBuilder.py:304`、`03_02_ShotPlan_QTTSVoiceBuilder.py:124`：env-key 分支也调 `apply_provider_proxy`。 |
| 13 | CORS `*+credentials` + 中间件顺序 | ✅ 已修 | `app.py:75-82`：CORS 改为最后一次 `add_middleware` → 成为最外层；`allow_origins` 显式列出 frontend + 18080 + nip.io，`allow_origin_regex` 兜 nip.io/loopback；移除 `*`。 |
| 14 | `provider_resolver.ABROAD_PROVIDERS` 漏 `sync`、命名不一致 | ✅ 已修 | `provider_resolver.py:9`：`ABROAD_PROVIDERS = {openai, xai, grok, gemini, google, sync}`；`22-28`：`normalize_provider` 把 `google→gemini`、`grok→xai`；`49`：`resolve_endpoint` 给 `sync` 加 `base_url`。`opencrew_runtime_secrets.py:64-71` 委托 backend 实现，仅在 import 失败时本地兜底。 |
| 15 | login 不 strip / setup strip 的不一致 | ✅ 已修 | `auth.py:71-72,172,187`：抽出 `normalized_password`，setup 与 login 都先调用。 |

---

## B. 新发现 / 修复留下的新 bug

### B-1（P0）`setup_request_allowed` 信任 `X-Forwarded-For`，bootstrap 防护可被 header 欺骗

- 文件：`backend/opcrew_backend/routes/auth.py:143-160`
- 现象：
  - `request_peer_host` 优先返回 `request.headers["x-forwarded-for"]` 的第 0 段，仅当 header 缺失时回落到 `request.client.host`。
  - 这意味着 LAN 上任意客户端发 `POST /api/auth/setup` 时只要带上 `X-Forwarded-For: 127.0.0.1`，无论真实来源 IP 是什么，`setup_request_allowed` 都判 True，绕过新加的 loopback / `OPENCREW_SETUP_TOKEN` 闸门，把 Round 1 #6 的「bootstrap 抢占」漏洞 100% 复现。
  - 经过 Caddy 反代时更隐蔽：Caddy 默认会把客户端 IP **追加**到 `X-Forwarded-For`，但代码只取 `split(",", 1)[0]`，等同采信客户端伪造的最前一段；Caddy 既没做 trusted_proxies 配置（参见 `scripts/opencrew_nipio_caddy.sh`），也没显式 strip 掉客户端原始 header。
- 触发示例（任意 LAN 主机能 reach backend 8011 或 Caddy 18081 即可）：
  ```
  curl -H 'X-Forwarded-For: 127.0.0.1' \
       -H 'Content-Type: application/json' \
       -d '{"password":"attacker-pass"}' \
       http://<mac-mini>:8011/api/auth/setup
  ```
- 修复建议：
  1. 默认 **不** 信任 `X-Forwarded-For`；仅当 `request.client.host` 属于一个显式配置的 `OPENCREW_TRUSTED_PROXIES` 列表（Caddy 上游 IP）时才解析 forwarded chain。
  2. 取「最右一段」而不是「最左一段」作为对端 IP（Caddy 是追加，所以最右才是上游真实写入的值）。
  3. 即便如此，bootstrap 攻击面仍较大；建议把 `OPENCREW_SETUP_TOKEN` 改成「未配置密码时强制要求 token」而不是 OR-loopback，安装脚本一次性生成 token 并写入 launchd plist。

### B-2（P1）4KB Content-Length 闸门可被 chunked transfer-encoding 绕过

- 文件：`backend/opcrew_backend/routes/auth.py:209-213`
- 现象：
  - `int(request.headers.get("content-length") or "0")` 在客户端用 `Transfer-Encoding: chunked` 或干脆不发 `Content-Length` 时返回 0，跳过 413 检查。
  - 之后 FastAPI/Starlette 仍会先把整个 body 缓存到内存里，再交给 pydantic 跑 `max_length=256`。攻击者可以 chunked 推任意大小，配合 240k PBKDF2，本来要拦住的 DoS 仍然可用。
- 修复建议：
  1. 必装路径上的 Caddy 配 `request_body { max_size 4KB }`（当前 `scripts/opencrew_nipio_caddy.sh:64-77` 没有这个 directive，建议补上）。
  2. 直连 backend 路径上：写一个 ASGI 中间件，包裹 `receive` 协程，累计 body 字节数超过阈值就返回 413；不要依赖 Content-Length 头。

### B-3（P2）`apply_provider_proxy` direct 分支无条件 `pop` 环境代理，覆盖操作员手工配置

- 文件：`ToolLibrary/opencrew_runtime_secrets.py:76-79`
- 现象：
  - 为修 Round 1 #8 的 sticky 问题，direct 分支现在 unconditional `os.environ.pop(...)` 把 `HTTP_PROXY` / `HTTPS_PROXY` / 小写版全部删掉。
  - 后果：若操作员开 CLI 之前已经在 shell 里 export 了公司代理（mihomo 之外的另一个出口），跑任何一个 CN provider 都会把这个代理删掉，且不会恢复；同一会话后续脚本静默丢代理。Phase 0 现场较少见，但远程调试时容易踩坑。
- 修复建议：
  1. 进程启动时把当前 `HTTP_PROXY`/`HTTPS_PROXY` 快照下来；direct 分支 restore 到快照值（缺失则 pop）而不是无条件 pop。
  2. 更彻底：不再操作 `os.environ`，改成 per-call 注入 transport（urllib `ProxyHandler` / requests `proxies=`），跟 backend 的 `provider_urlopen` 一致。这样 ToolLibrary 与 backend 也只剩一套代理决策。

### B-4（P2）`request_peer_host` 没归一化 IPv6-mapped IPv4 loopback

- 文件：`backend/opcrew_backend/routes/auth.py:147,160`
- 现象：uvicorn 在 dual-stack socket 下偶尔返回 `::ffff:127.0.0.1`，不在 `{"127.0.0.1", "::1", "localhost"}` 集合里，导致即使是真正的本机请求也会因「不是 loopback」而被拒。
- 修复建议：用 `ipaddress.ip_address(host).is_loopback` 判断；或在集合里补 `"::ffff:127.0.0.1"`。

---

## C. Round 1 二级建议（cleanup / altitude）的当前状态

| 项 | 状态 | 备注 |
| --- | --- | --- |
| 三套并行 proxy-policy 机制 | ⚠️ 部分缓解 | ToolLibrary 现已委托 backend `proxy_policy_for_provider`，留下 backend 的 name-based 与 `rebuild_router` 的 host-based 两份；建议下一轮把 host-based 也合并掉。 |
| Legacy ciphertext → secret_store 5 处重复 | ❌ 未处理 | 仍然 5 处；建议改成一次性数据 migration 写 `schema_migrations`，运行期取消 lazy 回退。 |
| `routes/auth.py` 重造 itsdangerous/SessionMiddleware | ❌ 未处理 | 现状可工作，但建议改用 Starlette 的 `SessionMiddleware`+`itsdangerous`，省两段自管代码（HMAC 拼装、`session_secret` 设置项）。 |
| `services/local_usage.py` 裸 SQL+`CAST(:units_json AS JSONB)` | ❌ 未处理 | 建议改用 `db/schema.py:141` 的 SQLAlchemy `Table.insert()`，避免 Postgres dialect 锁定 + 列名两处维护。 |
| `migrate_legacy_provider_keys` 已批量 set_many | ✅ 已修 | `context.py:139` 调用 `set_many`，一次 read-modify-write。 |
| `LocalSecretStore.get/has` 每次解密整张表 | ❌ 未处理 | 高频读路径仍然每次全量解密；建议加内存缓存（set/delete 时 invalidate）。 |
| `auth.py` 每请求最多 4 次 SELECT | ❌ 未处理 | `configured_password_hash` + `session_secret` 仍然每次都查 DB；建议在 `ctx` 上缓存，env/setup/login 时 invalidate。 |
| `opencrew_runtime_secrets._secret_store()` 每次新建实例 | ❌ 未处理 | 现状文件锁能兜并发安全，但 per-call 文件 IO 仍贵；建议 module-level lazy singleton。 |
| `mihomo.py` enable/save 不动 mihomo 进程 | ❌ 未处理 | UI 仍展示「开关」语义，但保存只改 setting + secret，不启动 / 重载。要么补 launchd 托管，要么改成「检测到/未检测到」纯只读。 |
| 中间件白名单仍是硬编码集合 | ❌ 未处理 | 现在加了 `path.startswith("/api/session-share/")`、`/api/sessions/im/send`，未来再加 public 接口仍要改 `auth.py`。建议改成路由 dependency / marker 自描述。 |
| `routes/media_model_config.py` 薄壳 + `rebuild_router.py` 双路径 import | ❌ 未处理 | 仍然 try/except 双导入；建议二选一。 |
| 前端 auth gate 放在 `<ModelConfigProvider>` 内 | ❌ 未处理 | provider effect 仍可能在 authReady 前执行；建议把 provider 树整体延迟到 `authReady`，或抽 `<AuthGate>`。 |
| `loadInitialData` 顺序 await 7 个请求 | ❌ 未处理 | 仍然串行；登录后白屏时长可显著缩短，改成 `Promise.allSettled`。 |
| 两个 smoke 脚本 80 行模板复用 | ❌ 未处理 | 抽 `_smoke_common.py`。 |
| `DebugConsole.jsx:113` EventSource 缺 `withCredentials` | ❌ 未处理 | same-origin 当前部署没事；将来跨域 SSE 会断。 |

---

## D. 重新验证的测试证据

- 直接读改动后的源码（`local_secrets.py`、`auth.py`、`app.py`、`context.py`、`provider_resolver.py`、`opencrew_runtime_secrets.py`、`rebuild_router.py` 段、`03_02_*VoiceBuilder.py` 段）。
- `git grep` 与原 finder 路径再过一遍：未发现旧 bug 残留。
- 用户已自验证的项（不重复跑）：
  - `backend/scripts/opencrew_phase0_stack_smoke.py --json` 通过。
  - `backend/scripts/opencrew_p0_stack_smoke.py --json` 通过（含无 app cookie 的 session-share、Caddy Basic Auth）。
  - 后端 contract tests 30/30、`frontend npm run build`、`git diff --check` 全部通过。
  - 大 auth body 返回 413（**注意**：smoke 走的是带 Content-Length 的请求，B-2 的 chunked 绕过未被覆盖）。
- 我额外查的 Caddy 反代脚本 `scripts/opencrew_nipio_caddy.sh`：
  - 没有 `request_body { max_size … }`；B-2 的依赖修复需要补这个 directive。
  - 没有 `trusted_proxies`/`header_up X-Forwarded-For` 清洗；B-1 的 `X-Forwarded-For` 在反代链路下也是不可信的。

---

## E. 修复优先级（基于 Round 2 新发现）

| 阶段 | 修复项 | 依据 |
| --- | --- | --- |
| P0（出货前必须） | **B-1（X-Forwarded-For 欺骗 → bootstrap 抢占）** | 直接重现 Round 1 #6，且 LAN 内一条 curl 即可触发 |
| P1（公网/nip.io 测试前） | **B-2（chunked body 绕过）**、Caddy `request_body max_size` | 单台 LAN 攻击者即可拒服务 |
| P2 | B-3（restore prior proxy 而不是无条件 pop）、B-4（IPv6 loopback 归一化），C 的「proxy-policy 三套合并」 | 兼容性 / 一致性 |
| P3 | C 的剩余 cleanup / altitude（缓存 / 单例 / itsdangerous / 数据迁移搬到 migrations 等） | 长期可维护性 |

---

## F. 不再追究

- Round 1 已 REFUTED 的安全分支（`provider_urlopen` 空 proxy_url 兜底；`tmp_path.replace` + 二次 chmod；`int(payload.exp or 0)`；`provider_key_from_row` 内联）保持原结论，不变。
- `_file_lock` 每次 `chmod` 重设 0600：冗余，但无害，不列入修复清单。
- `set_many` 用 `str(...)` 强转非字符串 ref/value：防御性写法，不视为 bug。

---

## G. Round 2 处理记录（Phase 0）

### 已修复

- **B-1 X-Forwarded-For bootstrap 欺骗**：`/api/auth/setup` 不再信任客户端 `X-Forwarded-For`；无 token bootstrap 只接受直连 loopback 且不存在 forwarded headers。反代或非 loopback 场景必须提供 `OPENCREW_SETUP_TOKEN`。
- **B-2 chunked body 绕过**：新增 ASGI body limit middleware，对 `/api/auth/setup` 与 `/api/auth/login` 真实累计 body 字节数，超过 4KB 直接返回 413；Caddy 模板也补充 `request_body { max_size 4KB }`。
- **B-3 ToolLibrary 代理环境覆盖**：ToolLibrary 运行时代理策略改为保存启动时代理环境快照；direct provider 会恢复原始代理值，而不是无条件删除操作员已有代理。
- **B-4 IPv6-mapped loopback**：loopback 判断改用 `ipaddress`，覆盖 `127.0.0.1`、`::1` 与 `::ffff:127.0.0.1` 等合法本机地址。

### 已补测试

- 新增 `backend/tests/contracts/test_phase0_auth_hardening_contract.py` 覆盖 setup loopback/token 策略、伪造 forwarded header 拒绝、chunked body 413。
- 新增 `backend/tests/contracts/test_phase0_secret_and_proxy_contract.py` 覆盖 secret store 默认路径/权限/并发写/corrupt fallback、provider proxy policy、ToolLibrary 代理恢复与 secret ref 读取。
- 增强 `backend/scripts/opencrew_phase0_stack_smoke.py`：验证未登录 401 也带 CORS header。
- 增强 `backend/scripts/opencrew_p0_stack_smoke.py`：验证公开 session-share 路由不依赖 app cookie。

### 验证结果

- `backend/.venv/bin/python -m unittest discover -s backend/tests/contracts`：41 tests passed。
- `backend/scripts/opencrew_phase0_stack_smoke.py --json`：通过。
- `backend/scripts/opencrew_p0_stack_smoke.py --json`：通过，覆盖本机 Caddy Host header 访问 `1.42.112.164.nip.io`。
- `npm run build`：通过。
- `git diff --check`：通过。

备注：公网 `http://1.42.112.164.nip.io` 仍然无法从外部连通，当前阻塞点是机器/NAT/端口映射，不是应用栈。Phase 0 已完成本机和本机 Caddy 反代路径自动化验证。
