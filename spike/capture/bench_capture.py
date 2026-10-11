r"""Priority-2 spike: measure dxcam capture + WinRT OCR latency on native Windows.

Run with a text-dense window visible on the primary monitor (spikes share the
backend venv - there is no spike-local environment):
    ..\..\backend\env\Scripts\python.exe bench_capture.py
Options:
    --reps 20            timed repetitions
    --jpeg-quality 75    JPEG quality for the vision-path encode stage
    --detail blocks      result granularity: blocks (default), lines, words (debug)
    --max-blocks 5       cap on merged text-block rects (0 = no cap)
    --downscale 1.0      OCR-resolution experiment (e.g. 0.5 halves width/height)
    --region L,T,R,B     capture only this rectangle instead of full screen

Recognition cost is driven by bitmap size, not by how many rects are kept;
--detail only changes payload size (the `aggregate + json` stage times it).

Also benchmarks the change-gate (int16 mean-abs-diff, full-res and stride-4)
before the OCR loop; gate rows land in the same results table, and MAD values
for an identical pair vs consecutive frames calibrate the gate threshold.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import platform
import statistics
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import dxcam
    import numpy as np
    from PIL import Image
    from PIL import __version__ as PILLOW_VERSION

    WINRT_MODERN = True
    try:
        from winrt.windows.graphics.imaging import (
            BitmapDecoder,
            BitmapPixelFormat,
            SoftwareBitmap,
        )
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import (
            Buffer,
            DataWriter,
            InMemoryRandomAccessStream,
        )
    except ImportError:
        WINRT_MODERN = False
        from winsdk.windows.graphics.imaging import (
            BitmapDecoder,
            BitmapPixelFormat,
            SoftwareBitmap,
        )
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage.streams import (
            Buffer,
            DataWriter,
            InMemoryRandomAccessStream,
        )
else:
    WINRT_MODERN = None

HERE = Path(__file__).parent
RESULTS = HERE / "results.md"
OCR_SAMPLE = HERE / "ocr_sample.json"


def pc() -> float:
    return time.perf_counter()


async def await_op(op):
    if hasattr(op, "__await__"):
        return await op
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, op.get)


def grab_latest(cam, region=None, timeout_s: float = 2.0):
    deadline = pc() + timeout_s
    while pc() < deadline:
        frame = cam.grab(new_frame_only=False, region=region)
        if frame is not None:
            return frame
        time.sleep(0.005)
    raise TimeoutError(f"dxcam returned no frame within {timeout_s}s")


def maybe_downscale_bgra(frame: np.ndarray, factor: float) -> np.ndarray:
    if factor >= 1.0:
        return frame
    rgba = frame[..., [2, 1, 0, 3]]
    img = Image.fromarray(rgba)
    img = img.resize(
        (max(1, int(img.width * factor)), max(1, int(img.height * factor))),
        Image.LANCZOS,
    )
    return np.asarray(img)[..., [2, 1, 0, 3]]


def change_gate_mad(a: np.ndarray, b: np.ndarray, stride: int = 1) -> float:
    """Mean-abs-diff gate on 0-255 scale. The int16 cast is mandatory:
    uint8 subtraction wraps around (10-250 == 16 in uint8), silently
    under-reporting the difference on exactly the frames that changed."""
    if stride > 1:
        a = a[::stride, ::stride]
        b = b[::stride, ::stride]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def encode_jpeg(frame_bgra: np.ndarray, quality: int) -> bytes:
    rgba = frame_bgra[..., [2, 1, 0, 3]]
    img = Image.fromarray(rgba).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


async def bitmap_from_jpeg(jpeg: bytes) -> SoftwareBitmap:
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(jpeg)
    await await_op(writer.store_async())
    writer.detach_stream()
    stream.seek(0)
    decoder = await await_op(BitmapDecoder.create_async(stream))
    return await await_op(decoder.get_software_bitmap_async())


def bitmap_from_bgra(raw: bytes, width: int, height: int) -> SoftwareBitmap:
    try:
        buf = Buffer(len(raw))
        memoryview(buf)[:] = raw
    except (TypeError, ValueError):
        writer = DataWriter()
        writer.write_bytes(raw)
        buf = writer.detach_buffer()
    return SoftwareBitmap.create_copy_from_buffer(
        buf, BitmapPixelFormat.BGRA8, width, height
    )


async def recognize(engine, bitmap) -> str:
    result = await await_op(engine.recognize_async(bitmap))
    return result


def rect_dict(r) -> dict:
    return {"x": r.x, "y": r.y, "width": r.width, "height": r.height}


def make_rect_mapper(region, factor: float):
    """Map OCR rects back to full-screen coordinates.

    Recognition runs on the captured bitmap (region-cropped, then possibly
    downscaled), so its rects are relative to that image; undo both transforms.
    """
    left = float(region[0]) if region else 0.0
    top = float(region[1]) if region else 0.0
    inv = 1.0 / factor

    def map_rect(rect: dict) -> dict:
        return {
            "x": round(left + rect["x"] * inv, 1),
            "y": round(top + rect["y"] * inv, 1),
            "width": round(rect["width"] * inv, 1),
            "height": round(rect["height"] * inv, 1),
        }

    return map_rect


def extract_lines(result, map_rect) -> list[dict]:
    return [
        {
            "text": line.text,
            "words": [
                {"text": word.text, "rect": map_rect(rect_dict(word.bounding_rect))}
                for word in line.words
            ],
        }
        for line in result.lines
    ]


def union_rect(rects: list[dict]) -> dict:
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for r in rects:
        x, y, w, h = r["x"], r["y"], r["width"], r["height"]
        x0 = min(x0, x)
        y0 = min(y0, y)
        x1 = max(x1, x + w)
        y1 = max(y1, y + h)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def line_rect(line: dict) -> dict:
    return union_rect([w["rect"] for w in line["words"]])


def cluster_lines(
    entries: list[tuple[dict, str]], gap: float
) -> list[list[tuple[dict, str]]]:
    """Single-linkage: merge rects whose vertical gap <= `gap` and that overlap horizontally.

    `entries` are (rect, text) pairs; a y-sorted sweep keeps the pair count near-linear.
    """
    order = sorted(range(len(entries)), key=lambda i: entries[i][0]["y"])
    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for pos, i in enumerate(order):
        ri = entries[i][0]
        bottom = ri["y"] + ri["height"]
        left = ri["x"]
        right = left + ri["width"]
        for j in order[pos + 1 :]:
            rj = entries[j][0]
            if rj["y"] > bottom + gap:
                break
            if min(right, rj["x"] + rj["width"]) > max(left, rj["x"]):
                ri_root, rj_root = find(i), find(j)
                if ri_root != rj_root:
                    parent[rj_root] = ri_root

    groups: dict[int, list[tuple[dict, str]]] = {}
    for i, entry in enumerate(entries):
        groups.setdefault(find(i), []).append(entry)
    return list(groups.values())


def _block_from(members: list[tuple[dict, str]]) -> dict:
    ordered = sorted(members, key=lambda e: (e[0]["y"], e[0]["x"]))
    return {
        "rect": union_rect([rect for rect, _ in ordered]),
        "text": "\n".join(text for _, text in ordered),
    }


def _closest_pair(blocks: list[dict]) -> tuple[int, int]:
    best = None
    for i in range(len(blocks)):
        a = blocks[i]["rect"]
        ax = a["x"] + a["width"] / 2
        ay = a["y"] + a["height"] / 2
        for j in range(i + 1, len(blocks)):
            b = blocks[j]["rect"]
            dx = ax - (b["x"] + b["width"] / 2)
            dy = ay - (b["y"] + b["height"] / 2)
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, i, j)
    return best[1], best[2]


def _grid_bucket(blocks: list[dict], max_blocks: int) -> list[dict]:
    """Group blocks sharing a coarse screen cell so the pairwise merge stays cheap."""
    cols = max(4, int((max_blocks * 16 / 9) ** 0.5) + 1)
    rows = max(3, int((max_blocks * 9 / 16) ** 0.5) + 1)
    centers = [
        (b["rect"]["x"] + b["rect"]["width"] / 2, b["rect"]["y"] + b["rect"]["height"] / 2)
        for b in blocks
    ]
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    x_lo, y_lo = min(xs), min(ys)
    x_span = max(max(xs) - x_lo, 1.0)
    y_span = max(max(ys) - y_lo, 1.0)
    cells: dict[tuple[int, int], list[dict]] = {}
    for block, (cx, cy) in zip(blocks, centers):
        gx = min(cols - 1, int((cx - x_lo) / x_span * cols))
        gy = min(rows - 1, int((cy - y_lo) / y_span * rows))
        cells.setdefault((gx, gy), []).append(block)
    return [
        _block_from([(b["rect"], b["text"]) for b in group])
        for group in cells.values()
    ]


def _merge_to(blocks: list[dict], max_blocks: int) -> list[dict]:
    """Greedy nearest-center agglomeration until <= `max_blocks` (cells first, then pairs)."""
    blocks.sort(key=lambda b: (b["rect"]["y"], b["rect"]["x"]))
    if max_blocks <= 0 or len(blocks) <= max_blocks:
        return blocks
    if len(blocks) > max(64, 4 * max_blocks):
        blocks = _grid_bucket(blocks, max_blocks)
    while len(blocks) > max_blocks:
        i, j = _closest_pair(blocks)
        first, second = blocks[i], blocks[j]
        if (second["rect"]["y"], second["rect"]["x"]) < (
            first["rect"]["y"],
            first["rect"]["x"],
        ):
            first, second = second, first
        merged = {
            "rect": union_rect([first["rect"], second["rect"]]),
            "text": first["text"] + "\n" + second["text"],
        }
        blocks = [b for k, b in enumerate(blocks) if k not in (i, j)]
        blocks.append(merged)
    blocks.sort(key=lambda b: (b["rect"]["y"], b["rect"]["x"]))
    return blocks


def build_blocks(lines: list[dict], max_blocks: int) -> list[dict]:
    entries = [
        (line_rect(line), line["text"]) for line in lines if line["words"]
    ]
    if not entries:
        return []
    heights = sorted(rect["height"] for rect, _ in entries)
    gap = max(2.0, heights[len(heights) // 2])
    blocks = [_block_from(members) for members in cluster_lines(entries, gap)]
    return _merge_to(blocks, max_blocks)


def build_payload(
    text: str, lines: list[dict], detail: str, max_blocks: int
) -> dict:
    """Shape OCR output for the wire: blocks (default), lines, or raw words."""
    if detail == "words":
        return {"text": text, "lines": lines}
    if detail == "lines":
        return {
            "text": text,
            "lines": [
                {"text": line["text"], "rect": line_rect(line)}
                for line in lines
                if line["words"]
            ],
        }
    return {"text": text, "blocks": build_blocks(lines, max_blocks)}


def payload_from_result(result, detail: str, max_blocks: int, map_rect) -> dict:
    return build_payload(result.text, extract_lines(result, map_rect), detail, max_blocks)


def count_rects(payload: dict) -> int:
    if "blocks" in payload:
        return len(payload["blocks"])
    lines = payload.get("lines", [])
    if lines and "words" in lines[0]:
        return sum(len(line["words"]) for line in lines)
    return len(lines)


def stage_stats(values: list[float]) -> dict:
    xs = sorted(values)
    return {
        "min": xs[0],
        "med": statistics.median(xs),
        "p95": xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))],
    }


def fmt_ms(values: list[float]) -> str:
    s = stage_stats(values)
    return f"{s['med'] * 1000:7.1f} / {s['p95'] * 1000:7.1f} / {s['min'] * 1000:7.1f}"


def parse_downscale(text: str) -> float:
    value = float(text)
    if not 0 < value <= 1:
        raise argparse.ArgumentTypeError(f"downscale must be in (0, 1], got {value}")
    return value


def parse_region(text: str | None):
    if not text:
        return None
    parts = tuple(int(p.strip()) for p in text.split(","))
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be L,T,R,B")
    left, top, right, bottom = parts
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise argparse.ArgumentTypeError("region must satisfy 0 <= L < R and 0 <= T < B")
    return parts


async def run(args) -> None:
    languages = list(OcrEngine.available_recognizer_languages)
    if not languages:
        sys.exit(
            "No WinRT OCR language installed. Open Windows Settings > Time & Language > "
            "Language > Add a language (with Optical character recognition), or check "
            r"C:\Windows\OCR."
        )
    tags = [lang.language_tag for lang in languages]
    print(f"OCR languages available: {', '.join(tags)}")

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        engine = OcrEngine.try_create_from_language(languages[0])
    print(f"OCR engine: {engine.recognizer_language.language_tag}")

    region = args.region
    with dxcam.create(output_color="BGRA") as cam:
        width, height = cam.width, cam.height
        if region is not None and (region[2] > width or region[3] > height):
            sys.exit(f"region {region} exceeds the {width}x{height} capture surface")
        map_rect = make_rect_mapper(region, args.downscale)
        print(f"Capture: {width}x{height} BGRA, region={region or 'full screen'}")

        t0 = pc()
        grab_latest(cam)
        cold_grab = pc() - t0
        for _ in range(2):
            grab_latest(cam)
        print(f"Warm-up grab: {cold_grab * 1000:.1f} ms (includes adapter init, excluded from stats)")

        probe = grab_latest(cam, region=region)
        if args.downscale < 1.0:
            probe = maybe_downscale_bgra(probe, args.downscale)
        print(f"OCR bitmap: {probe.shape[1]}x{probe.shape[0]} (downscale={args.downscale})")

        gate_full, gate_stride = [], []
        frame_a = grab_latest(cam, region=region)
        if args.downscale < 1.0:
            frame_a = maybe_downscale_bgra(frame_a, args.downscale)
        frame_b = grab_latest(cam, region=region)
        if args.downscale < 1.0:
            frame_b = maybe_downscale_bgra(frame_b, args.downscale)
        frame_copy = frame_a.copy()
        mad_same = change_gate_mad(frame_a, frame_copy)
        mad_same_s4 = change_gate_mad(frame_a, frame_copy, stride=4)
        mad_seq = change_gate_mad(frame_a, frame_b)
        mad_seq_s4 = change_gate_mad(frame_a, frame_b, stride=4)
        for _ in range(args.reps):
            t = pc()
            change_gate_mad(frame_a, frame_copy)
            gate_full.append(pc() - t)
            t = pc()
            change_gate_mad(frame_a, frame_copy, stride=4)
            gate_stride.append(pc() - t)
        print(
            f"Change-gate MAD: identical pair full/stride4 = "
            f"{mad_same:.3f}/{mad_same_s4:.3f}, consecutive frames = "
            f"{mad_seq:.3f}/{mad_seq_s4:.3f} (0-255 scale)"
        )
        del frame_a, frame_b, frame_copy

        grabs, encodes, dec_bitmaps, dec_ocrs, buf_bitmaps, buf_ocrs = [], [], [], [], [], []
        aggregates = []
        first_result = None

        for i in range(args.reps):
            t = pc()
            frame = grab_latest(cam, region=region)
            if args.downscale < 1.0:
                frame = maybe_downscale_bgra(frame, args.downscale)
            t1 = pc()
            jpeg = encode_jpeg(frame, args.jpeg_quality)
            t2 = pc()
            bm = await bitmap_from_jpeg(jpeg)
            t3 = pc()
            res_a = await recognize(engine, bm)
            t4 = pc()
            raw = frame.tobytes()
            bm2 = bitmap_from_bgra(raw, frame.shape[1], frame.shape[0])
            t5 = pc()
            await recognize(engine, bm2)
            t6 = pc()

            grabs.append(t1 - t)
            encodes.append(t2 - t1)
            dec_bitmaps.append(t3 - t2)
            dec_ocrs.append(t4 - t3)
            buf_bitmaps.append(t5 - t4)
            buf_ocrs.append(t6 - t5)
            payload = payload_from_result(res_a, args.detail, args.max_blocks, map_rect)
            payload_json = json.dumps(payload, ensure_ascii=False)
            t7 = pc()
            aggregates.append(t7 - t6)
            if first_result is None:
                first_result = res_a
            print(
                f"rep {i + 1:2d}/{args.reps} ok, OCR chars={len(res_a.text)}, "
                f"payload={len(payload_json.encode('utf-8'))} B"
            )

    if first_result is None:
        sys.exit("No reps completed.")

    sample = payload_from_result(first_result, args.detail, args.max_blocks, map_rect)
    OCR_SAMPLE.write_text(
        json.dumps(sample, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    sample_bytes = len(json.dumps(sample, ensure_ascii=False).encode("utf-8"))
    sample_rects = count_rects(sample)
    # Privacy: counts only - never print or persist a preview of recognized text.
    print(
        f"\nOCR sample written to {OCR_SAMPLE.name}: {len(sample['text'])} chars, "
        f"{len(first_result.lines)} lines, {sample_rects} rects, {sample_bytes} B "
        f"(detail={args.detail})"
    )

    dec_total = [g + e + b + o for g, e, b, o in zip(grabs, encodes, dec_bitmaps, dec_ocrs)]
    buf_total = [g + b + o for g, b, o in zip(grabs, buf_bitmaps, buf_ocrs)]

    rows = [
        ("grab()", grabs),
        ("change-gate full-res (int16)", gate_full),
        ("change-gate stride 4 (int16)", gate_stride),
        ("jpeg encode", encodes),
        ("bitmap via JPEG decoder", dec_bitmaps),
        ("OCR (JPEG path)", dec_ocrs),
        ("bitmap via CreateCopyFromBuffer", buf_bitmaps),
        ("OCR (buffer path)", buf_ocrs),
        (f"aggregate + json ({args.detail})", aggregates),
        ("TOTAL sensory (JPEG path)", dec_total),
        ("TOTAL sensory (buffer path)", buf_total),
    ]

    header = f"{'stage':38s} {'median ms':>9s} {'p95 ms':>9s} {'min ms':>9s}"
    print("\n" + header)
    for name, values in rows:
        print(f"{name:38s} {fmt_ms(values)}")

    def md_line(name: str, values: list[float]) -> str:
        s = stage_stats(values)
        return (
            f"| {name} | {s['med'] * 1000:.1f} | {s['p95'] * 1000:.1f} | {s['min'] * 1000:.1f} |"
        )

    lines = [
        "",
        f"## Capture spike @ {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- OS: {platform.platform()}",
        f"- Python: {platform.python_version()} ({'winrt-*' if WINRT_MODERN else 'winsdk'} bindings)",
        (f"- dxcam {getattr(dxcam, '__version__', '?')}, "
        f"Pillow {PILLOW_VERSION}, numpy {np.__version__}"),
        (f"- Capture: {width}x{height} BGRA, region={region or 'full'}, "
        f"jpeg_quality={args.jpeg_quality}, downscale={args.downscale}, reps={args.reps}, "
        f"detail={args.detail}, max_blocks={args.max_blocks}"),
        f"- OCR languages: {', '.join(tags)}",
        (f"- Gate MAD (0-255 scale): identical pair full/stride4 = "
        f"{mad_same:.3f}/{mad_same_s4:.3f}, consecutive frames = "
        f"{mad_seq:.3f}/{mad_seq_s4:.3f}"),
        "",
        "| stage | median ms | p95 ms | min ms |",
        "|---|---|---|---|",
        *(md_line(name, values) for name, values in rows),
        (f"- OCR sample: {len(sample['text'])} chars, {len(first_result.lines)} lines, "
        f"{sample_rects} rects, {sample_bytes / 1024:.1f} KiB (detail={args.detail})"),
        "",
    ]
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nAppended results to {RESULTS}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Priority-2 capture + WinRT OCR spike")
    parser.add_argument("--reps", type=int, default=20)
    parser.add_argument("--jpeg-quality", type=int, default=75)
    parser.add_argument(
        "--detail",
        choices=("blocks", "lines", "words"),
        default="blocks",
        help="result granularity: blocks (default), lines, or words (raw per-word boxes, debug)",
    )
    parser.add_argument(
        "--max-blocks",
        type=int,
        default=5,
        help="cap on merged text-block rects, 0 = no cap (detail=blocks)",
    )
    parser.add_argument(
        "--downscale",
        type=parse_downscale,
        default=1.0,
        help="OCR image scale factor, e.g. 0.5 halves width/height",
    )
    parser.add_argument(
        "--region",
        type=parse_region,
        default=None,
        help="L,T,R,B pixel rectangle (left,top,right,bottom)",
    )
    args = parser.parse_args()
    if not IS_WINDOWS:
        sys.exit(
            "This spike requires native Windows: dxcam (DXGI) and WinRT do not exist in WSL."
        )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
