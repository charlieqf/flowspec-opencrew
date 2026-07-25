# OpenCrew Python 包边界与导入路径治理方案

## 2026-06-21 C3 状态

OpenClip/Koubo 后端已在 C1 迁入 `backend/opcrew_backend/koubo/`,C3 删除了 `OpenClip/` 空壳和 4 个 OpenClip bridge 文件,`backend/opcrew_backend/app.py` 现在直接从 `opcrew_backend.koubo` 导入 Koubo router builder。

本文下面保留的 `OpenClip/backend/`、`OpenClip.backend.openclip_backend` 和 OpenClip bridge 引用是迁移前的设计依据和问题证据,不是当前要继续维护的运行路径。当前剩余的 Python 边界治理重点是 `ModelConfig/backend/`、`WorkflowAssistant/backend/`、剩余的 media model wrapper、ToolLibrary 脚本入口,以及残留的 `sys.path.insert`/wildcard import 约束。

## 2026-07-25 批次 A 状态

仓内代码、测试、CI、运行入口和 `routes/__init__.py` 均未引用 `backend/opcrew_backend/routes/asr_config.py`；当前 ASR 路由由 `opcrew_model_config.router` 直接注册。OpenCrew 的交付物是应用而非对外 Python SDK，因此本批次删除该 wildcard 兼容转发层，不再为 `opcrew_backend.routes.asr_config` 保留公共导入承诺。历史章节中与该文件有关的证据以本状态说明为准。

## 结论

当前仓库的 Python 模块边界不是由 packaging 声明出来的，而是由运行时 `sys.path.insert(...)` 临时拼出来的。这是系统性架构问题，不是少数文件的局部代码风格问题。

直接后果是：

- 同一份源码可以被加载成不同模块身份，导致类对象、单例、模块级缓存、`isinstance` 判断在跨边界时静默失效。
- 历史上 `backend/`、`ModelConfig/backend/`、`OpenClip/backend/` 之间存在未声明的双向依赖；C1/C3 已收口 OpenClip 这一支,但其他边界仍依赖特定启动方式。
- import 顺序变成隐式约束，自动 import 重排可能直接破坏启动。
- wildcard bridge 掩盖真实导出面，静态分析、IDE 跳转和 API 审计失效。
- 仓库难以 wheel 化、容器分层、PyInstaller 打包或切换 launcher。

优先级：高。这个问题会影响后续所有后端重构、测试可信度和部署稳定性。

## 部署模型与前置约束

本方案的落地形态是按 OpenCrew 实际交付模型设计的，而不是通用的 PyPI 安装场景：

- 交付方式为**整目录拷贝**到每台 Mac mini，不引入针对一方代码（first-party packages）的额外安装动作。
- 所有 Mac mini 使用**相同的绝对部署路径**和**相同的目录结构**（例如统一部署到固定根目录）。
- 第三方依赖仍由现有流程在每台机器现建 venv（`scripts/opencrew_local_stack.sh` 已经 `python -m venv backend/.venv` 并安装 `backend/requirements.txt`），这一步本来就存在，不属于本方案新增的安装动作。

基于以上约束，模块边界**不通过 `pip install` 一方代码来声明**，而是通过启动器在单点设置 `PYTHONPATH` 来声明三个 Python 根。因为部署路径和结构完全一致，且启动脚本已相对自身算出 `ROOT_DIR`，这个 `PYTHONPATH` 在每台机器上解析到完全相同的包，行为确定且可重定位。

> 说明：`pip install -e` 会把源码绝对路径写入 venv 的 editable `.pth`，需要在每台机器重跑安装，和"整目录拷贝、不新增安装动作"的交付模型相冲突，因此本方案不采用它作为运行期机制。`pyproject.toml` 仅作为将来需要 wheel 化 / PyInstaller 打包时的可选项保留，不是当前前置条件。

## 当前状态

仓库目前只有依赖清单，没有 Python 打包配置：

- `backend/requirements.txt`
- `ToolLibrary/Analysis_V1/requirements-runtime.txt`

未发现 tracked 的 `pyproject.toml`、`setup.py` 或 `setup.cfg`。因此这些 Python 根目录没有被声明为可安装包，只能依赖启动路径和运行时路径注入。

