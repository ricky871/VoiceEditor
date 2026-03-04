# VoiceEditor Copilot Instructions

## 1) 项目定位与目标
- 本项目是一个基于 `IndexTTS2` 的视频配音/语音重构工具，支持 CLI 与 NiceGUI Web 界面。
- 代码修改应优先保证：稳定性、可恢复性（中断后可继续）、跨平台兼容（Windows/Linux）。

## 2) 技术栈与运行约束
- Python 版本：`>=3.11, <3.12`（见 `pyproject.toml`）。
- 包管理与运行：统一使用 `uv`，不要引入与 `uv` 冲突的环境管理流程。
- 主要依赖：`nicegui`、`faster-whisper`、`pysrt`、`pydub`、`torch`、`indextts`。

## 3) 目录职责（改动时请遵守边界）
- `main.py`：CLI 入口与任务编排（setup/run）。
- `main_gui.py`：Web UI 流程控制、状态管理、异步任务触发。
- `src/video_handler.py`：视频下载、音频提取、转写前处理。
- `src/tts_generator.py` 与 `src/tts/`：字幕解析、分句合成、时长对齐、清单输出。
- `src/audio_merger.py`：音视频合流与字幕烧录。
- `src/resource_manager.py`：工作目录与中间产物管理。
- `ui/`：UI 状态与主题。
- `index-tts/`：上游算法子模块，除非用户明确要求，否则不要修改。
- `checkpoints/`：模型与权重目录，绝不在代码任务中改写或重生成权重文件。

## 4) 编码风格与实现偏好
- 优先使用 `pathlib.Path` 处理路径，不要混用大量手写字符串拼接。
- 保持现有类型标注风格（`list[dict]`、`Path | None` 等）与函数职责粒度。
- 日志优先：使用 `logging` 输出流程信息与错误；避免新增无必要 `print`（CLI 的用户交互提示除外）。
- 修改应最小化且聚焦根因，不做无关重构。
- 新增配置优先走现有参数链路（CLI 参数 / AppState / 函数入参），避免隐藏常量。

## 5) 平台兼容与媒体处理注意事项
- Windows 路径在 FFmpeg 滤镜场景下需考虑转义（例如字幕路径中的冒号）。
- 保持“变速不变调”对齐策略，不要引入会明显改变音高的默认处理。
- 对长流程任务（下载、转写、合成）保留可中断/可恢复行为。

## 6) GUI 相关约束（`main_gui.py`）
- 保持事件驱动和异步边界：耗时任务放到 `run.io_bound(...)`。
- 不要阻塞 UI 主线程；进度条、状态文本与日志需要同步更新。
- 涉及文件访问时，必须限制在工作目录上下文，避免路径越界。

## 7) 测试与验证优先级
- 先做最小验证：仅运行与改动直接相关的命令。
- 常用验证命令：
  - `uv run .\main.py --help`
  - `uv run .\main_gui.py --port 8196`
  - 针对 SRT 解析可用小脚本验证 `SRTProcessor.parse(...)`。
- 若改动影响 TTS/对齐逻辑，至少验证：
  - 能生成 `work/out_segs/manifest.json`
  - 分段音频命名与条目数一致
  - 不破坏 `--stitch` 合流路径

## 8) 提交代码时的结果要求
- 明确说明修改了哪些文件、为什么修改、如何验证。
- 若存在已知限制（如模型未下载、缺少 FFmpeg、端口占用），要在说明中给出可执行的下一步。
- 不要虚构运行结果；无法在当前环境验证时要显式说明。
