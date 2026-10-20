## What changed

Describe the behavior change and the failure mode it prevents.

## Safety checklist

- [ ] No client/brand names, campaign IDs, creator names, phone/VIN, real amounts, URLs,
      screenshots, cookies, tokens, or deliverables are included.
- [ ] I did not raise concurrency, shorten delays, or add CAPTCHA/rate-limit bypassing.
- [ ] Money-changing behavior remains human-approved (tools report, do not auto-adjust).
- [ ] Link-verification changes respect the `anon` / `login` lane separation.

## Test checklist

- [ ] `python3 -m compileall scripts tests`
- [ ] `python3 -m unittest discover -s tests -v`
- [ ] Added/updated a test that runs without network, Chrome, or Swift (where possible)
- [ ] Updated `CHANGELOG.md` for user-visible changes
