# VoiceEditor 故障排查指南

**最后更新**: 2026-04-11 (Remote NiceGUI Hardening + Timer Lifecycle Fix)  
**版本**: 基于可靠性 97% 的稳定版本

---

## 快速诊断

### 问题: "远端 GUI 只有粘贴 URL 可用" / "WebSocket connection failed"

**症状**:
```text
浏览器控制台反复出现:
WebSocket connection to 'ws://<host>:8196/_nicegui_ws/socket.io/...&transport=websocket' failed

页面能打开，但只有 URL 输入框可用；
点击“1) 开始处理”或“2) 开始合成”没有反应
```

**原因**:
1. 旧版本 GUI 主按钮通过同步 lambda 返回 coroutine，远端点击时可能根本没有把 async 流程调度起来
2. `AppState` 旧实现会在持锁期间写 warning，UILogHandler 回流时可能造成页面假死
3. 远端 systemd 服务如果允许 websocket upgrade，在某些链路上会反复出现升级失败；当前默认改为 polling-only
4. 旧版页面使用 `ui.timer(...)` 刷日志/状态，页面销毁或重连时可能报 `The parent slot of the element has been deleted`

**解决方案**:

1. **确认已部署 2026-04-11 之后的修复版本**
   ```bash
   uv run python scripts/remote_sync.py --sync
   uv run python scripts/remote_sync.py --exec "bash deploy_service.sh"
   ```

2. **检查远端服务是否按 polling-only 启动**
   ```bash
   uv run python scripts/remote_sync.py --exec "journalctl -u voiceeditor -n 20 --no-pager"
   ```
   期望看到:
   ```text
   Starting VoiceEditor GUI on http://10.245.54.160:8196 (bind: http://0.0.0.0:8196)
   Socket.IO transports: polling
   ```

3. **确认 systemd 环境变量已写入**
   ```bash
   uv run python scripts/remote_sync.py --exec "sudo systemctl cat voiceeditor"
   ```
   重点检查:
   - `VOICEEDITOR_GUI_PUBLIC_HOST=<远端 IP>`
   - `VOICEEDITOR_GUI_SOCKET_IO_TRANSPORTS=polling`
   - `VOICEEDITOR_GUI_RECONNECT_TIMEOUT=60`

4. **如果浏览器仍报 websocket failed，但页面仍可用**
   - 先确认服务是否真的已经重载到 `VOICEEDITOR_GUI_SOCKET_IO_TRANSPORTS=polling`
   - 如果仍不是 polling-only，重新部署 systemd 模板
   - 如果已经是 polling-only 但仍报 websocket，说明浏览器加载的是旧页面资源，先强制刷新再复测

5. **如果日志出现 parent-slot deleted**
   ```text
   RuntimeError: The parent slot of the element has been deleted.
   ```
   说明远端仍在运行旧版本页面级 timer；重新同步并重启服务:
   ```bash
   uv run python scripts/remote_sync.py --sync
   uv run python scripts/remote_sync.py --exec "sudo systemctl restart voiceeditor"
   ```

**验证命令**:
```bash
uv run pytest tests/test_ui_state.py tests/test_main_gui.py
uv run python scripts/remote_sync.py --exec "sudo systemctl status voiceeditor --no-pager"
```

**当前已知状态**:
- 本地聚焦回归: 13/13 通过
- ricky 上 systemd 服务已按新配置启动
- 主页面 `http://10.245.54.160:8196` 可正常返回
- 最后仍需浏览器内手工确认按钮交互完全恢复

### 问题: "CUDA Out of Memory"

**症状**: 
```
CUDA Out of Memory at segment X. Saving current progress...
```

**原因**: 生成长音频或参数设置过高时，GPU 显存不足。

**解决方案** (按优先级):

1. **降低扩散步数** (最快)
   ```bash
   uv run ./main.py --diffusion-steps 15 --srt-pattern work/*.srt
   ```
   - 默认: 25 步
   - 快速模式: 15 步 (质量略低，速度快 40%)
   - 草稿模式: 10 步 (最快，质量明显降低)

