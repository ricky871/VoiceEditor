#Requires -Version 5
<#
.SYNOPSIS
    IndexTTS2 本地推理环境一键部署脚本（Windows）- COT 重构版本
.DESCRIPTION
    使用 uv 作为 Python 环境/包管理器，支持 GPU/CPU 自动检测，镜像自动回退。
    集成了模型 Checkpoints 自动下载功能，支持 HuggingFace 和 ModelScope 两种源。
    
    重构特点：
    - 使用链式思考（COT）方法组织逻辑
    - 模块化设计，主要流程清晰易懂
    - 可复用的工具函数集中管理
    - 改进的错误处理和日志追踪
.PARAMETER PythonVersion
    Python 版本，默认 3.10
.PARAMETER RepoUrl
    Git 仓库地址，默认 https://github.com/index-tts/index-tts.git
.PARAMETER ProjectDir
    项目目录路径，默认当前目录下 index-tts
.PARAMETER UseGPU
    是否优先使用 GPU，默认 $true
.PARAMETER ForceCnMirror
    是否强制使用清华 PyPI 镜像，默认 $true
.PARAMETER DownloadSource
    下载 Checkpoints 的源，可选值：huggingface(默认)、modelscope、hf-mirror
.PARAMETER SkipCheckpointDownload
    是否跳过 Checkpoints 下载，默认 $false
.PARAMETER VerboseLogging
    是否开启详细日志，默认 $false
.PARAMETER VenvPath
    虚拟环境路径，默认为空（自动使用父级目录）
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\deploy_indextts2_refactored.ps1 -PythonVersion 3.10 -UseGPU $true
    powershell -ExecutionPolicy Bypass -File .\deploy_indextts2_refactored.ps1 -DownloadSource modelscope
#>

[CmdletBinding()]
Param(
    [Parameter(Mandatory = $false)]
    [string]$PythonVersion = "3.10",
    
    [Parameter(Mandatory = $false)]
    [string]$RepoUrl = "https://github.com/index-tts/index-tts.git",
    
    [Parameter(Mandatory = $false)]
    [string]$ProjectDir = (Join-Path (Get-Location) "index-tts"),
    
    [Parameter(Mandatory = $false)]
    [bool]$UseGPU = $true,
    
    [Parameter(Mandatory = $false)]
    [bool]$ForceCnMirror = $false,
    
    [Parameter(Mandatory = $false)]
    [ValidateSet("huggingface", "modelscope", "hf-mirror")]
    [string]$DownloadSource = "huggingface",
    
    [Parameter(Mandatory = $false)]
    [bool]$SkipCheckpointDownload = $false,
    
    [Parameter(Mandatory = $false)]
    [bool]$VerboseLogging = $false,

    [Parameter(Mandatory = $false)]
    [string]$VenvPath = ""
)

# ======================================
# 第一步：初始化全局状态与设置
# ======================================

$ErrorActionPreference = 'Stop'
$WarningPreference = 'Continue'

# 全局状态变量
$script:State = @{
    LastError       = $null
    ExitCode        = 0
    UserCancelled   = $false
    WebUIProcess    = $null
    ColorSupport    = $null
    PyIndexUrl      = ""
    HasGPU          = $false
    TorchInstalled  = $false
    DepsInstalled   = $false
}

# 注册 Ctrl+C 处理
$null = Register-EngineEvent -SourceIdentifier ConsoleCancel -SupportEvent -Action {
    $script:State.UserCancelled = $true
    Write-Warn "检测到 Ctrl+C，准备安全终止..."
}

# Trap 处理全局异常
trap {
    $script:State.LastError = $_
    Write-Error_ "致命错误: $($_.Exception.Message)"
    $script:State.ExitCode = 1
    exit 1
}

# ======================================
# 第二步：日志与输出工具函数
# ======================================

function Test-ColorSupport {
    <#
    .SYNOPSIS
        检测终端是否支持颜色输出
    #>
    $null -ne $host.UI.RawUI.BackgroundColor -and $null -ne $host.UI.RawUI.ForegroundColor
}

function Initialize-ColorSupport {
    <#
    .SYNOPSIS
        初始化颜色支持设置
    #>
    $script:State.ColorSupport = Test-ColorSupport
}

function Write-Info {
    param([string]$Message)
    if ($script:State.ColorSupport) {
        Write-Host "ℹ️  $Message" -ForegroundColor Cyan
    } else {
        Write-Host "[INFO] $Message"
    }
}

function Write-Warn {
    param([string]$Message)
    if ($script:State.ColorSupport) {
        Write-Host "⚠️  $Message" -ForegroundColor Yellow
    } else {
        Write-Host "[WARN] $Message"
    }
}

function Write-Error_ {
    param([string]$Message)
    if ($script:State.ColorSupport) {
        Write-Host "❌ $Message" -ForegroundColor Red
    } else {
        Write-Host "[ERROR] $Message"
    }
}

function Write-Ok {
    param([string]$Message)
    if ($script:State.ColorSupport) {
        Write-Host "✅ $Message" -ForegroundColor Green
    } else {
        Write-Host "[OK] $Message"
    }
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    if ($script:State.ColorSupport) {
        Write-Host "=== $Title ===" -ForegroundColor Magenta
    } else {
        Write-Host "=== $Title ==="
    }
}

