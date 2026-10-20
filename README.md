# content-acceptance-audit

**Content acceptance audit toolkit for creator campaigns — evidence-first, human-in-the-loop.**

**Claude Code skill name:** `yanshou-audit`

[中文文档](README.zh-CN.md) · [Skill methodology](SKILL.md) · [Safety](docs/safety.md)

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

`yanshou-audit` turns a campaign acceptance spreadsheet into an auditable deliverable:
judgement columns filled, screenshots cross-checked with OCR, links and follower counts
verified, money reconciled, and an evidence pack that can survive a later audit.

It is packaged as a [Claude Code](https://claude.com/claude-code) skill (`SKILL.md`) **and**
as standalone Python/Swift CLI tools. The skill is written in Chinese because the workflows
and table conventions are Chinese-market oriented; the code is usable independently.

---

## ⚠️ Read this first

This tool touches **live platform accounts** and **money-related decisions**.

- **Never commit or share** login profiles, cookies, API keys, source spreadsheets,
  screenshots, or deliverables. `.gitignore` blocks the common paths; you are still responsible.
- **Login-state automation is limited to ≤5 items per batch, short sessions, slow, with a human present.**
  Anonymous/headless verification is the default and the only lane suitable for bulk work.
- **Stop immediately on a real CAPTCHA** and cool down for at least 30 minutes.
- **Never change payout amounts automatically.** Report findings and let the business decide.
- Follow the platform's terms of service and applicable law. This project does not provide
  CAPTCHA bypassing, rate-limit evasion, or unauthorized access.

See [docs/safety.md](docs/safety.md) for the full red-line list.

---

## What it does

| Capability | What you get |
|---|---|
| **Table audit pipeline** | Preflight checks, completion-rate calibration, OCR, link checks, judgement, styled workbook |
| **Screenshot OCR cross-check** | Vision OCR with cache, multi-screenshot cross-validation, bbox pixel adjudication |
| **Link validity** | Three-layer check (URL pattern → screenshot state → human spot-check), anonymous + login lanes |
| **Human review console** | Opens small batches of tabs for a human to eyeball; human-paced, no blind automation |
| **Follower verification** | Headless DOM + screenshot OCR, measured at verification time, evidence archived |
| **Evidence chain** | `audit_state.json`, `work_rows.json`, optional run log, screenshot evidence pack, SHA256 dossier, reproduce commands |
| **Two-phase audit** | Phase 1 incentive review; phase 2 settlement review 30/60 days later |

---

## Requirements

| Component | Needed for | Notes |
|---|---|---|
| Python 3.9+ | everything | `openpyxl`, `xlrd`, `Pillow` |
| Google Chrome | link/follower verification | macOS default path; override with `CHROME_BIN` |
| macOS + Xcode CLI tools | OCR, bbox, video frames | Swift + Vision/AVFoundation; Linux can run table/link logic only |
| Douyin account | login-state spot checks only | use your own account; never share the profile |

```bash
python3 -m pip install -r requirements.txt
```

---

## Install

### As a Claude Code skill

```bash
git clone https://github.com/Liousee/-content-acceptance-audit.git ~/.claude/skills/yanshou-audit
# then ask Claude Code to run an acceptance audit
```

> The scripts are launched with `python3`. Install `requirements.txt` in that interpreter
> (activate your venv before starting Claude Code, or install with `pip --user`).

### As standalone tools

```bash
git clone https://github.com/Liousee/-content-acceptance-audit.git yanshou-audit
cd yanshou-audit
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 scripts/make_template.py --out 验收审核底表模板.xlsx
```

---

## Quick start

### 1. Create a blank workbook

```bash
python3 scripts/make_template.py --out 验收审核底表模板.xlsx
```

Fill the `验收链接` sheet (one work per row), then put your project rules in
`config/rules.local.json` (copy from `config/rules.example.json`).

### 2. Run the pipeline

```bash
python3 scripts/audit_pipeline.py 验收审核底表模板.xlsx \
  --rules config/rules.local.json
```

Output: `<name>-AI审核结果-YYYYMMDD.xlsx` plus `audit_state.json`.

### 3. Verify links (anonymous first)

```bash
# bulk screening, no account identity
python3 scripts/link_verify.py <表.xlsx> --lane anon --min-fee 1

# bridge results into the deliverable
python3 scripts/link_review.py <表.xlsx>
```

For the few rows that need a decisive check, use the human console (login state):

```bash
bash scripts/login_douyin.sh          # one-time QR login
python3 scripts/link_console.py <表.xlsx> --batch 5 --min-fee 1 --pause 8
```

### 4. Verify followers

```bash
python3 scripts/fans_check.py <表.xlsx> --evidence-dir ./粉丝核验 --report
```

### 5. Pin values near a threshold

```bash
swiftc -O scripts/bbox.swift -o /tmp/bboxbin
python3 scripts/pin_value.py <表.xlsx> 完播率 12 35 101
```

### 6. Evidence, dossier, and cleanup

```bash
python3 scripts/adjudicate.py --work-dir ./PO0000000000 12 35
python3 scripts/evidence.py --work-dir ./PO0000000000
python3 scripts/dossier.py --work-dir ./PO0000000000 --rules config/rules.local.json
bash scripts/clean_cache.sh ./PO0000000000
```

---

## Repository layout

```
.
├── SKILL.md                    # Claude Code skill methodology (Chinese)
├── README.md / README.zh-CN.md
├── config/
│   └── rules.example.json      # tier configuration template
├── docs/
│   ├── architecture.md         # pipeline stages, caches, state files
│   ├── safety.md               # account/rate-limit red lines
│   ├── data-verification.md    # OCR, bbox, known data-error patterns
│   ├── deliverable-spec.md     # workbook/evidence/dossier conventions
│   └── table-profile.md        # Profile A schema and adaptation guide
├── examples/
│   └── annotations.example.json
├── scripts/                    # 28 reusable tools
├── tests/
└── .github/workflows/ci.yml
```

---

## Configuration

`config/rules.example.json` is a **fictional example**. Copy it to
`config/rules.local.json` (gitignored) and set:

- `tiers`: named thresholds (`play`, `interact`, `finish`, `label`)
- `incentive_tiers`: incentive amount → tier
- `fee_tiers`: acceptance fee → tier

Interaction rate is a decimal (`0.005` = 0.5%). Completion rate is a percentage
(`5` = 5%).

The Profile A column layout is documented in [docs/table-profile.md](docs/table-profile.md).
The pipeline reads columns by position; if your source table differs, adapt
`load_source()` or write a profile adapter.

---

## What is intentionally not included

- **Client/brand names, campaign IDs, creator names, real fees/tiers** — removed.
- **Historical deliverables, source spreadsheets, screenshots, cookies** — never included.
- **A multi-sheet 8-column profile adapter** — the public repo ships the two-sheet profile;
  multi-sheet schemas are project-specific. See `docs/table-profile.md` for the adaptation pattern.
- **Any guarantee of platform stability** — Douyin's anti-bot behavior changes; automation is
  best-effort and human review remains the final authority.

---

## Testing

```bash
python3 -m compileall scripts tests
python3 -m unittest discover -s tests -v
```

CI runs the same checks on Ubuntu (no macOS-only OCR tests).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please do not include real campaign data,
credentials, or client-identifying details in issues or pull requests.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Not affiliated with Douyin/ByteDance or any platform mentioned. Use at your own risk and
in compliance with platform terms, contracts, and applicable law. The authors are not
responsible for account restrictions, data loss, or financial decisions made with this tool.