2. **切换 CPU 模式** (兼容所有硬件)
   ```bash
   # 编辑 src/config.py 或设置环境变量
   export DEVICE=cpu
   uv run ./main.py
   ```
   - 更慢但不受显存限制
   - 适合 GPU 不足的场景

3. **处理少量分段**
   ```bash
   # 使用脚本分批处理
   python scripts/batch_process.py --batch-size 5
   ```
   - 每次处理 5-10 个分段后清空 GPU 缓存

4. **检查其他应用占用**
   ```bash
   nvidia-smi  # 查看 GPU 占用
   ```
   - 关闭其他 GPU 应用 (Python、浏览器等)

---

### 问题: "SRT 文件格式错误"

**症状**:
```
Failed to parse SRT: Invalid subtitle format
Fallback parsing recovered X entries
```

**原因**: SRT 文件格式不标准 (缺失序号、错误的时间码格式等)。

**解决方案**:

1. **检查 SRT 格式** (标准格式)
   ```
   1
   00:00:01,000 --> 00:00:05,000
   第一个字幕
   
   2
   00:00:06,000 --> 00:00:10,000
   第二个字幕
   ```

2. **修复格式错误的文件**
   ```bash
   # 使用专业字幕编辑工具
   # - Subtitle Edit (免费)
   # - Aegisub (开源)
   # - FFmpeg (命令行)
   ffmpeg -i input.srt -c:s srt output.srt
   ```

3. **VoiceEditor 会自动跳过**
   - 格式错误的条目会被跳过，处理继续
   - 查看日志了解哪些条目被跳过: `[Segment Diagnostic] ... Skipping`

---

### 问题: "视频合成失败" / "FFmpeg 错误"

**症状**:
```
FFmpeg subprocess failed: ...
或
Failed to mux audio: Permission denied
```

**原因**: 路径中含有特殊字符、权限问题、或 FFmpeg 未正确安装。

---

### 问题: "Windows 上字幕烧录失败" / "Subtitle Burning on Windows"

**症状**:
```
FFmpeg filter failed with path: C:\\Users\\...
或
subtitles filter error: unmatched quote  
```

**原因**: ✅ **已在 2026-04-09 修复**
- Windows 路径使用反斜杠 `\`，FFmpeg 过滤器语法不兼容
- 例如: `subtitles='C:\\path\\file.srt'` 在 FFmpeg 中失败

**解决方案**:

1. **自动修复** (推荐，已默认启用)
   ```
   VoiceEditor 现在自动处理 Windows 路径:
   - 转换为 POSIX 格式: C:\path → C:/path
   - 冒号转义: C:/path → C\:/path
   - FFmpeg 兼容格式: subtitles='C\\:/path/file.srt' ✅
   ```

2. **验证 FFmpeg 安装正确**
   ```bash
   ffmpeg -version
   # 应该返回 ffmpeg 版本信息
   ```

3. **如果仍有问题，简化路径名称**
   ```bash
   # 不好: [2024-04-09] 我的视频 (带字幕).mp4
   # 好:   my_video.mp4
   ```

**技术细节**:
- 修复位置: `src/tts/audio_pipeline.py` 行 13-38
- 修复位置: `src/audio_merger.py` 行 88-114
- 方法: `ensure_safe_srt_for_ffmpeg()`
- 测试覆盖: 23 个路径转义测试 (100% 通过)

---

**解决方案**:

1. **验证 FFmpeg 已安装**
   ```bash
   ffmpeg -version
   ```
   - Windows: 从 ffmpeg.org 下载官方版本
   - Linux: `sudo apt install ffmpeg`
   - macOS: `brew install ffmpeg`

2. **检查路径中的特殊字符**
   - VoiceEditor 自动处理: `(` `)` `[` `]` `"` `:` 等 14 种特殊字符
   - 如果仍失败，简化路径名称:
   ```bash
   # 不好: C:\Users\用户名\Videos\[2024-04.09]我的视频 (1).mp4
   # 好: C:\Videos\my_video.mp4
   ```

