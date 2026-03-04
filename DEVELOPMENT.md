# VoiceEditor 技术架构与开发指南

本文档面向维护者，描述当前代码实现（CLI + GUI）的职责边界、核心数据流、关键算法和改动注意事项。

## 1) 设计目标

- 在本地完成“视频 → 转写 → 配音 → 合流”的闭环流程。
- 对长耗时任务保持可中断、可恢复。
- 尽量保证 Windows / Linux 行为一致。

---

## 2) 入口与职责

### `main.py`（CLI）

- 提供 `setup` / `run` 两个子命令。
- `setup`：调用 `src/setup_env.py` 同步依赖并下载模型。
- `run`：执行完整流水线（视频处理 → 手工字幕编辑 → TTS 合成 → 合流）。
- 支持 URL 快捷模式：当第一个参数以 `http://` / `https://` / `BV` 开头时，自动改写为 `run --url ...`。

### `main_gui.py`（NiceGUI）

- 负责 UI 状态机、日志显示、字幕编辑与任务控制。
- 视频处理阶段通过 `run.io_bound(...)` 执行，避免阻塞 UI 主线程。
- 合成阶段通过异步子进程执行 `src/tts_generator.py`，逐行抓取日志并回显。
- 启动时会自动寻找空闲端口。

---

## 3) 端到端流程

1. 输入视频来源（主要是 URL）。
2. `src/video_handler.py`
   - 下载/定位视频
   - 提取 WAV 音频
   - faster-whisper 转写 SRT
   - 选取参考音 `*_voice.wav`
3. 用户编辑 SRT（CLI 打开外部编辑器 / GUI 内嵌编辑）。
4. `src/tts_generator.py`
   - 解析 SRT
   - 按句推理 TTS
   - 对齐到目标时长
   - 写出 `manifest.json`
5. `src/tts/audio_pipeline.py`
   - 根据清单拼接整轨音频
   - 使用 ffmpeg 合流到视频

---

## 4) 模块边界

### `src/video_handler.py`

- `VideoEngine.download_video`：使用 `yt_dlp` 获取视频。
- `extract_audio`：ffmpeg 转单声道 44.1k WAV。
- `transcribe`：faster-whisper 生成 SRT，并使用 OpenCC 做繁转简。
- `extract_voice_ref`：从音频中搜索高能量且不过载片段，导出参考音。

### `src/tts/processor.py`

- `SRTProcessor.parse`：解析字幕为结构化条目。
- `TTSSynthesizer.synthesize`：逐句合成、缓存复用、失败记录、清单增量保存。

### `src/tts/audio_pipeline.py`

- `retime_segment_to_target`：将句子音频严格贴合字幕时长。
- `stitch_segments_from_manifest`：按 `start_ms` 叠加分句得到整轨。
- `mux_audio_video`：ffmpeg 将整轨音频合流到视频。

### `src/resource_manager.py`

- 统一创建工作目录、输出目录和文件路径。

---

## 5) 关键数据结构

### `video_data`（视频处理输出）

```json
{
  "video": "...",
  "audio": "...",
  "srt": "...",
  "voice_ref": "...",
  "duration": 123.45
}
```

### `SRTProcessor.parse(...)` 条目结构

```json
{
  "id": 1,
  "text": "字幕文本",
  "start_ms": 1000,
  "end_ms": 2500,
  "dur_ms": 1500
}
```

### `manifest.json` 条目结构（合成后）

```json
{
  "id": 1,
  "text": "字幕文本",
  "start_ms": 1000,
  "end_ms": 2500,
  "wav": ".../seg_0001.wav",
  "dur_target_ms": 1500,
  "dur_actual_ms": 1500,
  "diff_ms": 0,
  "speed_factor": 1.0
}
```

---

## 6) 时长对齐策略（当前实现）

位于 `src/tts/audio_pipeline.py`：

- 若生成音频比目标短：补静音（不减速，避免“慢放感”）。
- 若仅轻微超长（约 2% 内或 <50ms）：直接截断。
- 若明显超长：使用 `librosa.effects.time_stretch` 做加速，再做末端微调。
- 最终保证每句长度严格对齐目标时间窗。

---

## 7) 可恢复与中断语义

- `manifest.json` 采用临时文件 + 原子替换写入，降低中断损坏风险。
- 每 5 句（以及最后一次）会执行增量保存。
- 若历史片段存在且文本一致，会复用缓存跳过推理。
- CLI 支持 `KeyboardInterrupt` 退出；GUI 支持停止按钮终止当前任务。

---

## 8) 跨平台与安全注意事项

- ffmpeg 相关路径必须考虑 Windows 兼容（尤其字幕滤镜场景中的冒号转义）。
- GUI 文件浏览器通过路径归属检查，阻止访问工作目录之外的文件。
- 清空工作目录前会进行根目录与边界防护，避免误删项目根路径。

---

## 9) 开发约束（本仓库约定）

- 使用 `pathlib.Path` 处理路径。
- 使用 `logging` 输出运行信息，避免新增无必要 `print`。
- 改动最小化，优先修根因，不做无关重构。
- 优先走现有参数链路（CLI 参数 / AppState / 函数入参）。
- 默认不改 `index-tts/` 与 `checkpoints/`。

---

## 10) 最小验证清单

```bash
uv run .\main.py --help
uv run .\main.py run --help
uv run .\main_gui.py --help
```

SRT 解析快速验证：

```bash
D:/Coding/Python/VoiceEditor/.venv/Scripts/python.exe -c "from pathlib import Path; from src.tts.processor import SRTProcessor; p=Path('work/144_p02_1.srt'); print('exists', p.exists()); print('count', len(SRTProcessor.parse(p)))"
```

若改动影响合成流程，至少验证：

- `work/out_segs/manifest.json` 可生成。
- 分段音频数量与字幕条目数量一致（允许个别失败但需有日志）。
- 合流路径可产出最终视频或可解释的错误日志。

---

## 11) 已知限制（截至当前实现）

- `main.py` 的快捷模式只识别 URL/BV 前缀，不适用于任意本地路径。
- `run` 子命令中的 `--stitch` 当前为兼容参数，实际流程默认执行合并。
- `run` 子命令中的 `--cn` 目前未直接影响主流程行为（主要在 setup 阶段有意义）。

---

*Last Updated: 2026-03-04*
