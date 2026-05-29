# Route registry

Canonical list for IL9Cast. Verify with: `grep -n "@app.route" app.py`

**Site mode:** [ARCHIVE_MODE.md](ARCHIVE_MODE.md) — no live scraper after 2026-03-17.

## Pages

| Path | Response |
|------|----------|
| `GET /` | Landing (`landing_new.html`) |
| `GET /rjmc` | Landing with election-night preview flag |
| `GET /markets` | Archived prediction markets |
| `GET /odds` | Precinct model + post-primary results |
| `GET /methodology` | Methodology foldouts |
| `GET /about` | About |
| `GET /money` | Fundraising / FEC (canonical) |
| `GET /fundraising` | 301 → `/money` |
| `GET /outside-money` | 301 → `/money#independent-expenditures` |
| `GET /money/<slug>` | Per-candidate fundraising |
| `GET /candidates` | Candidate profiles |
| `GET /updates` | Updates |
| `GET /case-study/bid-ask-spreads` | Bid/ask case study |
| `GET /model/methodology` | PDF (`docs/IL9Cast_Methodology_CORRECTED.pdf`) |
| `GET /unsubscribe` | Email unsubscribe (token query params) |
| `GET /sitemap.xml` | Sitemap |
| `GET /robots.txt` | Robots |
| *(other)* | `404.html` |

## Public APIs

| Path | Notes |
|------|--------|
| `GET /healthz` | Liveness `{"status":"ok"}` |
| `GET /api/manifold` | Archived JSON (`data/archive/manifold.json`) |
| `GET /api/kalshi` | Archived JSON (`data/archive/kalshi.json`) |
| `GET /api/snapshots` | Full snapshot array (heavy) |
| `GET /api/snapshots/count` | Counts only |
| `GET /api/snapshots/chart` | `period=1d\|7d\|all`, `epsilon` (default 0.5); ETag / 304 |
| `GET /api/model/precincts` | GeoJSON; gzip when `Accept-Encoding: gzip` |
| `GET /api/fec/candidates` | Hardcoded FEC summary |
| `GET /api/download/snapshots` | JSONL download |
| `GET /api/download/snapshots/csv` | CSV export |
| `POST /api/snapshot` | **410** — live collection ended |
| `POST /api/subscribe` | **410** — alerts ended |

## Admin (`Authorization: Bearer ADMIN_API_TOKEN`)

| Path | Method |
|------|--------|
| `/api/admin/repair-snapshots` | POST |
| `/api/admin/recover-snapshots` | POST (`apply`, `bridge`, `csv_only`, `bridge_minutes`, `max_bridge_hours`) |
| `/api/admin/bridge-to-present` | POST |
| `/api/admin/force-csv-recovery` | POST |
| `/api/admin/send-csv-backup` | POST |
| `/api/admin/fix-kalshi-gap` | POST |
| `/api/test-swing-alert` | GET |
| `/api/broadcast` | POST |

Returns **503** if `ADMIN_API_TOKEN` is unset; **401** if token invalid.
