# Speech Editor - 视频处理脚本 (PowerShell版本)
# 功能: 从URL下载视频，提取音频，并使用Whisper生成字幕

param(
    [string]$VideoUrl = "",
    [string]$WorkDir = ""
)

# ============================================================================
# 全局配置
# ============================================================================
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Config = @{
    PythonVersion = "3.10"
    PipMirror = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
    WhisperModel = "small"
    WhisperLanguage = "zh"
    DefaultWorkDir = "work"
    RequiredPackages = @("yt_dlp", "openai-whisper", "torch", "librosa", "soundfile")
    AudioFormat = "wav"
    AudioBitrate = "192"
    RefAudioDuration = 30
}

# ============================================================================
# 核心函数 - Python 脚本执行
# ============================================================================

function Invoke-PythonScript {
    <#
    .SYNOPSIS
        执行Python脚本并返回结果
    .PARAMETER PythonExe
        Python 执行文件路径
    .PARAMETER Script
        Python 脚本代码
    .PARAMETER ScriptName
        脚本名称（用于日志）
    #>
    param(
        [string]$PythonExe,
        [string]$Script,
        [string]$ScriptName = "Python Script"
    )
    
    try {
        $tempFile = [System.IO.Path]::GetTempFileName()
        & $PythonExe -c $Script > $tempFile 2>&1
        $output = Get-Content $tempFile -Raw
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
        
        # 提取最后一个 JSON 对象（从最后一行往前找）
        $lines = $output -split "`n"
        $jsonOutput = ""
        
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            $line = $lines[$i].Trim()
            if ($line.StartsWith('{') -and $line.EndsWith('}')) {
                $jsonOutput = $line
                break
            }
        }
        
        if ([string]::IsNullOrWhiteSpace($jsonOutput)) {
            Write-Log "$ScriptName 返回无效数据" "Error"
            return $null
        }
        
        return $jsonOutput | ConvertFrom-Json
    }
    catch {
        Write-Log "$ScriptName 执行失败: $_" "Error"
        return $null
    }
}

function Test-PythonResult {
    <#
    .SYNOPSIS
        检查Python执行结果
    #>
    param(
        [PSObject]$Result,
        [string]$TaskName
    )
    
    if ($null -eq $Result) {
        return $false
    }
    
    $isSuccess = $Result.success
    if ($isSuccess -is [System.String]) {
        $isSuccess = [System.Convert]::ToBoolean($isSuccess)
    }
    
    if (-not $isSuccess) {
        $errorMsg = if ($Result.error) { $Result.error } else { "未知错误" }
        Write-Log "$TaskName 失败: $errorMsg" "Error"
        return $false
    }
    
    return $true
}


# ============================================================================
# 日志和输出函数
# ============================================================================

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("Info", "Error", "Success", "Warning")]
        [string]$Level = "Info"
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $color = @{
        "Info"    = "Cyan"
        "Error"   = "Red"
        "Success" = "Green"
        "Warning" = "Yellow"
    }
    
    Write-Host "[$timestamp] " -NoNewline
    Write-Host $Message -ForegroundColor $color[$Level]
}


# ============================================================================
# Python 环境管理
# ============================================================================

function Test-PythonEnvironment {
    Write-Log "检查 Python 虚拟环境..." "Info"
    
    $venvPath = Join-Path -Path (Get-Location) -ChildPath ".venv"
    
    if (Test-Path -Path $venvPath) {
        Write-Log "找到虚拟环境: $venvPath" "Success"
        return $venvPath
    }
    
    Write-Log "虚拟环境不存在，尝试使用 uv 创建..." "Info"
    
    try {
        $uvCheck = uv --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Log "错误: 未找到 uv 工具。请先安装 uv：pip install uv" "Error"
            return $null
        }
        
        Write-Log "使用 uv 创建 Python $($Config.PythonVersion) 虚拟环境..." "Info"
        & uv venv --python $Config.PythonVersion $venvPath
        
        if ($LASTEXITCODE -eq 0) {
            Write-Log "虚拟环境创建成功: $venvPath" "Success"
            return $venvPath
        } else {
            Write-Log "错误: uv 创建虚拟环境失败" "Error"
            return $null
        }
    }
    catch {
        Write-Log "错误: 创建虚拟环境失败: $_" "Error"
        return $null
    }
}

