# Deliverable specification

Every audit should leave a package that a different person can understand months later.

## Workbook

Filename: `<source-name>-AI审核结果-YYYYMMDD.xlsx`

Sheets:

| Sheet | Contents |
|---|---|
| `验收链接` | source rows + review 1-4 + `完播率(截图OCR)` + `人工确认项` |
| `审核规则` | copied from the source, with the current rule version |
| `AI审核留痕` | audit time/tool/source, rule text, verification record, per-row detail, link validity columns |

`link_review.py` writes a separate `链接复核/` report (Markdown + JSON) and injects link
status into the audit trail; it does not add a workbook sheet by default.

Rules:

- **Full rebuild every time.** Do not patch a generated workbook by keyword.
- Same-day rebuilds overwrite the same date-stamped file; different days keep history.
- Style every sheet, including sheets copied from the source.
- Header: dark blue fill, white bold text, borders, frozen first row.
- Alternate row fill; pass = green, fail = red, pending = orange.
- Manual-confirmation cells: light yellow fill, orange text.
- Hyperlink URL cells; sensible column widths; wrap long text.

## `验收链接` columns

Keep the source 37-column layout and append:

| Column | Meaning |
|---|---|
| 审核1 | homepage reachable + followers ≥ threshold |
| 审核2 | publish time after launch, ≤72 h, duration ≥15 s |
| 审核3 | incentive tier |
| 审核4 | fee tier + tax/gross reconciliation |
| 完播率(截图OCR) | OCR value used for judgement |
| OCR互动率 | sampled OCR recomputation, with counts |
| 人工确认项 | what a human must check, and why |

## AI audit trail

Sections:

1. Rule text and calibration notes (how the completion column was interpreted)
2. Data verification record (checks, coverage, corrections)
3. Result summary (counts, rule version, state file location)
4. Per-row detail: screenshot OCR completion, manual item, failure reason, link status,
   fee involvement

Every claim in the trail should be reproducible from a saved command and a saved state file.

## Link review columns and report

The per-row detail in `AI审核留痕` includes:

- `链接有效性`: `有效` / `失效(跳转推荐流/404)` (red) / `未复核` (orange)
- `涉及验收费用`: `是(金额)` / `否` / `缺数据`
- `核验方式`, `核验证据`, `金额影响`

`link_review.py` also writes a `链接复核/` report (Markdown + JSON) with the full evidence.
Humans filter `链接有效性 = 失效` and `涉及验收费用 = 是` to find rows needing a decision.

## Evidence pack

`截图证据清单.xlsx`:

- **截图清单**: OSS/local link, screenshot type, OCR-read fields, values
- **作品字段快照**: title, duration, play count, interaction rate, completion rate, tier,
  amount, link validity — the baseline for a later “is this still the same work?” check

Do not store raw images in the repository. Store links plus the field snapshot so the record
survives even after image links expire.

## Dossier

`AI审核底档-<date>.md` and `.html`, ten sections:

1. Task identity (batch, table profile, review number)
2. Execution info (time, tools, decision method)
3. Input file SHA256
4. Rule basis (including business clarifications and dates)
5. Execution steps (tool, parameters, output, result counts)
6. Decision method and calibration
7. Result summary and money reconciliation
8. Exceptions and manual items
9. Reproduce commands
10. Artifact list and SHA256

If your runner emits `audit_runlog.jsonl` (start/end/duration per command), keep it next to the
dossier; `dossier.py` includes the last 20 entries automatically.

## Money reconciliation

- Net total, gross total, incentive total, and their proportions must tie out.
- Never modify amounts automatically.
- For invalid links or disputed data, produce an **amount impact list** and wait for approval.
- If approved: set the acceptance fee (net) to 0 and gross to 0, note “not settled as an
  acceptance fee”; the incentive is unaffected unless the business says otherwise.

## Two-phase packages

Phase 1 (incentive) and phase 2 (settlement) use the same format. Phase 2 additionally:

- re-checks links at settlement time (phase 1 results are stale)
- compares title/duration/key fields to detect a replaced work
- re-checks whether the data still meets the tier
- uses phase 1 as the baseline; does not upgrade tiers for data growth
- only adjusts the acceptance fee for invalid works; paid incentives are untouched
