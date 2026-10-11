"""OCR block aggregation: cluster, merge-to-N, reading order, rect mapping."""

from __future__ import annotations

from pixhelp.ocr import (
    build_blocks,
    build_payload,
    line_rect,
    make_rect_mapper,
    union_rect,
)


def word(text: str, x: float, y: float, w: float = 40.0, h: float = 12.0) -> dict:
    return {"text": text, "rect": {"x": x, "y": y, "width": w, "height": h}}


def line(text: str, x: float, y: float, words: int = 2) -> dict:
    return {
        "text": text,
        "words": [word(f"{text}-{i}", x + i * 50, y) for i in range(words)],
    }


def test_union_rect_covers_all_members():
    rects = [
        {"x": 0, "y": 0, "width": 10, "height": 10},
        {"x": 20, "y": 5, "width": 10, "height": 10},
    ]
    assert union_rect(rects) == {"x": 0, "y": 0, "width": 30, "height": 15}


def test_line_rect_is_union_of_word_rects():
    ln = line("hello", 10, 20)
    r = line_rect(ln)
    assert r["x"] == 10 and r["y"] == 20
    assert r["width"] == 50 + 40  # second word starts at +50, is 40 wide


def test_adjacent_lines_cluster_into_one_block():
    lines = [line("a", 0, 0), line("b", 0, 14)]  # gap 2 < line height 12
    blocks = build_blocks(lines, max_blocks=5)
    assert len(blocks) == 1
    assert blocks[0]["text"] == "a\nb"


def test_distant_lines_stay_separate():
    lines = [line("a", 0, 0), line("b", 0, 200)]
    blocks = build_blocks(lines, max_blocks=5)
    assert len(blocks) == 2


def test_merge_down_to_max_blocks_reading_order():
    lines = [line(str(i), 0, i * 100) for i in range(10)]
    blocks = build_blocks(lines, max_blocks=3)
    assert len(blocks) == 3
    ys = [b["rect"]["y"] for b in blocks]
    assert ys == sorted(ys)


def test_max_blocks_zero_is_uncapped():
    lines = [line(str(i), 0, i * 100) for i in range(7)]
    assert len(build_blocks(lines, max_blocks=0)) == 7


def test_empty_lines_yield_no_blocks():
    assert build_blocks([], max_blocks=5) == []
    assert build_blocks([{"text": "", "words": []}], max_blocks=5) == []


def test_payload_detail_levels():
    lines = [line("a", 0, 0), line("b", 0, 200)]
    words = build_payload("t", lines, "words", 5)
    assert "lines" in words and "words" in words["lines"][0]
    lns = build_payload("t", lines, "lines", 5)
    assert lns["lines"][0]["rect"]["y"] == 0
    blocks = build_payload("t", lines, "blocks", 5)
    assert len(blocks["blocks"]) == 2


def test_rect_mapper_undoes_region_and_downscale():
    mapper = make_rect_mapper(region=(100, 200, 900, 700), factor=0.5)
    mapped = mapper({"x": 10.0, "y": 20.0, "width": 40.0, "height": 12.0})
    assert mapped == {"x": 120.0, "y": 240.0, "width": 80.0, "height": 24.0}


def test_rect_mapper_full_screen_identity():
    mapper = make_rect_mapper(region=None, factor=1.0)
    assert mapper({"x": 5, "y": 6, "width": 7, "height": 8}) == {
        "x": 5.0,
        "y": 6.0,
        "width": 7.0,
        "height": 8.0,
    }
