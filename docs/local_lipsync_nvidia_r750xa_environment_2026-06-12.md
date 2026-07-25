# 本地对口型部署目标机环境记录：Dell R750xa / 4x RTX 4090

记录时间：2026-06-12
用途：记录 NVIDIA 服务器环境，用于后续调研和部署本地对口型模型，替代当前云端 lipsync 服务。
访问限制：当前只能通过 jumpserver 网页终端访问服务器，因此部署、巡检、测试都应提供可复制粘贴的命令行、sh 脚本或 Python 脚本。

更新：已通过 Tailscale 打通本机到服务器的 SSH 访问，后续不再需要依赖 jumpserver 网页终端执行常规命令。

## 远程访问

Tailscale：
- 节点名：`opencrew-r750xa-lipsync`
- Tailscale IP：`100.80.103.11`

本机 SSH key：
- 私钥：`~/.ssh/opencrew_r750xa`
- 公钥已追加到服务器 `ubuntu` 用户的 `~/.ssh/authorized_keys`

本机连接命令：

```bash
ssh -i ~/.ssh/opencrew_r750xa ubuntu@100.80.103.11
```

远程命令示例：

```bash
ssh -i ~/.ssh/opencrew_r750xa ubuntu@100.80.103.11 "hostname && nvidia-smi -L && cd /data/app/musetalk && pwd"
```

已验证远程命令输出：

```text
ubuntu
GPU 0: NVIDIA GeForce RTX 4090
GPU 1: NVIDIA GeForce RTX 4090
GPU 2: NVIDIA GeForce RTX 4090
GPU 3: NVIDIA GeForce RTX 4090
/data/app/musetalk
```

## 服务器概况

服务器：
- 型号：Dell PowerEdge R750xa
- 登录目标：`ubuntu@192.168.4.3`
- 系统：Ubuntu 22.04.4 LTS
- Kernel：`Linux 5.15.0-127-generic`
- 架构：`x86_64`
- 主机名：`ubuntu`

用户提供的硬件信息：
- CPU：`6338N x2`
- 内存：`DELL 原装 64GB x4`
- 控制器：`H755`
- 硬盘：`Intel 7.68T U.2 x2`
- GPU：第三方 NVIDIA RTX 4090 x4

已验证存储信息：
- 根分区：`/dev/mapper/ubuntu--vg-ubuntu--lv`
- 文件系统：`ext4`
- 根分区容量：`6.9T`
- 已用：`1.3T`
- 可用：`5.4T`
- 使用率：`19%`
- 物理盘视图：`sda 7T PERC H755N Front`

## GPU / CUDA

`nvidia-smi` 已正常识别 4 张 RTX 4090：

| GPU | 型号 | 显存 | Bus-Id | 探测时显存占用 |
| --- | --- | --- | --- | --- |
| 0 | NVIDIA GeForce RTX 4090 | 24564 MiB | `00000000:17:00.0` | 17548 MiB |
| 1 | NVIDIA GeForce RTX 4090 | 24564 MiB | `00000000:65:00.0` | 13512 MiB |
| 2 | NVIDIA GeForce RTX 4090 | 24564 MiB | `00000000:CA:00.0` | 12302 MiB |
| 3 | NVIDIA GeForce RTX 4090 | 24564 MiB | `00000000:E3:00.0` | 12366 MiB |

驱动和 CUDA：
- NVIDIA Driver：`560.35.03`
- `nvidia-smi` CUDA Version：`12.6`
- `nvcc`：CUDA compilation tools `12.4`, `V12.4.131`

判断：
- 单张 4090 有约 24GB 显存，满足 MuseTalk 1.5 推理。
- LatentSync 1.6 官方推理要求约 18GB VRAM，单张 4090 理论满足，但需要先空出一张 GPU。
- 4 张 4090 不是自动合并成 96GB 显存；默认应按“一个 worker 绑定一张 GPU”的方式调度，除非模型明确支持多卡并行或模型切分。

## 当前资源占用

内存：
- 总内存：`251Gi`
- 已用：`52Gi`
- 可用：`196Gi`
- buff/cache：`192Gi`

