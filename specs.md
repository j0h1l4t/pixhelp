# System Architecture Blueprint: Autonomous Live Screen Analysis Agent

## 1. Executive Summary
This document outlines the software architecture for an autonomous desktop agent designed to observe a live Windows environment, analyze user behavior, and provide non-intrusive feedback.

**Target Machine:** laptop with an Intel Core i7-10510U (4 cores / 8 threads, AVX2/FMA/BMI2, **no AVX-512**), Intel UHD integrated graphics (no discrete GPU), and 8GB RAM.

**Hardware Constraint Acknowledgment:**
Every design choice below is driven by that machine. Due to the lack of a discrete GPU, the design pivots from a high-frequency microservice model to a consolidated, low-frequency, highly optimized asynchronous pipeline. Local LLM inference is CPU-only via prebuilt `llama.cpp` binaries; OCR is the OS-native WinRT engine (no model download, no Python-side inference).

**Source of truth for runtime knobs:** `backend/config.yaml`. When this document and the config disagree on a default, the config wins; this document explains *why* the default is what it is.

---

## 2. High-Level Architecture
The system is divided into **four** logical boundaries, interacting over local IPC (WebSockets on loopback) and an external telemetry protocol (OTLP over HTTP).

1. **Consolidated Python Backend (Sensory + Cognitive):** A single asynchronous Python process (`backend/pixhelp/`) handling screen capture, the change-gate, native OCR extraction, AI orchestration, the feedback pipeline, and the WebSocket server that feeds the UI.
2. **Local Inference Engine:** A standalone, prebuilt `llama.cpp` **Windows CPU** binary (`llama-server.exe`) providing LLM/VLM intelligence (plain install, never compiled from source). The backend spawns and supervises it.
3. **Presentation Layer:** A lightweight Flutter desktop app (`ui/`) rendering invisible, always-on-top, click-through overlay toasts.
4. **Remote Telemetry:** An outsourced observability pipeline (OTLP) to monitor system health without consuming local RAM.

**Runtime topology:** `Flutter ui` ⇐ WebSocket (127.0.0.1) ⇐ `Python backend` ⇒ HTTP (127.0.0.1:8080) ⇒ `llama-server`; the backend also exports OTLP to a remote collector. Nothing listens on non-loopback interfaces.

---

## 3. Component Specifications

### 3.1. The Consolidated Backend (Python 3.11+; validated on 3.14)
To minimize RAM usage and CPU context switching, the Sensory and Cognitive tiers are merged into a single `asyncio` event loop living in `backend/pixhelp/`.

*   **Concurrency Model (Required):** The event loop must stay responsive — it also serves the WebSocket client and the OTel batch processor. Therefore every blocking call runs off-loop:
    *   `dxcam.grab(...)` — blocking poll → `asyncio.to_thread`.
    *   WinRT `recognize_async` — awaitable but ~0.4–1.1s of native work → run in a thread executor (`await_op` pattern proven in `spike/capture/bench_capture.py`).
    *   Pillow JPEG encode (~85–105ms) → `asyncio.to_thread`.
    *   **Frame ownership:** dxcam reuses its capture buffer; the frame returned by `grab()` must be **copied (or fully consumed) before any `await`**, or the next grab can mutate it mid-OCR.
*   **Capture Subsystem:** Uses `dxcam` to grab the GPU frame buffer at the configured heartbeat rate. Always call `grab(new_frame_only=False)` — the default `grab()` returns `None` when the desktop is idle, which is the common case at this polling rate. Use `output_color="BGRA"` to avoid the OpenCV dependency. Multi-monitor: select via `dxcam.create(output_device=N)` from `capture.monitor`. DPI: dxcam captures physical pixels; overlay rects sent to Flutter are physical pixels too, and Flutter must convert via the monitor's DPI scale when anchoring.
*   **Change-Gate (Required):** Before running OCR, compare the new frame against the last frame that was OCR'd using a **numpy mean-abs-diff with a mandatory `int16` cast** — `uint8` subtraction wraps around (`10 - 250 == 16` in uint8), silently under-reporting the diff on exactly the frames that changed. Two implementations are benchmarked by the spike (`change-gate full-res` and `change-gate stride 4` rows in `spike/capture/results.md`): exact full-frame diff vs a `frame[::4, ::4]` strided sample (~1/16 the pixels). The implementation and threshold (`gate.threshold_mad`, plus a hysteresis margin) are chosen from those measurements; the gate is the reason the worst-case OCR cost is acceptable — the *typical* heartbeat pays only the gate.
    *   Caveats: video playback / GIFs / blinking cursors near the threshold will pass the gate by design; hysteresis (separate enter/exit thresholds) prevents flapping on noise (compression-free BGRA makes noise minor, but cursor blink is real).
