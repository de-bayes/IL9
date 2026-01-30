from flask import Flask, jsonify, render_template, request, send_file
import random
import math
from datetime import datetime, timedelta, timezone
import requests
import json
import os
import time as _time
import atexit
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)


# ===== PATH RESOLUTION =====

def resolve_data_path(filename='historical_snapshots.jsonl'):
    """
    Resolve the correct data directory, checking Railway persistent volume first.
    Priority: /data/ -> /app/data/ -> local data/
    """
    for candidate_dir in ['/data', '/app/data']:
        candidate_path = os.path.join(candidate_dir, filename)
        if os.path.exists(candidate_dir):
            return candidate_path
    # Fallback to local data/ directory
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', filename)


# Path to historical data storage (JSONL format - JSON Lines)
HISTORICAL_DATA_PATH = resolve_data_path('historical_snapshots.jsonl')

# Seed data path - git-tracked backup that Railway will use to initialize the volume
SEED_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'seed_snapshots.json')

# Legacy JSON path for migration
LEGACY_JSON_PATH = os.path.join(os.path.dirname(__file__), 'data', 'historical_snapshots.json')

# ===== JSONL HELPER FUNCTIONS =====

def read_snapshots_jsonl(filepath):
    """
    Read snapshots from JSONL file.
    Each line is a separate JSON object.
    Returns list of snapshot dictionaries.
    """
    snapshots = []
    if not os.path.exists(filepath):
        return snapshots

    try:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    snapshot = json.loads(line)
                    snapshots.append(snapshot)
                except json.JSONDecodeError as e:
                    print(f"[{datetime.now().isoformat()}] Error parsing line {line_num}: {e}")
                    continue
    except (IOError, OSError) as e:
        print(f"[{datetime.now().isoformat()}] Error reading JSONL file: {e}")

    return snapshots