Swap：
- `/swap.img`
- 大小：`8G`
- 已用：`8G`
- 使用率：`100%`

备注：
- 物理内存可用量仍然很高，swap 满暂时不是对口型部署阻塞项。
- 后续如果出现系统响应慢、OOM 或推理启动异常，需要单独清理 swap 或排查长期驻留服务。

GPU 占用进程，探测时间点为 2026-06-12：

| GPU | 主要占用 |
| --- | --- |
| 0 | `/data/miniconda3/envs/vc/bin/python3.10` 多进程、`py39_face`、`gsocr` 等 |
| 1 | `/data/miniconda3/envs/py10/bin/python`、`ollama_llama_server` |
| 2 | `ollama_llama_server` |
| 3 | `ollama_llama_server` |

高内存进程：
- Elasticsearch Java 进程 RSS 约 `28.5GB`
- 多个长期 Python/Gunicorn/Ollama 服务常驻

部署注意：
- MuseTalk 推理可以先尝试绑定 GPU 1。
- LatentSync 1.6 推理前建议释放一整张 4090，避免和 Ollama 或其他长期服务抢显存。

## 非干扰测试原则

这台服务器上有其他业务应用和长期驻留服务。所有 OpenCrew 本地对口型测试必须遵守以下约束：

禁止操作，除非用户明确批准：
- 不重启服务器。
- 不停止、重启或 kill 现有业务服务，包括但不限于 Docker、Ollama、Elasticsearch、Kibana、Nginx、Gunicorn、现有 Python 服务。
- 不执行系统升级、内核升级或大范围 `apt upgrade`。
- 不清理 swap、不修改系统级内核参数、不修改网络配置。
- 不修改已有业务应用目录中的未知配置。
- 不占用所有 GPU，不启动多卡并发压力测试。

测试运行约束：
- 每次推理前先检查 `nvidia-smi`，选择当时显存余量最大的单张 GPU。
- 默认只绑定一张 GPU，例如 `CUDA_VISIBLE_DEVICES=1`。
- LatentSync 1.6 这类显存需求高的模型，必须确认目标 GPU 至少有约 18GB 可用显存后再运行。
- 首轮测试只跑短样本，优先 5-10 秒视频。
- 测试产物写入独立目录，避免覆盖现有结果，例如：
  - `/data/app/musetalk/results/opencrew_probe/`
  - `/data/app/opencrew-lipsync/`
- 不启动常驻 API 服务，除非已经确认端口、资源占用和回滚方式。
- 如需启动常驻 worker，应先使用前台命令验证，再考虑 systemd 或 supervisor。
- 不使用 Docker 部署新服务，除非先确认 NVIDIA Container Toolkit 和 Docker 现有业务影响。

远程操作习惯：
- 优先执行只读巡检命令。
- 写入操作限定在 OpenCrew 测试目录或明确的模型目录。
- 长任务使用 `tmux` 或 `nohup` 前，先确认不会造成 GPU/CPU/磁盘持续高负载。
- 任何 kill、restart、reboot、apt upgrade、docker compose down/up 等操作都需要单独确认。

## Docker 状态

Docker：
- Docker Version：`27.3.1`
- Docker Root Dir：`/var/lib/docker`
- Runtimes：`io.containerd.runc.v2`, `runc`
- Default Runtime：`runc`

判断：
- 当前 Docker 没有看到 `nvidia` runtime。
- 不建议先走 Docker 部署 MuseTalk/LatentSync。
- 短期优先使用 conda 环境部署；如后续要 Docker 化，需要先安装和验证 NVIDIA Container Toolkit。

## Conda / Python / ffmpeg

Conda：
- Conda 根目录：`/data/miniconda3`
- Conda 版本：`24.11.3`
- base Python：`3.12.2`

已有 conda 环境较多，和对口型相关的环境：
- `musetalk`
- `liveportrait`
- `sadtalker`
- `vc`
- `py39_face`
- `py10`

ffmpeg：
- 版本：`7.1.1`
- 来源：conda-forge
- 路径环境来自 `/data/miniconda3`

## 已有 MuseTalk 环境

应用目录：
- `/data/app/musetalk`

