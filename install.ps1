# voicehist 一键安装 (Windows / PowerShell)
#   iwr -useb https://raw.githubusercontent.com/GreenBr/voicehist/main/install.ps1 | iex
# 或 clone 之后直接： .\install.ps1

$ErrorActionPreference = "Stop"
$Root = Join-Path $env:USERPROFILE ".voicehist"
$Venv = Join-Path $Root "venv"
$Py   = Join-Path $Venv "Scripts\python.exe"
$Pyw  = Join-Path $Venv "Scripts\pythonw.exe"

function Say($m, $c = "White") { Write-Host $m -ForegroundColor $c }

Say ""
Say "  voicehist - 本地语音输入 + 完整历史留底" Cyan
Say "  ============================================" Cyan
Say ""

# ---------- 1. Python ----------
Say "[1/6] 检查 Python..." Yellow
$sysPy = $null
foreach ($c in @("python", "py")) {
    try {
        $v = & $c --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)") {
            if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9) { $sysPy = $c; break }
        }
    } catch {}
}
if (-not $sysPy) {
    Say "  找不到 Python 3.9+。请先安装：https://www.python.org/downloads/" Red
    Say "  安装时记得勾选 Add Python to PATH" Red
    exit 1
}
Say "  OK - $(& $sysPy --version)" Green

# ---------- 2. 显示卡 ----------
Say "[2/6] 检查显示卡..." Yellow
$gpu = $false
try {
    $null = nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0) { $gpu = $true }
} catch {}
if ($gpu) {
    Say "  侦测到 NVIDIA GPU - 会用 GPU 加速" Green
} else {
    Say "  没有 NVIDIA GPU - 会退回 CPU（能用，但转写较慢）" Yellow
}

# ---------- 3. 复制程式 ----------
Say "[3/6] 安装程式档..." Yellow
New-Item -ItemType Directory -Force $Root | Out-Null
$src = $PSScriptRoot
foreach ($f in @("voice.py", "history_gui.py", "settings_gui.py", "make_icons.py")) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p $Root -Force }
}
Say "  已放到 $Root" Green

# ---------- 4. 虚拟环境 + 套件 ----------
Say "[4/6] 建立虚拟环境并安装套件（这步最久，请耐心等）..." Yellow
if (-not (Test-Path $Py)) { & $sysPy -m venv $Venv }
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet faster-whisper sounddevice pyperclip keyboard pillow pystray
if ($gpu) {
    Say "  安装 CUDA 执行库（约 2GB）..." Yellow
    & $Py -m pip install --quiet nvidia-cublas-cu12 nvidia-cudnn-cu12
}
Say "  套件安装完成" Green

# ---------- 5. 图示 ----------
Say "[5/6] 产生图示..." Yellow
$env:PYTHONIOENCODING = "utf-8"
& $Py (Join-Path $Root "make_icons.py") | Out-Null
Say "  OK" Green

# ---------- 6. 捷径 ----------
Say "[6/6] 建立捷径..." Yellow
$ws  = New-Object -ComObject WScript.Shell
$dsk = [Environment]::GetFolderPath('Desktop')
function New-Lnk($path, $script, $icon, $desc) {
    $l = $ws.CreateShortcut($path)
    $l.TargetPath       = $Pyw
    $l.Arguments        = '"' + (Join-Path $Root $script) + '"'
    $l.WorkingDirectory = $Root
    $l.IconLocation     = (Join-Path $Root $icon)
    $l.Description      = $desc
    $l.Save()
}
New-Lnk "$dskoicehist.lnk" "voice.py" "voiceinput.ico" "voicehist - 语音输入（点开待命，Ctrl+空白 开始讲话）"
Say "  桌面捷径已建立" Green

$ans = Read-Host "  要设定开机自动启动吗？(Y/n)"
if ($ans -ne "n" -and $ans -ne "N") {
    $stp = [Environment]::GetFolderPath('Startup')
    New-Lnk "$stpoicehist.lnk" "voice.py" "voiceinput.ico" "voicehist（开机自动启动）"
    Say "  已设定开机自动启动" Green
}

Say ""
Say "  安装完成！" Green
Say ""
Say "  双击桌面的 voicehist 启动（第一次会下载约 1.5GB 的模型，请等它跑完）"
Say "  启动后按 Ctrl+空白 开始讲话，再按一次或按 ESC 结束"
Say "  历史与设定：系统匣图示右键，或再点一次桌面图示"
Say ""
