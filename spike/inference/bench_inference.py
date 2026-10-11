r"""Priority-1 spike: validate the local-model reasoning budget on the target laptop.

Everything here checks one assumption (specs.md §5): that `llama-server`
returns a feedback rule within the reasoning deadline (default 8s) for BOTH
paths, and finds the vision image budget that makes the vision path fit:

  - cold start        spawn llama-server -> GET /health 200 (model load time)
  - text-only path    Qwen2.5-0.5B  (OCR JSON prompt): prefill, TTFT, total, tok/s
  - vision path       Qwen3-VL-2B + mmproj at one or more pixel budgets:
                      JPEG encode, prefill, TTFT, total, tok/s
  - contract check    the reply parses as a feedback-rule JSON object
                      (same prompt + response_format as backend/pixhelp/agent.py)

Usage (native Windows; spikes share the backend venv - there is no
spike-local environment):
    ..\..\backend\env\Scripts\python.exe bench_inference.py
Options:
    --mode both|text|vision    which path(s) to measure (default both)
    --reps 5                   timed repetitions per path/budget
    --deadline 8.0             reasoning deadline to validate against (s)
    --max-tokens 300           matches backend agent.py
    --pixels 1003520,262144,65536
                               vision budgets to sweep (comma-separated;
                               config vision.max_pixels is 1003520)
    --quality 75               JPEG quality (config vision.jpeg_quality)
    --image PATH               real screenshot instead of the synthetic screen
                               (local only; never written to results or console)
    --threads 4  --context 4096  --port 8080
    --no-spawn                 reuse a running llama-server (--mode single only)
    --hard-timeout 180         per-request ceiling while measuring (s); kept
                               generous so overruns are measured, not clipped

Privacy: results.md records timings and counts ONLY - never prompt text, OCR
content, screenshot pixels, or model output. The OCR payload (if present) is
read from spike/capture/ocr_sample.json, which is gitignored.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import aiohttp
    from PIL import Image, ImageDraw
    from PIL import __version__ as PILLOW_VERSION

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.md"
DEFAULT_PAYLOAD = HERE.parent / "capture" / "ocr_sample.json"

VISION_REPO = "models--Qwen--Qwen3-VL-2B-Instruct-GGUF"
VISION_WEIGHTS = "Qwen3VL-2B-Instruct-Q4_K_M.gguf"
VISION_MMPROJ = "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"
TEXT_REPO = "models--Qwen--Qwen2.5-0.5B-Instruct-GGUF"
TEXT_WEIGHTS = "qwen2.5-0.5b-instruct-q4_k_m.gguf"

# Compact copy of backend/pixhelp/agent.py:SYSTEM_PROMPT - keep in sync.
SYSTEM_PROMPT = """\
You are pixhelp, a non-intrusive desktop assistant. You see a snapshot of the
user's screen: OCR text grouped into a few blocks (with full-screen pixel
rects) and, on the vision path, a downscaled JPEG of the same frame.

Decide whether a short, actionable tip is warranted RIGHT NOW. Most of the
time the answer is no - output {"show": false}. Only speak up for concrete,
repeated-mistake-class issues. Never narrate what is on screen. Never invent
content that is not evidenced.

Respond with a single JSON object matching exactly:
{"show": bool, "title": str (<=60 chars), "body": str (<=280 chars),
 "anchor_rect": {"x":int,"y":int,"width":int,"height":int}|null,
 "severity": "info"|"warn", "cooldown_s": number}