3. **检查文件权限**
   ```bash
   # Windows
   icacls "work" /grant:r "%USERNAME%":F
   
   # Linux
   chmod -R 755 work/
   chmod -R 755 checkpoints/
   ```

4. **检查磁盘空间**
   ```bash
   # 确保 work/ 目录有至少 5GB 可用空间
   # 每个分钟视频需要约 30-50MB
   ```

---

### 问题: "识别视频失败" / "没有检测到音频"

**症状**:
```
Failed to extract audio from video
或
No audio stream found
```

**原因**: 视频格式不支持、编码不兼容、或下载/传输损坏。

**解决方案**:

1. **尝试转换视频格式**
   ```bash
   # 转换为标准 MP4
   ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4
   ```
   - 支持的格式: MP4, MKV, MOV, AVI, FLV, WebM

2. **检查视频完整性**
   ```bash
   # 验证视频文件
   ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 video.mp4
   ```

3. **提取音频后手动处理**
   ```bash
   # 手动提取音频
   ffmpeg -i input.mp4 work/audio.wav
   # 使用 faster-whisper 转写
   faster-whisper work/audio.wav --output_format json
   ```

---

### 问题: "GPU 无法使用" / "只用 CPU"

**症状**:
```
GPU: not available (CPU mode)
或
torch.cuda.is_available() = False
```

**原因**: CUDA 未安装、驱动过期、或环境变量配置错误。

**解决方案**:

1. **检查 NVIDIA 驱动**
   ```bash
   nvidia-smi
   ```
   - 若无输出，访问 nvidia.com 下载最新驱动

2. **检查 CUDA 安装**
   ```bash
   nvcc --version
   # 或
   cat /usr/local/cuda/version.txt
   ```
   - Windows: CUDA Toolkit 需单独安装 (PyTorch 的 torch 包会自带)
   - Linux: `sudo apt install cuda-toolkit`

3. **重新安装 PyTorch**
   ```bash
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
   ```
   - 确保版本匹配 CUDA 版本 (通常 CUDA 11.8)

4. **设置环境变量**
   ```bash
   # Windows
   set PATH=%PATH%;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8\bin
   
   # Linux
   export PATH=/usr/local/cuda/bin:$PATH
   export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
   ```

5. **暂时使用 CPU** (确保完成)
   ```bash
   # 编辑 config.py
   # device = "cpu"  # 强制 CPU
   uv run ./main.py --diffusion-steps 10  # 降低步数加速
   ```

---

### 问题: "内存不足 (OOM)" / "程序崩溃"

**症状**:
```
RuntimeError: CUDA out of memory
或
MemoryError: Unable to allocate ...
或
Killed (程序无故退出)
```

**原因**: 处理长视频或系统内存紧张。

**解决方案**:

1. **减少内存占用** (应用级)
   ```bash
   # 降低扩散步数和批大小
   uv run ./main.py --diffusion-steps 15 --srt-pattern work/*.srt
   ```

2. **启用长视频模式**
   - VoiceEditor 自动检测 >50 个分段为长视频
   - 每 10 个分段后自动清理 GPU 缓存
   - 无需手动配置