仓库状态：
- 该目录不是 Git 仓库，无法通过 `git log` 追溯版本。
- 文件时间集中在 2025-07 到 2025-09，推测为手动拷贝或打包部署版本。

Conda 环境：
- 环境名：`musetalk`
- Python：`3.10.18`
- Python 路径：`/data/miniconda3/envs/musetalk/bin/python`

关键 Python 包：
- `torch 2.0.1+cu118`
- `torchvision 0.15.2+cu118`
- `torchaudio 2.0.2+cu118`
- `diffusers 0.30.2`
- `transformers 4.39.2`
- `accelerate 0.28.0`
- `mmcv 2.0.1`
- `mmdet 3.1.0`
- `mmpose 1.1.0`
- `opencv-python 4.9.0.80`
- `openmim 0.3.9`
- `gradio 5.24.0`

已观察到的问题：
- `pip list` 输出有 `WARNING: Ignoring invalid distribution -orch`，说明环境里可能有残留的 torch 包元数据。
- 当前 `conda run -n musetalk` 下 PyTorch 只报告 `cuda count = 1`，但 `nvidia-smi` 能看到 4 张 GPU。后续如需多卡并发，需要进一步确认是否存在 cgroup、容器、环境变量或驱动访问限制。

## MuseTalk 权重和配置

现有权重文件：

| 文件 | 大小 |
| --- | --- |
| `models/musetalk/pytorch_model.bin` | 3400076549 bytes |
| `models/musetalkV15/unet.pth` | 3400074924 bytes |
| `models/musetalk/musetalk.json` | 748 bytes |
| `models/musetalkV15/musetalk.json` | 748 bytes |
| `models/syncnet/latentsync_syncnet.pt` | 1488019828 bytes |
| `models/sd-vae/diffusion_pytorch_model.bin` | 334707217 bytes |
| `models/whisper/pytorch_model.bin` | 151095027 bytes |
| `models/dwpose/dw-ll_ucoco_384.pth` | 406878486 bytes |
| `models/face-parse-bisent/79999_iter.pth` | 53289463 bytes |
| `models/face-parse-bisent/resnet18-5c106cde.pth` | 46827520 bytes |

配置文件：
- `configs/inference/test.yaml`
- `configs/inference/realtime.yaml`

`inference.sh` 支持：
- `sh inference.sh v1.0 normal`
- `sh inference.sh v1.0 realtime`
- `sh inference.sh v1.5 normal`
- `sh inference.sh v1.5 realtime`

内置测试配置 `configs/inference/test.yaml`：

```yaml
task_0:
 video_path: "data/video/yongen.mp4"
 audio_path: "data/audio/yongen.wav"

task_1:
 video_path: "data/video/yongen.mp4"
 audio_path: "data/audio/eng.wav"
 bbox_shift: -7
```

## 当前部署判断

本机已经具备本地对口型部署条件：

1. MuseTalk 1.5 权重已存在，环境基本齐全，且内置样例已跑通，应优先作为本地生产基线验证。
2. LatentSync 1.6 可以作为高质量档或质量对照，但不应直接混装进现有 `musetalk` 环境，建议新建独立 conda 环境。
3. Wav2Lip 不再作为主方向。原因是质量和授权都不适合作为正式商业替代，只保留为兼容或历史对照。
4. 现阶段不要优先使用 Docker，因为 NVIDIA runtime 未配置。
5. 在接入 OpenCrew 之前，先完成命令行级别的样例推理、业务样本推理和质量验收。

建议优先级：

| 优先级 | 方案 | 用途 |
| --- | --- | --- |
| P0 | MuseTalk 1.5 | 本地生产基线，先跑通固定输入输出 |
| P1 | LatentSync 1.6 | 高质量档/修复档/对照评估 |
| P2 | VideoReTalking / KeySync | 特殊样本或补充研究 |
| 不推荐 | Wav2Lip | 历史对照，不作为正式替代 |

## 后续验证命令

确认样例文件：

```bash
cd /data/app/musetalk && ls -lh data/video/yongen.mp4 data/audio/yongen.wav data/audio/eng.wav
```

