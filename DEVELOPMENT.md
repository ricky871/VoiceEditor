# VoiceEditor 技术架构与开发指南

本文档面向开发者，深入解析 `VoiceEditor` 的核心逻辑、算法实现及管线流转机制。

## 1. 系统架构全景

`VoiceEditor` 设计为典型的“三层架构”：
1. **交互逻辑层 (main.py)**：负责参数解析、环境自检、SRT 手工干预逻辑及各阶段任务编排。
2. **核心业务层 (src/)**：封装了视频下载、音频分析、TTS 片段推理的关键算法。
3. **算法引擎层 (index-tts/)**：基于 `IndexTTS2` 的 GPT 模型底层推理接口。

---

## 2. 核心算法解析

### A. 智能参考音提取 (`src/video_handler.py`)
程序并非随机提取音频，而是通过 **RMS (均方根) 能量检测** 在原音轨中寻找人声段：
- 计算步长为 0.5s 的能量图。
- 排除极端静音区，过滤掉低信噪比片段。
- 最终选取一个连续 $10s$ 且能量最平稳的片段作为 TTS 的参考音色 (Ref Voice)，以最大程度降低环境噪音对克隆质量的干扰。

### B. 时长补偿对齐算法 (`src/tts_generator.py`)
这是解决“音画不同步”的核心逻辑。IndexTTS2 的生成时长是概率性的，必须进行确定性对齐：
1. **生成长度预测**：根据 SRT 每行时长 $T_{target}$，按 $150 Tokens/sec$ 计算期望生成的 Token 数。
2. **偏差检测**：计算生成音频时长 $T_{actual}$ 与 $T_{target}$ 的比例 $R = T_{actual} / T_{target}$。
3. **非线性微调 (Retiming)**：
    - 若偏差 $\le 2\%$: 采用简单的 Padding (加噪补齐) 或 Truncation (截断)。
    - 若偏差 $> 2\%$: 使用 `pydub.speedup` 与 `atempo` 滤镜进行**变速不变调**的精细收缩/拉伸。

### C. 环境沙盒化 (`src/setup_env.py`)
基于 `uv sync` 实现零配置启动，自动注入 `HF_ENDPOINT` 环境变量以适配国内开发者。

---

## 3. 详细处理流程 (COT Stage)

`src/tts_generator.py` 内部遵循 10 阶段链式思考模型：
1. **Stage 1-3**: 环境自检与 GPU 分配。
2. **Stage 4**: 模型加载（自适应加载 `gpt.pth`, `s2mel.pth`）。
3. **Stage 5**: SRT 编解码处理。
4. **Stage 6**: **分段并发推理**：遍历字幕行，生成波形。
5. **Stage 7**: 生成 `manifest.json` 记录所有对齐元数据。
6. **Stage 8-9**: **Stitch & Mux**：音频无缝拼接与 FFmpeg 硬件加速合流。
7. **Stage 10**: 度量指标展示与缓存清理。

---

## 4. 跨平台兼容性说明

- **FFmpeg 滤镜**：在 Windows 下，SRT 字幕烧录 (`vf subtitles`) 的路径处理复杂。本项目在 `audio_merger.py` 中实现了专用的路径转义逻辑，通过 `replace(":", "\\:")` 解决了 Windows 下盘符冒号在 FFmpeg 滤镜字符串中的冲突问题。
- **编码处理**：全链路采用 `UTF-8` 编码，支持多语言视频及字幕转写。

---

## 5. 开发路线图 (Roadmap)

- [ ] **BGM 分离 (Vocal Removal)**：目前采用的是全音轨替换。后续计划集成 `UVR5/Demucs` 模型，提取背景音乐并与新配音混缩。
- [ ] **并行推理优化**：长视频支持拆分多块并行处理，提升生成吞吐量。
- [ ] **实时预览界面**：在编辑器修正 SRT 的同时，提供单句配音效果反馈。

---
*Last Updated: February 2026*
