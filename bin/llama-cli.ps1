# Thin wrapper (PowerShell): interactive llama-cli with the pinned vision model.
$ErrorActionPreference = "Stop"

$llamaHome = if ($env:LLAMA_HOME) { $env:LLAMA_HOME } else { Join-Path $env:LOCALAPPDATA "llama-b11552-bin-win-cpu-x64" }
$exe = Join-Path $llamaHome "llama-cli.exe"
if (-not (Test-Path $exe)) { throw "llama-cli.exe not found at $exe (set LLAMA_HOME)" }

$hfRoot = Join-Path $env:USERPROFILE ".cache\huggingface"
$env:HF_HOME = $hfRoot
$vlDir = Get-ChildItem (Join-Path $hfRoot "hub\models--Qwen--Qwen3-VL-2B-Instruct-GGUF\snapshots") -Directory | Select-Object -First 1
if (-not $vlDir) { throw "Qwen3-VL-2B GGUF not found in $hfRoot\hub" }
$model = Join-Path $vlDir.FullName "Qwen3VL-2B-Instruct-Q4_K_M.gguf"

& $exe -m $model @args