确认指定 GPU 可见：

```bash
cd /data/app/musetalk && CUDA_VISIBLE_DEVICES=1 conda run -n musetalk python -c "import os, torch; print('CUDA_VISIBLE_DEVICES=', os.environ.get('CUDA_VISIBLE_DEVICES')); print('count=', torch.cuda.device_count()); print('name=', torch.cuda.get_device_name(0)); print('mem=', torch.cuda.mem_get_info(0))"
```

运行 MuseTalk 1.5 内置样例：

```bash
cd /data/app/musetalk && CUDA_VISIBLE_DEVICES=1 conda run -n musetalk python3 -m scripts.inference --inference_config ./configs/inference/test.yaml --result_dir ./results/probe_v15 --unet_model_path ./models/musetalkV15/unet.pth --unet_config ./models/musetalkV15/musetalk.json --version v15
```

检查输出：

```bash
cd /data/app/musetalk && find results/probe_v15 -type f -printf "%p  %s bytes\n" | sort
```

已验证输出，记录于 2026-06-12：

```text
results/probe_v15/v15/yongen_eng.mp4  6440261 bytes
results/probe_v15/v15/yongen_yongen.mp4  871492 bytes
```

结论：
- MuseTalk 1.5 内置样例推理链路已跑通。
- 下一步应使用业务输入视频和业务生成音频进行固定样本验证。

## 接入 OpenCrew 前的验收点

最小验收：
- MuseTalk 1.5 内置样例可成功生成视频。已完成。
- 业务输入 `mp4 + wav` 可通过固定命令生成结果。
- 输出视频时长、帧率、音轨、分辨率符合预期。
- 口型同步质量可接受，无明显脸部错位、严重抖动、牙齿/嘴部异常。
- 单 GPU 推理时显存峰值和耗时可记录。
- 失败时可以保留日志、输入、输出路径。

## OpenCrew MuseTalk Demo 记录

记录时间：2026-06-12
目标：使用 OpenCrew 现有视频和音频资源，跑通一条 MuseTalk 1.5 对口型 demo，供人工检查效果。

本地素材：
- 源视频：`docs/artifacts/3ad393e19f0325b5bdee80c4e2ac18d3.mp4`
- 音频：`ToolLibrary/Analysis_V1/VoiceCatalog/gemini-3.1-flash-tts-preview/Aoede_fixed_cn_v1_16s.wav`

处理方式：
- 从源视频截取 8 秒竖屏片段：`720 x 1280`, `30fps`
- 从 TTS 音频截取前 8 秒并转为：`16000Hz`, `mono`, `pcm_s16le`
- 远端目录：`/data/app/opencrew-lipsync-demo/20260612_musetalk_v15_opencrew_assets_demo2`
- GPU：通过 `CUDA_VISIBLE_DEVICES=2` 绑定单张 RTX 4090
- MuseTalk 参数：`v15`, `batch_size=4`, `use_float16`

远端推理命令核心：

```bash
cd /data/app/musetalk
CUDA_VISIBLE_DEVICES=2 /data/miniconda3/bin/conda run --no-capture-output -n musetalk \
  python3 -m scripts.inference \
  --inference_config /data/app/opencrew-lipsync-demo/20260612_musetalk_v15_opencrew_assets_demo2/config/opencrew_demo.yaml \
  --result_dir /data/app/opencrew-lipsync-demo/20260612_musetalk_v15_opencrew_assets_demo2/output \
  --unet_model_path ./models/musetalkV15/unet.pth \
  --unet_config ./models/musetalkV15/musetalk.json \
  --version v15 \
  --gpu_id 0 \
  --batch_size 4 \
  --use_float16
```

观察到的问题：
- MuseTalk 原脚本本次实际只写出了 207 张生成帧，缺失 33 张。
- 原脚本用 `%08d.png` 连续序列封装视频，遇到缺失帧会提前停止，第一次输出只有 2 个视频帧，虽然容器时长显示 8 秒。
- 这个问题不一定是模型失败，而是“生成帧缺失时缺少补帧/容错封装”的工程问题。