*   **OCR Subsystem:** Abandons heavy frameworks (like PaddleOCR) in favor of the **Native Windows Runtime (WinRT) OCR API** via the **`winrt-*` split packages** (`winrt-runtime`, `winrt-Windows.Media.Ocr`, `winrt-Windows.Graphics.Imaging`, `winrt-Windows.Storage.Streams`, `winrt-Windows.Foundation`, `winrt-Windows.Foundation.Collections`, `winrt-Windows.Globalization`) — *not* the legacy monolithic `winsdk` package. Convert the captured frame to a `SoftwareBitmap` via **`CreateCopyFromBuffer`** (raw BGRA → `Buffer`), *not* the JPEG→`BitmapDecoder` roundtrip, which wastes ~130ms.
    *   **Measured cost (1920×1080, text-dense screen, buffer path, 5 full-res runs in `spike/capture/results.md`):** median **411–829ms**, p95 **492–1,069ms**. Run-to-run variance is driven by screen content, not by code changes; budget with the *worst* run.
    *   Mitigations, if needed: downscale the frame before OCR (`ocr.downscale 0.5`), or capture only a region (`capture.region L,T,R,B`); both shrink the bitmap, which is the only lever — output granularity does not affect recognition cost.
    *   **Language packs:** `OcrEngine` uses user-profile languages; the spike detected 4 installed (en-GB, en-US, es-MX, zh-Hans-CN). The backend pins languages from `ocr.languages` (`try_create_from_language`) rather than the implicit user profile, so cost and behavior don't drift with OS settings.
*   **OCR Output Granularity:** The recognition result is aggregated before it leaves the backend: word boxes union into line rects, lines single-linkage cluster (vertical gap ≤ ~1 line height **and** horizontal overlap), then greedy nearest-center merging down to **≤ 5 text-block rects** (`ocr.detail {blocks,lines,words}` default `blocks`, `ocr.max_blocks 5`; `words` kept for debugging). Full text is preserved — each block carries its lines' text joined in reading order. On a text-dense 1080p frame this turns 479 word rects / ~50 KiB JSON into **5 rects / ~5–7 KiB**. Aggregation + serialization measured **~7–17ms median (p95 ≤ 23ms; 43/64ms at downscale 0.8)**. Rects are always mapped back to full-screen coordinates when `region`/`downscale` crop the OCR bitmap.
*   **JPEG Encode (Lazy, Vision-Path Only):** The frame is JPEG-encoded (Pillow, measured **82–105ms median / 102–132ms p95**) **only at the moment it is actually sent to the vision model**. It is never encoded for OCR (the buffer path feeds raw BGRA), never encoded on change-gated (skipped) heartbeats, and never encoded on text-only heartbeats.
*   **Agentic Loop:** Replaces the heavy FastMCP server of earlier designs. The main loop (`backend/pixhelp/main.py`) implements a **fixed-tick heartbeat** (see §9): on each tick it gathers the image/OCR data (if the gate passed), constructs the prompt, queries the local inference engine over HTTP, validates the feedback rule, and pushes accepted rules to the UI over WebSocket. **Routing rule:** the vision model is the default; if the reasoning deadline (§9) is hit `N` consecutive times (config), the backend degrades to the text-only model for the session (telemetry records the degradation); it re-tries vision on the next config restart.

### 3.2. Local Inference Engine (`llama.cpp`)
Standard Python AI runners (like vLLM) cannot execute without an NVIDIA/AMD GPU. We utilize `llama.cpp` for bare-metal CPU inference.