"""


# --- fixtures -----------------------------------------------------------------


def synthetic_screen(width: int = 1280, height: int = 800) -> Image.Image:
    """Privacy-safe stand-in for a text-dense desktop frame (no real content)."""
    img = Image.new("RGB", (width, height), (243, 244, 246))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, width - 40, height - 40], fill=(255, 255, 255),
                outline=(120, 120, 130))
    d.rectangle([40, 40, width - 40, 84], fill=(32, 34, 40))
    d.text((56, 55), "Document Editor - unsaved changes", fill=(240, 240, 245))
    rows = [
        ("File name", "quarterly-report-v3.docx"),
        ("Location", "C:/Users/demo/Documents/Reports"),
        ("Author", "Demo User"),
        ("Last saved", "Today 09:41"),
        ("Word count", "1,284"),
        ("Status", "12 spelling issues found"),
    ]
    y = 116
    for label, value in rows:
        d.text((72, y), label, fill=(70, 70, 85))
        d.rectangle([300, y - 8, 760, y + 20], outline=(160, 160, 175),
                    fill=(250, 250, 252))
        d.text((312, y), value, fill=(20, 20, 30))
        y += 48
    d.text((72, y + 16), "Warning: closing now will discard unsaved edits.",
           fill=(180, 60, 40))
    d.text((72, y + 44), "Apply the suggested accessibility alt-text before export.",
           fill=(40, 40, 60))
    d.rectangle([width - 360, height - 120, width - 200, height - 76],
                fill=(230, 232, 238), outline=(120, 120, 140))
    d.text((width - 330, height - 104), "Don't Save", fill=(20, 20, 30))
    d.rectangle([width - 180, height - 120, width - 60, height - 76],
                fill=(40, 96, 200), outline=(30, 70, 160))
    d.text((width - 150, height - 104), "Save", fill=(255, 255, 255))
    return img


def clamp_pixels(img: Image.Image, max_pixels: int) -> Image.Image:
    w, h = img.size
    if w * h <= max_pixels:
        return img
    factor = (max_pixels / (w * h)) ** 0.5
    return img.resize((max(1, int(w * factor)), max(1, int(h * factor))),
                      Image.LANCZOS)


def jpeg_b64(img: Image.Image, quality: int) -> tuple[bytes, str]:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    raw = buf.getvalue()
    return raw, base64.b64encode(raw).decode("ascii")


def load_payload(path: Path) -> tuple[dict, str]:
    """OCR payload for the prompt; returns (payload, provenance label).

    Content never reaches the console or results.md - only sizes do.
    """
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        rects = len(data.get("blocks", data.get("lines", [])))
        kib = len(json.dumps(data, ensure_ascii=False).encode()) / 1024
        return data, f"capture/ocr_sample.json ({rects} rects, {kib:.1f} KiB)"
    blocks = [
        {"rect": {"x": 72 + i * 90, "y": 116 + i * 48, "width": 460, "height": 28},
         "text": f"sample row {i}"}
        for i in range(5)
    ]
    return {"text": "synthetic", "blocks": blocks}, "built-in synthetic (5 rects)"


def messages_for(payload: dict, image_b64: str | None) -> list[dict]:
    ocr = json.dumps(payload, ensure_ascii=False)[:12000]
    if image_b64 is None:
        user: str | list = "Screen OCR (blocks):\n" + ocr
    else:
        user = [
            {"type": "text", "text": "Screen OCR (blocks):\n" + ocr},
            {"type": "text", "text": "Screen image (downscaled JPEG):"},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# --- llama.cpp plumbing -------------------------------------------------------


def resolve_gguf(repo: str, filename: str) -> Path:
    import os

    for hub in (
        Path.home() / ".cache" / "huggingface" / "hub",
        Path(os.environ.get("USERPROFILE", "")) / ".cache" / "huggingface" / "hub",
    ):
        if hub.is_dir():
            hits = sorted(hub.glob(f"{repo}/snapshots/*/{filename}"))
            if hits:
                return hits[-1]
    raise FileNotFoundError(f"{filename} not found in the HF cache")


def resolve_server() -> Path:
    import os

    root = Path(os.environ.get("LOCALAPPDATA", "")) / "llama-b11552-bin-win-cpu-x64"
    exe = root / "llama-server.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"llama-server.exe not found: {exe}")
    return exe


def spawn_server(exe: Path, model: Path, mmproj: Path | None, port: int,
                 threads: int, context: int) -> subprocess.Popen:
    args = [str(exe), "-m", str(model), "--host", "127.0.0.1",
            "--port", str(port), "-t", str(threads), "-c", str(context),
            "--parallel", "1"]
    if mmproj is not None:
        args += ["--mmproj", str(mmproj)]
    return subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def wait_health(session: aiohttp.ClientSession, base: str,
                      timeout_s: float) -> float:
    """Seconds from call start until GET /health == 200 (cold start)."""
    t0 = time.perf_counter()
    deadline = t0 + timeout_s
    while time.perf_counter() < deadline:
        try:
            async with session.get(f"{base}/health",
                                   timeout=aiohttp.ClientTimeout(total=2.0)) as r:
                if r.status == 200:
                    return time.perf_counter() - t0
        except (aiohttp.ClientError, TimeoutError):
            # Connection refused / not listening yet while the model loads.
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("llama-server /health not ready")


async def query(session: aiohttp.ClientSession, base: str, body: dict,
                hard_timeout_s: float) -> dict:
    """Streaming request; returns ttft/total/prefill/parse result.

    `prefill` and generation rates come from llama-server's `timings`.
    """
    out = {"ttft_ms": None, "total_ms": None, "prefill_ms": None,
           "prompt_tokens": None, "completion_tokens": None, "tps": None,
           "valid_json": False, "error": None,
           "grammar": bool(body.get("response_format"))}
    pieces: list[str] = []
    timings: dict = {}
    t0 = time.perf_counter()
    timeout = aiohttp.ClientTimeout(total=hard_timeout_s)
    try:
        async with session.post(f"{base}/v1/chat/completions", json=body,
                                timeout=timeout) as resp:
            if resp.status >= 400:
                out["error"] = f"HTTP {resp.status}: {(await resp.text())[:160]}"
                return out
            buf = b""
            done = False
            async for chunk in resp.content:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    obj = json.loads(data)
                    if isinstance(obj.get("timings"), dict):
                        timings = obj["timings"]
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            if not pieces:
                                out["ttft_ms"] = (time.perf_counter() - t0) * 1000
                            pieces.append(piece)
                if done:
                    break
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
        total_ms = (time.perf_counter() - t0) * 1000
        kind = "TimeoutError" if isinstance(exc, TimeoutError) else type(exc).__name__
        out["error"] = f"{kind} after {total_ms:.0f}ms (hard-timeout {hard_timeout_s:.0f}s)"
        out["total_ms"] = total_ms
        return out

    out["total_ms"] = (time.perf_counter() - t0) * 1000
    if out["ttft_ms"] is None:
        out["ttft_ms"] = out["total_ms"]
    out["prefill_ms"] = timings.get("prompt_ms")
    out["prompt_tokens"] = timings.get("prompt_n")
    out["completion_tokens"] = timings.get("predicted_n")
    tps = timings.get("predicted_tps")
    if tps is None and out["total_ms"]:
        tps = (out["completion_tokens"] or 0) / (out["total_ms"] / 1000)
    out["tps"] = tps

    text = "".join(pieces).strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        rule = json.loads(text)
        out["valid_json"] = isinstance(rule, dict) and "show" in rule
    except json.JSONDecodeError:
        out["valid_json"] = False
    return out


async def query_with_fallback(session, base: str, body: dict,
                              hard_timeout_s: float) -> dict:
    """Retry once without response_format if the server rejects it (HTTP 400)."""
    result = await query(session, base, body, hard_timeout_s)
    if result["error"] and result["error"].startswith("HTTP 400") and body.get(
            "response_format"):
        slim = {k: v for k, v in body.items() if k != "response_format"}
        result = await query(session, base, slim, hard_timeout_s)
        result["grammar"] = False
        result["note"] = "response_format rejected; retried without grammar"
    return result


# --- measurement --------------------------------------------------------------


def stage_stats(values: list[float]) -> dict:
    xs = sorted(values)
    return {
        "min": xs[0],
        "med": statistics.median(xs),
        "p95": xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))],
    }


def fmt_ms(values: list[float]) -> str:
    s = stage_stats(values)
    return f"{s['med']:8.1f} / {s['p95']:8.1f} / {s['min']:8.1f}"


def chat_body(model: Path, messages: list[dict], max_tokens: int,
              grammar: bool) -> dict:
    body = {"model": model.name, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0.2, "stream": True}
    if grammar:
        body["response_format"] = {"type": "json_object"}
    return body


def build_image(image_path: str | None, pixels: int) -> Image.Image:
    if image_path:
        img = Image.open(image_path).convert("RGB")
    else:
        img = synthetic_screen()
    return clamp_pixels(img, pixels)


async def measure_reps(session, base: str, model: Path, args, payload: dict,
                       pixels: int | None) -> dict:
    """Reps at one path/budget. pixels=None means text-only (no image)."""
    samples = {"ttft": [], "total": [], "prefill": [], "encode": [], "tps": [],
               "valid": 0, "deadline_hits": 0, "errors": 0, "grammar": None,
               "prompt_tokens": None}
    for i in range(args.reps):
        image_b64 = None
        if pixels is not None:
            t = time.perf_counter()
            img = build_image(args.image, pixels)
            _, image_b64 = jpeg_b64(img, args.quality)
            samples["encode"].append((time.perf_counter() - t) * 1000)
        body = chat_body(model, messages_for(payload, image_b64),
                         args.max_tokens, grammar=True)
        result = await query_with_fallback(session, base, body, args.hard_timeout)
        if result["error"]:
            samples["errors"] += 1
            print(f"  rep {i + 1}/{args.reps}: ERROR {result['error']}")
            continue
        samples["grammar"] = result["grammar"]
        samples["ttft"].append(result["ttft_ms"])
        samples["total"].append(result["total_ms"])
        samples["prefill"].append(result["prefill_ms"] or result["ttft_ms"])
        samples["tps"].append(result["tps"] or 0.0)
        samples["valid"] += int(result["valid_json"])
        samples["prompt_tokens"] = result["prompt_tokens"]
        if result["total_ms"] > args.deadline * 1000:
            samples["deadline_hits"] += 1
        print(f"  rep {i + 1}/{args.reps}: ttft={result['ttft_ms']:.0f}ms "
              f"total={result['total_ms']:.0f}ms "
              f"prefill={samples['prefill'][-1]:.0f}ms "
              f"json={'ok' if result['valid_json'] else 'BAD'}")
    return samples


async def measure_mode(mode: str, model: Path, mmproj: Path | None, args,
                       payload: dict) -> dict:
    """One model: cold start once, then reps for every pixel budget."""
    base = f"http://127.0.0.1:{args.port}"
    proc = None
    if not args.no_spawn:
        proc = spawn_server(args.server, model, mmproj, args.port,
                            args.threads, args.context)
    out = {"cold_s": None, "budgets": {}}
    try:
        async with aiohttp.ClientSession() as session:
            out["cold_s"] = await wait_health(session, base, args.spawn_timeout)
            print(f"  cold start: {out['cold_s']:.1f}s")
            budgets = [None] if mode == "text" else args.pixel_list
            for pixels in budgets:
                if pixels is not None:
                    print(f"-- {mode}@{pixels}px (q{args.quality}) --")
                out["budgets"][pixels] = await measure_reps(
                    session, base, model, args, payload, pixels)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return out


async def run(args) -> None:
    payload, provenance = load_payload(Path(args.payload))
    modes = ["text", "vision"] if args.mode == "both" else [args.mode]
    if args.no_spawn and len(modes) > 1:
        sys.exit("--no-spawn works with a single --mode (one model is loaded "
                 "per server)")

    args.server = resolve_server()
    models = {
        "text": (resolve_gguf(TEXT_REPO, TEXT_WEIGHTS), None),
        "vision": (resolve_gguf(VISION_REPO, VISION_WEIGHTS),
                   resolve_gguf(VISION_REPO, VISION_MMPROJ)),
    }
    print(f"payload: {provenance}")
    print(f"deadline: {args.deadline:.1f}s, reps: {args.reps}, "
          f"threads: {args.threads}, hard-timeout: {args.hard_timeout:.0f}s")
    if "vision" in modes:
        source = (f"--image clamped to {args.pixel_list}" if args.image
                  else f"synthetic screen clamped to {args.pixel_list} "
                       "(no real content)")
        print(f"vision image: {source}")

    out: dict[str, dict] = {}
    for mode in modes:
        model, mmproj = models[mode]
        print(f"\n== {mode} path: {model.name} ==")
        out[mode] = await measure_mode(mode, model, mmproj, args, payload)

    write_results(args, provenance,
                  ("--image" if args.image else "synthetic") if "vision" in modes
                  else None, out)


def _p50(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[len(values) // 2]


def write_results(args, provenance: str, image_source: str | None,
                  out: dict) -> None:
    rows: list[tuple[str, list[float]]] = []
    bullets: list[str] = []
    verdicts: list[str] = []

    for mode, mode_out in out.items():
        rows.append((f"cold start ({mode} model)", [mode_out["cold_s"] * 1000]))
        for pixels, s in mode_out["budgets"].items():
            label = f"{mode}@{pixels}px" if pixels is not None else mode
            if s["encode"]:
                rows.append((f"jpeg encode ({label})", s["encode"]))
            rows.append((f"{label}: ttft", s["ttft"]))
            rows.append((f"{label}: prefill", s["prefill"]))
            rows.append((f"{label}: total", s["total"]))
            tps_p50 = _p50(s["tps"])
            ok = len(s["total"])
            tps_txt = f"{tps_p50:.1f}" if tps_p50 is not None else "n/a"
            bullets.append(
                f"- {label}: grammar={'on' if s['grammar'] else 'off'}, "
                f"prompt_tokens={s['prompt_tokens']}, gen p50={tps_txt} tok/s, "
                f"valid JSON={s['valid']}/{ok}, "
                f"deadline hits={s['deadline_hits']}/{ok}, "
                f"errors={s['errors']}"
            )
            if s["total"]:
                p95 = stage_stats(s["total"])["p95"]
                verdicts.append(
                    f"{label} p95={p95:.0f}ms -> "
                    f"{'PASS' if p95 <= args.deadline * 1000 else 'FAIL'} "
                    f"(deadline {args.deadline * 1000:.0f}ms)"
                )
                combined = args.sensory_ms + p95
                bullets.append(
                    f"- {label} budget: sensory p95 {args.sensory_ms:.0f}ms + "
                    f"reasoning p95 {p95:.0f}ms = {combined:.0f}ms "
                    f"({'within' if combined <= args.interval_ms else 'OVER'} "
                    f"the {args.interval_ms:.0f}ms tick)"
                )

    lines = [
        "",
        f"## Inference spike @ {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- OS: {platform.platform()}",
        (f"- Python: {platform.python_version()} (aiohttp, Pillow "
         f"{PILLOW_VERSION})"),
        (f"- Server: {args.server.parent.name}, threads={args.threads}, "
         f"context={args.context}, port={args.port}"),
        (f"- Deadline: {args.deadline:.1f}s, reps={args.reps}, "
         f"max_tokens={args.max_tokens}, hard_timeout={args.hard_timeout:.0f}s"),
        f"- Payload: {provenance}",
    ]
    if image_source:
        lines.append(f"- Vision image: {image_source}, q{args.quality}, "
                     f"budgets={args.pixel_list}")
    lines += ["", "| stage | median ms | p95 ms | min ms |", "|---|---|---|---|"]
    lines += [f"| {name} | {fmt_ms(values)} |" for name, values in rows
              if values]
    lines += bullets + [f"- VERDICT: {v}" for v in verdicts] + [""]

    print()
    print("| stage                          |    med |     p95 |     min |")
    for name, values in rows:
        if values:
            print(f"| {name:30s} | {fmt_ms(values)} |")
    print()
    for b in bullets:
        print(b)
    for v in verdicts:
        print(f"VERDICT: {v}")

    with RESULTS.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nAppended results to {RESULTS}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local llama.cpp reasoning-budget spike")
    parser.add_argument("--mode", choices=("both", "text", "vision"),
                        default="both")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--deadline", type=float, default=8.0)
    parser.add_argument("--max-tokens", type=int, default=300)
    parser.add_argument("--pixels", default="1003520,262144,65536",
                        help="comma-separated vision pixel budgets to sweep")
    parser.add_argument("--quality", type=int, default=75)
    parser.add_argument("--image", default=None,
                        help="path to a real screenshot (local only)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-spawn", action="store_true")
    parser.add_argument("--spawn-timeout", type=float, default=120.0)
    parser.add_argument("--hard-timeout", type=float, default=180.0)
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    parser.add_argument("--sensory-ms", type=float, default=1240.0,
                        help="capture+OCR+JPEG p95 from the capture spike")
    parser.add_argument("--interval-ms", type=float, default=8000.0,
                        help="heartbeat tick to check the combined budget against")
    args = parser.parse_args()
    args.pixel_list = [int(p) for p in str(args.pixels).split(",") if p.strip()]

    if not IS_WINDOWS:
        sys.exit("This spike requires native Windows: llama-server.exe and "
                 "the HF cache are Windows-side (not available in WSL).")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