临时修复方式：
- 保留中间生成帧和原始抽帧。
- 对缺失帧使用原始视频帧 fallback。
- 用补齐后的 240 张帧重新封装音视频。

最终可检查结果：
- 本机文件：`docs/artifacts/lipsync_demo_20260612/opencrew_musetalk_v15_aoede_8s_fixed.mp4`
- 本机日志：`docs/artifacts/lipsync_demo_20260612/musetalk_v15_run.log`
- 远端文件：`/data/app/opencrew-lipsync-demo/20260612_musetalk_v15_opencrew_assets_demo2/output_fixed/v15/opencrew_musetalk_v15_aoede_8s_fixed.mp4`

最终结果校验：
- 时长：`8.000000s`
- 分辨率：`720 x 1280`
- 帧率：`30fps`
- 视频帧：`240`
- 音频：`AAC`, `16000Hz`, `mono`
- 文件大小：约 `3.8MB`

人工检查反馈：
- 口型同步效果可接受。
- 主要问题是嘴部和面部融合脱节，原因与人物头部/面部晃动有关。
- 该问题属于 MuseTalk 在动态头部姿态、mask 融合、bbox/landmark 跟踪稳定性上的工程风险，不能只用静态正脸样本验收。

已补充参数对照：

| 版本 | 参数 | 本机结果 |
| --- | --- | --- |
| `jaw_wide` | `--parsing_mode jaw --extra_margin 25 --left_cheek_width 140 --right_cheek_width 140` | `docs/artifacts/lipsync_demo_20260612/variants/opencrew_musetalk_v15_aoede_8s_jaw_wide_fixed.mp4` |
| `raw_margin20` | `--parsing_mode raw --extra_margin 20 --left_cheek_width 90 --right_cheek_width 90` | `docs/artifacts/lipsync_demo_20260612/variants/opencrew_musetalk_v15_aoede_8s_raw_margin20_fixed.mp4` |

两个对照版校验：
- 时长：`8.000000s`
- 分辨率：`720 x 1280`
- 帧率：`30fps`
- 视频帧：`240`
- 音频：`AAC`, `16000Hz`, `mono`

下一步建议：
- 用这三个结果人工比较：默认版、`jaw_wide`、`raw_margin20`。
- 如果参数调整仍无法解决脱节，需要评估 LatentSync 1.6 作为高质量档，尤其关注头部运动样本。
- 对 MuseTalk 接入层增加样本筛选：优先正脸、轻微头动、口部无遮挡；头动明显或手/产品遮挡口部时降级到高质量模型或云端 fallback。
- 对 MuseTalk 输出增加后处理校验和补帧封装；原脚本输出不能直接作为最终产物。

接入启示：
- OpenCrew 的本地 lipsync provider 不能直接信任 MuseTalk 原脚本的最终 mp4。
- 接入层应校验输出视频帧数、时长、音频流和可解码帧数。
- 对缺失帧应有 fallback 策略：优先用原始帧补齐，再封装完整视频。
- 测试素材带有原字幕，字幕内容与新 TTS 不匹配；这次只用于口型检查，正式流程应在口型生成后再处理字幕。

建议测试集：
- 中文口播男声/女声
- 英文样本
- 正脸、轻微侧脸、头部运动样本
- 低清和高清视频
- 短视频 5-10 秒
- 中等长度 30-60 秒

接入方式建议：
- 不把模型直接嵌入 OpenCrew backend 进程。
- 在 NVIDIA 服务器上提供独立 `local-lipsync` 服务或队列 worker。
- OpenCrew 只提交任务：`source_video + generated_audio + options`。
- worker 输出：`final_video.mp4 + logs + metadata`。
- 云端 lipsync 服务保留为 fallback，直到本地方案通过验收。

## LatentSync 1.6 部署记录

记录时间：2026-06-12
目标：在 GPU 服务器上部署 LatentSync 1.6，用于和 MuseTalk 1.5 做质量对照，重点验证 MuseTalk 在头部/面部晃动样本上的嘴脸脱节问题。

