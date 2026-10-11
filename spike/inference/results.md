
## Inference spike @ 2026-10-11 22:26:21

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (aiohttp, Pillow 12.3.0)
- Server: llama-b11552-bin-win-cpu-x64, threads=4, context=4096, port=8080
- Deadline: 8.0s, reps=5, max_tokens=300, hard_timeout=180s
- Payload: capture/ocr_sample.json (5 rects, 6.6 KiB)
- Vision image: synthetic, q75, budgets=[1003520, 262144, 65536]

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| cold start (text model) |   3692.3 /   3692.3 /   3692.3 |
| text: ttft |    186.3 /  70802.2 /    124.7 |
| text: prefill |    107.5 /  70751.6 /    101.4 |
| text: total |  37411.9 / 101610.9 /  28866.5 |
| cold start (vision model) |  21692.5 /  21692.5 /  21692.5 |
| jpeg encode (vision@1003520px) |    140.1 /    384.0 /    123.0 |
| vision@1003520px: ttft |   2371.4 / 126252.2 /   1817.6 |
| vision@1003520px: prefill |    402.8 /   1685.9 /    389.1 |
| vision@1003520px: total |  33185.2 / 128471.7 /   4433.7 |
| jpeg encode (vision@262144px) |    109.2 /    156.7 /     86.7 |
| vision@262144px: ttft |  30981.5 /  58340.9 /   3622.2 |
| vision@262144px: prefill |  28966.0 /  57093.6 /    838.4 |
| vision@262144px: total |  96584.1 / 116802.3 /  76366.0 |
| jpeg encode (vision@65536px) |     79.9 /    114.2 /     67.9 |
| vision@65536px: ttft |   1311.3 /  36805.3 /   1286.9 |
| vision@65536px: prefill |    355.9 /  34463.7 /    311.6 |
| vision@65536px: total |  27891.9 /  71979.4 /  27280.5 |
- text: grammar=on, prompt_tokens=1, gen p50=9.3 tok/s, valid JSON=1/4, deadline hits=4/4, errors=1
- text budget: sensory p95 1240ms + reasoning p95 101611ms = 102851ms (OVER the 8000ms tick)
- vision@1003520px: grammar=on, prompt_tokens=1, gen p50=2.0 tok/s, valid JSON=3/3, deadline hits=2/3, errors=2
- vision@1003520px budget: sensory p95 1240ms + reasoning p95 128472ms = 129712ms (OVER the 8000ms tick)
- vision@262144px: grammar=on, prompt_tokens=1, gen p50=1.8 tok/s, valid JSON=2/2, deadline hits=2/2, errors=3
- vision@262144px budget: sensory p95 1240ms + reasoning p95 116802ms = 118042ms (OVER the 8000ms tick)
- vision@65536px: grammar=on, prompt_tokens=1, gen p50=3.0 tok/s, valid JSON=4/4, deadline hits=4/4, errors=1
- vision@65536px budget: sensory p95 1240ms + reasoning p95 71979ms = 73219ms (OVER the 8000ms tick)
- VERDICT: text p95=101611ms -> FAIL (deadline 8000ms)
- VERDICT: vision@1003520px p95=128472ms -> FAIL (deadline 8000ms)
- VERDICT: vision@262144px p95=116802ms -> FAIL (deadline 8000ms)
- VERDICT: vision@65536px p95=71979ms -> FAIL (deadline 8000ms)
