# OpenCrew 架构与目录说明（先读这个）

> 本文件解决一个反复出现的困惑:**仓库里有 `frontend/`、`backend/`、Koubo 模块、`WorkflowAssistant/`,到底跑哪棵、改哪棵?** 结论先行,细节在后。

## TL;DR —— 跑哪棵、改哪棵

| 你要做的事 | 改哪里 | 生效方式 |
|---|---|---|
| 改外壳 App(导航、登录、session-tasks、整体布局) | `frontend/src/`、`backend/opcrew_backend/` | 前端 vite 热载(本机 `hmr:false`,需刷新);后端需重启 |
| 改 **分镜 / 视频合成(composer)/ 素材库 / Analysis V1** | 前端:`frontend/src/modules/koubo/`;后端:`backend/opcrew_backend/koubo/` | 同上(前端刷新;后端重启) |
| 改 Workflow Assistant 抽屉/规划后端 | `WorkflowAssistant/frontend/src/`、`WorkflowAssistant/backend/workflow_assistant/` | 同上(前端刷新;后端重启) |
| 改 composer 的渲染逻辑(ffmpeg / HyperFrame) | `ToolLibrary/Analysis_V1/06_01_VideoPlanComposer.py` | 后端按需调用,重跑即生效 |

Koubo/OpenClip 代码目前只有一份:前端在 `frontend/src/modules/koubo/`,后端在 `backend/opcrew_backend/koubo/`。旧 `OpenClip/` 根目录已在 C3 清理;历史设计/验证文档归档到 `docs/openclip-legacy/`。

## 运行拓扑(本机)

- **前端 = 顶层 `frontend/`**:OpenCrew 外壳(Solid + Vite)。端口 `18080`,`hmr:false`(改动需刷新页面)。启动进程是 `frontend/node_modules/.bin/vite`。
- **后端 = `backend/`(包 `opcrew_backend`)**:FastAPI,`127.0.0.1:8011`,跑在 screen 会话 `opencrew-backend` 里,入口 `backend/main.py`。环境变量 `OPENCREW_BACKEND_RELOAD` **未设 = 无自动重载,改后端必须重启 screen 会话**。日志 `/tmp/opencrew-backend.log`。
- **统一启动脚本**:`scripts/opencrew_local_stack.sh`(它只启 `frontend/` + `backend/`,**不单独启 OpenClip 或 WorkflowAssistant**)。

## Koubo / WorkflowAssistant 是怎么接进来的

Koubo 是主前端内的模块,但仍保留原来的 cache-bust `?v=` 语义:

```js
import AnalysisV1Module from "./modules/koubo/AnalysisV1/AnalysisV1Module.jsx?v=...";
import KouboStoryBoardModule from "./modules/koubo/KouboStoryBoardModule.jsx?v=...";
import UploadAssetLibraryPage from "./modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx?v=...";
import { AnalysisV1MediaSidebar } from "./modules/koubo/AnalysisV1/components/AnalysisV1DialogueView.jsx";
```

Koubo 后端已在 C1 迁入主后端包,C3 后由 `app.py` 直接从 Koubo 包导入 router builder:

```py
from opcrew_backend.koubo import (
    build_koubo_storyboard_router,
    build_oc_rebuild_router,
    build_oc_storyboard_router,
    build_openclip_router,
)
```

旧 OpenClip bridge 文件(`openclip_bridge.py`、`oc_rebuild_bridge.py`、`oc_storyboard_bridge.py`、`koubo_storyboard_bridge.py`)已删除;`app.py` 的 `include_router` 顺序保持不变。

`WorkflowAssistant/` 还没有迁入主树:

1. **前端:被 Koubo 模块反向相对 import**
   ```js
   import SharedWorkflowAssistantDrawer from "../../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx";
   ```
   目前出现在 `frontend/src/modules/koubo/OpenClipModule.jsx` 和 `frontend/src/modules/koubo/OCRebuildModule.jsx`。因此 `frontend/vite.config.ts` 的 `server.fs.allow: [path.resolve(__dirname, "..")]` 仍然需要保留。

2. **后端:sys.path 注入 + import**(`backend/opcrew_backend/routes/workflow_assistant_bridge.py`)
   ```py
   sys.path.insert(0, str(ROOT))                 # ROOT = 仓库根
   from WorkflowAssistant.backend.workflow_assistant import build_workflow_assistant_router
   ```
   该 router 同样在 `app.py` 里 `include_router`。

3. **后端:WorkflowAssistant 运行时反向依赖 Koubo repository**
   ```py
   from opcrew_backend.koubo.repository import OpenClipRepository
   from opcrew_backend.koubo.rebuild_repository import OCRebuildRepository
   ```
   目前在 `WorkflowAssistant/backend/workflow_assistant/routes.py`。

## 已知坑

