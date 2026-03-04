# VoiceEditor (powered by IndexTTS2)

`VoiceEditor` 是一个全自动、交互式的视频配音与语音重构工具。它集成了视频下载、语音转写、零样本 (Zero-Shot) 语音克隆及音画自动同步算法，旨在为视频创作者提供一键式的本地化配音方案。

---

## ✨ 核心特性

- **🚀 极简工作流**：支持视频 URL（Bilibili/YouTube）或本地文件，一行命令完成全流程。
- **🎙️ 零样本语音克隆**：基于 **IndexTTS2** 引擎，仅需 10 秒参考音频即可精准复刻原片音色。
- **📝 交互式校对**：自动生成字幕并智能弹出编辑器，允许人工修正文字/时间轴后继续合成。
- **⏳ 智能对齐**：采用 `Time Stretching` (时延补偿) 算法与 FFmpeg 变速不变调滤镜，确保音画严丝合缝。
- **⚡ 硬件加速**：原生支持 CUDA, MPS (Mac), XPU (Intel) 及 CPU 推理；内置 OpenVINO 模型适配（开发中）。
- **🌐 一键环境搭建**：基于 `uv` 构建，自动补齐 Python 环境及 5GB+ 模型权重，国内镜像全加速。

---

## 🛠️ 环境准备

在开始之前，请确保您的系统中已安装以下前置软件：

### 1. 安装 FFmpeg (核心音视频处理)
- **Windows**: 推荐使用 [Scoop](https://scoop.sh/) 安装：`scoop install ffmpeg`。或从 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下载解压，并将 `bin` 目录手动添加到系统环境变量 `PATH`。
- **Linux**: 使用包管理器安装，例如 Ubuntu/Debian: `sudo apt update && sudo apt install ffmpeg`。

### 2. 安装 uv (包与环境管理)
本项目强制使用 [uv](https://github.com/astral-sh/uv) 进行管理，不再建议手动配置虚拟环境。
- **Windows**: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Linux/macOS**: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 3. 初始化环境与权重
针对中国大陆用户，默认已启用 ModelScope 和 TUNA 镜像：
```bash
uv run main.py setup
```
*该步将安装 Python 3.11 并自动下载所有必需的模型 Checkpoints。*

---

## 🚦 快速开始

### 方式 A: Web GUI (推荐)
提供更加直观的交互界面，支持实时查看日志和进度。
```bash
# 启动 Web 服务 (默认端口 8080)
uv run main_gui.py

# 指定端口与 IP (支持远程访问)
uv run main_gui.py --port 8196 --host 0.0.0.0
```

### 方式 B: 命令行 (CLI)
直接输入视频链接（支持 Bilibili、YouTube 或本地路径）：
```bash
# 极简模式 (默认启用 stitch 合成)
uv run main.py "https://www.bilibili.com/video/BV1qctczUEfn"
```

---

## 🐧 Linux 部署与自动运行

本项目为 Linux 环境提供了完整的一键安装与 Systemd 服务脚本：

### 1. 一键安装
```bash
chmod +x install.sh
./install.sh
```

### 2. 开机自动启动 (Systemd)
如果您希望在服务器上长期运行 Web GUI 并随系统启动：
```bash
chmod +x deploy_service.sh
sudo ./deploy_service.sh
```
部署成功后，可通过 `http://<服务器IP>:8080` 访问。

- **查看服务状态**: `sudo systemctl status voiceeditor`
- **查看实时日志**: `journalctl -u voiceeditor -f`

---

## 📖 命令行参数详解

### `main.py run` (主任务)
| 参数 | 简写 | 说明 | 默认值 |
| :--- | :--- | :--- | :--- |
| `url` | (位置参数) | 视频链接 (Youtube/Bili) 或本地文件路径 | **(必选)** |
| `--output` | `-o` | 最终合成视频的输出路径 | `work/output.mp4` |
| `--work-dir` | - | 中间产物目录 | `work` |
| `--stitch` | - | 是否将生成的音频合回视频 | `True` |
| `--lang` | - | 目标语言代码 (如 `zh`, `en`) | `zh` |
| `--diffusion-steps`| - | TTS 采样步数 (越高质越好，越慢) | `25` |
| `--whisper-model` | - | 转写模型 (`base`, `small`, `medium`, `large-v3`) | `small` |
| `--emo-text` | - | 情感引导词，例如 `[happy]` 或 `[fast-paced]` | `""` |
| `--verbose` | `-v` | 显示详细推理进度与日志 | `False` |

---

## 📁 项目结构

- [main.py](main.py): 统一入口，支持 `setup` 和 `run` 子命令。
- [src/](src/): 核心逻辑（资源管理、视频流水线、TTS 推理、FFmpeg 合流）。
- [index-tts/](index-tts/): 算法后端 (Submodule)。
- [checkpoints/](checkpoints/): 模型权重与 OpenVINO XML 定义。
- [work/](work/): 临时产物目录，存放生成的音频片段与中间 SRT。

---

## 🤝 开发者参考

若需了解对齐算法实现或其他技术细节，请参阅 [DEVELOPMENT.md](DEVELOPMENT.md)。

*Powered by IndexTTS2, OpenAI Whisper & uv*




