# IL9Cast - Illinois 9th District Primary Forecast

A real-time prediction market aggregator for the Illinois 9th Congressional District Democratic Primary scheduled for **March 17, 2026**.

IL9Cast combines live market data from **Manifold Markets** and **Kalshi** using a sophisticated weighted aggregation formula to provide accurate, up-to-the-minute probability estimates for each candidate.

## Features

- **Real-Time Aggregation** - Combines Manifold Markets and Kalshi data every 3 minutes with intelligent weighting (40% Manifold, 60% Kalshi components)
- **Multi-Layer Smoothing** - Spike dampening (±3% per interval), EMA filtering, RDP simplification, and monotone cubic interpolation for trustworthy visualization
- **Thin-Market Fallback** - Handles candidates with no buy-side liquidity by falling back to last-price instead of invalid midpoint calculations
- **Central Time Display** - All timestamps shown in Central Time (CT) with automatic DST handling
- **Historical Tracking** - Persistent data collection with ~480 snapshots per day (3-minute intervals)
- **Gap Detection** - Dashed lines on charts indicate data loss from Railway or AWS outages (2-hour threshold)
- **Downloadable Data** - Complete historical dataset available in JSONL format for analysis

## Quick Start

### Development

```bash
# Clone repository
git clone https://github.com/de-bayes/IL9.git
cd IL9

# Install dependencies
pip install -r requirements.txt

# Run local development server
python app.py
# Visit http://localhost:5000
```

### Production (Railway)

```bash
# Push to main branch (auto-deploys via Railway)
git push origin main

# Or manual deploy
railway up
```

## Architecture Overview

### Backend (Flask)
- **Framework**: Flask 2.3.2 with Gunicorn WSGI server
- **Scheduling**: APScheduler for background data collection every 3 minutes
- **Storage**: JSONL format for append-only, corruption-resistant data persistence
- **Data Processing**: Multi-layer smoothing pipeline with RDP simplification

### Frontend (Chart.js)
- **Visualization**: Interactive charts with monotone cubic interpolation
- **Timezone**: All times in Central Time via `Intl.DateTimeFormat`
- **Responsiveness**: Mobile-optimized with automatic RDP simplification for performance

### Data Collection Pipeline

Every 3 minutes:

1. Fetch from **Manifold Markets** (`/v0/slug/who-will-win-the-democratic-primary-RZdcps6dL9`)
2. Fetch from **Kalshi** (`/trade-api/v2/markets?series_ticker=KXIL9D`)
3. Normalize candidate names across platforms
4. Apply thin-market fallback (fallback to last_price when yes_bid=0)
5. Aggregate using weighted formula:
   - Manifold: 40%
   - Kalshi last price: 42%
   - Kalshi midpoint (bid/ask): 12%
   - Kalshi liquidity-adjusted: 6%
6. Apply soft normalization (30% strength toward sum=100%)
7. Create snapshot with timestamp
8. Atomically append to `data/historical_snapshots.jsonl`

### Chart Smoothing Stack

The chart rendering applies multiple smoothing layers for a trustworthy visualization:

1. **Spike Dampening** (collection level) - Caps probability changes to ±3% per 3-minute interval
2. **EMA Smoothing** (server-side) - Exponential moving average with alpha=0.15
3. **RDP Simplification** (server-side) - Ramer-Douglas-Peucker algorithm with epsilon=0.5 reduces 480 daily points to ~100-200
4. **Monotone Cubic Interpolation** (frontend) - Prevents overshoot with tension=0.5
5. **Chart.js Rendering** - Smooth curves via quadratic Bézier approximation

## Key Technical Details

### Data Format

Historical data stored in `/data/historical_snapshots.jsonl` (JSONL format):
```json
{"candidates": [{"name": "Daniel Biss", "probability": 63.6, "hasKalshi": true}], "timestamp": "2026-01-30T19:45:30Z"}
```

**Why JSONL?**
- Append-only writes (no need to read entire file)
- Corruption-proof (each line is self-contained)
- 35% space savings vs JSON array
- Automatic migration from legacy JSON format

### Thin-Market Fallback

When a candidate has no buy-side bids on Kalshi (yes_bid=0), the traditional midpoint formula `(yes_bid + yes_ask) / 2` produces invalid results.

**Example**: Mike Simmons trading at ~1% last_price:
- **Without fallback**: (0 + 19) / 2 = 9.5% ❌ Wildly inflated
- **With fallback**: Uses last_price = 1% ✓ Correct

