# scripts/deploy — OpenCrew 双机分步部署

把生产(macmini-1)的 OpenCrew 部署/更新到测试机(macmini-4)。
**全部从 macmini-1 运行**,脚本内部用 ssh/rsync 作用于 macmini-4。
每个脚本单一职责、**幂等**(重复跑安全)、**可单独执行**。没有"大脚本"。

## 配置

先复制 `00_config.env.example` 为 `00_config.env`,填入本机/测试机路径、数据库口令与 OpenCode 口令。
`00_config.env` 是本机配置文件,不会提交。
代码同步采用 **从 macmini-1 rsync 镜像**(macmini-4 无需 GitHub 私有库凭据);
隧道用 quick tunnel(免域名)。

## 全量首装(按序执行)

```bash
cd scripts/deploy
./01_check_env.sh        # 体检(只读)
./10_free_ports.sh       # 停掉占端口的 marker/本地 opencode(已做过则幂等跳过)
./20_sync_code.sh        # 拉代码
./30_sync_assets.sh      # rsync ffmpeg + ~/.opencrew(密钥/会话/模型)
./40_migrate_db.sh       # 克隆库 + 路径重写
./45_rewrite_paths.sh    # 重写会话文件里的绝对路径
./50_opencode.sh         # 起 4096 专用 OpenCode
./60_stack.sh restart    # 起整栈(建 venv/npm/codesign)
./70_tunnel.sh           # 起 quick tunnel,打印随机 trycloudflare 对外网址(免域名)
./90_verify.sh           # 验收
```

## 日常增量更新(代码改动后)

```bash
./20_sync_code.sh && ./60_stack.sh restart && ./90_verify.sh
```

## 常用单步

| 目的 | 命令 |
|------|------|
| 重新灌生产数据 | `./40_migrate_db.sh --fresh` |
| 同步新模型/产物 | `./30_sync_assets.sh` |
| 重建 venv/前端依赖 | `./60_stack.sh restart --rebuild` |
| 重起 OpenCode | `./50_opencode.sh --restart` |
| 无登录系统级自启(macmini-4 本机) | `sudo ./85_system_launchd.sh install` |
| 还原 marker/本地 opencode | `./10_free_ports.sh --restore` |
| 体检 / 验收 | `./01_check_env.sh` / `./90_verify.sh` |

## 约定

- 幂等:脚本先探测状态再决定动作;`40` 用 `--clean --if-exists` 覆盖刷新。
- 安全:对 macmini-4 上别的项目(marker/opencode)只 `bootout+disable`,不删 plist/数据,可 `--restore`。
- 顺序依赖:`40` 需先 `20`(代码)与库可用;`60` 需 `20/30`;`90` 任何时候只读。
- `80_launchd.sh` 是登录后自启;`85_system_launchd.sh` 安装系统级 LaunchDaemon,
  机器启动后无需 GUI 登录即可恢复 PG/OpenCode/OpenCode Gateway/前后端/正式命名隧道。