*   **Installation (Plain, No Build):** Use the official prebuilt **Windows CPU x64** release (e.g. `llama-b11552-bin-win-cpu-x64`, extracted under `%LOCALAPPDATA%`). `bin/llama-server` and `bin/llama-cli` in this repo are **tracked, thin shell/PowerShell wrappers** pointing at those `.exe`s with sane dev flags (they compute Windows paths via `wslpath`; raw `/mnt/c/...` values are unusable by the `.exe`s). **There is no C++ toolchain:** do not install Visual Studio Build Tools or CMake, and never compile llama.cpp from source (no `-DGGML_*` / `-DCMAKE_*` flags). The stock CPU build targets the i7-10510U's AVX2/FMA/BMI2 ISA (no AVX-512 required). *Not applicable by design:* OpenVINO backend (text-only upstream — cannot run the vision model); SYCL iGPU backend (requires 11th-gen Core+); Vulkan (`-DGGML_VULKAN=1` would require a custom build — out of scope).
*   **Models of Record (pinned, already cached under the HF cache):**
    *   *Vision (default):* **`Qwen/Qwen3-VL-2B-Instruct-GGUF`** → `Qwen3VL-2B-Instruct-Q4_K_M.gguf` (1.1 GiB weights) **plus** `mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf` (425 MiB). The mmproj file is **mandatory** to serve images — `llama-server` loads it via `--mmproj` and fails vision requests without it. Enables spatial GUI understanding. Image-encoder prefill runs on CPU cores and can consume much of the heartbeat, so the vision payload is downscaled/cropped (§3.1) before send.
    *   *Text-only (fallback):* **`Qwen/Qwen2.5-0.5B-Instruct-GGUF`** → `qwen2.5-0.5b-instruct-q4_k_m.gguf` (469 MiB). Consumes only the WinRT OCR JSON — the fast path when the vision deadline budget is blown.
*   **Execution:** The backend **spawns and supervises** `llama-server.exe` (process lifecycle in `backend/pixhelp/inference.py`), bound to `127.0.0.1:8080`, `-t 4` (physical cores) to prevent thread thrashing, `-c` sized for the vision token budget (see §12), one request slot (`--parallel 1`). Model paths are **pinned local file paths from `config.yaml` — never resolved via `-hf` at runtime** (network access at startup is a failure mode, and re-resolution can silently pull a different revision). Startup is considered ready when `GET /health` returns 200; the backend polls `/health` and restarts the server with backoff on exit/crash.

### 3.3. The Presentation Layer (Flutter & Dart, `ui/`)
A zero-friction, always-on-top notification system.

*   **Technology:** Flutter desktop app. `window_manager` is the primary API — it already exposes `setIgnoreMouseEvents(true)` (click-through) and `setBackgroundColor(Colors.transparent)`. Direct Dart FFI to `user32.dll` is the **fallback**, needed only for styles `window_manager` does not expose.
*   **Win32 Styles:** `WS_EX_LAYERED | WS_EX_TRANSPARENT` (transparent + click-through) **plus `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`** — without these, toasts steal focus when they appear and show up in the taskbar/alt-tab. `HWND_TOPMOST` is maintained (re-asserted if another topmost window steals it).
*   **Behavior:** The window background is strictly transparent. It ignores all mouse clicks, allowing the user to interact with underlying applications seamlessly. Toasts anchor to the **work area** of the monitor containing the anchor rect (`SystemParametersInfo(SPI_GETWORKAREA)` / monitor work area), never over the taskbar; multi-monitor means the toast renders on the monitor where the triggering content was.
*   **State Management:** Connects to the backend WebSocket (§3.4). Incoming accepted feedback rules trigger animated `OverlayEntry` toasts that dismiss themselves after a set duration; a `clear` message dismisses the current toast immediately.

### 3.4. IPC Contract (Backend ⇄ Flutter)
The backend **hosts** the WebSocket server; Flutter is the client.

*   **Transport:** `ws://127.0.0.1:<ipc.port>` (loopback only), token-authenticated (first message must be `{"type":"hello","token":...}`; mismatch ⇒ close). One client at a time; a new connection supersedes the old.
*   **Envelope (versioned):** every message carries `schema_version` (currently `1`) and `type`.
    *   Server → client: `{"type":"toast","id","title","body","anchor_rect":{"x","y","width","height"},"severity":"info|warn","ttl_s"}`, `{"type":"clear"}`, `{"type":"ping"}`.
    *   Client → server: `{"type":"hello","token"}`, `{"type":"pong"}`.
