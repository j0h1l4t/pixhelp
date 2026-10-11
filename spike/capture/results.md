
## Capture spike @ 2026-10-11 12:42:14

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (winrt-* bindings)
- dxcam ?, Pillow 12.3.0, numpy 2.5.4
- Capture: 1920x1080 BGRA, region=full, jpeg_quality=75, downscale=1.0, reps=20
- OCR languages: en-GB, en-US, es-MX, zh-Hans-CN

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| grab() | 11.3 | 14.3 | 9.6 |
| jpeg encode | 104.4 | 131.8 | 77.9 |
| bitmap via JPEG decoder | 28.8 | 57.0 | 21.8 |
| OCR (JPEG path) | 841.1 | 1081.3 | 634.1 |
| bitmap via CreateCopyFromBuffer | 21.3 | 27.4 | 17.4 |
| OCR (buffer path) | 828.6 | 1068.6 | 542.0 |
| TOTAL sensory (JPEG path) | 1000.2 | 1264.6 | 763.5 |
| TOTAL sensory (buffer path) | 868.2 | 1106.5 | 572.4 |
- OCR sample: 3204 chars, 169 lines

## Capture spike @ 2026-10-11 16:01:46

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (winrt-* bindings)
- dxcam ?, Pillow 12.3.0, numpy 2.5.4
- Capture: 1920x1080 BGRA, region=full, jpeg_quality=75, downscale=1.0, reps=20, detail=blocks, max_blocks=5
- OCR languages: en-GB, en-US, es-MX, zh-Hans-CN

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| grab() | 7.6 | 14.4 | 5.7 |
| jpeg encode | 84.0 | 122.8 | 71.9 |
| bitmap via JPEG decoder | 24.5 | 35.2 | 21.3 |
| OCR (JPEG path) | 546.5 | 775.7 | 435.7 |
| bitmap via CreateCopyFromBuffer | 20.9 | 30.7 | 16.3 |
| OCR (buffer path) | 483.6 | 694.5 | 440.9 |
| aggregate + json (blocks) | 9.8 | 13.8 | 7.9 |
| TOTAL sensory (JPEG path) | 657.3 | 940.8 | 538.9 |
| TOTAL sensory (buffer path) | 511.2 | 746.1 | 468.7 |
- OCR sample: 2974 chars, 121 lines, 5 rects, 6.4 KiB (detail=blocks)

## Capture spike @ 2026-10-11 16:02:37

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (winrt-* bindings)
- dxcam ?, Pillow 12.3.0, numpy 2.5.4
- Capture: 1920x1080 BGRA, region=full, jpeg_quality=75, downscale=1.0, reps=20, detail=blocks, max_blocks=5
- OCR languages: en-GB, en-US, es-MX, zh-Hans-CN

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| grab() | 10.5 | 13.4 | 7.0 |
| jpeg encode | 95.9 | 124.3 | 80.6 |
| bitmap via JPEG decoder | 27.5 | 43.3 | 20.8 |
| OCR (JPEG path) | 615.6 | 793.6 | 492.4 |
| bitmap via CreateCopyFromBuffer | 21.6 | 29.7 | 17.9 |
| OCR (buffer path) | 577.7 | 693.3 | 462.5 |
| aggregate + json (blocks) | 11.0 | 12.7 | 8.1 |
| TOTAL sensory (JPEG path) | 746.7 | 936.0 | 624.5 |
| TOTAL sensory (buffer path) | 614.6 | 733.3 | 492.4 |
- OCR sample: 2974 chars, 121 lines, 5 rects, 6.4 KiB (detail=blocks)

## Capture spike @ 2026-10-11 16:07:04

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (winrt-* bindings)
- dxcam ?, Pillow 12.3.0, numpy 2.5.4
- Capture: 1920x1080 BGRA, region=full, jpeg_quality=75, downscale=0.8, reps=20, detail=blocks, max_blocks=5
- OCR languages: en-GB, en-US, es-MX, zh-Hans-CN

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| grab() | 242.5 | 304.6 | 210.9 |
| jpeg encode | 45.9 | 63.3 | 37.2 |
| bitmap via JPEG decoder | 15.5 | 42.2 | 13.2 |
| OCR (JPEG path) | 520.3 | 688.9 | 469.4 |
| bitmap via CreateCopyFromBuffer | 28.5 | 38.7 | 23.7 |
| OCR (buffer path) | 495.9 | 669.7 | 439.6 |
| aggregate + json (blocks) | 43.5 | 64.1 | 5.9 |
| TOTAL sensory (JPEG path) | 829.6 | 1124.3 | 733.3 |
| TOTAL sensory (buffer path) | 767.9 | 1001.0 | 678.7 |
- OCR sample: 2101 chars, 72 lines, 5 rects, 4.8 KiB (detail=blocks)

## Capture spike @ 2026-10-11 16:07:35

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (winrt-* bindings)
- dxcam ?, Pillow 12.3.0, numpy 2.5.4
- Capture: 1920x1080 BGRA, region=full, jpeg_quality=75, downscale=1.0, reps=20, detail=blocks, max_blocks=5
- OCR languages: en-GB, en-US, es-MX, zh-Hans-CN

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| grab() | 6.8 | 10.3 | 4.9 |
| jpeg encode | 82.5 | 102.3 | 69.0 |
| bitmap via JPEG decoder | 22.7 | 28.8 | 20.0 |
| OCR (JPEG path) | 442.1 | 522.0 | 403.9 |
| bitmap via CreateCopyFromBuffer | 19.0 | 21.7 | 16.4 |
| OCR (buffer path) | 411.5 | 492.2 | 376.2 |
| aggregate + json (blocks) | 7.3 | 10.6 | 5.8 |
| TOTAL sensory (JPEG path) | 558.7 | 708.6 | 512.4 |
| TOTAL sensory (buffer path) | 437.3 | 519.6 | 400.3 |
- OCR sample: 2577 chars, 96 lines, 5 rects, 5.6 KiB (detail=blocks)

## Capture spike @ 2026-10-11 16:08:46

- OS: Windows-11-10.0.26300-SP0
- Python: 3.14.5 (winrt-* bindings)
- dxcam ?, Pillow 12.3.0, numpy 2.5.4
- Capture: 1920x1080 BGRA, region=full, jpeg_quality=75, downscale=1.0, reps=20, detail=blocks, max_blocks=5
- OCR languages: en-GB, en-US, es-MX, zh-Hans-CN

| stage | median ms | p95 ms | min ms |
|---|---|---|---|
| grab() | 10.2 | 14.2 | 5.8 |
| jpeg encode | 98.4 | 131.3 | 76.8 |
| bitmap via JPEG decoder | 27.6 | 35.3 | 21.3 |
| OCR (JPEG path) | 637.9 | 951.3 | 510.2 |
| bitmap via CreateCopyFromBuffer | 21.4 | 27.1 | 18.3 |
| OCR (buffer path) | 569.9 | 850.6 | 485.8 |
| aggregate + json (blocks) | 17.3 | 22.5 | 10.4 |
| TOTAL sensory (JPEG path) | 788.0 | 1101.0 | 630.5 |
| TOTAL sensory (buffer path) | 605.7 | 881.4 | 515.0 |
- OCR sample: 2976 chars, 239 lines, 5 rects, 6.6 KiB (detail=blocks)
