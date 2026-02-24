本计划旨在将 `VoiceEditor` 项目从一个脚本集合转化为一个结构化的完整开发方案。我们将保留现有的 [README.md](README.md) 作为面向用户的快速开始手册，并新建 [DEVELOPMENT.md](DEVELOPMENT.md) 文档，详细记录系统架构、技术核心算法、管道流程及未来扩展路线图。

**步骤**

1.  **创建核心开发文档 [DEVELOPMENT.md](DEVELOPMENT.md)**：
    *   **项目定位**：定义为基于 **IndexTTS2** 的自动化视频配音与音频重构流程工具，强调其“零样本”语音克隆与“时戳对齐”的独特性。
    *   **系统架构设计**：
        *   **数据层**：解析 `.srt` 字幕、视频元数据，以及使用 `yt-dlp` 获取的原始视频流。
        *   **逻辑层**：
            *   环境自动化管理 ([1. deploy_indextts2.ps1](1.%20deploy_indextts2.ps1))：基于 `uv` 的轻量化依赖隔离。
            *   前置处理与指纹提取 ([2. process_video.ps1](2.%20process_video.ps1))：利用能量最小窗口算法提取 30s 免噪语音参考。
            *   核心推理引擎 ([3. text_to_voice.py](3.%20text_to_voice.py))：详细描述 `tokens_per_sec` 换算逻辑以及 `pydub` 的音频伸缩补偿算法。
        *   **表现层**：`ffmpeg` 驱动的多轨道混流与字幕内嵌逻辑 ([4. merge_out_segs.py](4.%20merge_out_segs.py))。
    *   **关键技术细节**：
        *   **时长控制**：分析如何将毫秒级字幕区间映射到 IndexTTS 的 `max_mel_tokens` 参数。
        *   **情感注入**：记录如何利用 `emo_text` 与 `emo_audio_prompt` 控制合成语气。
    *   **技术栈列表**：IndexTTS2, Python 3.10, OpenAI Whisper, FFmpeg, PowerShell, Gradio (IndexTTS 内置)。

2.  **更新项目蓝图与路线图 (Roadmap)**：
    在 [DEVELOPMENT.md](DEVELOPMENT.md) 中规划后续演进方向：
    *   **并发合成**：引入 `multiprocessing` 或多协程实现字幕段的并行推理。
    *   **流式架构**：将脚本流程转化为基于 API 的长连接服务。
    *   **智能场景感知**：加入背景音分离 (BGM separation) 以便更完美地回填环境音。

3.  **完善 [README.md](README.md) 的参考索引**：
    *   在 [README.md](README.md) 顶部或末尾添加指向 [DEVELOPMENT.md](DEVELOPMENT.md) 的链接，引导开发者深入了解底层逻辑。

**验证**

*   **文档完整性检查**：确保所有关键脚本（1-4号脚本）均有对应的架构说明和逻辑注释。
*   **链接有效性**：检查文档中的文件跳转链接（如指向 [3. text_to_voice.py](3.%20text_to_voice.py)）是否正确。
*   **逻辑自洽**：通过 [DEVELOPMENT.md](DEVELOPMENT.md) 中的描述，开发者应能清晰理解为何需要 `voice_ref.wav` 以及它是如何生成的。

**决策**

*   **文档分离**：选择新建 [DEVELOPMENT.md](DEVELOPMENT.md) 而非直接修改现有的 [README.md](README.md)，以保持用户指南的简洁性与技术文档的深度。
*   **技术侧重**：将重点放在“时长匹配”算法上，因为这是将通用 TTS 转化为“视频编辑器”核心能力的关联点。