部署原则：
- 独立源码目录：`/data/app/latentsync`
- 独立 conda 环境：`latentsync16`
- 不复用或修改 `/data/app/musetalk`
- 不执行官方 `setup_env.sh` 中的 `sudo apt install libgl1`
- 不重启、不停止现有服务、不占用 GPU 做推理，直到确认有单张 4090 至少约 18GB 可用显存

源码：
- 官方仓库：`https://github.com/bytedance/LatentSync`
- 服务器直接从 `github.com` 下载 zip 会长时间无流量；改用 `https://codeload.github.com/bytedance/LatentSync/zip/refs/heads/main`
- 源码包：`/data/app/latentsync_download/LatentSync-main.zip`
- SHA256：`4eab0b0b8e281b862900de1226635a4620f997d137005057722158cda06d9775`
- 解压目录：`/data/app/latentsync`
- 本地记录文件：`/data/app/latentsync/.opencrew_source_ref`

环境：
- Python：`3.10.13`
- ffmpeg：通过 conda-forge 安装在 `latentsync16` 环境内
- PyTorch：`torch 2.5.1+cu121`
- torchvision：`0.20.1+cu121`
- 关键依赖：`diffusers 0.32.2`, `transformers 4.48.0`, `accelerate 0.26.1`, `mediapipe 0.10.11`, `insightface 0.7.3`, `onnxruntime-gpu 1.21.0`
- 磁盘占用：`/data/app/latentsync` 约 `4.9GB`，`/data/miniconda3/envs/latentsync16` 约 `8.0GB`

安装命令核心：

```bash
cd /data/app
mkdir -p /data/app/latentsync_download
cd /data/app/latentsync_download
curl -4 -L --connect-timeout 20 --max-time 900 --retry 1 \
  -o LatentSync-main.zip \
  https://codeload.github.com/bytedance/LatentSync/zip/refs/heads/main

rm -rf /data/app/latentsync LatentSync-main
unzip -q LatentSync-main.zip
mv LatentSync-main /data/app/latentsync

/data/miniconda3/bin/conda create -y -n latentsync16 -c conda-forge python=3.10.13 ffmpeg
cd /data/app/latentsync
/data/miniconda3/envs/latentsync16/bin/python -m pip install --no-input --timeout 120 --retries 10 -r requirements.txt
```

依赖安装备注：
- `numpy==1.26.4` 首次从 PyPI CDN 下载时多次超时，已先单独用清华镜像和长超时安装，再重跑 `requirements.txt`。
- `insightface==0.7.3` 已在服务器本地成功构建 wheel。
- 安装只写入 `/data/miniconda3/envs/latentsync16` 和用户 pip cache，没有触碰系统包。

权重下载：
- 官方 `huggingface.co` 在服务器当前网络下不可达，表现为 `Connection refused` 或 `Network is unreachable`。
- `hf-mirror.com` 可访问，因此使用 `HF_ENDPOINT=https://hf-mirror.com` 或镜像 URL 下载。
- Whisper 权重已下载：`/data/app/latentsync/checkpoints/whisper/tiny.pt`, `75572083 bytes`
- LatentSync 1.6 主模型已下载：`/data/app/latentsync/checkpoints/latentsync_unet.pt`, `5072222488 bytes`
- 主模型 SHA256：`0a478e89eb660f82da4c35dbdde8a5adfb27f99d1b4e50edd03729e1e98316d3`
- 主模型下载耗时约 `33m54s`，平均速度约 `2.38 MB/s`

主模型下载命令：

```bash
cd /data/app/latentsync
mkdir -p checkpoints
ionice -c2 -n7 nice -n 10 wget -c \
  --tries=30 \
  --timeout=30 \
  --read-timeout=180 \
  --progress=dot:giga \
  -O checkpoints/latentsync_unet.pt \
  https://hf-mirror.com/ByteDance/LatentSync-1.6/resolve/main/latentsync_unet.pt
```

部署后的轻量验证命令：

```bash
cd /data/app/latentsync
CUDA_VISIBLE_DEVICES="" /data/miniconda3/envs/latentsync16/bin/python -c "import torch, diffusers, transformers, accelerate, cv2, insightface, onnxruntime; print('imports ok'); print('torch', torch.__version__, torch.version.cuda)"
find checkpoints -maxdepth 3 -type f -printf '%p %s bytes\n' | sort
```

