# VoiceEditor (powered by IndexTTS2)

VoiceEditor 是一个基于 IndexTTS2 的视频配音与语音重构工具，提供 CLI 与 NiceGUI 两种使用方式，覆盖：视频获取、语音转写、参考音提取、分句合成、音视频合流。

## 📚 文档导航

- **[README.md](README.md)** - 项目介绍和使用指南（你在这里）
- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** - 完整项目状态和问题解决进展
- **[TEST_COVERAGE.md](TEST_COVERAGE.md)** - 测试覆盖详情和改进规划
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - 架构设计和开发指南
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - 常见问题和故障排查

> 开发者: 请先阅读 [DEVELOPMENT.md](DEVELOPMENT.md)  
> 测试人员: 请先阅读 [TEST_COVERAGE.md](TEST_COVERAGE.md)  
> 用户/运维: 请先阅读 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## ✨ 核心能力

- 自动化流程：从视频到最终配音视频的一站式流水线。
- 字幕可编辑：转写后可手工修正字幕，再进入合成。
- 时长强对齐：按字幕时间窗对每句音频进行对齐，降低口型错位。
- 可恢复执行：合成阶段会持续写出 `manifest.json`，便于失败后继续。
- 跨平台运行：主要支持 Windows / Linux。

## 🧱 环境要求

- Python：`>=3.11, <3.12`
- 包管理：`uv`
- 系统依赖：`ffmpeg`（必须在 PATH 中）
- 运行设备：CPU 可运行，CUDA 可显著加速

## 🚀 快速开始

### 1) 初始化依赖与模型

```bash
uv run .\main.py setup
```

> 首次执行会同步依赖并下载较大模型文件，请预留时间与磁盘空间。

### 2) 启动 Web GUI（推荐）

```bash
uv run .\main_gui.py --host 0.0.0.0 --port 8196
```

- 程序会在启动时自动检测端口，若端口被占用，会自动尝试后续可用端口。
- 浏览器打开后按页面步骤执行：
  1. 开始处理（下载/转写/提取参考音）
  2. 编辑字幕
  3. 开始合成

### 3) CLI 使用

#### 3.1 URL 快捷模式

```bash
uv run .\main.py "https://www.bilibili.com/video/BVxxxx"
```

> 快捷模式仅在首参数以 `http://`、`https://` 或 `BV` 开头时触发。

#### 3.2 标准模式（推荐）

```bash
uv run .\main.py run --url "https://www.youtube.com/watch?v=xxxx" --work-dir work
```

也支持位置参数形式：

```bash
uv run .\main.py run "https://www.youtube.com/watch?v=xxxx"
```

显示详细日志：

```bash
uv run .\main.py --verbose run --url "https://www.youtube.com/watch?v=xxxx"
```

## ⚙️ `main.py run` 参数

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `--url` / `pos_url` | 视频来源（URL 或可被 yt-dlp 解析的输入） | 必填其一 |
| `--work-dir` | 工作目录 | `work` |
| `--output` | 最终视频输出路径 | 空（自动生成） |
| `--whisper-model` | Whisper 模型大小 | `small` |
| `--lang` | 语言参数（转写/合成链路） | `zh` |
| `--emo-text` | 情绪提示词 | `""` |
| `--diffusion-steps` | TTS diffusion 步数 | `25` |
| `--stitch` | 启用最终音频拼接与合流 | 当前实现默认开启 |
| `--cn` | 中国镜像相关参数（保留） | `True` |

### 输出路径规则

- 未传 `--output` 时，默认输出为：`<work-dir>/<输入视频名>_dubbed.<原后缀>`。
- 合成中间产物在 `work/segments/`。

## 📁 主要产物

- `work/*.srt`：转写字幕（可编辑）
- `work/style_ref.wav`：提取的音色参考（由 IndexTTS2 使用）
- `work/segments/seg_0001.wav ...`：分句合成音频
- `work/segments/segments.json`：分句清单（用于恢复和统计）
- `work/audio_dubbed.wav`：合并后的整轨音频
- `work/*_dubbed.mp4`（或 `--output` 指定路径）：最终视频

## ♻️ 可恢复与缓存行为

- 若已有字幕或参考音，流程会优先复用缓存。
- 合成时若发现已有片段且文本一致，会跳过重复推理。
- `manifest.json` 采用增量写入（每 5 句及结束时保存一次）。
- 中断后可重新运行，已完成片段通常可继续复用。

## 🐧 Linux 部署（可选）

### 一键安装

```bash
chmod +x install.sh
./install.sh
```

### 配置 systemd 服务

```bash
chmod +x deploy_service.sh
sudo ./deploy_service.sh
```

查看状态与日志：

```bash
sudo systemctl status voiceeditor
journalctl -u voiceeditor -f
```

## 🛠️ 常见问题

### 1) `ffmpeg` 不存在

- 现象：抽取音频或合流失败。
- 处理：安装 ffmpeg 并确保 `ffmpeg -version` 可执行。

### 2) GUI 启动失败或立即退出

- 先检查参数是否正常：

```bash
uv run .\main_gui.py --help
```

- 再检查依赖是否完整：

```bash
uv sync
```

### 3) 首次运行很慢

- 首次会下载/初始化模型，属于预期行为。

### 4) 合成失败但已有部分输出

- 检查 `work/segments/segments.json` 与分段文件是否存在。
- 重新执行通常可复用已完成片段。

## 🤝 开发文档

详细架构、数据结构和模块职责请见 [DEVELOPMENT.md](DEVELOPMENT.md)。
