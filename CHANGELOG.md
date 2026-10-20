# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning: SemVer.

## [1.0.0] - 2026-10-20

### Added

- Public, sanitized release of the acceptance-audit toolkit.
- Profile A two-sheet pipeline with configurable tiers (`config/rules.example.json`).
- Screenshot OCR, bbox pixel adjudication, threshold pinning.
- Anonymous and login link-verification lanes with caching, dedupe, rate limits, and
  circuit breaking.
- Human review console for link spot checks.
- Follower verification (DOM + OCR) with evidence archiving.
- Evidence pack (`evidence.py`), SHA256 dossier (`dossier.py`), audit state, and optional command log.
- Blank 37-column template generator.
- Smoke tests and GitHub Actions CI.

### Security

- Conservative defaults: anonymous lane 3 workers / 8 s spacing / 200 per 30 min;
  login lane ≤5 per batch, short sessions, stop on the first CAPTCHA.
- `.gitignore` blocks login profiles, caches, source workbooks, screenshots, and outputs.

### Removed for the public release

- Client/brand names, campaign IDs, creator names, real fee tiers, internal links,
  personal paths, login cookies, and historical deliverables.
- The project-specific multi-sheet 8-column adapter (see `docs/table-profile.md` for the
  adaptation pattern).