The fallback is applied to both midpoint and liquidity-weighted components.

### Central Time Display

All timestamps use IANA timezone `America/Chicago` via `Intl.DateTimeFormat`, which automatically handles Central Standard Time (CST) and Central Daylight Time (CDT) transitions.

### Gap Detection

Dashed lines on charts indicate periods of data loss due to outages. The 2-hour threshold means only significant Railway or AWS outages trigger the visualization (excludes normal sub-3-minute timing variations).

## API Endpoints

### JSON APIs
- `GET /api/manifold` - Current Manifold market data
- `GET /api/kalshi` - Current Kalshi market data
- `GET /api/snapshots` - All historical snapshots (full dataset)
- `GET /api/snapshots/chart?period=<1d|7d|all>` - Chart-optimized snapshots with smoothing/RDP applied

### Pages
- `GET /` - Landing page with features overview
- `GET /markets` - Live prediction market visualization
- `GET /methodology` - Detailed methodology with animated foldouts
- `GET /fundraising` - Campaign finance data (in development)
- `GET /about` - Project information and contact
- `GET /odds` - Forecasting model (in development)

## Development Setup

### Local Testing

```bash
# Install with development dependencies
pip install -r requirements.txt

# Run with auto-reload
python app.py

# Test API endpoints
curl http://localhost:5000/api/manifold
curl http://localhost:5000/api/snapshots/chart?period=1d
```

### Data Management

```bash
# View recent snapshots
tail -n 50 data/historical_snapshots.jsonl

# Check data file size
ls -lh data/historical_snapshots.jsonl

# Count snapshots (without loading all into memory)
python -c "import app; print(app.count_snapshots_jsonl())"
```

## Deployment (Railway)

### Configuration
- **Builder**: NIXPACKS
- **Start Command**: `gunicorn app:app --preload`
- **Health Check**: `GET /` with 100s timeout
- **Persistent Volume**: `/app/data` (survives container restarts)
- **Environment**: Port 8000 (or `$PORT` if set)

### Important Notes
- `--preload` flag ensures single APScheduler instance (prevents duplicate collection jobs)
- Persistent volume keeps historical data between deployments
- Auto-restart up to 10 times on failure

## Dependencies

- **Flask 2.3.2** - Web framework
- **APScheduler** - Background task scheduling
- **Requests** - HTTP client for APIs
- **Gunicorn** - WSGI application server

See `requirements.txt` for complete list.

## Common Tasks

### Inspect Historical Data

```bash
# View last 5 snapshots
tail -n 5 data/historical_snapshots.jsonl | python -m json.tool

# Check data quality (gaps between snapshots)
python -c "
import json
from datetime import datetime
with open('data/historical_snapshots.jsonl') as f:
    lines = [json.loads(line.strip()) for line in f]
    for i in range(1, min(5, len(lines))):
        prev = datetime.fromisoformat(lines[i-1]['timestamp'].rstrip('Z'))
        curr = datetime.fromisoformat(lines[i]['timestamp'].rstrip('Z'))
        gap = (curr - prev).total_seconds()
        print(f'Gap {i}: {gap:.0f}s ({gap/60:.1f} min)')
"
```

### Test Chart Rendering

```bash
# Get optimized data for 1-day period
curl 'http://localhost:5000/api/snapshots/chart?period=1d' | python -m json.tool | head -20

# Verify RDP simplification is working
curl 'http://localhost:5000/api/snapshots/chart?period=all' | \
  python -c "import sys, json; d=json.load(sys.stdin); print(f'Points: {len(d[0][\"data\"])}')"
```

## Data Privacy & Consent

IL9Cast aggregates publicly available prediction market data. No personal data is collected or stored beyond what is necessary for chart rendering and historical analysis.

## Technical Documentation

For detailed technical documentation including:
- Complete data collection pipeline
- Market aggregation methodology
- Chart smoothing algorithms
- Railway infrastructure details
- Failure modes and defenses

See [CLAUDE.md](./CLAUDE.md) for comprehensive technical documentation.

## About

IL9Cast was created by Ryan McComb, a student at Evanston Township High School with a passion for politics, data science, and prediction markets.

For feedback or questions, visit the [GitHub repository](https://github.com/de-bayes/IL9) or check the [About page](https://il9cast.com/about) for contact information.

## License

This project is open source. See LICENSE file for details.

---

**Last Updated**: January 30, 2026
**Data Available From**: January 30, 2026 (historical data from Jan 15-30 available upon request due to AWS volume issues)