function Get-PythonExecutable {
    param(
        [string]$VenvPath
    )
    
    if ([string]::IsNullOrWhiteSpace($VenvPath)) {
        return "python"
    }
    
    $pythonExe = Join-Path -Path $VenvPath -ChildPath "Scripts" -AdditionalChildPath "python.exe"
    
    if (Test-Path -Path $pythonExe) {
        return $pythonExe
    }
    
    return "python"
}


function Setup-PipMirror {
    param([string]$PythonExe = "python")
    
    Write-Log "配置 pip 镜像源..." "Info"
    
    $appDataPath = $env:APPDATA
    $pipConfigDir = Join-Path -Path $appDataPath -ChildPath "pip"
    
    if (-not (Test-Path -Path $pipConfigDir)) {
        New-Item -ItemType Directory -Path $pipConfigDir -Force | Out-Null
    }
    
    $pipConfigFile = Join-Path -Path $pipConfigDir -ChildPath "pip.ini"
    
    $pipConfig = @"
[global]
index-url = $($Config.PipMirror)
extra-index-url = https://pypi.org/simple/
"@
    
    $pipConfig | Out-File -FilePath $pipConfigFile -Encoding UTF8 -Force
    Write-Log "pip 镜像已配置为清华大学源" "Success"
}


function Test-Dependencies {
    param([string]$PythonExe = "python")
    
    Write-Log "检查必要的 Python 包..." "Info"
    
    Write-Log "升级 pip..." "Info"
    & $PythonExe -m ensurepip --upgrade 2>&1 | Out-Null
    & $PythonExe -m pip install --upgrade pip setuptools wheel -i $Config.PipMirror 2>&1 | Out-Null
    
    Setup-PipMirror -PythonExe $PythonExe
    
    $missingPackages = @()
    
    foreach ($package in $Config.RequiredPackages) {
        $checkCmd = "`"$PythonExe`" -c `"import $($package.Replace('-', '_'))`""
        try {
            Invoke-Expression $checkCmd 2>&1 | Out-Null
            Write-Log "  ✓ $package" "Success"
        }
        catch {
            Write-Log "  ✗ $package (缺失)" "Warning"
            $missingPackages += $package
        }
    }
    
    if ($missingPackages.Count -gt 0) {
        Write-Log "缺失的包: $($missingPackages -join ', ')" "Warning"
        Write-Log "正在安装缺失的包..." "Info"
        & $PythonExe -m pip install $missingPackages -i $Config.PipMirror 2>&1 | Out-Null
        
        if ($LASTEXITCODE -ne 0) {
            Write-Log "警告: 部分依赖包安装失败" "Warning"
            return $false
        }
        Write-Log "依赖包安装成功" "Success"
    }
    
    return $true
}