function Write-Verbose_ {
    param([string]$Message)
    if ($VerboseLogging) {
        Write-Info "[Verbose] $Message"
    }
}

# ======================================
# 第三步：用户交互与状态检查
# ======================================

function Test-Cancel {
    <#
    .SYNOPSIS
        检查用户是否请求取消，并执行安全清理
    #>
    if (-not $script:State.UserCancelled) {
        return
    }

    Write-Warn "用户请求终止，正在清理..."
    
    Invoke-Cleanup
    exit 1
}

function Invoke-Cleanup {
    <#
    .SYNOPSIS
        执行安全清理操作
    #>
    try {
        if ($script:State.WebUIProcess -and -not $script:State.WebUIProcess.HasExited) {
            Stop-Process -Id $script:State.WebUIProcess.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Verbose_ "终止 WebUI 进程时出错: $_"
    }

    try {
        $stack = Get-Location -Stack
        if ($stack.Count -gt 0) {
            Pop-Location -ErrorAction SilentlyContinue | Out-Null
        }
    } catch {
        Write-Verbose_ "恢复目录栈时出错: $_"
    }

    Unregister-Event -SourceIdentifier ConsoleCancel -ErrorAction SilentlyContinue
}

# ======================================
# 第四步：系统检查函数
# ======================================

function Get-SystemInfo {
    <#
    .SYNOPSIS
        获取并显示系统信息
    #>
    Write-Section "系统信息检查"
    Write-Info "按 Ctrl+C 可随时终止脚本"
    Test-Cancel

    $OSInfo = Get-WmiObject -Class Win32_OperatingSystem
    Write-Info "操作系统: $($OSInfo.Caption)"
    Write-Info "PowerShell 版本: $($PSVersionTable.PSVersion)"

    $IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($IsAdmin) {
        Write-Ok "以管理员身份运行"
    } else {
        Write-Warn "非管理员身份运行，某些操作可能受限"
    }
}

function Test-PythonAvailable {
    <#
    .SYNOPSIS
        检测系统是否已安装 Python
    #>
    Write-Section "依赖自检"
    Test-Cancel

    $pythonFound = $false
    try {
        $pyVersion = & py -V 2>&1
        $pythonFound = $true
        Write-Ok "检测到系统 Python: $pyVersion"
    } catch {
        Write-Verbose_ "py 命令不可用，尝试 python 命令"
        try {
            $pyVersion = & python -V 2>&1
            $pythonFound = $true
            Write-Ok "检测到系统 Python: $pyVersion"
        } catch {
            Write-Verbose_ "未检测到系统 Python，将由 uv 管理"
        }
    }
    
    return $pythonFound
}

function Test-GpuAvailable {
    <#
    .SYNOPSIS
        检测是否有可用的 NVIDIA GPU
    #>
    if ($UseGPU) {
        try {
            & nvidia-smi 2>&1 | Out-Null
            Write-Ok "检测到 NVIDIA GPU"
            return $true
        } catch {
            Write-Warn "未检测到 NVIDIA GPU，将使用 CPU 版本"
            return $false
        }
    }
    return $false
}

# ======================================
# 第五步：工具软件安装函数
# ======================================

function Install-Uv {
    <#
    .SYNOPSIS
        确保 uv 工具已安装，如未安装则自动安装
    #>
    Write-Section "安装/检查 uv"
    Test-Cancel

    try {
        $uv = & uv --version 2>&1
        Write-Ok "uv 已安装: $uv"
        return $true
    } catch {
        Write-Warn "uv 未安装，尝试自动安装..."
        
        # 尝试通过 pip 安装
        try {
            Write-Info "通过 pip 安装 uv..."
            & py -m pip install --user uv 2>&1 | Out-Null
            $uv = & uv --version 2>&1
            Write-Ok "uv 安装成功: $uv"
            return $true
        } catch {
            Write-Error_ "uv 安装失败"
            Write-Host "请手动安装 uv: https://docs.astral.sh/uv/getting-started/installation/"
            return $false
        }
    }
}

function Install-PythonVersion {
    <#
    .SYNOPSIS
        通过 uv 安装指定版本的 Python
    #>
    param([string]$Version)
    
    Write-Section "安装 Python $Version"
    Test-Cancel

    try {
        Write-Info "确保 Python $Version 可用..."
        & uv python install $Version 2>&1 | ForEach-Object { Write-Verbose_ $_ }
        Write-Ok "Python $Version 已准备"
        return $true
    } catch {
        Write-Error_ "Python 安装失败: $_"
        Write-Host "请检查网络或尝试指定 -ForceCnMirror `$true"
        return $false
    }
}

# ======================================
# 第六步：源码管理函数
# ======================================

function Get-SourceCode {
    <#
    .SYNOPSIS
        获取项目源码（Clone 或 Pull）
    #>
    param(
        [string]$Url,
        [string]$TargetDir
    )
    
    Write-Section "获取源码"
    Test-Cancel

    if (Test-Path -Path $TargetDir) {
        Write-Info "项目目录已存在: $TargetDir"
        try {
            Write-Info "执行 git pull..."
            & git -C $TargetDir pull --ff-only 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            Write-Ok "源码已更新"
            return $true
        } catch {
            Write-Warn "git pull 失败（可能存在本地修改），继续进行: $_"
            return $true
        }
    } else {
        Write-Info "克隆仓库: $Url"
        try {
            & git clone $Url $TargetDir 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            Write-Ok "仓库克隆成功"
            return $true
        } catch {
            Write-Error_ "仓库克隆失败: $_"
            return $false
        }
    }
}

# ======================================
# 第七步：虚拟环境管理函数
# ======================================

function Resolve-VenvPath {
    <#
    .SYNOPSIS
        确定虚拟环境路径
    #>
    param(
        [string]$ProvidedPath,
        [string]$ProjectDir
    )
    
    Write-Section "创建虚拟环境"
    Test-Cancel

    if (-not [string]::IsNullOrEmpty($ProvidedPath)) {
        Write-Info "使用指定虚拟环境路径: $ProvidedPath"
        return $ProvidedPath
    }
    
    $defaultPath = Join-Path (Split-Path $ProjectDir -Parent) ".venv"
    if (Test-Path -Path $defaultPath) {
        Write-Info "复用父级虚拟环境: $defaultPath"
    } else {
        Write-Info "未提供虚拟环境路径，默认创建/使用父级目录下的 .venv"
    }
    
    return $defaultPath
}

function Create-VirtualEnv {
    <#
    .SYNOPSIS
        创建虚拟环境
    #>
    param(
        [string]$VenvPath,
        [string]$PythonVersion
    )
    
    if (Test-Path -Path $VenvPath) {
        Write-Info "虚拟环境已存在: $VenvPath"
        return $true
    }
    
    try {
        Write-Info "创建虚拟环境..."
        & uv venv $VenvPath --python $PythonVersion 2>&1 | ForEach-Object { Write-Verbose_ $_ }
        Write-Ok "虚拟环境创建成功"
        return $true
    } catch {
        Write-Error_ "虚拟环境创建失败: $_"
        return $false
    }
}

# ======================================
# 第八步：PyPI 源配置函数
# ======================================

function Configure-PyPiMirror {
    <#
    .SYNOPSIS
        配置 PyPI 镜像源
    #>
    param([bool]$ForceCn)
    
    Write-Section "配置 PyPI 源"
    Test-Cancel

    $indexUrl = ""
    if ($ForceCn) {
        $indexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
        Write-Ok "强制使用清华镜像"
        $env:PIP_INDEX_URL = $indexUrl
        $env:PIP_TRUSTED_HOST = "pypi.tuna.tsinghua.edu.cn"
    } else {
        Write-Info "使用官方 PyPI 源（失败自动回退清华镜像）"
    }
    
    $script:State.PyIndexUrl = $indexUrl
    return $indexUrl
}

# ======================================
# 第九步：PyTorch 安装函数
# ======================================

function Install-PyTorch {
    <#
    .SYNOPSIS
        安装 PyTorch（GPU/CPU 自检与回退）
    #>
    param(
        [string]$PythonExe,
        [bool]$HasGPU
    )
    
    Write-Section "安装 PyTorch"
    Test-Cancel

    $installSuccess = $false
    $torchIndex = ""

    if ($HasGPU) {
        $torchIndex = "https://download.pytorch.org/whl/cu121"
        Write-Info "安装 GPU 版 PyTorch (CUDA 12.1)..."
        try {
            & uv pip install -p $PythonExe torch torchaudio --index-url $torchIndex 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            $installSuccess = $true
            Write-Ok "GPU 版 PyTorch 安装成功"
        } catch {
            Write-Warn "GPU 版 PyTorch 安装失败，回退 CPU 版: $_"
        }
    }

    if (-not $installSuccess) {
        $torchIndex = "https://download.pytorch.org/whl/cpu"
        Write-Info "安装 CPU 版 PyTorch..."
        try {
            & uv pip install -p $PythonExe torch torchaudio --index-url $torchIndex 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            $installSuccess = $true
            Write-Ok "CPU 版 PyTorch 安装成功"
        } catch {
            Write-Warn "CPU 版 PyTorch 安装失败，继续进行（可能由依赖冲突引起）"
        }
    }
    
    $script:State.TorchInstalled = $installSuccess
    return $installSuccess
}

function Test-PyTorch {
    <#
    .SYNOPSIS
        执行 PyTorch 自检
    #>
    param([string]$PythonExe)
    
    Write-Section "PyTorch 自检"
    Test-Cancel
    
    $PyCode = @'
import torch
import platform
print(f"torch: {torch.__version__}")
print(f"cuda available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"cuda device count: {torch.cuda.device_count()}")
    print(f"cuda device name: {torch.cuda.get_device_name(0)}")
print(f"python: {platform.python_version()}")
'@

    try {
        & uv run -p $PythonExe python -c $PyCode 2>&1 | ForEach-Object { Write-Info $_ }
        Write-Ok "PyTorch 自检完成"
        return $true
    } catch {
        Write-Warn "PyTorch 自检失败（可能需要手动检查）: $_"
        return $false
    }
}

# ======================================
# 第十步：项目依赖安装函数
# ======================================

function Install-ProjectDependencies {
    <#
    .SYNOPSIS
        安装项目依赖（uv sync 或 requirements.txt）
    #>
    param(
        [string]$ProjectDir,
        [string]$PythonExe,
        [string]$IndexUrl
    )
    
    Write-Section "安装项目依赖"
    Test-Cancel

    $installSuccess = $false

    # 尝试 uv sync --frozen
    if (Test-Path -Path (Join-Path $ProjectDir "pyproject.toml")) {
        Write-Info "检测到 pyproject.toml，尝试 uv sync..."
        try {
            & uv sync --frozen -p $PythonExe 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            $installSuccess = $true
            Write-Ok "项目依赖安装成功（uv sync --frozen）"
        } catch {
            Write-Warn "uv sync --frozen 失败，尝试 uv sync: $_"
            try {
                & uv sync -p $PythonExe 2>&1 | ForEach-Object { Write-Verbose_ $_ }
                $installSuccess = $true
                Write-Ok "项目依赖安装成功（uv sync）"
            } catch {
                Write-Warn "uv sync 失败，继续尝试 requirements.txt"
            }
        }
    }

    # 尝试 requirements.txt
    if (-not $installSuccess -and (Test-Path -Path (Join-Path $ProjectDir "requirements.txt"))) {
        Write-Info "尝试安装 requirements.txt..."
        try {
            $pipArgs = @("-p", $PythonExe, "-r", (Join-Path $ProjectDir "requirements.txt"))
            if ($IndexUrl) {
                $pipArgs += @("--index-url", $IndexUrl)
            }
            & uv pip install @pipArgs 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            $installSuccess = $true
            Write-Ok "requirements.txt 依赖安装成功"
        } catch {
            Write-Warn "requirements.txt 安装失败，尝试回退清华镜像..."
            try {
                $fallbackUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
                & uv pip install -p $PythonExe -r (Join-Path $ProjectDir "requirements.txt") --index-url $fallbackUrl 2>&1 | ForEach-Object { Write-Verbose_ $_ }
                $installSuccess = $true
                Write-Ok "requirements.txt 依赖安装成功（清华镜像）"
                $env:PIP_INDEX_URL = $fallbackUrl
                $env:PIP_TRUSTED_HOST = "pypi.tuna.tsinghua.edu.cn"
            } catch {
                Write-Error_ "依赖安装全部失败: $_"
            }
        }
    }

    if ($installSuccess) {
        Write-Ok "所有依赖已安装"
    } else {
        Write-Warn "部分依赖安装失败，但继续进行（可能仅需部分依赖）"
    }
    
    $script:State.DepsInstalled = $installSuccess
    return $installSuccess
}

# ======================================
# 第十一步：Windows 平台优化函数
# ======================================

function Optimize-WindowsPlatform {
    <#
    .SYNOPSIS
        处理 Windows 平台特殊问题（如 DeepSpeed）
    #>
    param([string]$PythonExe)
    
    Write-Section "Windows 平台优化"
    Test-Cancel

    if ($PSVersionTable.Platform -eq "Win32NT" -or $PSVersionTable.OS -match "Windows") {
        Write-Info "检测到 Windows 平台，检查 DeepSpeed 依赖..."
        
        try {
            $DeepspeedCheck = & $PythonExe -c "try:`n    import deepspeed`n    print('deepspeed_installed')`nexcept:`n    print('deepspeed_not_installed')" 2>&1
            
            if ($DeepspeedCheck -match "deepspeed_installed") {
                Write-Warn "检测到已安装 DeepSpeed，但 Windows 上可能存在编译问题"
                Write-Info "尝试卸载 DeepSpeed..."
                try {
                    & uv pip uninstall -p $PythonExe deepspeed -y 2>&1 | ForEach-Object { Write-Verbose_ $_ }
                    Write-Ok "DeepSpeed 已卸载"
                } catch {
                    Write-Warn "DeepSpeed 卸载失败，但不影响继续运行"
                }
            } else {
                Write-Ok "DeepSpeed 未安装或已卸载"
            }
        } catch {
            Write-Verbose_ "DeepSpeed 检查跳过: $_"
        }
        
        Write-Info "Windows 用户提示：如需 DeepSpeed 支持，请："
        Write-Host "  1. 安装 Visual Studio Build Tools 或 MSVC" -ForegroundColor Cyan
        Write-Host "  2. 设置环境变量和编译工具链" -ForegroundColor Cyan
        Write-Host "  3. 或使用 WSL2/Linux 虚拟机" -ForegroundColor Cyan
    }
}

# ======================================
# 第十二步：环境变量与目录准备函数
# ======================================

function Initialize-ProjectEnvironment {
    <#
    .SYNOPSIS
        初始化项目环境（目录、环境变量等）
    #>
    param([string]$ProjectDir)
    
    Write-Section "环境变量与目录准备"
    Test-Cancel

    # 创建 checkpoints 目录
    $CheckpointsDir = Join-Path $ProjectDir "checkpoints"
    if (-not (Test-Path -Path $CheckpointsDir)) {
        New-Item -ItemType Directory -Path $CheckpointsDir -Force | Out-Null
        Write-Ok "创建 checkpoints 目录"
    }

    # 创建 README 占位符
    $ReadmePlaceholder = Join-Path $CheckpointsDir "README_PLACEHOLDER.txt"
    if (-not (Test-Path -Path $ReadmePlaceholder)) {
        $ReadmeContent = @"
IndexTTS2 模型权重与配置占位符

请按照以下步骤放置模型文件：

1. 下载模型权重文件（如 .pt、.pth、.safetensors 等）
2. 将配置文件（config.yaml 等）放在此目录
3. 确保文件结构与 README.md 的说明一致

镜像选项（如下载缓慢）：
- Hugging Face 官方: https://huggingface.co
- 国内镜像: https://hf-mirror.com
- ModelScope: https://modelscope.cn

下载后将 HF_ENDPOINT 设置为对应镜像地址。
"@
        $ReadmeContent | Out-File -FilePath $ReadmePlaceholder -Encoding UTF8 -Force
        Write-Ok "创建 checkpoints/README_PLACEHOLDER.txt"
    }

    # 设置 Hugging Face 镜像
    $env:HF_ENDPOINT = "https://hf-mirror.com"
    Write-Ok "设置 HF_ENDPOINT=$env:HF_ENDPOINT"

    # 设置缓存目录
    $CacheDir = Join-Path $ProjectDir ".cache" "hf"
    if (-not (Test-Path -Path $CacheDir)) {
        New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    }
    $env:HUGGINGFACE_HUB_CACHE = $CacheDir
    Write-Ok "设置 HUGGINGFACE_HUB_CACHE=$CacheDir"

    # 设置下载超时
    $env:HF_HUB_DOWNLOAD_TIMEOUT = 300
    $env:HF_HUB_LOCAL_DIR_AUTO_SYMLINK_CACHE = 0
    $env:MODELSCOPE_DOWNLOAD_TIMEOUT = 300

    return $CheckpointsDir
}

# ======================================
# 第十三步：模型下载函数
# ======================================

function Test-CheckpointFile {
    <#
    .SYNOPSIS
        检测单个 checkpoint 文件
    #>
    param(
        [string]$FilePath,
        [string]$FileName
    )
    
    if (Test-Path -Path $FilePath) {
        $fileSize = (Get-Item $FilePath).Length / 1MB
        Write-Ok "✓ $FileName (已存在, 大小: $([Math]::Round($fileSize, 2)) MB)"
        return @{Status = "OK"; Size = $fileSize}
    } else {
        Write-Warn "✗ $FileName (缺失)"
        return @{Status = "MISSING"; Size = 0}
    }
}

function Get-MissingCheckpoints {
    <#
    .SYNOPSIS
        检测缺失的 checkpoint 文件
    #>
    param(
        [string]$CheckpointsDir,
        [array]$RequiredFiles
    )
    
    $missingFiles = @()
    
    foreach ($file in $RequiredFiles) {
        $filePath = Join-Path $CheckpointsDir $file
        if (-not (Test-Path -Path $filePath)) {
            $missingFiles += $file
        }
    }
    
    return $missingFiles
}

function Download-ModelCheckpoints {
    <#
    .SYNOPSIS
        下载模型 checkpoints（多源回退策略）
    #>
    param(
        [string]$CheckpointsDir,
        [string]$DownloadSource,
        [bool]$SkipDownload
    )
    
    Write-Section "自动下载模型Checkpoints"
    Test-Cancel

    if ($SkipDownload) {
        Write-Info "已跳过 Checkpoints 下载（-SkipCheckpointDownload `$true）"
        return $true
    }

    # 定义所需文件
    $RequiredFiles = @("bpe.model", "gpt.pth", "config.yaml", "s2mel.pth", "wav2vec2bert_stats.pt")
    
    # 检查缺失文件
    Write-Info "检查现有 checkpoint 文件..."
    foreach ($file in $RequiredFiles) {
        $filePath = Join-Path $CheckpointsDir $file
        Test-CheckpointFile -FilePath $filePath -FileName $file
    }

    $MissingFiles = Get-MissingCheckpoints -CheckpointsDir $CheckpointsDir -RequiredFiles $RequiredFiles
    
    if ($MissingFiles.Count -eq 0) {
        Write-Ok "所有checkpoints文件已存在，跳过下载"
        return $true
    }

    Write-Info "检测到缺失的checkpoints文件: $($MissingFiles -join ', ')"
    Write-Info "开始自动下载模型（源: $DownloadSource）..."

    # 确定下载源优先级
    $DownloadSources = @()
    if ($DownloadSource -eq "huggingface" -or $DownloadSource -eq "hf-mirror") {
        $DownloadSources += "huggingface"
        if ($DownloadSource -eq "huggingface") {
            $DownloadSources += "modelscope"
        }
    } elseif ($DownloadSource -eq "modelscope") {
        $DownloadSources += "modelscope"
        $DownloadSources += "huggingface"
    }

    $DownloadSuccess = $false

    # 尝试 HuggingFace 下载
    if (("huggingface" -in $DownloadSources) -and -not $DownloadSuccess) {
        $DownloadSuccess = Invoke-HuggingFaceDownload -CheckpointsDir $CheckpointsDir -Source $DownloadSource
    }

    # 尝试 ModelScope 下载
    if (("modelscope" -in $DownloadSources) -and -not $DownloadSuccess) {
        $DownloadSuccess = Invoke-ModelScopeDownload -CheckpointsDir $CheckpointsDir
    }

    # 验证下载结果
    Verify-CheckpointDownload -CheckpointsDir $CheckpointsDir -RequiredFiles $RequiredFiles -Success $DownloadSuccess

    return $DownloadSuccess
}

function Invoke-HuggingFaceDownload {
    <#
    .SYNOPSIS
        通过 HuggingFace 下载模型
    #>
    param(
        [string]$CheckpointsDir,
        [string]$Source
    )
    
    Write-Info "检查 huggingface-hub 工具..."
    try {
        & hf --version 2>&1 | Out-Null
        Write-Ok "huggingface-hub 已安装"
    } catch {
        Write-Warn "huggingface-hub 未安装，尝试安装..."
        try {
            & uv tool install "huggingface-hub[cli,hf_xet]" 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            Write-Ok "huggingface-hub 安装成功"
        } catch {
            Write-Warn "huggingface-hub 安装失败，尝试继续: $_"
        }
    }

    # 设置 HF_ENDPOINT
    if ($Source -eq "hf-mirror") {
        $env:HF_ENDPOINT = "https://hf-mirror.com"
        Write-Info "使用 HF_ENDPOINT: https://hf-mirror.com"
    }

    Write-Info "通过 huggingface-cli 下载模型..."
    try {
        & hf download IndexTeam/IndexTTS-2 --local-dir=$CheckpointsDir --repo-type=model --resume-download 2>&1 | ForEach-Object {
            if ($_ -match "Downloading|100%") {
                Write-Info $_
            } elseif ($_ -match "error|Error|failed|Failed") {
                Write-Warn $_
            } else {
                Write-Verbose_ $_
            }
        }

        Write-Ok "HuggingFace 模型下载成功"
        return $true
    } catch {
        Write-Warn "HuggingFace 下载异常: $_"
        if ($_ -match "timeout|Timeout|TimeoutError|ConnectionError|Network") {
            Write-Warn "⚠ 检测到网络超时或连接错误，可能是网络波动"
            Write-Info "已下载的部分文件已保留，可以重新运行脚本继续下载"
        }
        return $false
    }
}

function Invoke-ModelScopeDownload {
    <#
    .SYNOPSIS
        通过 ModelScope 下载模型
    #>
    param([string]$CheckpointsDir)
    
    Write-Info "尝试使用 ModelScope 下载..."
    Write-Info "检查 modelscope 工具..."
    
    try {
        & modelscope --version 2>&1 | Out-Null
        Write-Ok "modelscope 已安装"
    } catch {
        Write-Warn "modelscope 未安装，尝试安装..."
        try {
            & uv tool install "modelscope" 2>&1 | ForEach-Object { Write-Verbose_ $_ }
            Write-Ok "modelscope 安装成功"
        } catch {
            Write-Warn "modelscope 安装失败: $_"
        }
    }

    Write-Info "通过 modelscope 下载模型..."
    try {
        & modelscope download --model IndexTeam/IndexTTS-2 --local_dir=$CheckpointsDir 2>&1 | ForEach-Object {
            if ($_ -match "Downloading|100%|下载") {
                Write-Info $_
            } elseif ($_ -match "error|Error|failed|Failed") {
                Write-Warn $_
            } else {
                Write-Verbose_ $_
            }
        }

        Write-Ok "ModelScope 模型下载成功"
        return $true
    } catch {
        Write-Warn "ModelScope 下载异常: $_"
        if ($_ -match "timeout|Timeout|TimeoutError|ConnectionError|Network") {
            Write-Warn "⚠ 检测到网络超时或连接错误，可能是网络波动"
            Write-Info "已下载的部分文件已保留，可以重新运行脚本继续下载"
        }
        return $false
    }
}

function Verify-CheckpointDownload {
    <#
    .SYNOPSIS
        验证 checkpoint 下载结果
    #>
    param(
        [string]$CheckpointsDir,
        [array]$RequiredFiles,
        [bool]$Success
    )
    
    Write-Section "验证下载结果"

    $FilesOk = 0
    $FilesEmpty = 0
    $FilesMissing = 0
    $DownloadIncomplete = $false

    foreach ($file in $RequiredFiles) {
        $filePath = Join-Path $CheckpointsDir $file
        if (Test-Path -Path $filePath) {
            $fileSize = (Get-Item $filePath).Length
            if ($fileSize -gt 0) {
                $fileSizeMB = $fileSize / 1MB
                Write-Ok "✓ $file ($([Math]::Round($fileSizeMB, 2)) MB)"
                $FilesOk += 1
            } else {
                Write-Warn "⚠ $file (文件为空，可能下载中断)"
                $FilesEmpty += 1
                $DownloadIncomplete = $true
            }
        } else {
            Write-Warn "✗ $file (缺失)"
            $FilesMissing += 1
            $DownloadIncomplete = $true
        }
    }

    Write-Info "下载统计: 完成=$FilesOk, 为空=$FilesEmpty, 缺失=$FilesMissing, 总计=$($RequiredFiles.Count)"

    # 宽松验证逻辑
    if ($FilesOk -eq 0 -and $FilesMissing -eq $RequiredFiles.Count) {
        Write-Error_ "✗ 所有 checkpoints 文件都缺失，下载完全失败"
        Write-Warn "请尝试以下方案："
        Write-Host "  1. 检查网络连接是否正常" -ForegroundColor Yellow
        Write-Host "  2. 尝试更改下载源" -ForegroundColor Yellow
        Write-Host "  3. 手动从 https://huggingface.co/IndexTeam/IndexTTS-2 下载" -ForegroundColor Yellow
        exit 1
    } elseif ($DownloadIncomplete) {
        Write-Warn "⚠ 部分 checkpoints 文件下载不完整或缺失"
        Write-Info "已下载的文件将被保留，可继续下载"
        Write-Info "脚本仍将继续进行冒烟测试"
    } else {
        Write-Ok "所有 checkpoints 文件验证完成"
    }
}

# ======================================
# 第十四步：冒烟测试函数
# ======================================

function Invoke-SmokeTest {
    <#
    .SYNOPSIS
        执行冒烟测试：同步依赖并运行 WebUI
    #>
    param(
        [string]$ProjectDir,
        [string]$PythonExe
    )
    
    Write-Section "冒烟测试：同步依赖并运行 WebUI"
    Test-Cancel

    # 步骤 1: 运行 uv sync
    Write-Info "步骤 1/2: 运行 uv sync..."
    if ($PSVersionTable.Platform -eq "Win32NT" -or $PSVersionTable.OS -match "Windows") {
        Write-Info "Windows 平台：运行 uv sync --extra webui（跳过 deepspeed）..."
        try {
            & uv sync -p $PythonExe --extra webui 2>&1 | ForEach-Object {
                if ($_ -match "error|failed|Error") {
                    Write-Warn $_
                } else {
                    Write-Verbose_ $_
                }
            }
            Write-Ok "uv sync --extra webui 完成"
        } catch {
            Write-Error_ "uv sync --extra webui 失败: $_"
            exit 1
        }
    } else {
        try {
            & uv sync -p $PythonExe --all-extras 2>&1 | ForEach-Object {
                if ($_ -match "error|failed|Error") {
                    Write-Warn $_
                } else {
                    Write-Verbose_ $_
                }
            }
            Write-Ok "uv sync --all-extras 完成"
        } catch {
            Write-Error_ "uv sync 失败: $_"
            exit 1
        }
    }

    # 步骤 2: 尝试启动 WebUI
    Write-Info "步骤 2/2: 启动 WebUI 进行功能测试..."
    
    if (-not (Test-Path -Path (Join-Path $ProjectDir "webui.py"))) {
        Write-Warn "未找到 webui.py，跳过 WebUI 启动测试"
        return $true
    }

    return Invoke-WebUIStartup -ProjectDir $ProjectDir -PythonExe $PythonExe
}

function Invoke-WebUIStartup {
    <#
    .SYNOPSIS
        启动 WebUI 并监听输出
    #>
    param(
        [string]$ProjectDir,
        [string]$PythonExe
    )
    
    try {
        # 创建进程信息
        $processInfo = New-Object System.Diagnostics.ProcessStartInfo
        $processInfo.FileName = "uv"
        $processInfo.Arguments = "run", "webui.py"
        $processInfo.WorkingDirectory = $ProjectDir
        $processInfo.RedirectStandardOutput = $true
        $processInfo.RedirectStandardError = $true
        $processInfo.UseShellExecute = $false
        $processInfo.CreateNoWindow = $true

        $webUIProcess = New-Object System.Diagnostics.Process
        $webUIProcess.StartInfo = $processInfo
        $webUIProcess.Start() | Out-Null
        $script:State.WebUIProcess = $webUIProcess

        Write-Info "WebUI 进程已启动 (PID: $($webUIProcess.Id))"
        Write-Info "监听 WebUI 输出流，等待启动完成..."

        $urlFound = $false
        $maxWaitTime = 300
        $startTime = Get-Date

        while ((Get-Date) - $startTime -lt [timespan]::FromSeconds($maxWaitTime)) {
            Test-Cancel

            if ($webUIProcess.HasExited) {
                Write-Error_ "✗ WebUI 进程已退出 (退出码: $($webUIProcess.ExitCode))"
                return $false
            }

            if ($null -ne $webUIProcess.StandardOutput) {
                $line = $webUIProcess.StandardOutput.ReadLine()
                if ($null -ne $line) {
                    Write-Host $line -ForegroundColor Gray

                    if ($line -match "Running on local URL:|To create a public link") {
                        $urlFound = $true
                        Write-Ok "✓ WebUI 已成功启动"
                        Write-Info "检测到: $line"
                        break
                    }
                }
            }

            Start-Sleep -Milliseconds 100
        }

        if ($urlFound) {
            Write-Section "安装成功！WebUI 已就绪"
            Write-Ok "✓ 冒烟测试通过"
            
            # 倒计时后停止
            Write-Info "将在 10 秒后终止 WebUI 进程..."
            for ($i = 10; $i -gt 0; $i--) {
                Test-Cancel
                Write-Host "`r倒计时: $i 秒..." -NoNewline -ForegroundColor Cyan
                Start-Sleep -Seconds 1
            }
            Write-Host ""
            
            return $true
        } else {
            Write-Error_ "✗ 等待超时：未检测到 WebUI 启动成功信息"
            return $false
        }
    } catch {
        Write-Error_ "启动 WebUI 失败: $_"
        return $false
    } finally {
        # 清理进程
        Write-Info "停止 WebUI 进程..."
        if ($script:State.WebUIProcess -and -not $script:State.WebUIProcess.HasExited) {
            Stop-Process -Id $script:State.WebUIProcess.Id -Force -ErrorAction SilentlyContinue
        }
        $script:State.WebUIProcess = $null
        Write-Ok "WebUI 进程已停止"
    }
}

# ======================================
# 第十五步：最终诊断与输出函数
# ======================================

function Show-FinalDiagnostics {
    <#
    .SYNOPSIS
        显示最终诊断信息
    #>
    param(
        [string]$ProjectDir,
        [string]$PythonExe,
        [string]$CheckpointsDir
    )
    
    Write-Section "部署完成 - 环境诊断信息"

    Write-Ok "虚拟环境 Python 路径: $PythonExe"

    # 检查 PyTorch
    try {
        $TorchCheck = & uv run -p $PythonExe python -c "import torch; print(f'torch={torch.__version__}, cuda={torch.cuda.is_available()}')" 2>&1
        Write-Ok "PyTorch 状态: $TorchCheck"
    } catch {
        Write-Warn "无法获取 PyTorch 状态"
    }

    Write-Ok "项目根路径: $ProjectDir"
    Write-Ok "PyPI 镜像源: $(if ($script:State.PyIndexUrl) { $script:State.PyIndexUrl } else { '官方（自动回退清华镜像）' })"
    Write-Ok "Hugging Face 镜像: $env:HF_ENDPOINT"
    Write-Ok "检查点目录: $CheckpointsDir"

    Write-Section "快速启动与后续步骤"

    if (Test-Path -Path (Join-Path $ProjectDir "webui.py")) {
        Write-Info "WebUI 启动示例："
        Write-Host "  uv run webui.py" -ForegroundColor Cyan
    }

    Write-Section "✅ 一键部署完成"
    Write-Host "下一步："
    Write-Host "  1. 将模型权重放在 $CheckpointsDir 目录下"
    Write-Host "  2. 查看项目 README.md 获取详细使用说明"
    Write-Host "  3. 运行 WebUI 或 API-B 脚本进行推理"
}

# ======================================
# 主流程 - COT 链式思考执行
# ======================================

function Main {
    <#
    .SYNOPSIS
        主函数 - 按照 COT 逻辑流程执行部署
    #>
    
    try {
        # 初始化
        Initialize-ColorSupport
        
        # 第一步：系统检查
        Get-SystemInfo
        $pythonExists = Test-PythonAvailable
        $script:State.HasGPU = Test-GpuAvailable
        
        # 第二步：安装工具
        if (-not (Install-Uv)) {
            exit 1
        }
        
        if (-not (Install-PythonVersion -Version $PythonVersion)) {
            exit 1
        }
        
        # 第三步：获取源码
        if (-not (Get-SourceCode -Url $RepoUrl -TargetDir $ProjectDir)) {
            exit 1
        }
        
        # 切换到项目目录
        Push-Location $ProjectDir
        
        # 第四步：虚拟环境
        $ResolvedVenvPath = Resolve-VenvPath -ProvidedPath $VenvPath -ProjectDir $ProjectDir
        $PythonExe = Join-Path $ResolvedVenvPath "Scripts" "python.exe"
        
        if (-not (Create-VirtualEnv -VenvPath $ResolvedVenvPath -PythonVersion $PythonVersion)) {
            exit 1
        }
        
        # 第五步：PyPI 配置
        Configure-PyPiMirror -ForceCn $ForceCnMirror
        
        # 第六步：PyTorch 安装
        Install-PyTorch -PythonExe $PythonExe -HasGPU $script:State.HasGPU
        Test-PyTorch -PythonExe $PythonExe
        
        # 第七步：项目依赖
        Install-ProjectDependencies -ProjectDir $ProjectDir -PythonExe $PythonExe -IndexUrl $script:State.PyIndexUrl
        
        # 第八步：Windows 优化
        Optimize-WindowsPlatform -PythonExe $PythonExe
        
        # 第九步：环境初始化
        $CheckpointsDir = Initialize-ProjectEnvironment -ProjectDir $ProjectDir
        
        # 第十步：模型下载
        Download-ModelCheckpoints -CheckpointsDir $CheckpointsDir -DownloadSource $DownloadSource -SkipDownload $SkipCheckpointDownload
        
        # 第十一步：冒烟测试
        if (-not (Invoke-SmokeTest -ProjectDir $ProjectDir -PythonExe $PythonExe)) {
            exit 1
        }
        
        # 第十二步：最终诊断
        Show-FinalDiagnostics -ProjectDir $ProjectDir -PythonExe $PythonExe -CheckpointsDir $CheckpointsDir
        
        # 清理
        Pop-Location
        Invoke-Cleanup
        
        Write-Ok "部署流程完成！"
        exit 0
        
    } catch {
        Write-Error_ "主流程异常: $_"
        Invoke-Cleanup
        exit 1
    }
}

# ======================================
# 脚本入口
# ======================================

Main