- **前端根和模块目录不要混**:`frontend/` 是运行根,`frontend/src/modules/koubo/` 是 Koubo/Analysis V1/素材库源码,`WorkflowAssistant/` 仍在根外。
- **后端无自动重载**:改 `backend/opcrew_backend/koubo/`、`WorkflowAssistant/backend` 或 `backend/` 都要重启 `opencrew-backend` screen。
- **契约测试硬编码路径**:`backend/tests/contracts/` 下多处直接读前端/后端源码路径,改名/挪动要同步更新测试路径与 import shim。

## 前端改动强制流程

以后改任何前端 UI 之前,先跑:

```bash
scripts/opencrew_frontend_preflight.sh
```

该脚本会直接打印当前 `18080` listener、顶层入口、Koubo cache import 和浏览器实际会请求的版本串。不要只凭文件名搜索判断运行入口。

处理规则:

1. 改外壳、登录、导航、调试台等,改 `frontend/src/`。
2. 改分镜、视频合成 composer、素材库、Analysis V1,改 `frontend/src/modules/koubo/`。
3. 只要改到 Koubo 用户可见 UI,就同步 bump `frontend/src/App.jsx` 里的对应 `?v=...`。
4. 如果被改文件在 `frontend/src/modules/koubo/KouboStoryBoardModule.jsx` 里也是带 `?v=...` 的 import,同步 bump 那里的版本串。
5. 对 Koubo / composer 类用户反馈的 UI 修复,优先用脚本统一 bump 入口链,避免手改漏项:

   ```bash
   scripts/bump_koubo_frontend_cache_version.sh 20260621-your-change-v1
   ```

   这个脚本会同步更新 `frontend/index.html`、`frontend/src/main.tsx`、`frontend/src/App.jsx` 和 `frontend/src/modules/koubo/KouboStoryBoardModule.jsx` 里的相关 `?v=...`。

6. 验证必须针对 `http://127.0.0.1:18080/` 实际返回内容或浏览器截图,不能只验证源码文件和构建结果。

## 护栏

前端 cache 护栏已经迁到 Koubo 命名:

```bash
scripts/check_koubo_frontend_cache_bump.sh
scripts/bump_koubo_frontend_cache_version.sh 20260621-your-change-v1
scripts/opencrew_frontend_preflight.sh
```

- `bump_koubo_frontend_cache_version.sh` 会同步更新顶层入口链和 Koubo 下静态 import 的 `?v=`。
- `check_koubo_frontend_cache_bump.sh` 支持本地、pre-commit staged diff、CI base diff 三种检查模式。
- 本地要自动阻止提交,先跑 `scripts/install_opencrew_git_hooks.sh`;远端 PR/push 由 `.github/workflows/openclip-bridge-guard.yml` 执行同一检查。
- `opencrew_frontend_preflight.sh` 用实际 `18080` 返回内容验证浏览器会请求哪条链。

去掉 import 上的 `?v=...` 缓存串只有在 Vite/HMR/浏览器缓存问题被实证解决后才做;否则先自动化 bump,不要手删。

## 归并路线

C1 后端迁移已完成:`OpenClip/backend/openclip_backend` 已迁到 `backend/opcrew_backend/koubo/`,`WorkflowAssistant/backend/workflow_assistant/routes.py` 的 repository import 已更新,`openclip_analysis_runner.py` 已迁到 `backend/scripts/`,并增加 runner path 存在性 contract。

C2 前端迁移已完成后,OpenClip/Koubo 前端源码住在 `frontend/src/modules/koubo/`。C2 只迁 Koubo 前端,不迁 `WorkflowAssistant/frontend/src/`;因此 Vite `fs.allow` 仍要保留。

C3 已完成 OpenClip 收尾:先做静态路径审计,再删除 OpenClip bridge 和旧空壳目录,并把 OpenClip 历史设计/验证文档归档到 `docs/openclip-legacy/`。`?v=` 缓存串继续保留;`WorkflowAssistant/` 可按同样节奏单独迁,不要在 Koubo 收尾里顺手半迁。

## 关键证据:仍有反向依赖

Koubo 与主树、WorkflowAssistant 仍存在双向耦合:

- 前端:`frontend/src/modules/koubo/*` 多处依赖 `frontend/src/components/ModelPresetCards.jsx` 和 `frontend/src/debug/debugAdapter.js`。
- 前端:`frontend/src/modules/koubo/OpenClipModule.jsx`、`frontend/src/modules/koubo/OCRebuildModule.jsx` 直接 import `WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx`。
- 后端:`backend/opcrew_backend/koubo/*` 大量依赖 `opcrew_backend.context / adapters.opencode / db.schema / repositories.base / routes.media_model_config / services.provider_resolver`。
- 后端:`WorkflowAssistant/backend/workflow_assistant/routes.py` 直接依赖 Koubo repository。
- 后端:`backend/opcrew_backend/routes/workflow_assistant_bridge.py` 也通过 `sys.path` 从 `WorkflowAssistant.backend.workflow_assistant` 引入 router。

所以当前推荐路线仍是 C:承认 Koubo 是内部模块,逐步把仍在外部的 WorkflowAssistant 后续迁入主目录,再清理剩余 WorkflowAssistant/ModelConfig 过渡边界。
