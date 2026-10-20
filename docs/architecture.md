# Architecture

## Pipeline at a glance

```
source workbook (.xls/.xlsx)
        │
        ├─[1] load_source()       read rows by column position
        ├─[2] precheck()          formula / tax / time / column-shift checks
        ├─[3] OCR                 screenshots → text (cached per URL/path)
        ├─[4] calibrate_finish()  is the completion column real, fake, or 2s-bounce?
        ├─[5] check_links()       URL normalization + optional verify_db lookup
        ├─[6] audit_rows()        review 1-4 per rules config
        ├─[7] build_output()      styled workbook + AI trail + manual items
        ├─[8] archive             PO-style folder + audit_state.json
        └─[9] evidence/dossier    screenshot evidence pack + SHA256 dossier
```

The link/follower verification stack runs beside the pipeline and writes into
`~/.cache/yanshou/verify_db.jsonl`. `audit_pipeline.py --verify-db` reads that database
without modifying the source workbook.

## Components

| Layer | Files | Responsibility |
|---|---|---|
| Rules | `config/rules.example.json`, `--rules` | tier thresholds and amount→tier maps |
| Pipeline | `audit_pipeline.py` | preflight, OCR orchestration, judgement, output |
| OCR | `ocr.swift`, `ocr_batch.swift`, `crop_ocr.py`, `pin_value.py`, `bbox.swift` | screenshot text, pixel-level adjudication |
| Links | `link_verify.py`, `link_status.py`, `link_console.py`, `link_review.py`, `verify_common.py` | validity checks, caching, rate limiting, human console, annotation bridge |
| Followers | `fans_check.py` | DOM + OCR cross-check, resumable |
| Content | `dy_read.py`, `dy_media.py`, `dy_frames.swift`, `dy_capocr.swift`, `dy_asr.swift` | page text, media, frames, subtitles |
| Evidence | `audit_pipeline.py`, `adjudicate.py`, `evidence.py`, `dossier.py`, `verify_supp_images.py` | state, adjudication, screenshot evidence pack, SHA256 dossier, supplementary-image attribution |
| Utilities | `cdp_ws.py`, `clean_cache.sh`, `token_usage.py`, `make_template.py` | CDP, cleanup, cost, template |

## Caches

| Cache | Location | TTL / behavior |
|---|---|---|
| OCR text | `~/.cache/yanshou_ocr.json` | 7 days; keyed by URL or local path |
| Link results | `~/.cache/yanshou/verify_db.jsonl` | VALID 7d / INVALID 30d / uncertain not cached |
| Short-link resolution | in `verify_db` | permanent |
| Rate-limit state | `~/.cache/yanshou/rate_state.json` | token bucket + window budget + cooldown |
| Run lock | `~/.cache/yanshou/run.lock` | one verification run at a time |

Caches are **local and disposable**. Never commit them. They can contain campaign URLs
and titles.

## State files in a work folder

```
PO<id>/
├── <name>-原始.xlsx
├── <name>-AI审核结果-<date>.xlsx
├── AI审核底档-<date>.md / .html
├── 截图证据清单.xlsx
├── audit_state.json                # summary snapshot
├── work_rows.json                  # full row snapshot (for adjudicate/evidence/dossier)
├── audit_runlog.jsonl              # optional command log
├── 链接复核/
├── 粉丝核验/
└── 补图核销/
```

`audit_state.json` is the machine-readable snapshot; `work_rows.json` holds the full rows
used by `adjudicate.py`, `evidence.py`, and `dossier.py`. The workbook is regenerated from
the source plus these files. Never patch an output workbook by keyword search — rebuild it.

`diffs.json`, `ocr_results.json`, and `link_results.json` may be produced by
project-specific adapters; the core pipeline does not require them.

## Profiles

The public repo ships **Profile A** (two sheets, fixed 37-column layout). Other table shapes
are supported by adapting `load_source()` or writing a small adapter that produces the same
row dictionaries. See [table-profile.md](table-profile.md).

## Design choices

- **Full rebuild over incremental edits** — avoids prefix/keyword mismatches.
- **Request count is the scarce resource** — cache, normalize URLs, dedupe by video ID.
- **Human review is a feature, not a fallback** — the console is often faster than blind
  automation and carries the lowest account risk.
- **Evidence over assertions** — every automated verdict should be reproducible from saved
  state, OCR text, and command logs.