3. **系统级调整** (Linux/Mac)
   ```bash
   # 增加交换空间 (临时)
   sudo fallocate -l 4G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

4. **分批处理**
   ```bash
   # 手动分批，每次处理 10-20 个分段
   # 修改 SRT 文件，只保留要处理的部分
   # 运行合成
   # 重复
   ```

5. **关闭后台应用**
   ```bash
   # 关闭浏览器、IDE、其他 Python 程序等
   # Windows: 打开任务管理器，查看 GPU 占用
   # Linux: nvidia-smi -l 1  (监控 GPU 使用)
   ```

---

### 问题: "某个分段合成失败"

**症状**:
```
[Segment Diagnostic] ID=5 Error=TTS Synthesis Error
Segment 5 synthesis failed: ...
```

**原因**: 特定文本导致模型出错（无法生成语音、转写格式问题等）。

**解决方案**:

1. **跳过该分段**
   - 程序会自动标记为 `"failed": true`
   - 使用 `--stitch` 模式跳过失败分段继续合成

2. **修改文本内容**
   ```
   # SRT 中修改第 5 个分段
   5
   00:00:20,000 --> 00:00:25,000
   修改文本内容（避免特殊符号、过长等）
   ```
   - 避免: 过长文本 (>200 字)
   - 避免: 特殊符号 (口语词汇、表情符号等)
   - 重新运行合成

3. **检查日志获得详情**
   ```bash
   # 启用详细日志
   uv run ./main.py --verbose 2>&1 | tee synthesis.log
   # 查看 synthesis.log 了解具体错误
   ```

4. **手动修复**
   ```bash
   # 对于重要分段，使用其他 TTS 工具生成
   # 或从其他视频提取该段音频
   # 手动替换 work/out_segs/seg_0005.wav
   ```

---

## 日志和脚本

### 查看合成日志

```bash
# 启用详细输出
uv run ./main.py --verbose 2>&1 | tee work/synthesis.log

# 查看最后的错误
tail -50 work/synthesis.log

# 搜索特定问题
grep "ERROR\|CRITICAL" work/synthesis.log
grep "OOM\|Out of Memory" work/synthesis.log
```

### 恢复中断的合成

```bash
# 查看 manifest.json 了解已完成的分段
cat work/out_segs/manifest.json | jq '.[] | {id, text, failed}'

# 只有失败的分段会标记 "failed": true
# 重新运行相同命令会跳过已完成的分段，尝试失败的
uv run ./main.py
```

### 清理临时文件

```bash
# 清理所有生成的文件但保留源 SRT 和视频
rm -rf work/out_segs/*.wav
rm work/out_segs/manifest.json

# 完全重新开始
rm -rf work/
mkdir work/
```

---

## 性能优化

### 加速合成

```bash
# 最快模式 (质量可能下降)
uv run ./main.py --diffusion-steps 10 --speed 1.5

# 平衡模式 (推荐)
uv run ./main.py --diffusion-steps 20

# 高质量模式 (但速度慢)
uv run ./main.py --diffusion-steps 30
```

### 长视频处理 (>30 分钟)

```bash
# 方案 1: 降低参数
uv run ./main.py --diffusion-steps 15 --lang zh

# 方案 2: 分批处理 SRT 文件
# 将 work/input.srt 分为 input_part1.srt, input_part2.srt
# 依次处理每个部分

# 方案 3: 使用 CPU + 降低参数
export DEVICE=cpu
uv run ./main.py --diffusion-steps 10 --verbose
```

### GPU 内存监控

```bash
# 实时监控 (每秒更新)
watch -n 1 nvidia-smi

# 或在另一个终端
nvidia-smi -l 1
```

---

## 常见问题解答

**Q: 如何调整语速?**  
A: 使用 `--speed` 参数。范围 0.5-2.0，默认 1.0。

**Q: 如何保留原视频字幕?**  
A: 使用 `--burn-subs` 参数将字幕烧录到视频。

**Q: 如何强制重新生成所有音频?**  
A: 使用 `--force-regen` 参数。

**Q: 支持哪些语言?**  
A: 默认中文 (zh)，支持 en, ja, ko 等。使用 `--lang` 参数。

**Q: 可以中途停止吗?**  
A: 按 Ctrl+C 停止，已完成的分段和 manifest 会保留，下次运行时继续。

---

## 联系支持

如问题未在此指南中解决:

1. **查看完整日志** (启用 --verbose)
2. **检查 GitHub Issues** (搜索相关错误信息)
3. **查阅 DEVELOPMENT.md** 了解架构细节
4. **提交问题报告** (包含完整日志、操作系统、GPU 型号)

---

## 更新历史

| 版本 | 日期 | 变更 |
|------|-----|------|
| 1.0 | 2026-04-09 | 初版 (可靠性 90%) |

