# OpenCrew 双机部署手册:macmini-1 生产 / macmini-4 测试(可直接执行版)

> 制定 2026-06-15。本文已按 macmini-4 实测环境改写,命令可直接照抄。
> 约定:`PROD` = macmini-1(本机,生产),`TEST` = macmini-4(`100.76.9.120`,用户 `macmini-4`)。
> 已从 PROD 配好到 TEST 的免密 SSH:`ssh macmini-4@100.76.9.120`。

## 0. 决策摘要(已对齐)

| 维度 | 选择 |
|------|------|
| 测试对外暴露 | 独立 cloudflared **命名隧道**(需 Cloudflare 托管域名,见 §6) |
| 测试数据 | **完整克隆生产**(DB + `~/.opencrew`) |
| 测试密钥 | **复制生产密钥**(文件型,拷贝即解锁) |
| 代码版本 | 两台都跟 `main` |
| 端口对齐 | 与生产完全一致:后端 8011 / 前端 18080 / PG 5433 / OpenCode 4096 |
| OpenCode | 给 OpenCrew **单独起一个**,独占 4096 |

> ⚠️ 计量副作用:复制生产密钥 → 测试调用计入同一批发额度/计量线(利润=计量加价)。建议尽快给 TEST 换独立 LLM key,避免污染生产计量。详见 §8.1。

---

## 1. TEST(macmini-4)实测环境

- 身份:Tailscale 名 `macs-mac-mini-3`(自动命名,非物理编号)/ `100.76.9.120` / 用户 `macmini-4` / `HOME=/Users/macmini-4`。
- 硬件:M4 · 16GB · macOS 26.4 · arm64 · 磁盘可用 ~95GB。
- **核心运行时已现成且版本对齐生产**(无需安装):

| 组件 | macmini-4 | 生产 |
|------|-----------|------|
| Homebrew 5.1.15 / Xcode CLT | ✓ | — |
| python@3.12 **3.12.13** | ✓ | 一致 |
| postgresql@16 **16.14** | ✓ | 一致 |
| node 25.9 / npm 11.12 | ✓ | 25.8,基本一致 |
| cloudflared 2026.3 / ffmpeg 8.1.1 | ✓ | — |
| bun(`~/.bun/bin/bun`)/ git / screen / rsync / uv | ✓ | — |

> 注意:非交互 SSH 默认不加载 `/opt/homebrew/bin`。本文凡在 TEST 上跑 brew/node/pg 等,统一用 `ssh macmini-4@100.76.9.120 'zsh -lc "..."'` 走登录 shell,或显式 `export PATH="/opt/homebrew/bin:$PATH"`。

- 目标端口现状:**8011 / 18080 / 5433 / 4096 全部空闲**(已于 2026-06-15 停掉占用端口的别的服务,见 §2)。

---

## 2. 已完成:腾出端口(stop marker + 本地 opencode)

为对齐生产端口,已在 macmini-4 用 `launchctl bootout + disable` 停掉并禁用两套**别的项目**(均可逆,plist 全保留,数据未删):

| 服务 | label | 原占端口 |
|------|-------|----------|
| marker 四件套 | `com.marker.public-{api,postgres,redis,worker}` | 18080 / 55432 / 56379 |
| 本地 opencode | `com.local.opencode-{server,gateway}` | 4096 |

**如需还原这两套服务**(在 TEST 执行):
```bash
ssh macmini-4@100.76.9.120 'bash -lc '"'"'
for L in com.marker.public-postgres com.marker.public-redis com.marker.public-worker com.marker.public-api \
         com.local.opencode-server com.local.opencode-gateway; do
  launchctl enable gui/501/$L
  launchctl bootstrap gui/501 ~/Library/LaunchAgents/$L.plist
done'"'"''
```

---

## 3. 关键风险:用户名不同 → 历史数据里的绝对路径要重写

PROD 数据里写死了 `/Users/macmini-1/...`,TEST 用户是 `macmini-4`,必须重写为 `/Users/macmini-4/...`。实测命中量:

| 位置 | 命中 | 处理 |
|------|------|------|
| DB `sessions.workspace_dir` | **27 行** | 走 plain SQL dump + sed(§5.2) |
| DB `session_files.path` | 0 行(相对路径) | 无需改 |
| 磁盘会话 JSON | **1404 个文件** | grep + sed(§5.3) |
| DB `tunnel_runtime.command_path` | `/opt/homebrew/bin/cloudflared`(两台同) | 无需改 |

> 漏了路径重写,服务能起但历史会话会指向不存在的目录。

---

## 4. 部署 Runbook(按顺序执行)

### 4.1 [TEST] 建目录、确认运行时
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "
  mkdir -p ~/work/code ~/.opencrew
  brew --version | head -1; python3.12 --version; postgres --version; node --version; cloudflared --version | head -1
"'
```

### 4.2 [TEST] 拉代码(跟 main)
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "
  cd ~/work/code && [ -d OpenCrew ] || git clone <OpenCrew 仓库地址> OpenCrew
  cd OpenCrew && git checkout main && git pull
"'
```

