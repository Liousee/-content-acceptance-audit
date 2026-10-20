# Data verification

The goal is not to trust the spreadsheet. It is to establish which source is authoritative
for each field, then check every row against it.

## Source authority

| Source | Completion rate | Interaction counts | Notes |
|---|---|---|---|
| Work data detail page (with completion + likes/comments/shares/saves) | ✅ authoritative | ✅ | the primary evidence |
| Content appeal / audience analysis page | ⚠️ has completion, no interaction counts | ❌ | manual-confirmation item |
| Work browse page | ❌ | ❌ | manual-confirmation item |
| DOU+ order page | ❌ early data only | ❌ | manual-confirmation item |
| Content diagnosis page | ❌ | ❌ | manual-confirmation item |

If a row has no work-data-detail screenshot, it can still be judged from the table, but the
row belongs in the **manual confirmation** column.

## Completion-rate column calibration

Never assume the column means what its header says. Three cases have been observed:

1. **Fake column** — values match `(likes+comments+shares+saves)/play × 100`. The column is
   actually an interaction rate. Use screenshot OCR for completion.
2. **Decimal real column** — value × 100 matches OCR (e.g. `0.154` ≈ `15.4%`). Use the column
   × 100, with OCR cross-check.
3. **Mixed-in 2s bounce rate** — table value equals the screenshot's “2s bounce rate”.
   Treat as a source error; use OCR and flag the row.

The pipeline's `precheck()` and `calibrate_finish()` detect these; still review the log.

## OCR parsing

- Parse the label row for completion rate; exclude `5s完播率`.
- Vision may misread `完播率` as `元播率`; match `('完播' in seg or '元播' in seg) and '5s' not in seg`.
- Missing OCR is **not** proof the field is absent. Mark `OCR未识别，需人工查看`.
- Zero counts may simply be hidden by the UI; missing values are treated as 0 only when the
  page shows no count.
- Use multiple screenshots per row; cross-check the most complete one.
- Randomly inspect 3–5 OCR results visually with the Read tool when possible.

## Known table error patterns

These require **full-field comparison**, not sampling:

1. **Mixed sources** — interaction columns combine values from different screenshots/times.
   The work-data detail page wins.
2. **Shares/saves swapped** — the interaction rate can still be self-consistent because the
   denominator uses the swapped total.
3. **Zero-fill on missing capture** — table says 0, screenshot shows 11/10/5.
4. **Single-row completion error** — table value far above screenshot value; likely another
   column or another time point.
5. **Wrong screenshot reading** — table reading count differs from screenshot; “x.x万” is a
   rounded display and cannot be used to derive an exact denominator.
6. **Whole-column shift** — a missing column pushes later fields left. Repair by shifting
   right and validating: incentives all in the tier set, tax all 6%, gross = net × 1.06.

## Threshold pinning

When a decision is within ~0.3 percentage points of a threshold, do not rely on one full-page
OCR pass:

```bash
swiftc -O scripts/bbox.swift -o /tmp/bboxbin
python3 scripts/pin_value.py <workbook> 完播率 <seq...>
# or crop and upscale
python3 scripts/crop_ocr.py <image> <x0> <y0> <x1> <y1> 5
```

`bbox.swift` prints tokens with normalized coordinates (origin bottom-left). The value token
directly below the label in the same x-column is authoritative. Low-resolution OCR can turn
`7.78%` into `170。` or `5.79%` into `79`; 5–8× upscaling recovers it.

For a disputed row, `adjudicate.py` reads all screenshots and writes `adjudication.json`.

## Interaction-rate sampling

- Rows with table interaction rate **<1%**: OCR-check all of them (recompute likes/comments/
  shares/saves).
- Other rows: randomly sample 20–30% for OCR cross-check.
- Record the sampled values in an `OCR互动率` column, including the four counts as evidence.
- If the screenshot has no interaction data (e.g. DOU+ order page), put the row in manual
  confirmation and request a new screenshot.

## Supplementary images

When the business sends replacement screenshots:

- Do **not** trust filenames. Match by content: publish time + play count + likes/comments/
  shares/saves.
- `verify_supp_images.py` outputs the mapping; archive with the original filename preserved.
- Write the local path back into the source screenshot column so the pipeline can OCR it
  directly.

## What to write in the audit trail

- Which source was authoritative for each field
- What was cross-checked and the coverage (e.g. all rows, sampled 25%)
- Every value that was corrected, with old → new
- Every row left to a human, with the exact reason
- The OCR/bbox command used for any pinned threshold decision
