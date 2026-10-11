"""WinRT OCR + pure aggregation of recognition results into <= N text blocks.

The aggregation helpers are pure (dicts in/out) so they are unit-testable on
any OS; only recognize()/_engine() touch WinRT and are Windows-only.
"""

from __future__ import annotations

import sys
from typing import Any
from collections.abc import Callable

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from winrt.windows.graphics.imaging import (  # type: ignore[import-not-found]
        BitmapPixelFormat,
        SoftwareBitmap,
    )
    from winrt.windows.media.ocr import OcrEngine  # type: ignore[import-not-found]
    from winrt.windows.storage.streams import Buffer, DataWriter  # type: ignore[import-not-found]


Rect = dict[str, float]


# --- pure aggregation (ported from spike/capture/bench_capture.py) ------------


def rect_dict(r: Any) -> Rect:
    return {"x": r.x, "y": r.y, "width": r.width, "height": r.height}


def make_rect_mapper(
    region: tuple[int, int, int, int] | None, factor: float
) -> Callable[[dict], dict]:
    """Undo region-crop + downscale so rects are full-screen coordinates."""
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


def union_rect(rects: list[Rect]) -> Rect:
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for r in rects:
        x, y, w, h = r["x"], r["y"], r["width"], r["height"]
        x0, y0 = min(x0, x), min(y0, y)
        x1, y1 = max(x1, x + w), max(y1, y + h)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def line_rect(line: dict) -> Rect:
    return union_rect([w["rect"] for w in line["words"]])


def cluster_lines(
    entries: list[tuple[Rect, str]], gap: float
) -> list[list[tuple[Rect, str]]]:
    """Single-linkage: merge rects with vertical gap <= gap and horizontal overlap."""
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
        left, right = ri["x"], ri["x"] + ri["width"]
        for j in order[pos + 1 :]:
            rj = entries[j][0]
            if rj["y"] > bottom + gap:
                break
            if min(right, rj["x"] + rj["width"]) > max(left, rj["x"]):
                ri_root, rj_root = find(i), find(j)
                if ri_root != rj_root:
                    parent[rj_root] = ri_root

    groups: dict[int, list[tuple[Rect, str]]] = {}
    for i, entry in enumerate(entries):
        groups.setdefault(find(i), []).append(entry)
    return list(groups.values())


def _block_from(members: list[tuple[Rect, str]]) -> dict:
    ordered = sorted(members, key=lambda e: (e[0]["y"], e[0]["x"]))
    return {
        "rect": union_rect([rect for rect, _ in ordered]),
        "text": "\n".join(text for _, text in ordered),
    }


def _closest_pair(blocks: list[dict]) -> tuple[int, int]:
    best = None
    for i in range(len(blocks)):
        a = blocks[i]["rect"]
        ax, ay = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
        for j in range(i + 1, len(blocks)):
            b = blocks[j]["rect"]
            dx = ax - (b["x"] + b["width"] / 2)
            dy = ay - (b["y"] + b["height"] / 2)
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, i, j)
    return best[1], best[2]


def _grid_bucket(blocks: list[dict], max_blocks: int) -> list[dict]:
    cols = max(4, int((max_blocks * 16 / 9) ** 0.5) + 1)
    rows = max(3, int((max_blocks * 9 / 16) ** 0.5) + 1)
    centers = [
        (b["rect"]["x"] + b["rect"]["width"] / 2, b["rect"]["y"] + b["rect"]["height"] / 2)
        for b in blocks
    ]
    xs, ys = [c[0] for c in centers], [c[1] for c in centers]
    x_lo, y_lo = min(xs), min(ys)
    x_span, y_span = max(max(xs) - x_lo, 1.0), max(max(ys) - y_lo, 1.0)
    cells: dict[tuple[int, int], list[dict]] = {}
    for block, (cx, cy) in zip(blocks, centers, strict=True):
        gx = min(cols - 1, int((cx - x_lo) / x_span * cols))
        gy = min(rows - 1, int((cy - y_lo) / y_span * rows))
        cells.setdefault((gx, gy), []).append(block)
    return [
        _block_from([(b["rect"], b["text"]) for b in group])
        for group in cells.values()
    ]


def _merge_to(blocks: list[dict], max_blocks: int) -> list[dict]:
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
    entries = [(line_rect(line), line["text"]) for line in lines if line["words"]]
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


def extract_lines(result: Any, map_rect: Callable[[dict], dict]) -> list[dict]:
    """WinRT OcrResult -> list-of-line dicts (requires a live WinRT result)."""
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


# --- Windows-only WinRT glue --------------------------------------------------


def create_engine(languages: tuple[str, ...] | None = None) -> Any:
    if not IS_WINDOWS:
        raise RuntimeError("WinRT OCR requires native Windows (not WSL)")
    if languages:
        from winrt.windows.globalization import Language

        for tag in languages:
            try:
                engine = OcrEngine.try_create_from_language(Language(tag))
            except Exception:
                engine = None
            if engine is not None:
                return engine
        raise RuntimeError(f"no WinRT OCR engine for languages {languages}")
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        langs = list(OcrEngine.available_recognizer_languages)
        if not langs:
            raise RuntimeError("no WinRT OCR language installed")
        engine = OcrEngine.try_create_from_language(langs[0])
    return engine


def bitmap_from_bgra(raw: bytes, width: int, height: int) -> Any:
    if not IS_WINDOWS:
        raise RuntimeError("WinRT OCR requires native Windows (not WSL)")
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


async def _await_op(op: Any) -> Any:
    if hasattr(op, "__await__"):
        return await op
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, op.get)


async def recognize_frame(
    engine: Any,
    frame_bgra: Any,
    map_rect: Callable[[dict], dict],
    detail: str = "blocks",
    max_blocks: int = 5,
) -> dict:
    """BGRA numpy frame -> aggregated payload dict (async; off-loop friendly)."""
    import asyncio

    height, width = frame_bgra.shape[:2]
    raw = frame_bgra.tobytes()
    loop = asyncio.get_running_loop()
    bitmap = await loop.run_in_executor(None, bitmap_from_bgra, raw, width, height)
    result = await _await_op(engine.recognize_async(bitmap))
    lines = extract_lines(result, map_rect)
    return build_payload(result.text, lines, detail, max_blocks)
