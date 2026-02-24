# VoiceEditor 开发方案与架构说明

本文档详细说明了 `VoiceEditor` 项目的系统架构、核心流程以及各模块的技术实现。

## 1. 项目概览
`VoiceEditor` 是一个基于 **IndexTTS2** 的自动化视频配音与音频重构工具。它能一键完成：下载视频 -> 语音转写字幕 -> 提取参考音 -> 零样本语音克隆 -> 音视频自动混流。

### 核心特性
- **一键式 CLI**：通过 `main.py` 统筹所有子任务。
- **环境隔离**：深度利用 `uv` 进行依赖管理与镜像加速。
- **精准对齐**：通过时戳驱动 TTS 推理，并自动进行音频伸缩补偿（Time Stretching）。
- **跨平台支持**：原生支持 Windows 与 Linux，通过 Python 替代了复杂的 PowerShell 脚本。

## 2. 目录结构
```text
VoiceEditor/
├── main.py                 # 唯一入口，支持 setup 与 run 命令
├── src/                    # 核心业务逻辑
│   ├── setup_env.py        # 环境初始化与模型下载逻辑
│   ├── video_handler.py    # 视频下载、音频提取、Whisper 转写
│   ├── tts_generator.py    # IndexTTS2 推理、时戳匹配
│   └── audio_merger.py     # 音频段拼合与 ffmpeg 混流
├── index-tts/              # 上游算法引擎 (Git Submodule)
├── checkpoints/            # 模型权重存储位
├── .cache/                 # 镜像缓存 (HuggingFace/ModelScope)
└── work/                   # 中间产物与输出目录
```

## 3. 核心管道流程 (Pipeline)

### 阶段 1: 环境初始化 (`setup`)
- **uv 同步**：利用 `uv sync` 在虚拟环境中安装所有依赖。
- **镜像注入**：默认使用清华 TUNA 镜像源加速 Python 包安装。
- **模型下载**：优先使用 **ModelScope** 下载 `IndexTeam/IndexTTS-2` 权重；若失败则切换至 **HF-Mirror**。

### 阶段 2: 视频前处理 (`run`)
- **yt-dlp**：下载高质量 MP4。
- **ffmpeg**：抽取 PCM 16bit 44.1kHz 音轨。
- **OpenAI Whisper**：生成带毫秒级时戳的 SRT 字幕。
- **参考音提取**：基于能量检测（Librosa RMS）寻找原始音轨中最静寂的片段（通常为清晰人声），作为 TTS 的 Prompt。

### 阶段 3: TTS 驱动合成
- **时长匹配策略**：
    1. 将 SRT 每一行的时间差 $T$ 映射到目标 Token 数：$Tokens = T \times 150.0$。
    2. 使用 `max_mel_tokens` 约束生成长度。
    3. 生成后通过 `pydub.speedup` 指数级调整语速（保持音调），确保最终音频贴合字幕轴。
- **情感注入**：支持通过 `--emo-text` (如 "[happy]") 引导合成语气。

### 阶段 4: 后处理与混流
- **Manifest 管理**：记录每一段语音的合成元数据（目标时长 vs 实际时长）。
- **ffmpeg 混合**：
    - 将新音频 (`-map 1:a:0`) 替换原视频音轨。
    - **字幕处理**：支持外挂 (Soft) 或烧录 (Hardburn) 模式。在 Windows 下自动处理路径转义以兼容 ffmpeg 滤镜语法。

## 4. 路线图 (Roadmap)
- [ ] **并行化推理**：利用多 GPU 或批量推理提高长视频处理速度。
- [ ] **BGM 分离**：引入人声分离模型，配音后保留原始背景音乐（Vocal Removal）。
- [ ] **WebUI 进阶版**：集成视频预览功能的低代码操作界面。
- [ ] **流式支持**：支持生成实时预览。

## 5. 开发者备注
- **路径管理**：始终以项目根目录作为 CWD 运行 `main.py`。
- **Windows vs Linux**：`audio_merger.py` 已处理路径斜杠差异；`setup_env.py` 已移除平台专有的 Admin/Root 检测。
