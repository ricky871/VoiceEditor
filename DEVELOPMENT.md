# VoiceEditor 技术架构与开发指南

本文档面向维护者，描述当前代码实现（CLI + GUI）的职责边界、核心数据流、关键算法和改动注意事项。

## 1) 设计目标

- 在本地完成“视频 → 转写 → 配音 → 合流”的闭环流程。
- 对长耗时任务保持可中断、可恢复。
- 尽量保证 Windows / Linux 行为一致。
- **文档维护约束**：严禁在根目录创建新的 `.md` 文件。所有更新必须集成至现有的 5 个核心文档（README, DEVELOPMENT, PROJECT_STATUS, TEST_COVERAGE, TROUBLESHOOTING）。

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
   - 选取参考音 `style_ref.wav`
3. 用户编辑 SRT（CLI 打开外部编辑器 / GUI 内嵌编辑）。
4. `src/tts_generator.py`
   - 解析 SRT
   - 按句推理 TTS
   - 对齐到目标时长
   - 写出 `segments.json` (清单文件)
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

## 6) 远程测试与同步 (Remote Testing)

为了在不同操作系统（如 Debian 12）上验证功能，项目提供了一个自动化同步与远程执行工具 `scripts/remote_tester.py`。

### 核心功能
- **增量同步**：基于 SFTP 比较文件大小和修改时间，仅上传变更文件。
- **环境隔离**：远程执行 `install.sh` 以确保依赖一致。
- **远程测试**：在远程主机上运行 `pytest` 并将结果回传。

### 使用方法
1. **配置**：在 `configs/remote_hosts.yaml` 中添加主机信息（该文件已加入 `.gitignore`）。
   ```yaml
   hosts:
     - name: ricky
       ip: 10.245.54.160
       user: ricky
       ssh_key: "C:/path/to/id_ed25519"
       password: "your_password"
       remote_dir: "/home/ricky/VoiceEditor"
   ```
2. **连接检查**：
   ```bash
   uv run scripts/remote_tester.py --check
   ```
3. **同步并执行测试**：
   ```bash
   uv run scripts/remote_tester.py --sync --test
   ```

### 注意事项
- 默认排除 `.git`, `.venv`, `__pycache__`, `work`, `outputs` 等目录。
- 首次同步 `checkpoints/` 可能较慢，后续同步为增量模式。

---

## 7) 关键数据结构

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

## 12) AI 执行规范（自动化开发指南）

### 修改前检查清单

在做任何代码变更前，必须确认：

- [ ] 问题明确定义（功能、条件、期望输出）
- [ ] 影响范围已评估（涉及的模块和入口）
- [ ] 约束条件已理解（跨平台、性能、向后兼容）
- [ ] 现有测试已审查（是否有冲突或需要更新）
- [ ] 验证方式已明确（如何证明修复有效）

### 修改后验证清单（必须执行）

```bash
# 1. 语法检查
python -m py_compile src/modified_module.py

# 2. 导入测试
python -c "import src.modified_module; print('OK')"

# 3. 相关单元测试
uv run pytest tests/test_related.py -xvs

# 4. 帮助文本和基本功能
uv run python main.py --help
uv run python main_gui.py --help
```

### 约束条件

**不允许的修改**:
- ❌ 改变 public API（函数签名、返回值类型）
- ❌ 删除已存在的参数或配置字段
- ❌ 直接修改 `index-tts/` 子模块
- ❌ 引入新的外部依赖（需先审查）
- ❌ 无相关测试的大规模重构

**允许的修改**:
- ✅ 添加内部函数或 private 方法
- ✅ 增加参数的默认值处理
- ✅ 完善错误处理和日志
- ✅ 添加测试用例
- ✅ 修改非 public 方法的实现

### 常见错误排除

| 错误 | 原因 | 排除步骤 |
|------|------|---------|
| `ModuleNotFoundError` | 缺少依赖 | 运行 `uv sync` 重新安装 |
| `FileNotFoundError` | 路径错误 | 检查 `Path()` 和平台差异 |
| `CUDA out of memory` | GPU 显存不足 | 降低参数或使用 CPU 模式 |
| `subprocess error` | FFmpeg 缺失 | 安装 FFmpeg 或检查 PATH |
| 测试失败 | 代码逻辑错误 | 运行 `pytest -xvs` 查看详细输出 |

### 任务优先级评估

按以下优先级处理问题：

1. **关键问题** (阻断功能发布) → 立即处理
2. **高优先级** (影响稳定性) → 本周处理  
3. **中优先级** (改善体验) → 本月处理
4. **低优先级** (优化) → 后续处理

详见 [CODE_REVIEW_STATUS.md](CODE_REVIEW_STATUS.md) 了解当前状态。

---

*Last Updated: 2026-04-09*