def append_snapshot_jsonl(filepath, snapshot):
    """
    Append a single snapshot to JSONL file.
    Atomic operation: writes to temp file then renames.
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # Write to temp file first
    temp_path = filepath + '.tmp'
    try:
        with open(temp_path, 'w') as f:
            f.write(json.dumps(snapshot) + '\n')

        # Atomic append: create new file with old content + new line
        if os.path.exists(filepath):
            # Read existing content
            with open(filepath, 'r') as existing:
                existing_content = existing.read()

            # Write existing + new to temp
            with open(temp_path, 'w') as f:
                f.write(existing_content)
                if existing_content and not existing_content.endswith('\n'):
                    f.write('\n')
                f.write(json.dumps(snapshot) + '\n')

        # Atomic replace
        os.replace(temp_path, filepath)
        return True

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error appending to JSONL: {e}")
        # Clean up temp file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise

def count_snapshots_jsonl(filepath):
    """Count total snapshots in JSONL file without loading all into memory"""
    if not os.path.exists(filepath):
        return 0

    count = 0
    with open(filepath, 'r') as f:
        for line in f:
            if line.strip():
                count += 1
    return count

# ===== TIMESTAMP PARSING =====

def parse_snapshot_timestamp(ts_str):
    """
    Parse ISO timestamp string to UTC datetime.
    Handles both Z-suffix and no-suffix (all are UTC).
    Returns None if unparseable.
    """
    if not ts_str:
        return None
    ts_clean = ts_str.rstrip('Z')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            dt = datetime.strptime(ts_clean, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ===== RAMER-DOUGLAS-PEUCKER SIMPLIFICATION =====

def _perpendicular_distance(point, line_start, line_end):
    """Calculate perpendicular distance from a point to a line segment."""
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    if dx == 0 and dy == 0:
        return math.sqrt((point[0] - line_start[0]) ** 2 + (point[1] - line_start[1]) ** 2)
    t = ((point[0] - line_start[0]) * dx + (point[1] - line_start[1]) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))
    proj_x = line_start[0] + t * dx
    proj_y = line_start[1] + t * dy
    return math.sqrt((point[0] - proj_x) ** 2 + (point[1] - proj_y) ** 2)


def rdp_simplify(points, epsilon):
    """
    Ramer-Douglas-Peucker polyline simplification.
    points: list of (x, y) tuples where x is normalized time (0-100), y is probability (0-100).
    Returns list of indices to keep.
    """
    if len(points) <= 2:
        return list(range(len(points)))

    # Find the point with the maximum distance from the line between first and last
    max_dist = 0
    max_idx = 0
    for i in range(1, len(points) - 1):
        d = _perpendicular_distance(points[i], points[0], points[-1])
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > epsilon:
        # Recurse on both halves
        left = rdp_simplify(points[:max_idx + 1], epsilon)
        right = rdp_simplify(points[max_idx:], epsilon)
        # Combine, avoiding duplicate at split point
        right_shifted = [max_idx + idx for idx in right]
        return left[:-1] + right_shifted
    else:
        return [0, len(points) - 1]


# ===== CHART DATA CACHE =====
_chart_cache = {'data': None, 'time': 0, 'key': None}


# ===== INITIALIZATION =====

def initialize_data():
    """
    Initialize the data directory and seed from backup if needed.
    On Railway: copies seed data to persistent volume on first deploy.
    Migrates from legacy JSON format to JSONL if needed.
    """
    data_dir = os.path.dirname(HISTORICAL_DATA_PATH)
    os.makedirs(data_dir, exist_ok=True)

    # Migrate from legacy JSON to JSONL if needed
    if os.path.exists(LEGACY_JSON_PATH) and not os.path.exists(HISTORICAL_DATA_PATH):
        print(f"[{datetime.now().isoformat()}] Migrating from JSON to JSONL format...")
        try:
            with open(LEGACY_JSON_PATH, 'r') as f:
                legacy_data = json.load(f)

            if isinstance(legacy_data, list):
                with open(HISTORICAL_DATA_PATH, 'w') as f:
                    for snapshot in legacy_data:
                        f.write(json.dumps(snapshot) + '\n')
                print(f"[{datetime.now().isoformat()}] Migrated {len(legacy_data)} snapshots to JSONL")

                # Backup legacy file
                backup_path = LEGACY_JSON_PATH + '.pre-jsonl-backup'
                if not os.path.exists(backup_path):
                    import shutil
                    shutil.copy2(LEGACY_JSON_PATH, backup_path)
                    print(f"[{datetime.now().isoformat()}] Legacy JSON backed up to {backup_path}")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Error migrating to JSONL: {e}")

    # Only seed data if historical file doesn't exist at all
    # Once Railway starts collecting, never overwrite its data
    if not os.path.exists(HISTORICAL_DATA_PATH) and os.path.exists(SEED_DATA_PATH):
        print(f"[{datetime.now().isoformat()}] Seeding data from {SEED_DATA_PATH}")
        try:
            with open(SEED_DATA_PATH, 'r') as src:
                seed_data = json.load(src)

            if isinstance(seed_data, list):
                with open(HISTORICAL_DATA_PATH, 'w') as dst:
                    for snapshot in seed_data:
                        dst.write(json.dumps(snapshot) + '\n')
                print(f"[{datetime.now().isoformat()}] Seeded {len(seed_data)} snapshots in JSONL format")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Error seeding data: {e}")

# Initialize data on module load
initialize_data()

# Mock candidate data
CANDIDATES = [
    {"id": 1, "name": "Maria Garcia", "party_role": "State Rep", "color": "#FF6B6B"},
    {"id": 2, "name": "James Wilson", "party_role": "Community Organizer", "color": "#4ECDC4"},
    {"id": 3, "name": "Dr. Sarah Ahmed", "party_role": "Physician", "color": "#45B7D1"},
    {"id": 4, "name": "Tom Mueller", "party_role": "Labor Leader", "color": "#FFA07A"},
    {"id": 5, "name": "Angela Chen", "party_role": "Tech Entrepreneur", "color": "#98D8C8"},
    {"id": 6, "name": "Robert Jackson", "party_role": "Former Alderman", "color": "#F7DC6F"},
]

# Routes
@app.route('/')
def landing():
    return render_template('landing_new.html')

@app.route('/odds')
def odds():
    return render_template('odds.html')

@app.route('/methodology')
def methodology():
    return render_template('methodology.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/markets')
def markets():
    return render_template('markets.html')

@app.route('/fundraising')
def fundraising():
    return render_template('fundraising.html')

# API Endpoints
@app.route('/api/forecast')
def get_forecast():
    """Generate mock forecast data"""
    random.seed(42)
    base_odds = [28, 22, 18, 16, 10, 6]
    odds = [max(1, o + random.randint(-3, 3)) for o in base_odds]
    total = sum(odds)
    odds = [round(100 * o / total) for o in odds]

    candidates = []
    for i, candidate in enumerate(CANDIDATES):
        candidates.append({
            **candidate,
            "probability": odds[i],
            "trend": random.choice(["up", "down", "stable"]),
            "polling_avg": round(odds[i] + random.uniform(-2, 2), 1),
            "change": random.randint(-5, 5),
            "last_update": (datetime.now() - timedelta(hours=random.randint(1, 48))).isoformat()
        })

    return jsonify({
        "candidates": candidates,
        "last_updated": datetime.now().isoformat(),
        "primary_date": "2026-03-17"
    })

@app.route('/api/timeline')
def get_timeline():
    """Generate mock polling trend data"""
    timeline = []
    start_date = datetime.now() - timedelta(days=90)

    for day in range(0, 91, 7):
        current_date = start_date + timedelta(days=day)
        day_data = {
            "date": current_date.strftime("%Y-%m-%d"),
            "candidates": {}
        }

        base = [25, 22, 18, 15, 12, 8]
        for i, candidate in enumerate(CANDIDATES):
            variance = random.randint(-4, 4)
            day_data["candidates"][candidate["name"]] = max(1, base[i] + variance)

        timeline.append(day_data)

    return jsonify(timeline)

@app.route('/api/manifold')
def get_manifold():
    """Proxy Manifold Markets API to avoid CORS"""
    try:
        response = requests.get('https://api.manifold.markets/v0/slug/who-will-win-the-democratic-primary-RZdcps6dL9')
        response.raise_for_status()
        result = jsonify(response.json())
        result.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        result.headers['Pragma'] = 'no-cache'
        result.headers['Expires'] = '0'
        return result
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/kalshi')
def get_kalshi():
    """Proxy Kalshi API to avoid CORS"""
    try:
        response = requests.get('https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXIL9D&status=open')
        response.raise_for_status()
        result = jsonify(response.json())
        result.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        result.headers['Pragma'] = 'no-cache'
        result.headers['Expires'] = '0'
        return result
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/manifold/history')
def get_manifold_history():
    """Get Manifold market history for chart"""
    try:
        # Get the market first to get the ID
        market_response = requests.get('https://api.manifold.markets/v0/slug/who-will-win-the-democratic-primary-RZdcps6dL9')
        market_response.raise_for_status()
        market = market_response.json()
        market_id = market.get('id')

        # Get bets for this market
        bets_response = requests.get(f'https://api.manifold.markets/v0/bets?contractId={market_id}&limit=1000')
        bets_response.raise_for_status()
        bets = bets_response.json()

        return jsonify({
            "market": market,
            "bets": bets
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/kalshi/history/<ticker>')
def get_kalshi_history(ticker):
    """Get Kalshi market history for a specific ticker"""
    try:
        response = requests.get(f'https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/history?limit=1000')
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/snapshot', methods=['POST'])
def save_snapshot():
    """Save a historical snapshot of aggregated probabilities (JSONL format)"""
    try:
        # Get new snapshot from request
        new_snapshot = request.json
        # Use UTC with Z suffix for consistent timezone handling
        new_snapshot['timestamp'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Append to JSONL file
        append_snapshot_jsonl(HISTORICAL_DATA_PATH, new_snapshot)

        # Count total snapshots
        total = count_snapshots_jsonl(HISTORICAL_DATA_PATH)

        return jsonify({"success": True, "total_snapshots": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/snapshots')
def get_snapshots():
    """Retrieve historical snapshots for charting (reads JSONL format)"""
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        return jsonify(snapshots)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/snapshots/chart')
def get_snapshots_chart():
    """
    Return RDP-simplified snapshots for chart rendering.
    Params:
      period: '1d', '7d', 'all' (default 'all')
      epsilon: RDP tolerance (default 0.3)
    Returns ~200-400 points instead of 5000+ raw.
    """
    global _chart_cache
    try:
        period = request.args.get('period', 'all')
        epsilon = float(request.args.get('epsilon', '0.3'))
        cache_key = f'{period}:{epsilon}'

        # 60-second cache
        now = _time.time()
        if _chart_cache['key'] == cache_key and _chart_cache['data'] and (now - _chart_cache['time']) < 60:
            return jsonify(_chart_cache['data'])

        # Read all snapshots
        all_snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if not all_snapshots:
            return jsonify([])

        # Parse timestamps and filter bad ones
        parsed = []
        for snap in all_snapshots:
            dt = parse_snapshot_timestamp(snap.get('timestamp', ''))
            if dt:
                parsed.append((dt, snap))
        parsed.sort(key=lambda x: x[0])

        if not parsed:
            return jsonify([])

        # Filter by period
        now_utc = datetime.now(timezone.utc)
        if period == '1d':
            cutoff = now_utc - timedelta(days=1)
            parsed = [(dt, s) for dt, s in parsed if dt >= cutoff]
        elif period == '7d':
            cutoff = now_utc - timedelta(days=7)
            parsed = [(dt, s) for dt, s in parsed if dt >= cutoff]
        # 'all' keeps everything

        if not parsed:
            return jsonify([])

        # Normalize time axis to 0-100 for RDP (same scale as probability 0-100)
        t_first = parsed[0][0].timestamp()
        t_last = parsed[-1][0].timestamp()
        t_range = t_last - t_first if t_last != t_first else 1.0

        # Collect all candidate names across all snapshots
        all_candidates = set()
        for _, snap in parsed:
            for c in snap.get('candidates', []):
                all_candidates.add(c['name'])

        # Run RDP per candidate, collect union of kept indices
        kept_indices = set()
        kept_indices.add(0)
        kept_indices.add(len(parsed) - 1)

        for cand_name in all_candidates:
            # Build polyline for this candidate
            points = []
            index_map = []  # maps polyline index -> parsed index
            for i, (dt, snap) in enumerate(parsed):
                for c in snap.get('candidates', []):
                    if c['name'] == cand_name:
                        x = ((dt.timestamp() - t_first) / t_range) * 100.0
                        y = c.get('probability', 0)
                        points.append((x, y))
                        index_map.append(i)
                        break

            if len(points) > 2:
                rdp_indices = rdp_simplify(points, epsilon)
                for ri in rdp_indices:
                    kept_indices.add(index_map[ri])

        # Detect real gaps (>1 hour) in the RAW data before RDP
        GAP_THRESHOLD_SECS = 3600  # 1 hour
        gaps = []
        for i in range(1, len(parsed)):
            gap_secs = (parsed[i][0] - parsed[i - 1][0]).total_seconds()
            if gap_secs > GAP_THRESHOLD_SECS:
                gaps.append({
                    'start': parsed[i - 1][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'end': parsed[i][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                })

        # Build result from kept indices
        kept_sorted = sorted(kept_indices)
        result_snapshots = []
        for idx in kept_sorted:
            dt, snap = parsed[idx]
            result_snapshots.append(snap)

        result = {
            'snapshots': result_snapshots,
            'gaps': gaps
        }

        # Cache and return
        _chart_cache = {'data': result, 'time': now, 'key': cache_key}

        resp = jsonify(result)
        resp.headers['Cache-Control'] = 'public, max-age=30'
        return resp

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download/snapshots')
def download_snapshots():
    """Download all historical snapshot data as JSONL file"""
    try:
        if os.path.exists(HISTORICAL_DATA_PATH):
            return send_file(
                HISTORICAL_DATA_PATH,
                mimetype='application/x-ndjson',
                as_attachment=True,
                download_name='il9cast_historical_data.jsonl'
            )
        else:
            return jsonify({"error": "No data available"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Background task to automatically collect and save data
def collect_market_data():
    """Fetch market data and save snapshot automatically"""
    try:
        print(f"[{datetime.now().isoformat()}] Running automatic data collection...")

        # Fetch Manifold data
        manifold_data = {}
        try:
            manifold_response = requests.get('https://api.manifold.markets/v0/slug/who-will-win-the-democratic-primary-RZdcps6dL9', timeout=10)
            manifold_response.raise_for_status()
            manifold_market = manifold_response.json()

            answers = manifold_market.get('answers', [])
            for answer in answers:
                if answer.get('text') != 'Other' and 'schakowsky' not in answer.get('text', '').lower():
                    name = normalize_candidate_name(answer.get('text', ''))
                    manifold_data[name] = {
                        'probability': round(answer.get('probability', 0) * 100, 1),
                        'displayName': answer.get('text', '')
                    }
        except Exception as e:
            print(f"Error fetching Manifold data: {e}")

        # Fetch Kalshi data
        kalshi_data = {}
        try:
            kalshi_response = requests.get('https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXIL9D&status=open', timeout=10)
            kalshi_response.raise_for_status()
            kalshi_markets = kalshi_response.json().get('markets', [])

            for market in kalshi_markets:
                display_name = market.get('subtitle') or market.get('title', '')
                if 'schakowsky' not in display_name.lower():
                    name = normalize_candidate_name(display_name)
                    last_price = market.get('last_price', 0)
                    yes_bid = market.get('yes_bid', 0)
                    yes_ask = market.get('yes_ask', 0)
                    midpoint = (yes_bid + yes_ask) / 2

                    # Calculate liquidity-weighted price
                    spread = yes_ask - yes_bid
                    liquidity_price = midpoint

                    if spread > 0 and last_price > 0:
                        position_in_spread = max(0, min(1, (last_price - yes_bid) / spread))
                        offset_from_mid = position_in_spread - 0.5
                        spread_factor = max(0.2, 1 - (spread / 10) * 0.8)
                        price_shift = max(-3, min(3, offset_from_mid * 6 * spread_factor))
                        liquidity_price = max(0, min(100, midpoint + price_shift))

                    kalshi_data[name] = {
                        'last_price': last_price,
                        'midpoint': midpoint,
                        'liquidity': liquidity_price,
                        'displayName': display_name
                    }
        except Exception as e:
            print(f"Error fetching Kalshi data: {e}")

        # Calculate aggregated probabilities
        if manifold_data or kalshi_data:
            all_candidates = set(list(manifold_data.keys()) + list(kalshi_data.keys()))
            aggregated = []

            for candidate_key in all_candidates:
                manifold_prob = manifold_data.get(candidate_key, {}).get('probability', 0)
                kalshi_info = kalshi_data.get(candidate_key, {})
                kalshi_last = kalshi_info.get('last_price', 0)
                kalshi_mid = kalshi_info.get('midpoint', 0)
                kalshi_liq = kalshi_info.get('liquidity', kalshi_mid)

                has_kalshi = kalshi_last > 0 or kalshi_mid > 0

                if has_kalshi:
                    aggregate = (0.40 * manifold_prob) + (0.42 * kalshi_last) + (0.12 * kalshi_mid) + (0.06 * kalshi_liq)
                else:
                    aggregate = manifold_prob

                if aggregate > 0 or manifold_prob > 0:
                    display_name = manifold_data.get(candidate_key, {}).get('displayName') or kalshi_info.get('displayName', candidate_key)
                    clean_name = clean_candidate_name(display_name)

                    aggregated.append({
                        'name': clean_name,
                        'probability': aggregate,
                        'hasKalshi': has_kalshi
                    })

            # Soft normalization (30% strength)
            total = sum(c['probability'] for c in aggregated)
            if total > 0:
                for c in aggregated:
                    fully_normalized = (c['probability'] / total) * 100
                    adjustment = fully_normalized - c['probability']
                    c['probability'] = c['probability'] + (adjustment * 0.30)

            aggregated.sort(key=lambda x: x['probability'], reverse=True)

            # Save snapshot with UTC timestamp (Z suffix marks it as UTC)
            snapshot = {
                'candidates': [{
                    'name': c['name'],
                    'probability': round(c['probability'], 1),
                    'hasKalshi': c['hasKalshi']
                } for c in aggregated],
                'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            }

            # Append to JSONL file (atomic operation)
            try:
                append_snapshot_jsonl(HISTORICAL_DATA_PATH, snapshot)
                total_count = count_snapshots_jsonl(HISTORICAL_DATA_PATH)
                print(f"[{datetime.now().isoformat()}] Snapshot saved successfully. Total snapshots: {total_count}")
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Error saving snapshot: {e}")
                raise

        else:
            print(f"[{datetime.now().isoformat()}] No data collected from either API")

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error in automatic data collection: {e}")

def normalize_candidate_name(name):
    """Normalize candidate name for matching across platforms"""
    import re
    cleaned = name.lower()
    # Remove common prefixes
    cleaned = re.sub(r'^wil\s+', '', cleaned)
    cleaned = re.sub(r'^will\s+', '', cleaned)
    # Remove common suffixes
    cleaned = re.sub(r'\s+be the democratic nominee.*$', '', cleaned)
    cleaned = re.sub(r'\s+for il-9.*$', '', cleaned)
    cleaned = re.sub(r'\s+win.*$', '', cleaned)
    cleaned = cleaned.replace('?', '')
    cleaned = re.sub(r'^dr\.\s*', '', cleaned)
    cleaned = cleaned.strip()

    # Handle name variations/misspellings
    name_variations = {
        'kat abughazaleh': 'kat abugazaleh',
    }
    if cleaned in name_variations:
        cleaned = name_variations[cleaned]

    return cleaned

def clean_candidate_name(name):
    """Clean up candidate name for display"""
    import re
    # Case-insensitive cleaning for display
    cleaned = re.sub(r'^wil\s+', '', name, flags=re.IGNORECASE)
    cleaned = re.sub(r'^will\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+be the democratic nominee.*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+for il-9.*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace('?', '').strip()
    return cleaned

# Set up background scheduler only if not running under gunicorn workers
# This prevents duplicate schedulers when gunicorn spawns multiple workers
import sys
if 'gunicorn' not in sys.argv[0]:
    # Running locally or in single-process mode
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=collect_market_data, trigger="interval", minutes=1)
    scheduler.start()

    # Run initial data collection on startup
    collect_market_data()

    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())
else:
    # Running under gunicorn - only start scheduler in the main process
    # Use gunicorn's preload mode with a single background thread
    from threading import Thread
    import time

    def scheduler_thread():
        """Background thread for data collection when running under gunicorn"""
        # Wait a bit for app to fully start
        time.sleep(5)
        collect_market_data()  # Initial collection

        while True:
            time.sleep(1 * 60)  # 1 minute
            try:
                collect_market_data()
            except Exception as e:
                print(f"Error in scheduler thread: {e}")

    # Start scheduler thread
    thread = Thread(target=scheduler_thread, daemon=True)
    thread.start()

if __name__ == '__main__':
    # Use debug mode only for local development
    import os
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
