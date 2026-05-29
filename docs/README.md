# IL9Cast documentation index

Use this index to find the right doc. **Start with [ARCHIVE_MODE.md](ARCHIVE_MODE.md)** for how the site works today.

## Current (post–March 17, 2026)

| Document | Purpose |
|----------|---------|
| [ARCHIVE_MODE.md](ARCHIVE_MODE.md) | Static archive: no live scraper, frozen APIs, read-only JSONL |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Railway / Gunicorn deploy, health check, env vars |
| [ROUTES.md](ROUTES.md) | Canonical route registry |
| [../CLAUDE.md](../CLAUDE.md) | Agent-oriented overview + commands (see archive banner at top) |

## Data & recovery

| Document | Purpose |
|----------|---------|
| [DATA_PROTECTION_PROMPT.md](DATA_PROTECTION_PROMPT.md) | Rules for JSONL/CSV recovery sessions |
| [VOLUME_DELETION_INCIDENT_2026_02_13.md](VOLUME_DELETION_INCIDENT_2026_02_13.md) | Feb 2026 volume wipe postmortem |
| [RECOVERY_SUMMARY.md](RECOVERY_SUMMARY.md) | Jan 2026 data recovery summary |

## Historical / superseded

| Document | Purpose |
|----------|---------|
| [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | **Stale** — Jan 2026 live-collection deploy |
| [RECOVERY_SUMMARY.md](RECOVERY_SUMMARY.md) | Jan 2026 incident notes |

## Content & ops

| Document | Purpose |
|----------|---------|
| [SEO_ANALYTICS_SETUP.md](SEO_ANALYTICS_SETUP.md) | Google Analytics / Search Console |
| [FACT_CHECK_RESULTS.md](FACT_CHECK_RESULTS.md) | Candidate profile fact-check notes |
| [VAN_API_VERIFICATION.md](VAN_API_VERIFICATION.md) | NGP VAN API notes |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/import_repo_csv.py` | One-shot CSV → JSONL import / recovery |
| `scripts/smoke_test_perf.py` | Local smoke tests (`/healthz`, chart ETag, precincts gzip) |
