# OpenCrew 双环境运维手册(生产 macmini-1 / 测试 macmini-4)

> 最后更新 2026-06-15。本文是两套环境的**权威运维参考**:环境清单、日常操作、踩过的坑。
> 配套:部署 runbook 见 `docs/two_node_prod_test_deployment_plan_2026-06-15.md`;分步脚本见 `scripts/deploy/`。

## 1. 架构概览

- **生产 = macmini-1**(本机):继续供公网小范围用户试用。**不在生产机做实验、不在生产机手动起 dev server**(曾出现 18180 遗留实例事故)。
- **测试 = macmini-4**:生产的完整克隆(数据+密钥),两台都跟 `main`,仅靠独立隧道区分对外入口。所有改动先在测试验证,再晋升到生产。
- 同步模型:**macmini-1 是权威源**(有 GitHub 私有库凭据并跟 main),macmini-4 从 macmini-1 **rsync 镜像**(自身无 GitHub 凭据)。

## 2. 环境清单

| 项 | 生产 macmini-1 | 测试 macmini-4 |
|----|----------------|----------------|
| 角色 | 生产(公网试用) | 测试 |
| 登录用户 / HOME | `macmini-1` / `/Users/macmini-1` | `macmini-4` / `/Users/macmini-4` |
| Tailscale | `macmini-1` `100.111.102.99` | `macs-mac-mini-3` `100.84.196.118`→实为本机;**实测用** `100.76.9.120` |
| LAN IP | `192.168.0.6` | `192.168.0.5` |
| 从 macmini-1 SSH | — | `ssh macmini-4@100.76.9.120`(已配免密) |
| 仓库 | `/Users/macmini-1/work/code/OpenCrew` | `/Users/macmini-4/work/code/OpenCrew` |
| 数据目录 | `/Users/macmini-1/.opencrew` | `/Users/macmini-4/.opencrew` |
| 后端 / 前端 / PG / OpenCode / OpenCode Gateway | 8011 / 18080 / 5433 / 4096 / 5096 | 同(完全对齐) |
| 对外隧道 | quick tunnel(随机 trycloudflare 域名) | 独立 quick tunnel(随机域名) |
| 进程托管 | screen(`opencrew-backend`/`-frontend`)+ nohup 隧道 | 同 |

> ⚠️ Tailscale 节点名 `macs-mac-mini-3` 是设备加入时按计算机名"mac's Mac mini (3)"自动生成 + 去重计数,**不是物理编号**,别被误导。

## 3. 访问与凭据

- **应用登录**:生产与测试**同一套口令**(测试从生产克隆了 `secrets.enc` + 设置库)。分管理员/普通用户两级,口令由运营方掌握,**不写入仓库**。登录接口 `POST /api/auth/login` `{"password":"..."}`,成功下发 `opencrew_session` cookie。
- **OpenCode 服务(4096)**:HTTP Basic Auth,用户名 `opencode`,口令在 `scripts/deploy/00_config.env` 的 `OPENCODE_PASS`。仅绑 `127.0.0.1`,OpenCrew 自动从进程环境发现(`auth_source=process_env`),无需手动登录。
- **数据库**:`opencrew/opencrew@127.0.0.1:5433/opencrew`(本地开发默认)。
- **密钥**:`secret_store.key`+`secrets.enc` 文件型、非硬件绑定,拷贝即解锁;确保 `~/.opencrew` 700、key/enc 600、开 FileVault。

## 4. 日常运维操作(从 macmini-1 编排测试机)

分步脚本在 `scripts/deploy/`(幂等、可单独跑),详见其 `README.md`。常用:

| 目的 | 命令(在 macmini-1 `scripts/deploy/` 下) |
|------|-------------------------------------------|
| **测试机日常更新代码** | `./20_sync_code.sh && ./60_stack.sh restart && ./90_verify.sh` |
| 体检 / 验收 | `./01_check_env.sh` / `./90_verify.sh` |
| 看/起对外网址 | `./70_tunnel.sh`(打印当前 trycloudflare 网址) |
| 重新灌生产数据 | `./40_migrate_db.sh --fresh && ./45_rewrite_paths.sh` |
| 同步新模型/产物 | `./30_sync_assets.sh` |
| 重建 venv/前端依赖 | `./60_stack.sh restart --rebuild` |
| 重起 OpenCode | `./50_opencode.sh --restart` |