| Python 根 | 当前顶层包 | 当前导入方式 | 主要问题 |
| --- | --- | --- | --- |
| `backend/` | `opcrew_backend` | 由 `backend/main.py` 入口所在目录进入 `sys.path` | 入口路径决定可导入性 |
| `ModelConfig/backend/` | `opcrew_model_config` | `backend/opcrew_backend/app.py` 和 wrapper 运行时插入路径 | 与 `opcrew_backend` 存在未声明耦合 |
| `OpenClip/backend/` | `openclip_backend` | 生产侧经 `OpenClip.backend.openclip_backend`，测试侧经顶层 `openclip_backend` | 同一源码有两种模块身份 |
| `ToolLibrary/` | 多个脚本入口 | 子进程脚本内自行插入 repo/tool 路径 | 独立入口可短期兼容，但反映同一根因 |

## 证据

### 1. 运行时路径注入

主应用和 bridge 文件直接修改 `sys.path`：

- `backend/opcrew_backend/app.py:14`
- `backend/opcrew_backend/routes/openclip_bridge.py:9`
- `backend/opcrew_backend/routes/oc_rebuild_bridge.py:9`
- `backend/opcrew_backend/routes/oc_storyboard_bridge.py:9`
- `backend/opcrew_backend/routes/koubo_storyboard_bridge.py:9`
- `backend/opcrew_backend/routes/workflow_assistant_bridge.py:9`
- `backend/opcrew_backend/routes/media_model_config.py:10`

这些路径计算依赖固定目录层级，例如 `parents[3]`。目录一旦移动，或者启动 cwd/launcher 改变，导入行为就会变化。

### 2. OpenClip 模块身份分裂

生产代码把 OpenClip 当作 repo-root namespace 导入：

- `backend/opcrew_backend/routes/openclip_bridge.py:11`
- `backend/opcrew_backend/routes/oc_rebuild_bridge.py:11`
- `backend/opcrew_backend/routes/oc_storyboard_bridge.py:11`
- `backend/opcrew_backend/routes/koubo_storyboard_bridge.py:11`

导入形态为：

```python
from OpenClip.backend.openclip_backend import build_openclip_router
```

测试代码则把 `OpenClip/backend` 直接塞进 `sys.path`，再用顶层包名导入：

- `backend/tests/contracts/test_analysis_v1_artifact_billing_contract.py:14`
- `backend/tests/contracts/test_analysis_v1_artifact_billing_contract.py:18`

导入形态为：

```python
from openclip_backend.analysis_v1_artifact_billing import ...
```

另有测试直接用 `spec_from_file_location(...)` 给同一源码起临时模块名：

- `backend/tests/contracts/test_analysis_v1_task_process_indicator_mvp_contract.py:22`
- `backend/tests/contracts/test_analysis_v1_run_to_storyboard_tts_mode_contract.py:20`

这会让同一份源码以不同 key 进入 `sys.modules`，例如：

- `OpenClip.backend.openclip_backend`
- `openclip_backend`
- `openclip_backend_schemas_task_indicator_contract`

只要这些路径下存在类、单例、缓存、注册表或模块级锁，就可能出现跨模块身份不相等的问题。

### 3. ModelConfig 与 backend 双向耦合

主应用先把 `ModelConfig/backend` 插入路径，然后导入 `opcrew_model_config`。同时 `opcrew_model_config` 又反向依赖主后端：

- `backend/opcrew_backend/app.py:14`
- `ModelConfig/backend/opcrew_model_config/router.py:7`
- `ModelConfig/backend/opcrew_model_config/asr_config.py:19`
- `ModelConfig/backend/opcrew_model_config/media_model_config.py:25`

这说明 `opcrew_model_config` 并不是独立包，它需要 `opcrew_backend.context.AppContext` 和 provider resolver 等主后端能力。当前依赖关系没有任何 packaging 声明，只是因为两个目录同时在 `sys.path` 上才成立。

### 4. wildcard bridge 和常量污染

剩余的 ModelConfig bridge 使用 wildcard re-export：

- `backend/opcrew_backend/routes/media_model_config.py:12`

