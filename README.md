# VoiceEditor 使用说明

## 项目概览
- 作用：从视频拉取音频→转写字幕→零样本情感 TTS→拼接音频→可选替换视频音轨。
- 主要脚本：
  - [1. deploy_indextts2.ps1](1.%20deploy_indextts2.ps1) 自动部署 IndexTTS2 推理环境（uv + checkpoints 下载）。
  - [2. process_video.ps1](2.%20process_video.ps1) 下载视频、提取音频、用 Whisper 生成 SRT。
  - [3. text_to_voice.py](3.%20text_to_voice.py) 基于字幕逐段合成语音（IndexTTS2），可拼接、可回灌视频。
  - [4. merge_out_segs.py](4.%20merge_out_segs.py) 按 manifest 合并分段 WAV，并可用 ffmpeg 替换视频音轨、外挂/内嵌字幕。
- 依赖的上游项目：内置的 [index-tts](index-tts/README.md) 代码与模型权重。

## 环境要求
- 操作系统：Windows，PowerShell 5+（脚本已适配 Ctrl+C 友好退出）。
- Python：推荐 3.10（deploy 脚本会用 uv 自动安装）。
- 工具：git、ffmpeg（用于音频转码/混流）、可选 NVIDIA GPU + CUDA 驱动（自动检测，无 GPU 时走 CPU）。
- 网络：可访问 HuggingFace/ModelScope，或使用 hf-mirror；脚本已支持镜像参数。

## 快速开始
### 1) 部署 IndexTTS2 环境
```powershell
# 在仓库根目录执行（默认 clone index-tts 并创建 .venv）
powershell -ExecutionPolicy Bypass -File .\1. deploy_indextts2.ps1 -PythonVersion 3.10 -UseGPU $true -ForceCnMirror $true
```
要点：
- 自动：安装/检测 uv、安装指定 Python、clone/pull index-tts、创建/复用父级 .venv、安装依赖、可选下载 checkpoints。
- 常用开关：`-SkipCheckpointDownload $true` 跳过权重下载；`-DownloadSource modelscope` 切换源；`-VerboseLogging $true` 查看详细日志。

### 2) 下载视频并生成字幕
```powershell
# 可指定 URL 与工作目录；默认工作目录为 .\work
powershell -ExecutionPolicy Bypass -File .\2. process_video.ps1 -VideoUrl "<视频URL>" -WorkDir "work"
```
结果：
- 下载视频到 work
- 提取音频并存为 WAV
- 使用 Whisper 生成 SRT 字幕
- 同时保存一段 30 秒的参考音频 voice_ref.wav（选取能量最小片段以降低噪声）

### 3) 字幕驱动 TTS 合成
```powershell
# 使用 uv 运行，指定参考音频与字幕（支持通配符）
uv run -p .\.venv\Scripts\python.exe .\3. text_to_voice.py \
  --ref_voice .\work\voice_ref.wav \
  --srt .\work\*.srt \
  --stitch \
  --video "<可选: 原始视频路径>" \
  --output_video .\work\final_output.mp4
```
输出：
- 分段音频与 manifest.json 写入 work/out_segs
- 若指定 `--stitch`，会合成整段 WAV，并在传入 `--video` 时用 ffmpeg 生成带新音轨的视频（保留字幕流）。

常用参数摘录：
- `--duration_mode` (seconds|tokens)，`--tokens_per_sec` 控制时长映射。
- `--lang` 语言代码，`--speed` 语速，`--emo_text`/`--emo_audio` 情感控制。
- `--sample_rate` 输出采样率，`--gain_db` 统一增益。

### 4) 手动合并与混流（可选）
如果只想手动合并或重新混流，可直接用合并脚本：
```powershell
# 按 manifest 顺序合并分段 WAV
uv run -p .\.venv\Scripts\python.exe .\4. merge_out_segs.py \
  --manifest work/out_segs/manifest.json \
  --out work/merged_output.wav \
  --pad-gaps  # 依据 start_ms 插入静音，可去掉此行表示直接拼接

# 生成最终视频（替换音轨，外挂或烧录字幕）
uv run -p .\.venv\Scripts\python.exe .\4. merge_out_segs.py \
  --manifest work/out_segs/manifest.json \
  --out work/merged_output.wav \
  --video-in "<原视频>" \
  --subs "<字幕.srt>" \
  --out-video work/final_output.mp4 \
  --burn-subs  # 可选：烧录字幕；不加则外挂/软字幕
```

## 工作目录与产物
- work/: 默认工作区，包含下载的视频、音频、字幕。
- work/voice_ref.wav: 参考音频。
- work/out_segs/: 分段 WAV 与 manifest.json（记录时长、偏移、文件名）。
- work/merged_output.wav: 合并后的整段音频（可由 text_to_voice 或 merge_out_segs 生成）。
- work/final_output.mp4: 替换音轨后的成品视频（可选字幕）。

## 常见问题
- ffmpeg 未找到：请将 ffmpeg 加入 PATH，或在命令前设置 `$env:PATH`。
- 权重下载慢：在部署时使用 `-DownloadSource hf-mirror` 或 `-ForceCnMirror $true`；也可提前把模型放入 checkpoints/。
- 无 GPU：脚本会自动退回 CPU 路径，但合成速度较慢，可调小模型或缩短素材。

## 参考
- IndexTTS2 详细文档见 [index-tts/README.md](index-tts/README.md)。
- Whisper 参数与模型选择可参考 [openai/whisper](https://github.com/openai/whisper) 说明。
