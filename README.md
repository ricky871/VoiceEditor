# VoiceEditor (基于 IndexTTS2)

`VoiceEditor` 是一个全自动、交互式的视频配音与语音重构工具。它集成了视频下载、语音转写、零样本 (Zero-Shot) 语音克隆及音画自动同步算法，旨在为视频创作者提供一键式的本地化配音方案。

---

## ✨ 核心特性

- **🚀 极简工作流**：从视频 URL 到成品配音视频，仅需一行命令。
- **🎙️ 零样本语音克隆**：基于 **IndexTTS2** 引擎，仅需 10 秒参考音频即可复刻原片音色。
- **📝 交互式校对**：自动生成字幕并暂停，允许人工修正转写错误后继续合成。
- **⏳ 智能对齐**：采用 `Time Stretching` (时延补偿) 算法，确保配音时长与原片口型/字幕轴严格对齐。
- **🌐 环境隔离**：基于 `uv` 构建，无需担心 Python 环境污染，支持国内外镜像一键加速。

---

## 🛠️ 前提条件

本项目使用 [uv](https://github.com/astral-sh/uv) 管理依赖。

### 安装 uv
**方法一（推荐）：**
- **Windows**: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Linux/macOS**: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**方法二（如果安装脚本被拦截）：**
如果您所在的网络无法访问 `astral.sh` 或 GitHub，可以直接通过 pip 安装（需确保已配置国内 PyPI 镜像）：
```bash
pip install uv
```
或者手动下载单文件二进制包放到 PATH 环境变量路径中。

---

## 🚦 快速开始

### 1. 环境初始化
针对中国大陆用户，默认使用 ModelScope 和 TUNA 镜像：
```bash
uv run main.py setup --cn
```
*此操作将完成：安装 Python 3.10、同步依赖、下载约 5GB 的 IndexTTS2 模型权重。*

### 2. 执行全流程配音
提供一个 Bilibili、YouTube 或其他 `yt-dlp` 支持的链接：
```bash
uv run main.py run --url "https://www.bilibili.com/video/BV1px411A7m3" --stitch
```
**运行过程中的关键交互：**
1. **自动下载与转写**：生成 SRT 字幕。
2. **人工干预**：程序会自动调起系统默认编辑器打开 SRT。
3. **编辑并保存**：在编辑器中修正文字，保存并**关闭**编辑器。
4. **触发合成**：返回终端按 `ENTER`，程序将根据修正后的文字进行语音克隆与视频封装。

---

## 📖 命令行参数详解

### `main.py setup`
| 参数 | 说明 |
| :--- | :--- |
| `--cn` | 启用国内镜像 (ModelScope/TUNA)。**默认开启。** |
| `--skip-download` | 仅更新环境依赖，跳过模型下载。 |

### `main.py run`
| 参数 | 说明 |
| :--- | :--- |
| `--url` | **(必选)** 视频链接或本地视频路径。 |
| `--work-dir` | 中间产物目录，默认 `./work`。 |
| `--whisper-model` | 转写模型等级 (`tiny`, `small`, `base`, `medium`, `large-v3`)，默认 `small`。 |
| `--lang` | 目标语言代码，默认 `zh`。 |
| `--stitch` | 是否自动将生成的音频片段合成到原视频中。 |
| `--emo-text` | 情感引导词，例如 `[happy]` 或 `[fast-paced]`（依赖模型支持）。 |

---

## 📁 目录结构

- [main.py](main.py): 项目唯一入口。
- [src/](src/): 核心业务模块（包含环境准备、视频处理、TTS 生成、音频合并）。
- [index-tts/](index-tts/): 核心算法引擎。
- [checkpoints/](checkpoints/): 模型权重存储位。
- [work/](work/): 存放生成的音频片段、SRT 字幕及最终视频。

---

## 🤝 贡献与反馈

- **技术细节**：请参阅 [DEVELOPMENT.md](DEVELOPMENT.md) 了解对齐算法与系统架构。
- **模型说明**：核心算法基于 [IndexTeam/IndexTTS-2](https://github.com/IndexTeam/IndexTTS-2)。

---
*Powered by IndexTTS2 & OpenAI Whisper*