Koubo Storyboard 子包也大量使用：

- `OpenClip/backend/openclip_backend/koubo_storyboard/asset_core_services.py:29`
- `OpenClip/backend/openclip_backend/koubo_storyboard/task_routes.py:15`
- `OpenClip/backend/openclip_backend/koubo_storyboard/video_plan_routes.py:15`

当前 tracked 文件中，`OpenClip/backend/openclip_backend/koubo_storyboard` 下有 25 处 `from .constants import *`。这让模块公共面、依赖来源和命名冲突都无法被静态检查清楚。

### 5. ToolLibrary 的同类问题

`ToolLibrary/Analysis_V1`、`ToolLibrary/Rebuild_V1` 下有大量脚本入口自行插入 repo 或 tool 路径。由于这些脚本多由子进程独立拉起，短期危害低于主应用 bridge 层，但根因一致：运行环境没有通过 packaging 统一声明。

## 目标架构

目标是让 Python import 只依赖**启动器单点声明的 `PYTHONPATH`**所暴露的三个稳定包身份，而不是依赖 cwd、bridge 文件里的 `parents[n]` 探测，或测试里散落的路径注入。

推荐的包身份：

| 目录 | Canonical import |
| --- | --- |
| `backend/` | `opcrew_backend` |
| `ModelConfig/backend/` | `opcrew_model_config` |
| `OpenClip/backend/` | `openclip_backend` |

OpenClip 建议统一到 `openclip_backend`，不要继续把产品目录 `OpenClip/` 当作 Python namespace。`OpenClip/` 可以继续作为仓库内产品目录存在，但不应成为业务代码 import 名称的一部分。

目标导入示例：

```python
from openclip_backend import build_openclip_router
from opcrew_model_config import build_model_config_router
from opcrew_backend.context import AppContext
```

## 解决方案

### 阶段 0：建立约束和短期保护

1. 新增治理文档和 issue，将 `sys.path.insert` 视为受控例外，而不是可继续扩散的模式。
2. 暂时允许 ToolLibrary 独立脚本保留路径注入，但必须标记为 legacy script entrypoint。
3. 对 `backend/`、`ModelConfig/backend/`、`OpenClip/backend/` 的生产代码建立 no-new-`sys.path.insert` 约束。
4. 如果短期无法立即统一 `PYTHONPATH`，可增加单一过渡模块，例如 `opcrew_backend/_pathsetup.py`，由所有 legacy bridge 引用，避免每个 bridge 各自硬编码 `parents[n]`。

过渡模块只能作为迁移垫片，不能成为最终架构。

### 阶段 1：在启动器单点声明 PYTHONPATH

不为一方代码引入 `pip install`。改为在启动入口集中声明三个 Python 根，让 import 只依赖这一处声明。

`scripts/opencrew_local_stack.sh` 已经相对脚本自身算出 `ROOT_DIR`，在启动后端的命令里加一行 `PYTHONPATH` 即可：

```bash
cd "$BACKEND_DIR" && \
export PYTHONPATH="$ROOT_DIR/backend:$ROOT_DIR/ModelConfig/backend:$ROOT_DIR/OpenClip/backend" && \
... && exec "$BACKEND_PYTHON" main.py
```

要点：

- `ROOT_DIR` 相对脚本定位，因此该 `PYTHONPATH` 与绝对安装路径无关，整目录拷到任意路径都成立；又因所有机器路径和结构一致，每台解析到的包完全相同。
- `OpenClip/backend` 进入 `PYTHONPATH` 后，`openclip_backend` 成为**顶层包**，这是统一模块身份（阶段 2）的前提。
- 所有进程入口都必须经由这同一处声明：launchd plist、`opencrew_local_stack.sh`、容器 entrypoint、测试 runner、以及 ToolLibrary 子进程的 env。任何绕过它的入口都会重新引入身份分裂。

为避免在多个入口重复硬编码这三段路径，建议把它收敛成一个可被各启动器 source 的片段（例如 `scripts/opencrew_pythonpath.sh`，导出 `OPENCREW_PYTHONPATH`），各入口引用它而不是各自拼接。

