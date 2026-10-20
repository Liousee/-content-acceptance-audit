# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private
vulnerability reporting (Security → Advisories) or contact the maintainers directly, and include:

- affected file/version
- reproduction steps
- impact (account access, data exposure, credential leak, etc.)
- suggested fix, if any

## Scope

In scope:

- credential or cookie leakage
- account-takeover risk in the login/link tooling
- path traversal, command injection, or unsafe subprocess use
- unintended upload of local audit data

Out of scope:

- platform CAPTCHAs or rate limits (the project intentionally does not bypass them)
- account restrictions caused by using the tool outside the documented safety limits
- data the user manually commits despite `.gitignore`

## Handling secrets

- Never commit `~/.cache/yanshou-chrome`, `verify_db.jsonl`, `token_log.jsonl`, source
  workbooks, screenshots, or deliverables.
- Use `config/rules.local.json` for project rules; it is gitignored.
- If you accidentally commit a secret, rotate it immediately and purge it from history
  (a later delete commit is not enough).