*   **Reconnect/backpressure:** the client reconnects with exponential backoff (1s→30s cap). The server keeps at most one pending message per client; if the client stalls, older toasts are **dropped** (latest-wins) rather than queued — a stale toast is worse than no toast.
*   **Privacy:** messages carry only feedback text the model produced and an anchor rect — never raw OCR text, images, or window titles (§7).

### 3.5. Feedback-Rule Output Contract (Model → Backend)
"JSON feedback rule" is defined here; nothing else may reach the UI.

*   **Schema (v1):** `{"show": bool, "title": string ≤ 60 chars, "body": string ≤ 280 chars, "anchor_rect": {"x","y","width","height"} | null, "severity": "info"|"warn", "cooldown_s": int}`. `show: false` is the legitimate "nothing worth saying" answer and is the common case.
*   **Constrained decoding:** the request to `llama-server` uses JSON-schema-constrained output (`response_format` / grammar) so the model cannot emit prose; the backend still validates against the schema after parsing (defense in depth — a constraint miss is a soft failure, not a crash).
*   **Dedup / cooldown / hysteresis:** a rule whose normalized `title+body` was shown within `feedback.cooldown_s` is suppressed; toasts are rate-limited to at most one per `feedback.min_interval_s`; `show: false` resets nothing. Suppressions are counted in telemetry.

### 3.6. Configuration Surface
One file, `backend/config.yaml`, loaded and validated at startup (`backend/pixhelp/config.py`, PyYAML + dataclasses; any invalid/missing key is a **fatal startup error**, not a silent default). Sections: `heartbeat` (fixed tick `interval_s`, `skip_if_busy`), `capture` (`monitor`, `region`, `foreground_only`), `gate` (`stride`, `threshold_mad`, hysteresis), `ocr` (`downscale`, `languages`, `detail`, `max_blocks`), `vision` (`enabled`, `jpeg_quality`, `max_pixels`), `inference` (`host`, `port`, `threads`, `context`, `server_path`, `vision_model`, `vision_mmproj`, `text_model`, `reasoning_deadline_s`, `degrade_after_deadlines`), `feedback` (`min_interval_s`, `cooldown_s`, `max_body_chars`), `ipc` (`host`, `port`, `token_env`), `telemetry` (`enabled`, `service_name`, `endpoint_env`), `logging` (`level`, `path`, `max_mb`, `backup_count`, `console`). Secrets (IPC token, OTLP credentials) come from environment variables referenced by name, never inlined in the file.

---

## 4. Telemetry & Observability
Running local Docker containers (Jaeger/Prometheus) consumes ~2GB of RAM. Telemetry is outsourced to preserve local resources.

*   **Implementation:** the Python backend uses the OpenTelemetry SDK with an **`OTLPSpanExporter`** + **`BatchSpanProcessor`** (spans: heartbeat pipeline stages) **and an OTLP metrics reader** (counters/histograms: gate-pass ratio, OCR/reasoning latency, token rate, deadline hits, WS drops, restarts). The OTLP exporters ship in **`opentelemetry-exporter-otlp-proto-http`** — *not* in `opentelemetry-sdk` itself.
*   **Destination:** batched async export to a remote SaaS provider (e.g., Grafana Cloud, Honeycomb) or a self-hosted VPS; endpoint comes from the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable. Telemetry is **disabled by default** (`telemetry.enabled: false`) until an endpoint is configured — no outbound traffic otherwise.
*   **Strict Data Scrubbing:** because telemetry leaves the local machine, traces/metrics may contain **only** system metadata (latencies, token rates, counters, error codes). Raw OCR text, Base64 images, window titles, and file paths are prohibited from spans, metrics, attributes, **and log records** (the logging handler applies the same scrubber). Privacy extends to the local machine: no OCR text or frames in logs or on-disk artifacts (§7).
*   **Single Local Log File:** all components log through one `logging` config (`backend/config.yaml`): a rotating file at `backend/logs/pixhelp.log` (`logging.path`, `logging.max_mb`, mirrored to console when `logging.console: true`). The **same scrubbing filter is attached to every handler**, so local file logs obey the identical no-screen-content rule as exported telemetry. Event flow is traceable from one place: component messages (capture, gate, OCR, inference, IPC, feedback) plus a per-heartbeat **`flow:`** line summarizing stage timings, and explicit `flow: skipped N tick(s)` lines when the fixed tick coalesces (§5).

