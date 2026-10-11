# Thin wrapper (PowerShell): pinned Qwen3-VL-2B vision model on loopback.
$ErrorActionPreference = "Stop"

$llamaHome = if ($env:LLAMA_HOME) { $env:LLAMA_HOME } else { Join-Path $env:LOCALAPPDATA "llama-b11552-bin-win-cpu-x64" }
$exe = Join-Path $llamaHome "llama-server.exe"
if (-not (Test-Path $exe)) { throw "llama-server.exe not found at $exe (set LLAMA_HOME)" }

$hfRoot = Join-Path $env:USERPROFILE ".cache\huggingface"
$env:HF_HOME = $hfRoot
$vlDir = Get-ChildItem (Join-Path $hfRoot "hub\models--Qwen--Qwen3-VL-2B-Instruct-GGUF\snapshots") -Directory | Select-Object -First 1
if (-not $vlDir) { throw "Qwen3-VL-2B GGUFs not found in $hfRoot\hub" }
$model = Join-Path $vlDir.FullName "Qwen3VL-2B-Instruct-Q4_K_M.gguf"
$mmproj = Join-Path $vlDir.FullName "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"

$port = if ($env:PORT) { $env:PORT } else { "8080" }
$threads = if ($env:THREADS) { $env:THREADS } else { "4" }
$ctx = if ($env:CTX) { $env:CTX } else { "4096" }

& $exe -m $model --mmproj $mmproj --host 127.0.0.1 --port $port -t $threads -c $ctx --parallel 1