已验证输出：

```text
python imports ok
CUDA_VISIBLE_DEVICES=
torch 2.5.1+cu121
torch cuda 12.1
cuda available False
diffusers 0.32.2
transformers 4.48.0
accelerate 0.26.1
cv2 4.11.0
onnxruntime 1.21.0
ffmpeg version 8.1.1
checkpoints/latentsync_unet.pt 5072222488 bytes
checkpoints/whisper/tiny.pt 75572083 bytes
```

说明：
- 轻量验证时显式设置 `CUDA_VISIBLE_DEVICES=""`，因此 `cuda available False` 是预期结果，表示没有触发 GPU 初始化。
- 该验证只证明 Python 依赖、ffmpeg、权重文件路径齐全；尚未运行 LatentSync 推理。
- `albumentations` import 时尝试联网检查版本并超时，表现为 warning，不影响核心依赖导入。

推理前必须先检查：

```bash
nvidia-smi
```

LatentSync 1.6 官方 README 标注推理最低约 `18GB` VRAM；当前服务器已有 Ollama、VC、OCR、face 服务占用 GPU。没有空出单卡约 18GB 前，不应启动 LatentSync 1.6 推理。

官方推理脚本入口：

```bash
cd /data/app/latentsync
CUDA_VISIBLE_DEVICES=<GPU_ID> /data/miniconda3/envs/latentsync16/bin/python -m scripts.inference \
  --unet_config_path "configs/unet/stage2_512.yaml" \
  --inference_ckpt_path "checkpoints/latentsync_unet.pt" \
  --inference_steps 20 \
  --guidance_scale 1.5 \
  --enable_deepcache \
  --video_path "<input_video.mp4>" \
  --audio_path "<input_audio.wav>" \
  --video_out_path "<output_video.mp4>"
```

部署后只读 GPU 状态，记录于 2026-06-12 16:37 CST 左右：

| GPU | 总显存 MiB | 已用 MiB | 空闲 MiB | 结论 |
| --- | ---: | ---: | ---: | --- |
| 0 | 24564 | 19384 | 4728 | 不可跑 LatentSync 1.6 |
| 1 | 24564 | 13512 | 10600 | 不可跑 LatentSync 1.6 |
| 2 | 24564 | 12302 | 11810 | 不可跑 LatentSync 1.6 |
| 3 | 24564 | 12366 | 11746 | 不可跑 LatentSync 1.6 |

额外观察：
- GPU0 有一个 `/tmp/get_audio/test/OC_SORT` 相关 Python 进程占用约 `1830 MiB`，不是 LatentSync 部署产生的进程。
- LatentSync 1.6 推理前需要用户确认可以临时释放某张 4090，或等待现有任务结束。

周末复查，记录于 2026-06-13 11:26 CST：

| GPU | 总显存 MiB | 已用 MiB | 空闲 MiB | GPU 利用率 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 24564 | 17548 | 6564 | 0% | 不可跑 LatentSync 1.6 |
| 1 | 24564 | 13512 | 10600 | 0% | 不可跑 LatentSync 1.6 |
| 2 | 24564 | 12302 | 11810 | 0% | 不可跑 LatentSync 1.6 |
| 3 | 24564 | 12366 | 11746 | 0% | 不可跑 LatentSync 1.6 |

周末复查结论：
- 虽然 GPU 利用率为 0%，但显存仍被长期进程占用。
- GPU1/2/3 主要显存占用仍来自 `ollama_llama_server`。
- GPU0 仍由 `vc`、`py39_face`、`gsocr` 等服务占用。
- 仍没有单卡达到 LatentSync 1.6 推理所需的约 `18GB` 空闲显存。
- 如果要跑 LatentSync 1.6，需要用户确认可临时释放某张 GPU，例如暂停占用 GPU1/2/3 的 Ollama 服务或等待其释放。

## 参考来源

- MuseTalk: https://github.com/TMElyralab/MuseTalk
- LatentSync: https://github.com/bytedance/LatentSync
- Wav2Lip: https://github.com/Rudrabha/Wav2Lip