> `pyproject.toml` 与 `pip install -e` 不在本阶段。只有当未来确实需要 wheel 化或 PyInstaller 打包时，再单独引入 packaging，且届时应保证每台机器的绝对安装路径一致，使 editable 安装与拷贝部署都稳定。

### 阶段 2：统一 OpenClip 导入身份

把生产 bridge 从：

```python
from OpenClip.backend.openclip_backend import build_openclip_router
```

迁移为：

```python
from openclip_backend import build_openclip_router
```

同时把测试里的路径注入删除。测试 runner 走与生产相同的 `PYTHONPATH` 声明（阶段 1），即可直接 import canonical package，不再各自 `sys.path.insert`。

需要重点处理：

- `backend/opcrew_backend/routes/openclip_bridge.py`
- `backend/opcrew_backend/routes/oc_rebuild_bridge.py`
- `backend/opcrew_backend/routes/oc_storyboard_bridge.py`
- `backend/opcrew_backend/routes/koubo_storyboard_bridge.py`
- `backend/tests/contracts/test_analysis_v1_artifact_billing_contract.py`
- `backend/tests/contracts/test_analysis_v1_runner_executable_contract.py`

对于 `spec_from_file_location(...)` 加载源码的测试，优先改为 import canonical module，再检查对象或源码资源。确实需要源码文本扫描的测试，只读文件，不把源码加载成临时模块。

### 阶段 3：处理 ModelConfig 与 backend 的依赖关系

短期可以在文档和模块说明中显式承认：

- `opcrew_model_config` 依赖 `opcrew_backend`

但这只是"承认"，不解决问题：packaging 缺位时该依赖靠 `PYTHONPATH` 同时暴露两个根才成立，且是模块级 import 形成的真实环（`app.py` 模块级导入 `opcrew_model_config`，后者模块级导入 `opcrew_backend.context`）。删除路径注入（阶段 4）后，这个环可能在加载顺序上撞到 partially-initialized module。因此中长期方向是把跨模块共享接口收敛出来，从根上断环：

- `AppContext` 如果只是协议，应拆出 `Protocol` 或轻量接口。
- provider resolver 如果由主后端统一拥有，`opcrew_model_config` 不应直接 import 内部 service，而应通过注入的 resolver 或 facade 调用。

可选目标结构：

| 包 | 职责 |
| --- | --- |
| `opcrew_backend` | FastAPI app、认证、DB、session、应用装配 |
| `opcrew_model_config` | model/asr/media 配置路由和配置逻辑 |
| `opcrew_core` | `AppContext` protocol、共享类型、轻量工具 |

如果不引入 `opcrew_core`，至少要在 package metadata 和文档中明确 `opcrew_model_config -> opcrew_backend` 的依赖，避免它被误认为独立组件。

### 阶段 4：删除 bridge 层路径注入

前置门槛：删除前必须确认所有进程入口（launchd plist、`opencrew_local_stack.sh`、容器 entrypoint、测试 runner）都已设好阶段 1 的 `PYTHONPATH`，并通过一次**冷启动验证**。否则删掉注入后服务会直接 `ModuleNotFoundError` 起不来。

确认后，删除这些生产代码里的 `sys.path.insert`：

- `backend/opcrew_backend/app.py`
- `backend/opcrew_backend/routes/openclip_bridge.py`
- `backend/opcrew_backend/routes/oc_rebuild_bridge.py`
- `backend/opcrew_backend/routes/oc_storyboard_bridge.py`
- `backend/opcrew_backend/routes/koubo_storyboard_bridge.py`
- `backend/opcrew_backend/routes/workflow_assistant_bridge.py`
- `backend/opcrew_backend/routes/media_model_config.py`

删除后，应用启动失败应被视为启动器 `PYTHONPATH` 配置问题，而不是再通过局部补 path 修复。

### 阶段 5：消除 wildcard re-export

ASR wildcard wrapper 已在 2026-07-25 批次 A 中删除，不再改造成显式 re-export。剩余的 media model wrapper 应按真实调用面改为显式导出，或在确认没有兼容调用者后同样删除。

Koubo 的 `from .constants import *` 改为按名导入。常量很多时可以先分批处理：

