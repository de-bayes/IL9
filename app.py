from flask import Flask, jsonify, render_template, request
import random
from datetime import datetime, timedelta
import requests
import json
import os
import atexit
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# Path to historical data storage
# On Railway, this will be in the persistent volume at /app/data
HISTORICAL_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'historical_snapshots.json')

# Seed data path - git-tracked backup that Railway will use to initialize the volume
SEED_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'seed_snapshots.json')

def initialize_data():
    """
    Initialize the data directory and seed from backup if needed.
    On Railway: copies seed data to persistent volume on first deploy.
    """
    data_dir = os.path.dirname(HISTORICAL_DATA_PATH)
    os.makedirs(data_dir, exist_ok=True)

    # ONE-TIME RESET: Delete old data with wrong timezone format
    # Remove this block after Railway redeploys successfully
    reset_marker = os.path.join(data_dir, '.timezone_reset_done')
    if not os.path.exists(reset_marker):
        if os.path.exists(HISTORICAL_DATA_PATH):
            os.remove(HISTORICAL_DATA_PATH)
            print(f"[{datetime.now().isoformat()}] One-time reset: deleted old data for timezone fix")
        # Create marker so we don't reset again
        with open(reset_marker, 'w') as f:
            f.write('done')

    # Only seed data if historical file doesn't exist at all
    # Once Railway starts collecting, never overwrite its data
    if not os.path.exists(HISTORICAL_DATA_PATH) and os.path.exists(SEED_DATA_PATH):
        print(f"[{datetime.now().isoformat()}] Seeding data from {SEED_DATA_PATH}")
        with open(SEED_DATA_PATH, 'r') as src:
            seed_data = json.load(src)
        with open(HISTORICAL_DATA_PATH, 'w') as dst:
            json.dump(seed_data, dst, indent=2)
        print(f"[{datetime.now().isoformat()}] Seeded {len(seed_data)} snapshots")

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
    return render_template('landing.html')

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
    """Save a historical snapshot of aggregated probabilities"""
    try:
        # Ensure data directory exists
        os.makedirs(os.path.dirname(HISTORICAL_DATA_PATH), exist_ok=True)

        # Load existing snapshots
        if os.path.exists(HISTORICAL_DATA_PATH):
            with open(HISTORICAL_DATA_PATH, 'r') as f:
                snapshots = json.load(f)
        else:
            snapshots = []

        # Get new snapshot from request
        new_snapshot = request.json
        new_snapshot['timestamp'] = datetime.now().isoformat()

        # Append new snapshot
        snapshots.append(new_snapshot)

        # Save back to file
        with open(HISTORICAL_DATA_PATH, 'w') as f:
            json.dump(snapshots, f, indent=2)

        return jsonify({"success": True, "total_snapshots": len(snapshots)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/snapshots')
def get_snapshots():
    """Retrieve historical snapshots for charting"""
    try:
        if not os.path.exists(HISTORICAL_DATA_PATH):
            return jsonify([])

        with open(HISTORICAL_DATA_PATH, 'r') as f:
            snapshots = json.load(f)

        return jsonify(snapshots)
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
            from datetime import timezone
            snapshot = {
                'candidates': [{
                    'name': c['name'],
                    'probability': round(c['probability'], 1),
                    'hasKalshi': c['hasKalshi']
                } for c in aggregated],
                'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            }

            # Ensure data directory exists
            os.makedirs(os.path.dirname(HISTORICAL_DATA_PATH), exist_ok=True)

            # Load existing snapshots
            snapshots = []
            if os.path.exists(HISTORICAL_DATA_PATH):
                with open(HISTORICAL_DATA_PATH, 'r') as f:
                    snapshots = json.load(f)

            # Append new snapshot
            snapshots.append(snapshot)

            # Save back to file
            with open(HISTORICAL_DATA_PATH, 'w') as f:
                json.dump(snapshots, f, indent=2)

            print(f"[{datetime.now().isoformat()}] Snapshot saved successfully. Total snapshots: {len(snapshots)}")
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
    scheduler.add_job(func=collect_market_data, trigger="interval", minutes=3)
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
            time.sleep(3 * 60)  # 3 minutes
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