### 4.3 [PROD→TEST] 搬非 git 大件 + 数据目录
在 **PROD(本机)** 执行 rsync(排除 postgres 物理目录,DB 走逻辑 dump;排除 lock 与 Phase 0 测试残留):
```bash
# ffmpeg/ffprobe 二进制(被 .gitignore,不在仓库里)
rsync -av /Users/macmini-1/work/code/OpenCrew/ToolLibrary/.bin/ \
  macmini-4@100.76.9.120:/Users/macmini-4/work/code/OpenCrew/ToolLibrary/.bin/

# 加密数据目录:含 secret_store.key + secrets.enc(密钥)+ sessions + runtimes 模型
rsync -av --exclude 'postgres/' --exclude '*.lock' --exclude 'caddy/' --exclude 'npc/' \
  /Users/macmini-1/.opencrew/ macmini-4@100.76.9.120:/Users/macmini-4/.opencrew/

# 收紧密钥权限
ssh macmini-4@100.76.9.120 'chmod 700 ~/.opencrew; chmod 600 ~/.opencrew/secret_store.key ~/.opencrew/secrets.enc'
```
> 密钥是文件型、非硬件绑定:拷过去即在 TEST 解锁全部 provider key,无需重新录入。

### 4.4 [PROD] 导出生产库为纯 SQL
```bash
PGPASSWORD=opencrew pg_dump -h 127.0.0.1 -p 5433 -U opencrew -d opencrew -Fp -f /tmp/opencrew_prod.sql
```

### 4.5 [PROD→TEST] 路径重写后传到 TEST
```bash
sed 's#/Users/macmini-1/#/Users/macmini-4/#g' /tmp/opencrew_prod.sql > /tmp/opencrew_test.sql
scp /tmp/opencrew_test.sql macmini-4@100.76.9.120:/tmp/
```

---

## 5. 数据库与会话路径落地(TEST 执行)

### 5.1 初始化 PG16 集群(数据目录对齐生产路径)+ 端口 5433
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "
  [ -f ~/.opencrew/postgres/PG_VERSION ] || initdb -D ~/.opencrew/postgres
  pg_ctl -D ~/.opencrew/postgres -o \"-p 5433\" -l /tmp/opencrew-pg.log start || pg_ctl -D ~/.opencrew/postgres status
  sleep 2
  psql -p 5433 -d postgres -tc \"SELECT 1 FROM pg_roles WHERE rolname='opencrew'\" | grep -q 1 \
    || createuser -p 5433 -s opencrew
  psql -p 5433 -d postgres -c \"ALTER USER opencrew PASSWORD 'opencrew';\"
  psql -p 5433 -d postgres -tc \"SELECT 1 FROM pg_database WHERE datname='opencrew'\" | grep -q 1 \
    || createdb -p 5433 -O opencrew opencrew
"'
```

### 5.2 恢复(已重写路径的)dump
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "psql -p 5433 -U opencrew -d opencrew -f /tmp/opencrew_test.sql"'
# 校验:应为 0
ssh macmini-4@100.76.9.120 'zsh -lc "psql -p 5433 -U opencrew -d opencrew -tc \"SELECT count(*) FROM sessions WHERE workspace_dir LIKE '\''/Users/macmini-1/%'\''\""'
```

### 5.3 重写磁盘会话文件里的绝对路径(macOS sed -i 要带空串)
```bash
ssh macmini-4@100.76.9.120 'bash -lc '"'"'
  grep -rlZ "/Users/macmini-1" ~/.opencrew/sessions 2>/dev/null \
    | xargs -0 sed -i "" "s#/Users/macmini-1/#/Users/macmini-4/#g"
  echo "剩余命中: $(grep -rl "/Users/macmini-1" ~/.opencrew/sessions 2>/dev/null | wc -l)"
'"'"''
```

---

## 6. OpenCrew 专用 OpenCode(独占 4096)

4096 已腾空。用现成的 bun + opencode 源码起一个 **OpenCrew 专用**实例,带稳定 Basic Auth:
```bash
ssh macmini-4@100.76.9.120 'bash -lc '"'"'
  export PATH="$HOME/.bun/bin:/opt/homebrew/bin:$PATH"
  cd ~/work/code/opencode
  nohup env OPENCODE_SERVER_USERNAME="opencode" OPENCODE_SERVER_PASSWORD="<TEST稳定口令>" \
    bun run --cwd packages/opencode --conditions=browser src/index.ts serve \
    --hostname 127.0.0.1 --port 4096 > /tmp/opencrew-opencode-4096.log 2>&1 &
'"'"''
# 验证
ssh macmini-4@100.76.9.120 'curl -i -m 10 -u "opencode:<TEST稳定口令>" http://127.0.0.1:4096/global/health'
```
> 若不想复用别人那份源码,可改装 opencode CLI 再 `opencode serve --port 4096`,效果等价。启动后(§7.4)在 OpenCrew 里 `POST /api/setup/opencode/discover`,确认 `summary.opencode.status = ready`。

---

## 7. 起栈与隧道(TEST 执行)