---

## 5. Execution Flow & Latency Budgets
The heartbeat is a **fixed tick** of `heartbeat.interval_s` (default 8s, configurable in `config.yaml`; design range 5–10s). Ticks are scheduled from tick *start*; **if the previous heartbeat is still in flight when the next tick fires, that tick is skipped** (coalesced) and counted in telemetry — heartbeats never queue or overlap. Each heartbeat proceeds down exactly one of these paths:

1.  **Capture (~7–11ms measured median, p95 ≤ 14.4ms):** `dxcam` grabs the screen (off-loop).
2.  **Change-Gate (measured stage — see `spike/capture/results.md`):** frame is diffed against the last OCR'd frame (`int16` mean-abs-diff, full-res or stride-4 per measurement). If unchanged, the heartbeat ends here — **no OCR, no JPEG, no LLM call**. This is the common path; its cost is the gate only.
3.  **Sensory (on-change frames only):**
    *   *OCR (always):* buffer-path recognition, measured **411–829ms median / 492–1,069ms p95** full-screen, plus aggregation+JSON **7–17ms median** (~5–7 KiB payload).
    *   *JPEG (vision path only):* **82–105ms median / 102–132ms p95**.
    *   Sensory total: **~0.5–1.2s worst case** (OCR + aggregate + optional JPEG). The low-frequency heartbeat is precisely what makes this acceptable; the ~830ms-class cost scales with bitmap size only (`ocr.downscale`, `capture.region`).
4.  **Reasoning (deadline-capped, default 8s):** the payload goes to `llama-server`. Text-only inference sits at the low end of the budget; the vision path adds image-encoder prefill and may approach the deadline. **The HTTP call carries an 8s timeout** — on timeout the heartbeat ends as a no-op, the deadline counter increments, and after `inference.degrade_after_deadlines` consecutive hits the backend degrades to the text-only model (§3.1).
5.  **UI Render (~16ms to first frame):** accepted rules are pushed over WebSocket; Flutter animates the overlay.
6.  **Telemetry (async):** the BatchSpanProcessor ships metrics/spans in the background; it never blocks a heartbeat.

**Budget arithmetic:** worst-case on-change heartbeat ≈ 11ms + gate + 1.2s sensory + 8s reasoning ≈ **≤ 9.3s**, i.e. within the 5–10s tick design range; a heartbeat that overruns its tick simply causes the next tick to be skipped (no pile-up). Typical (gated) heartbeats are tens of milliseconds.

---

## 6. Reliability & Failure Policy
Every failure has a defined, non-crashing behavior; the agent degrades, it does not die.

*   **`llama-server` down / crash:** the backend (re)spawns it with exponential backoff (1s→60s cap); while it is down, on-change heartbeats run OCR but **skip reasoning** (telemetry counts them). Startup is gated on `GET /health`.
*   **Reasoning timeout:** 8s HTTP timeout ⇒ heartbeat no-op; consecutive hits trigger text-only degradation (§3.1, §5).
*   **OCR failure / WinRT exception:** skip the heartbeat, log the error code (scrubbed), continue. If OCR fails `M` times consecutively, pause capture for one tick interval before retrying (avoids hot-looping a broken state).
*   **Capture unavailable:** dxcam raises on secure desktop / UAC prompts / locked session / some HDR configurations. On exception: pause capture and retry with backoff; a locked session is *expected* to produce this — resume when the desktop returns.
*   **Flutter client gone:** the backend keeps running; WebSocket reconnect with backoff (§3.4). Toasts produced while no client is connected are dropped (latest-wins), not queued.
*   **Config invalid:** fatal at startup with a clear message — a misconfigured agent must not run silently.

---

## 7. Privacy & Security
The agent watches the screen; therefore privacy is an architectural constraint, not a feature.

