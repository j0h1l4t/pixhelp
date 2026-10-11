# pixhelp

A local-first, non-intrusive desktop assistant for Windows. It watches the
screen at a low fixed frequency — a change-gate skips idle frames, so OCR only
runs when something actually changed — asks a small local LLM whether a short,
actionable tip is warranted right now, and shows the answer as a click-through
toast in an invisible overlay. Nothing leaves the machine: no cloud inference,
no continuous capture loop, no background recording.

## How it works

`capture → change-gate → WinRT OCR → llama-server (local, CPU) → feedback gate → overlay toast`

- **`backend/`** — one async Python process (single `asyncio` loop) owning
  screen capture (dxcam), the numpy change-gate, native WinRT OCR, the agent
  loop against `llama-server`, and the WebSocket server that feeds the UI.
- **`ui/`** — Flutter desktop overlay: transparent, always-on-top,
  click-through window fed over a token-authenticated WebSocket on loopback.
- **`llama-server`** — prebuilt Windows CPU binary (never compiled from
  source), spawned and supervised by the backend. Models: Qwen3-VL-2B (vision)
  and Qwen2.5-0.5B (text-only fallback).
- **Telemetry** — optional OTLP export, off by default; no non-loopback
  listeners anywhere.

## Privacy

- Screen frames and OCR text live in memory only — never logged, exported, or
  written to disk.
- All IPC stays on `127.0.0.1` (token-authenticated); telemetry is disabled
  unless explicitly configured.
- Files committed to this repo must stay privacy-clean (see `AGENTS.md`).

## Repository layout

| path | contents |
|---|---|
| `backend/` | Python agent, unit tests, `config.yaml` (single source of truth for runtime knobs) |
| `ui/` | Flutter overlay app |
| `spike/` | measurement harnesses (`capture/`, `inference/`); their `results.md` files are the evidence base for the specs |
| `bin/` | tracked llama.cpp wrappers (WSL bash + PowerShell) |
| `specs.md` | architecture blueprint — the *why* behind every design choice |
| `AGENTS.md` | conventions and constraints for contributors/agents |

## Status

Scaffolded: the architecture is specified (`specs.md`), the backend's core
logic is unit-tested, and the measurement harnesses exist. End-to-end
integration on the target machine is still pending.

## Development

1. **Edit** — launch WSL from PowerShell (`PS> wsl`) and run your editor of
   choice (e.g. `opencode`); the source lives on the Windows filesystem.
2. **Backend** (Windows) — from `backend\`: `setup_env.ps1`, then
   `env\Scripts\python -m pixhelp`.
3. **UI** (Windows) — from `ui\`: `flutter run -d windows`.
4. **Checks** (repo root, backend venv) — `ruff check backend spike` and
   `python -m pytest backend/tests`.
5. **Spikes** (Windows, measurement only) — from inside a spike folder:
   `..\..\backend\env\Scripts\python.exe bench_capture.py` (spikes share the
   backend venv).
