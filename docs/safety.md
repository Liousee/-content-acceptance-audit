# Safety and account protection

This project automates work around live platform accounts. Treat account safety as more
important than completing a checklist.

## Non-negotiable rules

1. **Login-state automation is limited.** Use it only for a small number of disputed rows
   (≤5 per batch, short sessions), slowly, with the account owner present.
2. **Bulk work uses the anonymous lane.** No account identity, lower risk.
3. **Stop on a real CAPTCHA.** Cool down for at least 30 minutes. Do not retry in a loop.
4. **Never commit or share** cookies, profiles, API keys, source data, or outputs.
5. **Never change payout amounts automatically.** Report and wait for a human decision.
6. **Do not attempt to bypass access controls, CAPTCHAs, or rate limits.** That is out of scope.

## Lanes and rate limits

| Lane | Identity | Tool | Safe rate | Use |
|---|---|---|---|---|
| `anon` (default) | none | `link_verify.py --lane anon`, `link_status.py` | 15–30 links/min burst; 200 per 30 min window | full screening |
| `login` | account cookie | `link_console.py`, `link_verify.py --lane login` | ≤5 links per batch, short sessions, human present | decisive spot checks only |

Concrete anonymous parameters:

- concurrency ≤2–3
- ≥5–8 s between requests
- batches ≤50 links
- ≥10 minutes between batches
- stop and cool down ≥30 minutes on a real CAPTCHA

Concrete login parameters:

- one batch of ≤5 links at a time
- no “links per minute” target — the account, not throughput, is the constraint
- human present; stop if anything looks unusual

## Shell pages and false positives

Douyin serves anti-bot shell pages that can look like valid responses. The tools classify
these as `BLOCKED` / `EARLY`, never `VALID`:

- page title is the URL itself
- body contains `Please wait`, `加载中`, `ratelimit`, `验证码中间页`
- generic landing title (`抖音精选电脑版`, generic “记录美好生活” template)

HTTP 200 means **reachable**, not **openable**. Do not claim content-level validity from a
status code.

## CAPTCHA detection

Do **not** use `'captcha' in html` — normal pages contain that string in JS config.
Use one of:

- `<title>验证码中间页</title>`
- `验证码` appears in the first ~3 KB **and** the page is small (<200 KB)

## Credentials and data

Never commit or send:

| Item | Why |
|---|---|
| `~/.cache/yanshou-chrome/` | login cookies (`sessionid`, `sid_tt`) = account access |
| `~/.cache/yanshou/verify_db.jsonl` | campaign URLs, titles, verification history |
| `~/.cache/yanshou_ocr.json` | OCR text from screenshots |
| `PO*/` | source rows often contain names, phones, VINs, amounts |
| screenshots / evidence packs | personal and commercial data |
| `.env`, `settings.json`, tokens | credentials |

The repository `.gitignore` blocks common paths, but it cannot protect data you copy elsewhere.

## Account hygiene

- Use a dedicated account where possible; do not use a personal main account for automation.
- Keep the login profile local and encrypted at rest (full-disk encryption).
- Re-run `login_douyin.sh` when the session expires; do not copy a profile between machines.
- If a CAPTCHA or restriction appears, stop all runs and wait. Escalating request volume makes
  it worse.

## Legal and ethical use

Use this tool only on campaigns and accounts you are authorized to audit. Follow platform
terms, client contracts, privacy law, and data-protection requirements. When in doubt, ask the
business owner before running a check.