### 7.1 起 OpenCrew 整栈(脚本自动建 venv、npm install、codesign 原生扩展)
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "
  cd ~/work/code/OpenCrew
  rm -rf backend/.venv frontend/node_modules   # 跨机重建,勿直拷
  OPENCREW_DATA_DIR=\$HOME/.opencrew \
  DATABASE_URL='\''postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew'\'' \
  scripts/opencrew_local_stack.sh restart
  scripts/opencrew_local_stack.sh status
"'
```

### 7.2 本地健康检查
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "curl -fsS http://127.0.0.1:8011/api/health; echo; curl -fsS http://127.0.0.1:18080/api/auth/status"'
```

### 7.3 命名隧道(需 Cloudflare 托管域名 —— 唯一外部前提)
```bash
ssh macmini-4@100.76.9.120 'zsh -lc "
  cloudflared tunnel login
  cloudflared tunnel create opencrew-test
  cloudflared tunnel route dns opencrew-test test.<你的域名>
"'
```
`~/.cloudflared/config.yml`:
```yaml
tunnel: opencrew-test
credentials-file: /Users/macmini-4/.cloudflared/<TUNNEL_UUID>.json
ingress:
  - hostname: test.<你的域名>
    service: http://127.0.0.1:18080
  - service: http_status:404
```
启动:`cloudflared tunnel run opencrew-test`(建议 launchd 托管,见 §9)。

> ⚠️ 前端 `vite.config.ts:8` 的 `allowedHosts` 默认放行 `.trycloudflare.com` 与 `goldenstand.cn` 系列,**不含你的自定义测试域名**。需在该默认数组里加上 `test.<你的域名>`(代码改动,跟 main 同步到两台,多放一个 host 无害),否则前端会拒绝该 Host。

### 7.4 接 OpenCode
见 §6,起完后 `POST /api/setup/opencode/discover`,确认 ready。

---

## 8. 风险与注意

### 8.1 计量污染(最高优先)
复制生产密钥后测试流量计入同一批发额度。建议尽快给 TEST 换独立 LLM key + 独立 `secrets.enc`,或在计量侧标识 TEST 流量。

### 8.2 PostgreSQL
用逻辑 dump(§4.4/§5.2)而非物理拷贝:跨机更稳,且能在 SQL 文本层一次完成路径重写。两台同为 PG16.14,无版本风险。

### 8.3 原生扩展签名
`.venv` 必须重建(§7.1):venv 内 `.so/.dylib` 需在本机重新 ad-hoc 签名,stack 脚本的 `codesign_backend_native_extensions` 会处理;直拷 venv 会因绝对路径(`/Users/macmini-1`)+ 签名问题加载失败。

### 8.4 数据里的绝对路径
§3/§5 的路径重写是本方案最易漏的一步,务必跑校验(§5.2/§5.3 的命中计数应为 0)。

### 8.5 密钥可移植 = 双刃剑
`secret_store.key` 文件型、非硬件绑定,搬走即解锁。确保 `~/.opencrew` 700、key/enc 600,且 TEST 开启 FileVault。

---

## 9. 自启与运维(建议)

两台都建议改 launchd 托管,开机自启 + 崩溃重拉:
- `com.opencrew.postgres`:`postgres -D ~/.opencrew/postgres -p 5433`
- `com.opencrew.stack`:`scripts/opencrew_local_stack.sh start`
- `com.opencrew.tunnel`:`cloudflared tunnel run opencrew-test`

> macmini-4 上别的项目(marker / 本地 opencode)的 launchd agent 已 disable,不会和 OpenCrew 抢端口。

---

## 10. 两台都跟 main 的同步纪律

1. 改动只在 **TEST(macmini-4)** 先 `git pull` + 重启验证。
2. TEST 验收通过后,再到 **PROD(macmini-1)** `git pull` + `scripts/opencrew_local_stack.sh restart`(低峰期;含后端改动必须 restart)。
3. schema 迁移先在 TEST 跑通;PROD 操作前 `pg_dump` 备份到 `~/.opencrew/backups`。
4. 建议对每次晋升到生产的提交打 `prod-YYYYMMDD` tag,便于回滚。

---

## 11. 验收清单(macmini-4)

- [ ] `scripts/opencrew_local_stack.sh status` 三件套全绿
- [ ] `curl 127.0.0.1:8011/api/health` → 200
- [ ] `curl 127.0.0.1:18080/api/auth/status` → 200
- [ ] OpenCode `:4096` `/global/health` 可达;`/api/setup/summary` 显示 `opencode.status = ready`
- [ ] DB 校验:`sessions.workspace_dir` 含 `/Users/macmini-1` 的行数 = 0
- [ ] 磁盘校验:`~/.opencrew/sessions` 含 `/Users/macmini-1` 的文件数 = 0
- [ ] 命名隧道 `https://test.<域名>/` → 200,返回 OpenCrew 前端
- [ ] 登录后能看到与生产一致的会话/数据
- [ ] (建议)launchd 自启:重启 macmini-4 后全栈自动恢复
- [ ] (建议)TEST 已切独立 LLM key,计量不再污染生产