function New-WorkDirectory {
    param(
        [string]$Path
    )
    
    if (-not (Test-Path -Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        Write-Log "创建工作目录: $Path" "Success"
    }
    
    return $Path
}


# ============================================================================
# 核心处理函数
# ============================================================================

function Download-Video {
    param(
        [string]$Url,
        [string]$WorkDir,
        [string]$PythonExe = "python"
    )
    
    Write-Log "正在准备下载视频: $Url" "Info"
    
    $pythonScript = @"
import yt_dlp
import os
import json
import glob

url = '$($Url -replace "'", "'")'
work_dir = r'$WorkDir'

try:
    # 先获取视频信息
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'video')
    
    # 检查视频是否已存在
    video_extensions = ['*.mp4', '*.mkv', '*.flv', '*.webm', '*.mov', '*.avi']
    existing_files = []
    
    for ext_pattern in video_extensions:
        pattern = os.path.join(work_dir, f'{title}.{ext_pattern.replace("*.", "")}')
        if os.path.exists(pattern):
            existing_files.append(pattern)
    
    if existing_files:
        result = {
            'success': True,
            'video_path': existing_files[0],
            'title': title,
            'already_exists': True,
            'message': f'视频文件已存在'
        }
    else:
        # 视频不存在，开始下载
        video_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(work_dir, '%(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(video_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info)
        
        result = {
            'success': True,
            'video_path': video_path,
            'title': title,
            'already_exists': False,
            'message': '视频下载成功'
        }
except Exception as e:
    result = {
        'success': False,
        'error': str(e)
    }

print(json.dumps(result, ensure_ascii=False))
"@

    $result = Invoke-PythonScript -PythonExe $PythonExe -Script $pythonScript -ScriptName "视频下载"
    
    if (-not (Test-PythonResult -Result $result -TaskName "视频下载")) {
        return $null
    }
    
    if ($result.already_exists -eq $true -or $result.already_exists -eq "True") {
        Write-Log "[检查] $($result.message) - $($result.video_path)" "Warning"
    } else {
        Write-Log "[完成] $($result.message) - $($result.video_path)" "Success"
    }
    
    return @{
        VideoPath = $result.video_path
        Title = $result.title
    }
}


function Extract-Audio {
    param(
        [string]$Url,
        [string]$Title,
        [string]$WorkDir,
        [string]$PythonExe = "python"
    )
    
    Write-Log "正在提取音频..." "Info"
    
    $pythonScript = @"
import yt_dlp
import os
import json

url = '$($Url -replace "'", "'")'
title = '$($Title -replace "'", "'")'
work_dir = r'$WorkDir'

audio_opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': '$($Config.AudioFormat)',
        'preferredquality': '$($Config.AudioBitrate)',
    }],
    'outtmpl': os.path.join(work_dir, f'{title}.%(ext)s'),
    'quiet': True,
    'no_warnings': True,
}

try:
    with yt_dlp.YoutubeDL(audio_opts) as ydl:
        ydl.download([url])
    
    audio_path = os.path.join(work_dir, f"{title}.$($Config.AudioFormat)")
    
    if os.path.exists(audio_path):
        result = {
            'success': True,
            'audio_path': audio_path
        }
    else:
        result = {
            'success': False,
            'error': 'Audio file not found after extraction'
        }
except Exception as e:
    result = {
        'success': False,
        'error': str(e)
    }

print(json.dumps(result, ensure_ascii=False))
"@

    $result = Invoke-PythonScript -PythonExe $PythonExe -Script $pythonScript -ScriptName "音频提取"
    
    if (-not (Test-PythonResult -Result $result -TaskName "音频提取")) {
        return $null
    }
    
    Write-Log "[完成] 音频保存至: $($result.audio_path)" "Success"
    return $result.audio_path
}