*   **Data at rest:** none. Frames live in memory only and are freed at the end of each heartbeat; no screenshots, OCR dumps, or logs with screen text are written to disk (the spike's `ocr_sample.json` is gitignored for exactly this reason).
*   **Data in logs/telemetry:** the scrubber (§4) forbids OCR text, images, window titles, and user paths in spans, metrics, and logs.
*   **Data in prompts:** OCR text and the JPEG frame are sent **only to the local `llama-server` on loopback** — never to a remote API. (A future "cloud fallback" mode is out of scope and would require a new privacy review.)
*   **Active-window-only capture (privacy + perf option):** `capture.foreground_only: true` asks Win32 (`GetForegroundWindow` / `GetWindowRect`) for the foreground window's rect and captures only that region — the agent then *cannot* see background windows, and OCR gets a smaller bitmap (faster). Default is full-screen; the option is the recommended posture for shared desks.
*   **Network exposure:** everything binds `127.0.0.1`. The IPC socket is token-authenticated; the token lives in the environment, not in the repo. `llama-server` is reachable only on loopback.
*   **Secrets:** OTLP endpoint/credentials and the IPC token are supplied via environment variables referenced by name in `config.yaml`; no secrets are committed.

---

## 8. Resource Budget
The 8GB ceiling is the binding constraint. Budget (steady state, vision path active):

| Consumer | RAM (resident) | Notes |
|---|---|---|
| Windows 11 + drivers | ~2.5–3.0 GB | baseline, not ours |
| Flutter desktop overlay | ~300–500 MB | one window, no heavy plugins |
| Python backend | ~150–250 MB | incl. numpy/Pillow; frames are transient |
| `llama-server` (vision) | ~1.6–2.2 GB | 1.1 GiB weights + 425 MiB mmproj + KV cache + activations |
| **Total (vision mode)** | **~4.6–6.2 GB** | leaves ~1.8–3.4 GB headroom |
| `llama-server` (text-only) | ~0.6–1.0 GB | swap-in when degraded |
| Disk (models) | ~2.0 GB | 1.1 GiB + 425 MiB + 469 MiB pinned GGUFs |

*   **CPU:** capture/OCR/encode are bursty (≤ ~1.2s per on-change heartbeat); `llama-server` is capped at `-t 4` (physical cores). Idle heartbeats (gated) cost ~tens of ms — negligible fan/battery impact.
*   **VRAM:** none (iGPU unused by design; frames cross CPU↔GPU via dxcam's DXGI readback only).
*   If the budget is ever breached: first swap to text-only mode (frees >1GB), then reduce `vision.max_pixels` / `context`.

---

## 9. Minimalist Setup Requirements
To avoid bloated developer environments on the target laptop:

1.  **`llama.cpp` Runtime (Plain Install):** official prebuilt **Windows CPU x64** binary (e.g. `llama-b11552-bin-win-cpu-x64` under `%LOCALAPPDATA%`), plus the pinned GGUF models (§3.2, ~2.0 GB disk) in the HF cache. **No C++ toolchain** — no Visual Studio Build Tools, no CMake; llama.cpp is never built from source.
2.  **Python Environments (`venv`, no conda; pattern: `python.exe -m venv env`):**
    *   *Backend* (`backend/`, `backend/setup_env.ps1`): Python 3.11+ (validated 3.14; 3.11 is required because `asyncio.TimeoutError` and the builtin `TimeoutError` are the same class there — the deadline code depends on it) with `dxcam`, the seven `winrt-*` split packages (§3.1 — *not* legacy `winsdk`), `Pillow`, `numpy`, `aiohttp` (**role:** HTTP client to `llama-server` + `/health` polling), `websockets` (**role:** WebSocket *server* for Flutter), `pyyaml` (config), `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` (the OTLP exporters are *not* in the SDK package). OpenCV is deliberately omitted: `dxcam` runs in BGRA mode and Pillow handles the JPEG encode.
    *   *Spikes* (no environment of their own): `spike/capture/` and `spike/inference/` run from the backend venv (`backend\env\Scripts\python.exe spike\<name>\bench_*.py` from the repo root, or `..\..\backend\env\Scripts\python.exe` from inside the spike folder). Every spike import (`dxcam`, `winrt-*`, `Pillow`, `numpy`, `aiohttp`) is already a `backend/requirements.txt` dependency — there are no per-spike `requirements.txt` or `setup_env.ps1` files, and creating a `spike/*/env` is a mistake (delete it if it appears).
    *   *Dev tooling* (`backend/requirements-dev.txt`): `pytest`, `ruff` (installed by `backend/setup_env.ps1` alongside the runtime deps; any future spike-only dependency is appended here).
3.  **UI Toolchain:** standard Flutter Windows SDK (`flutter run -d windows` against `ui/`).

**Repository layout:** `backend/pixhelp/` (the agent), `backend/tests/` (pure-logic unit tests, import-guarded so they run off-Windows too), `ui/` (Flutter overlay), `bin/` (tracked llama.cpp wrappers), `spike/` (measurement harnesses — `spike/capture/` for the sensory pipeline and `spike/inference/` for the reasoning budget; both `results.md` files are versioned and are the evidence base for this document), `specs.md`, `AGENTS.md`.

---

## 10. Testing & Runbook
*   **Unit tests (`backend/tests/`, pure logic, run on any OS):** change-gate math incl. the uint8-wraparound regression (`test_gate.py`), OCR block aggregation/merging (`test_aggregate.py`), feedback-rule schema validation + cooldown/dedup (`test_feedback.py`), config load/validate/fail-fast (`test_config.py`). Windows-only modules (`dxcam`, `winrt`) are import-guarded; their integration is verified manually on the target machine.
*   **Measurement harnesses (run from the backend venv — `backend\env\Scripts\python.exe`; no spike-local environments):** `spike/capture/bench_capture.py` is the authority for sensory stage latencies (capture, gate, OCR, aggregate, JPEG); `spike/inference/bench_inference.py` validates the reasoning budget (cold start, prefill/TTFT, tok/s, verdicts, vision pixel-budget sweep against the 8s deadline). Re-run the relevant harness after perf-relevant changes and commit the appended `results.md` (timings only — no screen content).
*   **Dev startup order:** (1) `bin/llama-server` or let the backend spawn it — the backend supervises the process, so starting the backend alone is sufficient; (2) `backend` (`python -m pixhelp` with `backend/env`); (3) `ui` (`flutter run -d windows`). Liveness: `GET 127.0.0.1:8080/health` ⇒ 200; a `hello` over the IPC socket ⇒ `toast`/`ping` traffic.
*   **Release packaging (later):** backend → PyInstaller one-folder build (WinRT + dxcam need `--collect-all winrt`-style hooks); UI → `flutter build windows`. Out of scope for the scaffold phase.
*   **Logs:** follow the whole event flow in **one file — `backend/logs/pixhelp.log`**: one `flow:` line per heartbeat (`gate=pass|skip mad=… capture=…ms ocr=…ms jpeg=…ms reason=…ms toast=…`), tick-skip events, OCR/IPC/inference warnings, and startup/shutdown lines. Rotate/level via the `logging` section; the file is gitignored and content-scrubbed.
*   **Checks:** `ruff check backend spike` and `backend/env/Scripts/python -m pytest backend/tests` before considering a change done.

---

## 11. Appendix A — Measured Numbers (as of `spike/capture/results.md`, 2026-10-11)
All figures are 1920×1080 BGRA, full-screen unless noted; medians unless stated.

| Stage | Median | p95 | Source note |
|---|---|---|---|
| `grab()` | 6.8–11.3 ms | ≤ 14.4 ms | 5 runs; one 242 ms cold outlier excluded |
| Change-gate (int16) | *pending* | *pending* | stage added; awaiting Windows re-run |
| OCR buffer path (full-res) | 411–829 ms | 492–1,069 ms | 5 full-res runs |
| OCR @ downscale 0.8 | 496 ms | 670 ms | single run |
| Aggregate + JSON (blocks) | 7–17 ms | ≤ 22.5 ms | 43/64 ms at downscale 0.8 |
| JPEG encode (q75) | 82–105 ms | 102–132 ms | 5 runs |
| Payload size (blocks, ≤5) | 4.8–6.6 KiB | — | 5 rects |
| Reasoning (budget, not measured) | ≤ 8 s deadline | — | vision prefill dominates; text-only is far cheaper |

When the change-gate stage is benchmarked on the Windows machine, fill in the gate row and pin `gate.stride` / `gate.threshold_mad` in `config.yaml` from the measured MAD of idle vs. changing frames.
