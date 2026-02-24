# VoiceEditor 使用说明 (重塑版)

`VoiceEditor` 是一个基于 **IndexTTS2** 引擎的全自动短视频配音与语音重构工具，旨在实现从视频下载到成品配音的一键式流转。

---

## � 前提条件

本项目依赖 `uv` 进行包管理和环境调度。如果您的系统中尚未安装 `uv`，请执行以下命令：

### Windows
```pwsh
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Linux / macOS
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## �🚀 快速开始

本项目采用 `uv` 进行环境管理，无需手动配置 Python 环境。

### 1) 环境一键部署
如果您在中国大陆使用，请务必使用 `--cn` 参数以加速模型（ModelScope）和依赖（TUNA）的下载：
```pwsh
# 自动安装 Python 3.10、依赖库，并下载约 5GB 的模型权重
uv run main.py setup --cn
```

### 2) 一键全流程处理
提供一个视频 URL（支持 Bilibili, YouTube 等主流平台）：
```pwsh
# 自动执行：下载 -> 转写 -> 提取参考音 -> 语音克隆 -> 视频混流
uv run main.py run --url "https://www.bilibili.com/video/BV1..." --cn --stitch
```

---

## 🛠️ 命令详细说明

### `main.py setup`
*环境初始化与资源准备*
- `--cn`: 启用国内镜像加速（ModelScope 模型站 + 清华 PyPI 源）。
- `--skip-download`: 仅更新代码依赖，跳过模型权重检查。

### `main.py run`
*核心配音流水线*
- `--url`: 目标视频链接。
- `--emo-text`: 情感 Prompt，例如 `[happy]`、`[whispering]`（取决于 IndexTTS2 情感支持）。
- `--whisper-model`: 字幕转写模型（默认 `small`，追求精度可改为 `large-v3`）。
- `--stitch`: 合并所有生成片段并产出最终视频（默认输出至 `work/`）。
- `--lang`: 目标语种（默认 `zh`）。

---

## 📁 项目结构

- **[src/](src/)**: 重构后的 Python 业务逻辑模块。
- **[work/](work/)**: 默认工作目录及中间产物。
- **[checkpoints/](checkpoints/)**: 存储下载的 TTS 模型权重。
- **[DEVELOPMENT.md](DEVELOPMENT.md)**: 包含时长控制算法与跨平台实现的深度技术文档。

---

## ❓ 常见问题

- **FFmpeg 报错**：请确保系统已安装 `ffmpeg` 并已加入环境变量 `PATH`。
- **下载模型缓慢**：请确保在执行 `setup` 时带上了 `--cn` 参数，这将切换至阿里云 ModelScope 镜像。
- **显存不足**：建议至少 8GB 显存。若显存较低，可在 `main.py` 中尝试减小 `whisper-model` 大小。

---
*Powered by [IndexTTS2](index-tts/README.md) & OpenAI Whisper*