function Transcribe-Audio {
    param(
        [string]$AudioPath,
        [string]$WorkDir,
        [string]$Title,
        [string]$PythonExe = "python"
    )
    
    Write-Log "正在加载 Whisper 模型并转录音频 (文件: $(Split-Path -Leaf $AudioPath))..." "Info"
    Write-Log "使用模型: $($Config.WhisperModel) | 语言: $($Config.WhisperLanguage)" "Info"
    
    $pythonScript = @"
import whisper
from whisper.utils import get_writer
import os
import json
import sys

audio_path = r'$AudioPath'
work_dir = r'$WorkDir'
title = '$($Title -replace "'", "'")'
model_name = '$($Config.WhisperModel)'
language = '$($Config.WhisperLanguage)'

try:
    print(f"加载 Whisper {model_name} 模型...", file=sys.stderr)
    model = whisper.load_model(model_name)
    
    print(f"转录音频 (语言: {language})...", file=sys.stderr)
    result = model.transcribe(
        audio_path,
        language=language,
        verbose=False,
        fp16=False,
        temperature=0,
        best_of=5,
        beam_size=5,
        initial_prompt="这是中文内容。"
    )
    
    # 检查转录结果
    if result and 'segments' in result and len(result['segments']) > 0:
        total_duration = result.get('duration', 0)
        segment_count = len(result['segments'])
        print(f"成功转录 {segment_count} 个字幕段，总时长: {total_duration:.1f}秒", file=sys.stderr)
    
    print("正在生成 SRT 字幕文件...", file=sys.stderr)
    
    writer = get_writer("srt", work_dir)
    writer(result, f"{title}.srt", {
        "max_line_width": None,
        "max_line_count": None,
        "highlight_words": False
    })
    
    srt_path = os.path.join(work_dir, f"{title}.srt")
    
    # 验证SRT文件
    if os.path.exists(srt_path):
        with open(srt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            srt_lines = len(lines)
        print(f"SRT 文件已生成，包含 {srt_lines} 行", file=sys.stderr)
    
    output = {
        'success': True,
        'srt_path': srt_path,
        'segment_count': segment_count if result and 'segments' in result else 0
    }
except Exception as e:
    import traceback
    error_msg = f"{str(e)}\n{traceback.format_exc()}"
    output = {
        'success': False,
        'error': error_msg
    }

print(json.dumps(output, ensure_ascii=False))
"@

    $result = Invoke-PythonScript -PythonExe $PythonExe -Script $pythonScript -ScriptName "音频转录"
    
    if (-not (Test-PythonResult -Result $result -TaskName "音频转录")) {
        return $null
    }
    
    $segmentCount = if ($result.segment_count) { $result.segment_count } else { 0 }
    Write-Log "[完成] 字幕保存至: $($result.srt_path) (共 $segmentCount 个字幕段)" "Success"
    return $result.srt_path
}


function Extract-ReferenceAudio {
    param(
        [string]$AudioPath,
        [string]$WorkDir,
        [string]$PythonExe = "python"
    )
    
    Write-Log "正在提取噪音最小的 $($Config.RefAudioDuration) 秒参考音频..." "Info"
    
    $pythonScript = @"
import librosa
import numpy as np
import soundfile as sf
import os
import json

audio_path = r'$AudioPath'
work_dir = r'$WorkDir'
output_path = os.path.join(work_dir, 'voice_ref.wav')
ref_duration = $($Config.RefAudioDuration)

try:
    y, sr = librosa.load(audio_path, sr=None)
    audio_length = librosa.get_duration(y=y, sr=sr)
    
    if audio_length < ref_duration:
        output_y = y
        print(f'音频长度为 {audio_length:.1f}秒，短于{ref_duration}秒，使用整个音频')
    else:
        S = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=2048, hop_length=512)
        energy = np.mean(librosa.power_to_db(S), axis=0)
        
        hop_length = 512
        frame_length = len(energy)
        sample_length = int(ref_duration * sr)
        frame_30s = int((sample_length + hop_length - 1) // hop_length)
        
        min_energy = float('inf')
        best_start_frame = 0
        
        for i in range(max(0, frame_length - frame_30s + 1)):
            window_energy = np.mean(energy[i:min(i + frame_30s, frame_length)])
            if window_energy < min_energy:
                min_energy = window_energy
                best_start_frame = i
        
        best_start_sample = best_start_frame * hop_length
        end_sample = min(best_start_sample + sample_length, len(y))
        output_y = y[best_start_sample:end_sample]
    
    sf.write(output_path, output_y, sr)
    
    result = {
        'success': True,
        'output_path': output_path,
        'duration': float(len(output_y) / sr)
    }
except Exception as e:
    result = {
        'success': False,
        'error': str(e)
    }

print(json.dumps(result, ensure_ascii=False))
"@

    $result = Invoke-PythonScript -PythonExe $PythonExe -Script $pythonScript -ScriptName "参考音频提取"
    
    if (-not (Test-PythonResult -Result $result -TaskName "参考音频提取")) {
        return $null
    }
    
    $duration = [math]::Round($result.duration, 1)
    Write-Log "[完成] 参考音频已保存至: $($result.output_path) (时长: ${duration}s)" "Success"
    return $result.output_path
}


# ============================================================================
# 用户交互
# ============================================================================

function Get-VideoUrl {
    Write-Host ""
    $url = Read-Host "请输入 Bilibili 视频网址"
    
    if ([string]::IsNullOrWhiteSpace($url)) {
        Write-Log "错误: 网址不能为空" "Error"
        return $null
    }
    
    return $url.Trim()
}


# ============================================================================
# 主程序
# ============================================================================

function Main {
    Write-Host ""
    Write-Log "=== Speech Editor 视频处理工具 (PowerShell版本) ===" "Info"
    Write-Host ""
    
    # 步骤 1: 设置 Python 环境
    $venvPath = Test-PythonEnvironment
    if ($null -eq $venvPath) { exit 1 }
    
    $pythonExe = Get-PythonExecutable -VenvPath $venvPath
    Write-Log "使用 Python: $pythonExe" "Success"
    Write-Host ""
    
    # 步骤 2: 检查依赖包
    if (-not (Test-Dependencies -PythonExe $pythonExe)) {
        Write-Log "警告: 部分依赖包未安装，某些功能可能无法正常使用" "Warning"
        Write-Host ""
    }
    
    # 步骤 3: 准备工作目录
    $workDirectory = if ([string]::IsNullOrWhiteSpace($WorkDir)) {
        Join-Path -Path (Get-Location) -ChildPath $Config.DefaultWorkDir
    } else {
        $WorkDir
    }
    $workDirectory = New-WorkDirectory -Path $workDirectory
    Write-Host ""
    
    # 步骤 4: 获取视频 URL
    $url = if ([string]::IsNullOrWhiteSpace($VideoUrl)) {
        Get-VideoUrl
    } else {
        $VideoUrl
    }
    if ($null -eq $url) { exit 1 }
    Write-Host ""
    
    try {
        # 步骤 5: 下载视频
        $videoInfo = Download-Video -Url $url -WorkDir $workDirectory -PythonExe $pythonExe
        if ($null -eq $videoInfo) { exit 1 }
        Write-Host ""
        
        # 步骤 6: 提取音频
        $audioPath = Extract-Audio -Url $url -Title $videoInfo.Title -WorkDir $workDirectory -PythonExe $pythonExe
        if ($null -eq $audioPath) { exit 1 }
        Write-Host ""
        
        # 步骤 7: 提取参考音频（可选）
        $refAudioPath = Extract-ReferenceAudio -AudioPath $audioPath -WorkDir $workDirectory -PythonExe $pythonExe
        if ($null -eq $refAudioPath) {
            Write-Log "警告: 参考音频提取失败，但继续处理" "Warning"
        }
        Write-Host ""
        
        # 步骤 8: 转录音频
        $srtPath = Transcribe-Audio -AudioPath $audioPath -WorkDir $workDirectory -Title $videoInfo.Title -PythonExe $pythonExe
        if ($null -eq $srtPath) { exit 1 }
        
        Write-Host ""
        Write-Log "所有任务已成功完成！" "Success"
        Write-Log "生成的文件位置: $workDirectory" "Info"
        Write-Host ""
        
        Read-Host "请检查并编辑生成的字幕文件。按回车键退出脚本"
    }
    catch {
        Write-Log "发生错误: $_" "Error"
        exit 1
    }
}

# 执行主程序
Main
