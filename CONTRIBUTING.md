# Contributing

Thanks for improving `yanshou-audit`.

## Ground rules

- **No real campaign data.** Do not include client names, creator names, PO numbers,
  phone numbers, VINs, screenshots, cookies, deliverables, or real fee schedules in issues,
  pull requests, tests, or examples.
- **No credentials.** Never commit `.env`, login profiles, tokens, or `verify_db` contents.
- **Keep the safety limits.** Do not raise concurrency, shorten delays, or add CAPTCHA
  bypassing. Changes that make bulk login-state automation easier will be rejected.
- **Do not weaken the “human decides money” rule.** Amounts are reported, not changed.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 -m compileall scripts
python3 -m unittest discover -s tests -v
```

macOS-only checks (optional):

```bash
swiftc -O scripts/bbox.swift -o /tmp/bboxbin
swift scripts/ocr.swift <some-local-image.png>
```

## Style

- Python 3.9+, standard library where practical.
- Keep scripts runnable as `python3 scripts/<tool>.py` (the scripts add their own directory
  to `sys.path` so sibling imports work when launched by path).
- Comments and user-facing messages are in Chinese to match the domain; code identifiers
  in English.
- Prefer explicit CLI flags over environment variables, except for credentials/profiles.
- Do not add a dependency without updating `requirements.txt`.

## Pull requests

1. Describe the behavior change and the failure mode it prevents.
2. Add or update a test that runs without network/Chrome/Swift when possible.
3. Confirm `python3 -m compileall scripts` and the unit tests pass.
4. Confirm no secrets or real data are included (`git diff --stat`, `git status`).
5. Update `CHANGELOG.md` for user-visible changes.

## Reporting bugs

Include the tool name, command, Python/OS versions, and the error. Redact URLs, names,
and amounts. For link-verification issues, state whether the lane was `anon` or `login`.