1. 给 `constants.py` 定义 `__all__`，冻结公共常量面。
2. 每个 service/route 按实际使用名改显式导入。
3. 加静态检查禁止新增 wildcard import。

### 阶段 6：ToolLibrary 入口治理

ToolLibrary 脚本可以晚于主应用迁移，但最终应满足以下之一：

1. 由调用方在子进程 env 中注入与主应用相同的 `PYTHONPATH`（阶段 1 的同一处声明），脚本直接 import `opcrew_backend`、`openclip_backend` 等包。
2. 通过 `python -m package.module` 启动。
3. 如果必须保留独立脚本入口，则集中到一个明确的 bootstrap helper，并在文件头标注 legacy 例外。

不建议继续在每个脚本中散落 repo-root 探测和 `sys.path.insert`。

## 验收标准

完成后应满足：

1. fresh venv（仅装 `backend/requirements.txt`，不安装一方代码）中，仅靠启动器的 `PYTHONPATH` 即可导入三个 canonical 包：

   ```bash
   PYTHONPATH="$ROOT_DIR/backend:$ROOT_DIR/ModelConfig/backend:$ROOT_DIR/OpenClip/backend" \
     python -c "import opcrew_backend, opcrew_model_config, openclip_backend"
   ```

2. 生产代码中不再有 bridge 层路径注入：

   ```bash
   git grep -n "sys.path.insert" -- backend ModelConfig OpenClip
   ```

   结果应为空，或只剩明确标记的短期 `_pathsetup.py` 兼容层。

3. OpenClip 只保留一个模块身份：

   ```python
   import sys
   import openclip_backend

   assert "openclip_backend" in sys.modules
   assert "OpenClip.backend.openclip_backend" not in sys.modules
   ```

4. 测试不再通过 `sys.path.insert` 加载 `backend/` 或 `OpenClip/backend/`。

5. 生产 bridge 不再使用 wildcard import：

   ```bash
   git grep -n "from .* import \\*" -- backend ModelConfig OpenClip
   ```

   结果应为空，或仅剩已登记的 legacy 例外。

6. 应用可从 `opencrew_local_stack.sh`、launchd、测试 runner、容器 entrypoint 中以同一 package 身份启动（各入口共用同一处 `PYTHONPATH` 声明）。

7. 在一台干净 Mac mini 上，整目录拷贝到约定的绝对部署路径后，现建 venv 装依赖、不安装一方代码，即可冷启动成功。

## 风险和注意事项

- 统一 `PYTHONPATH` 后，现有循环依赖会暴露（不再被各文件的注入顺序掩盖）。不要用新的 `sys.path` hack 压回去，应通过依赖注入、Protocol 或共享 core 包处理。
- `OpenClip/__init__.py` 和 `OpenClip/backend/__init__.py` 当前让 `OpenClip.backend...` import 成立。迁移期不要立即删除，先把所有调用方改到 `openclip_backend`，再决定保留空壳兼容还是删除。
- `spec_from_file_location(...)` 测试如果加载的是有全局状态的模块，会继续制造模块身份分裂；这类测试应改为普通 import 或纯文本扫描。
- ToolLibrary 脚本数量多，建议后置迁移。主应用 bridge 层必须先清理，因为它直接影响服务启动和生产模块身份。

## 推荐落地顺序

1. 在启动器（`opencrew_local_stack.sh`、launchd 等）单点声明 `PYTHONPATH`，覆盖三个 Python 根；冷启动验证通过。
2. OpenClip 生产 bridge 相关步骤已由 C1/C3 完成；不要重新引入 `OpenClip/backend` 或 OpenClip bridge。
3. 删除 `app.py`、ModelConfig wrapper、WorkflowAssistant bridge 中剩余的路径注入,前提是对应启动入口已完成冷启动验证。
4. 显式化剩余 media model wrapper 的 re-export 并定义 `__all__`，或在独立审核后删除该 wrapper。
5. 分批清理 Koubo 的 `from .constants import *`。
6. 给 CI 增加 no-new-`sys.path.insert` 和 no-wildcard-import 检查。
7. 规划 ToolLibrary 脚本经子进程 `PYTHONPATH` 注入或 `python -m` 化的迁移。