**生产机(macmini-1)** 本地操作:`scripts/opencrew_local_stack.sh {status|restart|doctor}`;日志 `/tmp/opencrew-backend.log`、`/tmp/opencrew-frontend.log`;后端默认不 reload,改 Python 路由需 restart。

**晋升纪律**:改动先在测试跑通验证 → 再到生产 `git pull` + `scripts/opencrew_local_stack.sh restart`(低峰期);DB 迁移先测试跑通,生产操作前 `pg_dump` 备份到 `~/.opencrew/backups`。

## 5. 运维经验 / 踩过的坑(重要)

1. **代码同步用 rsync 镜像,不用 git clone**:macmini-4 无 GitHub 私有库凭据;`20_sync_code.sh` 从 macmini-1 镜像(含 .git,排除可重建/大目录,`--delete` 真镜像)。
2. **数据传输走 LAN 更快**:`00_config.env` 的 `TEST_RSYNC_HOST=macmini-4@192.168.0.5`,实测比 Tailscale 快 ~30%(省 WireGuard 开销)。注意 LAN RTT ~15ms 说明至少一端走 WiFi,**插有线网线才是数量级提速**。控制通道仍走 Tailscale(稳定)。
3. **路径重写必须做且别用 sed/perl -i**:用户名不同 → 历史数据里写死的 `/Users/macmini-1/` 要改成 `/Users/macmini-4/`(DB `sessions.workspace_dir` 27 行 + 1404 个会话 JSON;`session_files.path` 是相对路径不用动)。本机 `grep -rlZ` **不输出 NUL** → 用 `grep -rl` + 换行分割 + **Python** 改写(`_rewrite_paths_remote.sh`),sed/perl `-i` 经多层 ssh/xargs 全踩坑。DB 侧在 `40_migrate_db.sh` 用 plain dump + sed 文本替换一次搞定。
4. **macOS 自带 openrsync 不认 `--info=stats1`**:用 `--stats`。
5. **quick tunnel 必须 `--config /dev/null`**:macmini-4 上有别人的命名隧道配置 `~/.cloudflared/config.yml`(`opencode-macmini-4`),cloudflared 会自动加载它、把 `ha-connections:1`/`credentials-file` 串进 quick tunnel,导致边缘 404。隔离后正常。
6. **quick tunnel 网址会变 + 本地 DNS 负缓存**:重启隧道换新随机域名;频繁更换会让本机/路由器 DNS 负缓存这些新名(表现为 NXDOMAIN/HTTP 000),**非隧道故障**——真实用户走自家 ISP DNS 正常,本地缓存几分钟自恢复,或 `dscacheutil -flushcache`。
7. **首次 `60_stack` 较慢**:建 venv 装 whisper/torch/opencv + Analysis_V1 OCR + npm install + codesign,约 2 分钟。

## 6. macmini-4 上被停用的别的项目(为腾端口)

为对齐生产端口,已 `launchctl bootout + disable`(可逆,plist 全保留,数据未删):
- **marker** 四件套 `com.marker.public-{api,postgres,redis,worker}`(原占 18080/55432/56379,源码 `~/work/code/marker`)
- **本地 opencode 用户级副本** `com.local.opencode-{server,gateway}`(原占 4096/5096)。OpenCrew 专用 OpenCode 由系统启动编排恢复，Gateway 由 `com.opencrew.opencode-gateway.system` 接管。

**恢复**:`scripts/deploy/10_free_ports.sh --restore`(或手动 `launchctl enable gui/501/<label>` + `launchctl bootstrap gui/501 <plist>`)。

## 7. 注意事项

- **计量污染**:测试复制了生产密钥,测试流量计入同一批发额度/计量线(利润=计量加价)。建议尽快给测试换独立 LLM key + 独立 `secrets.enc`,或在计量侧标识 TEST 流量。
- **磁盘**:macmini-4 容器可用 ~95GB,够用但盯着 sessions 增长。
- **自启**:macmini-4 可在本机运行
  `sudo scripts/deploy/85_system_launchd.sh install`,安装系统级
  `com.opencrew.boot.system`、`com.opencrew.opencode-gateway.system` 与
  `com.opencrew.cloudflared.system`。三者恢复 PG/OpenCode/前后端、以 KeepAlive
  托管 OpenCode Gateway 和正式命名隧道;无需 GUI 登录。

## 8. 已验证状态(2026-06-15)

macmini-4 首次部署完成,经公网隧道端到端实测:管理员/普通用户登录均 200、OpenCode `status=ready`、克隆会话数 27 可见。**测试环境 100% 可用。**
