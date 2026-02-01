from flask import Flask, jsonify, render_template, request, send_file
import random
import math
import hashlib
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

# ===== EMAIL ALERT CONFIGURATION =====
SUBSCRIBERS_PATH = resolve_data_path('email_subscribers.jsonl')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', 'alerts@il9.org')
RESEND_FROM = f"IL9Cast <{RESEND_FROM_EMAIL}>"  # Display name + email
EMAIL_SECRET_SALT = os.environ.get('EMAIL_SECRET_SALT', 'il9cast-change-me')
SWING_THRESHOLD = 5.0  # percentage points to trigger alert
_swing_debounce = {}  # candidate_name -> last_alert_time (UTC timestamp)
_daily_summary_sent = None  # date string of last sent daily summary

# ===== FEC API CONFIGURATION =====
FEC_API_KEY = os.environ.get('FEC_API_KEY', 'DEMO_KEY')
FEC_API_BASE = 'https://api.open.fec.gov/v1'

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


# ===== EMAIL ALERT FUNCTIONS =====

def read_subscribers():
    """Read subscriber list from JSONL file. Returns list of {email, subscribed_at}."""
    subscribers = []
    if not os.path.exists(SUBSCRIBERS_PATH):
        return subscribers
    try:
        with open(SUBSCRIBERS_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    subscribers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        pass
    return subscribers

def add_subscriber(email, threshold=5.0):
    """Add a subscriber. Returns unsub token. Raises ValueError if duplicate."""
    email = email.lower().strip()
    threshold = float(threshold) if threshold else 5.0

    # Validate threshold range
    if threshold < 1.0 or threshold > 20.0:
        raise ValueError('Threshold must be between 1% and 20%')

    existing = read_subscribers()
    for sub in existing:
        if sub.get('email') == email:
            raise ValueError('Already subscribed')

    record = {
        'email': email,
        'threshold': threshold,
        'subscribed_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }

    os.makedirs(os.path.dirname(SUBSCRIBERS_PATH), exist_ok=True)
    with open(SUBSCRIBERS_PATH, 'a') as f:
        f.write(json.dumps(record) + '\n')

    return make_unsub_token(email)

def remove_subscriber(email):
    """Remove a subscriber by rewriting JSONL without that email."""
    email = email.lower().strip()
    if not os.path.exists(SUBSCRIBERS_PATH):
        return False

    kept = []
    found = False
    with open(SUBSCRIBERS_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get('email') == email:
                    found = True
                    continue
                kept.append(line)
            except json.JSONDecodeError:
                kept.append(line)

    if found:
        with open(SUBSCRIBERS_PATH, 'w') as f:
            for line in kept:
                f.write(line + '\n')
    return found

def make_unsub_token(email):
    """Generate unsubscribe token: sha256(email:salt)[:16]"""
    return hashlib.sha256(f"{email.lower().strip()}:{EMAIL_SECRET_SALT}".encode()).hexdigest()[:16]

def verify_unsub_token(email, token):
    """Verify an unsubscribe token matches."""
    return make_unsub_token(email) == token

def send_email(to, subject, html, text=None):
    """Send email via Resend API. Returns True on success."""
    if not RESEND_API_KEY:
        print(f"[{datetime.now().isoformat()}] Email skipped (no RESEND_API_KEY): {subject} -> {to}")
        return False
    try:
        payload = {
            'from': RESEND_FROM,
            'to': [to],
            'subject': subject,
            'html': html
        }
        if text:
            payload['text'] = text

        resp = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f"[{datetime.now().isoformat()}] Email sent: {subject} -> {to}")
            return True
        else:
            print(f"[{datetime.now().isoformat()}] Email failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Email error: {e}")
        return False

def send_welcome_email(email, threshold=5.0):
    """Send welcome email to new subscriber."""
    token = make_unsub_token(email)
    unsub_url = f"{request.host_url}unsubscribe?email={email}&token={token}"

    # Plain text version
    text = f"""
Welcome to IL9Cast Alerts!

You'll now receive:

⚡ Big Swing Alerts
Get notified immediately when any candidate moves {threshold:.1f}%+ in the prediction markets

📊 Daily Summary
Every morning at 8 AM CT: current standings and 24-hour changes

View Live Markets: {request.host_url}markets

---
Unsubscribe: {unsub_url}
    """

    # HTML version - email-safe (no backdrop-filter, no position absolute)
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; background-color: #0a0a0a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #1a1a1a; border: 2px solid #e67e22; border-radius: 12px;">
                        <!-- Header -->
                        <tr>
                            <td style="padding: 40px 40px 0 40px; text-align: center;">
                                <h1 style="margin: 0; color: #e67e22; font-size: 32px; font-weight: 600;">Welcome to IL9Cast</h1>
                                <p style="margin: 8px 0 0 0; color: #a0a0a0; font-size: 12px; letter-spacing: 1px; text-transform: uppercase;">Alert System Activated</p>
                            </td>
                        </tr>

                        <!-- Feature 1 -->
                        <tr>
                            <td style="padding: 32px 40px 0 40px;">
                                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px;">
                                    <tr>
                                        <td style="padding: 20px;">
                                            <div style="color: #e67e22; font-size: 24px; margin-bottom: 8px;">⚡</div>
                                            <h3 style="margin: 0 0 8px 0; color: #e0e0e0; font-size: 18px; font-weight: 600;">Big Swing Alerts</h3>
                                            <p style="margin: 0; color: #a0a0a0; font-size: 14px; line-height: 1.6;">Get notified immediately when any candidate moves {threshold:.1f}%+ in the prediction markets</p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Feature 2 -->
                        <tr>
                            <td style="padding: 16px 40px 0 40px;">
                                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px;">
                                    <tr>
                                        <td style="padding: 20px;">
                                            <div style="color: #e67e22; font-size: 24px; margin-bottom: 8px;">📊</div>
                                            <h3 style="margin: 0 0 8px 0; color: #e0e0e0; font-size: 18px; font-weight: 600;">Daily Summary</h3>
                                            <p style="margin: 0; color: #a0a0a0; font-size: 14px; line-height: 1.6;">Every morning at 8 AM CT: current standings and 24-hour changes</p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- CTA Button -->
                        <tr>
                            <td style="padding: 32px 40px; text-align: center;">
                                <a href="{request.host_url}markets" style="display: inline-block; background-color: #e67e22; color: #ffffff; text-decoration: none; padding: 14px 32px; border-radius: 6px; font-weight: 600; font-size: 16px;">View Live Markets →</a>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 0 40px 40px 40px; text-align: center; border-top: 1px solid #3a3a3a;">
                                <p style="margin: 20px 0 0 0; color: #666; font-size: 12px;">
                                    <a href="{unsub_url}" style="color: #666; text-decoration: underline;">Unsubscribe</a>
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    send_email(email, '⚡ Welcome to IL9Cast Alerts', html, text)

def check_swings_and_alert(new_snapshot, prev_snapshot):
    """Compare snapshots and send alerts based on each subscriber's threshold. 60-min debounce per candidate."""
    if not prev_snapshot:
        return

    prev_by_name = {c['name']: c['probability'] for c in prev_snapshot.get('candidates', [])}
    now_ts = _time.time()

    # Calculate all deltas
    all_swings = []
    for c in new_snapshot.get('candidates', []):
        name = c['name']
        new_prob = c['probability']
        old_prob = prev_by_name.get(name)
        if old_prob is None:
            continue
        delta = new_prob - old_prob
        if abs(delta) >= 1.0:  # Only track swings >= 1% (minimum threshold)
            all_swings.append({
                'name': name,
                'old': old_prob,
                'new': new_prob,
                'delta': delta
            })

    if not all_swings:
        return

    # Send alerts to each subscriber based on their threshold
    subscribers = read_subscribers()
    for sub in subscribers:
        email = sub['email']
        threshold = sub.get('threshold', 5.0)

        # Filter swings that meet this subscriber's threshold
        subscriber_swings = []
        for swing in all_swings:
            if abs(swing['delta']) >= threshold:
                # Check 60-minute debounce (per candidate, globally)
                last_alert = _swing_debounce.get(swing['name'], 0)
                if now_ts - last_alert < 3600:
                    continue
                subscriber_swings.append(swing)

        if subscriber_swings:
            # Update debounce for all candidates we're alerting about
            for swing in subscriber_swings:
                _swing_debounce[swing['name']] = now_ts
            send_swing_alert_to_subscriber(email, subscriber_swings)

def send_swing_alert_to_subscriber(email, swings):
    """Build and send swing alert email to a single subscriber."""
    if not swings:
        return

    # Build plain text version
    text_rows = []
    for s in swings:
        arrow = '▲' if s['delta'] > 0 else '▼'
        text_rows.append(f"{s['name']}: {s['old']:.1f}% → {s['new']:.1f}% ({arrow} {abs(s['delta']):.1f}%)")

    text = f"""
IL9Cast Big Swing Alert!

{chr(10).join(text_rows)}

View Live Markets: {request.host_url}markets
    """

    # Build HTML rows
    rows = ''
    for s in swings:
        arrow = '▲' if s['delta'] > 0 else '▼'
        color = '#27ae60' if s['delta'] > 0 else '#e74c3c'
        bg_color = '#1a2e1a' if s['delta'] > 0 else '#2e1a1a'  # Solid colors instead of rgba
        rows += f"""
                                <tr style="background-color: {bg_color};">
                                    <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: #e0e0e0; font-weight: 500;">{s['name']}</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: #a0a0a0;">{s['old']:.1f}%</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: #e0e0e0; font-weight: 600;">{s['new']:.1f}%</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: {color}; font-weight: 700; font-size: 16px;">
                                        {arrow} {abs(s['delta']):.1f}%
                                    </td>
                                </tr>"""

    subject = f"⚡ IL9Cast Alert: {swings[0]['name']} {'+' if swings[0]['delta'] > 0 else ''}{swings[0]['delta']:.1f}%"
    if len(swings) > 1:
        subject = f"⚡ IL9Cast Alert: {len(swings)} candidates moved significantly"

    token = make_unsub_token(email)
    unsub_url = f"{request.host_url}unsubscribe?email={email}&token={token}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; background-color: #0a0a0a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #1a1a1a; border: 2px solid #e67e22; border-radius: 12px;">
                        <!-- Badge -->
                        <tr>
                            <td style="padding: 32px 40px 0 40px; text-align: center;">
                                <div style="display: inline-block; background-color: #e67e22; color: #ffffff; padding: 8px 20px; border-radius: 20px; font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase;">
                                    ⚡ BIG SWING DETECTED
                                </div>
                            </td>
                        </tr>

                        <!-- Header -->
                        <tr>
                            <td style="padding: 24px 40px 32px 40px; text-align: center;">
                                <h1 style="margin: 0; color: #e67e22; font-size: 26px; font-weight: 700;">Market Movement Alert</h1>
                            </td>
                        </tr>

                        <!-- Data Table -->
                        <tr>
                            <td style="padding: 0 40px 32px 40px;">
                                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a; border: 1px solid #3a3a3a; border-radius: 8px;">
                                    <thead>
                                        <tr style="background-color: #2a2a2a;">
                                            <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">Candidate</th>
                                            <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">Before</th>
                                            <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">After</th>
                                            <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">Change</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rows}
                                    </tbody>
                                </table>
                            </td>
                        </tr>

                        <!-- CTA Button -->
                        <tr>
                            <td style="padding: 0 40px 32px 40px; text-align: center;">
                                <a href="{request.host_url}markets" style="display: inline-block; background-color: #e67e22; color: #ffffff; text-decoration: none; padding: 14px 32px; border-radius: 6px; font-weight: 600; font-size: 16px;">View Live Markets →</a>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 0 40px 32px 40px; text-align: center; border-top: 1px solid #3a3a3a;">
                                <p style="margin: 20px 0 0 0; color: #666; font-size: 12px;">
                                    <a href="{unsub_url}" style="color: #666; text-decoration: underline;">Unsubscribe</a>
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    send_email(email, subject, html, text)

def send_daily_summary():
    """Send daily summary email with current standings and 24h changes."""
    global _daily_summary_sent
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _daily_summary_sent == today_str:
        return
    _daily_summary_sent = today_str

    subscribers = read_subscribers()
    if not subscribers:
        return

    snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
    if not snapshots:
        return

    current = snapshots[-1]
    now_utc = datetime.now(timezone.utc)
    cutoff_24h = now_utc - timedelta(hours=24)

    # Find snapshot closest to 24h ago
    old_snapshot = None
    for snap in snapshots:
        dt = parse_snapshot_timestamp(snap.get('timestamp', ''))
        if dt and dt <= cutoff_24h:
            old_snapshot = snap

    old_by_name = {}
    if old_snapshot:
        old_by_name = {c['name']: c['probability'] for c in old_snapshot.get('candidates', [])}

    rows = ''
    for c in sorted(current.get('candidates', []), key=lambda x: x['probability'], reverse=True):
        name = c['name']
        prob = c['probability']
        old_prob = old_by_name.get(name)
        if old_prob is not None:
            delta = prob - old_prob
            arrow = '▲' if delta > 0 else ('▼' if delta < 0 else '—')
            color = '#27ae60' if delta > 0 else ('#e74c3c' if delta < 0 else '#a0a0a0')
            if delta != 0:
                change_str = f'{arrow} {abs(delta):.1f}%'
            else:
                change_str = '—'
        else:
            color = '#a0a0a0'
            change_str = 'New'

        rows += f"""
                                            <tr>
                                                <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: #e0e0e0; font-weight: 500;">{name}</td>
                                                <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: #e67e22; font-weight: 700; font-size: 16px;">{prob:.1f}%</td>
                                                <td style="padding: 14px; border-bottom: 1px solid #3a3a3a; color: {color}; font-weight: 600;">{change_str}</td>
                                            </tr>"""

    ct_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-6)))
    date_str = ct_time.strftime('%B %d, %Y')

    # Plain text version
    text_rows = []
    for c in sorted(current.get('candidates', []), key=lambda x: x['probability'], reverse=True):
        name = c['name']
        prob = c['probability']
        old_prob = old_by_name.get(name)
        if old_prob is not None:
            delta = prob - old_prob
            arrow = '▲' if delta > 0 else ('▼' if delta < 0 else '—')
            text_rows.append(f"{name}: {prob:.1f}% ({arrow} {abs(delta):.1f}% 24h)")
        else:
            text_rows.append(f"{name}: {prob:.1f}% (New)")

    text = f"""
IL9Cast Daily Summary - {date_str}

{chr(10).join(text_rows)}

View Live Markets: {request.host_url}markets
    """

    for sub in subscribers:
        email = sub['email']
        token = make_unsub_token(email)
        unsub_url = f"{request.host_url}unsubscribe?email={email}&token={token}"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin: 0; padding: 0; background-color: #0a0a0a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a;">
                <tr>
                    <td align="center" style="padding: 40px 20px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #1a1a1a; border: 2px solid #e67e22; border-radius: 12px;">
                            <!-- Badge -->
                            <tr>
                                <td style="padding: 32px 40px 0 40px; text-align: center;">
                                    <div style="display: inline-block; background-color: #e67e22; color: #ffffff; padding: 8px 20px; border-radius: 20px; font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase;">
                                        📊 DAILY SUMMARY
                                    </div>
                                </td>
                            </tr>

                            <!-- Header -->
                            <tr>
                                <td style="padding: 24px 40px 0 40px; text-align: center;">
                                    <h1 style="margin: 0 0 8px 0; color: #e67e22; font-size: 26px; font-weight: 700;">IL9 Democratic Primary</h1>
                                    <p style="margin: 0; color: #a0a0a0; font-size: 13px;">{date_str}</p>
                                </td>
                            </tr>

                            <!-- Data Table -->
                            <tr>
                                <td style="padding: 32px 40px;">
                                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a; border: 1px solid #3a3a3a; border-radius: 8px;">
                                        <thead>
                                            <tr style="background-color: #2a2a2a;">
                                                <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">Candidate</th>
                                                <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">Current</th>
                                                <th style="text-align: left; padding: 12px 14px; color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid #3a3a3a;">24h Change</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {rows}
                                        </tbody>
                                    </table>
                                </td>
                            </tr>

                            <!-- CTA Button -->
                            <tr>
                                <td style="padding: 0 40px 32px 40px; text-align: center;">
                                    <a href="{request.host_url}markets" style="display: inline-block; background-color: #e67e22; color: #ffffff; text-decoration: none; padding: 14px 32px; border-radius: 6px; font-weight: 600; font-size: 16px;">View Live Markets →</a>
                                </td>
                            </tr>

                            <!-- Footer -->
                            <tr>
                                <td style="padding: 0 40px 32px 40px; text-align: center; border-top: 1px solid #3a3a3a;">
                                    <p style="margin: 20px 0 0 0; color: #666; font-size: 12px;">
                                        <a href="{unsub_url}" style="color: #666; text-decoration: underline;">Unsubscribe</a>
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        send_email(email, f'📊 IL9Cast Daily Summary - {date_str}', html, text)

    print(f"[{datetime.now().isoformat()}] Daily summary sent to {len(subscribers)} subscriber(s)")


# ===== FEC API FUNCTIONS =====

def fetch_fec_candidate_data(candidate_name):
    """
    Fetch FEC data for a specific candidate by searching for their committee.
    Returns dict with financial metrics or None if not found.
    """
    try:
        # Map candidate names to FEC committee IDs
        # These will need to be looked up once candidates file with FEC
        fec_committee_map = {
            'Daniel Biss': None,  # To be filled when FEC ID is available
            'Mike Simmons': None,
            'Ram Villivalam': None,
            'Helly Shah': None,
            'Kat Abughazaleh': None,
            'Katie Stuart': None,
            'Benjy Dolich': None,
            'Greg Hoff': None,
            'Liz Fiedler': None
        }

        committee_id = fec_committee_map.get(candidate_name)
        if not committee_id:
            return None

        # Fetch candidate totals from FEC API
        url = f'{FEC_API_BASE}/candidate/{committee_id}/totals/'
        params = {
            'api_key': FEC_API_KEY,
            'cycle': 2026,
            'sort': '-cycle'
        }

        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            print(f"[{datetime.now().isoformat()}] FEC API error for {candidate_name}: {response.status_code}")
            return None

        data = response.json()
        if not data.get('results'):
            return None

        result = data['results'][0]

        # Extract key metrics
        return {
            'name': candidate_name,
            'total_raised': result.get('receipts', 0),
            'total_spent': result.get('disbursements', 0),
            'cash_on_hand': result.get('cash_on_hand_end_period', 0),
            'total_donors': result.get('individual_contributions_count', 0),
            'small_dollar_amount': result.get('individual_itemized_contributions', 0),
            'coverage_end_date': result.get('coverage_end_date', '')
        }
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error fetching FEC data for {candidate_name}: {e}")
        return None


def calculate_burn_rate(candidate_data):
    """
    Calculate burn rate metrics from FEC disbursement data.
    Returns dict with 2-week, 1-month, and 1.5-month projections.

    Note: This is a placeholder. Real implementation would require
    itemized disbursement data to calculate recent spending rates.
    """
    if not candidate_data:
        return None

    # For now, use simple average from total spent
    # In production, this would fetch itemized disbursements and calculate:
    # - Last 14 days of spending × 2 = monthly rate
    # - Last 30 days of spending × 1 = monthly rate
    # - Last 45 days of spending × 0.67 = monthly rate

    total_spent = candidate_data.get('total_spent', 0)

    # Placeholder calculation - would be replaced with real time-based data
    estimated_monthly = total_spent / 3  # Rough estimate assuming 3 months of data

    return {
        'burn_2week': estimated_monthly,  # Would be: last_14_days_spent * 2
        'burn_1month': estimated_monthly,  # Would be: last_30_days_spent * 1
        'burn_1_5month': estimated_monthly,  # Would be: last_45_days_spent * 0.67
        'cash_runway_months': candidate_data.get('cash_on_hand', 0) / estimated_monthly if estimated_monthly > 0 else 0
    }


def fetch_all_fec_data():
    """
    Fetch FEC data for all IL-09 2026 candidates.
    Returns list of candidate financial data dicts.
    """
    candidates = [
        'Daniel Biss',
        'Mike Simmons',
        'Ram Villivalam',
        'Helly Shah',
        'Kat Abughazaleh',
        'Katie Stuart',
        'Benjy Dolich',
        'Greg Hoff',
        'Liz Fiedler'
    ]

    results = []
    for candidate in candidates:
        data = fetch_fec_candidate_data(candidate)
        if data:
            # Add burn rate calculations
            burn_rates = calculate_burn_rate(data)
            if burn_rates:
                data.update(burn_rates)

            # Calculate additional metrics
            if data.get('total_raised', 0) > 0:
                data['spent_pct_of_raised'] = (data.get('total_spent', 0) / data['total_raised']) * 100
                data['avg_contribution'] = data['total_raised'] / data.get('total_donors', 1)
            else:
                data['spent_pct_of_raised'] = 0
                data['avg_contribution'] = 0

            if data.get('total_raised', 0) > 0:
                small_dollar = data.get('small_dollar_amount', 0)
                data['small_dollar_pct'] = (small_dollar / data['total_raised']) * 100
            else:
                data['small_dollar_pct'] = 0

            results.append(data)

    return results


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

def purge_old_data():
    """
    One-time purge: remove all snapshots before Jan 30, 2026.
    Clean start - old data from Jan 15-29 is discarded.
    Marker file prevents re-running on every restart.
    """
    data_dir = os.path.dirname(HISTORICAL_DATA_PATH)
    marker = os.path.join(data_dir, '.purge_pre_jan30_done')
    if os.path.exists(marker):
        return

    if not os.path.exists(HISTORICAL_DATA_PATH):
        # Nothing to purge, but mark as done
        os.makedirs(data_dir, exist_ok=True)
        with open(marker, 'w') as f:
            f.write('done')
        return

    print(f"[{datetime.now().isoformat()}] Purging all data before Jan 30, 2026...")
    cutoff = datetime(2026, 1, 30, 0, 0, 0, tzinfo=timezone.utc)
    kept = []
    total = 0

    try:
        with open(HISTORICAL_DATA_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    snap = json.loads(line)
                    dt = parse_snapshot_timestamp(snap.get('timestamp', ''))
                    if dt and dt >= cutoff:
                        kept.append(line)
                except json.JSONDecodeError:
                    continue

        # Rewrite file with only kept snapshots
        with open(HISTORICAL_DATA_PATH, 'w') as f:
            for line in kept:
                f.write(line + '\n')

        print(f"[{datetime.now().isoformat()}] Purged {total - len(kept)} old snapshots, kept {len(kept)}")

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error during purge: {e}")

    # Also delete any legacy JSON files on Railway volume
    for pattern_dir in ['/data', '/app/data', os.path.join(os.path.dirname(__file__), 'data')]:
        for fname in ['historical_snapshots.json']:
            fpath = os.path.join(pattern_dir, fname)
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    print(f"  Deleted legacy file: {fpath}")
                except Exception:
                    pass
        # Delete backup files
        if os.path.isdir(pattern_dir):
            for fname in os.listdir(pattern_dir):
                if '.backup.' in fname or '.pre-' in fname:
                    fpath = os.path.join(pattern_dir, fname)
                    try:
                        os.remove(fpath)
                        print(f"  Deleted backup file: {fpath}")
                    except Exception:
                        pass

    with open(marker, 'w') as f:
        f.write('done')

# Initialize data on module load
initialize_data()
purge_old_data()

# Mock candidate data
CANDIDATES = [
    {"id": 1, "name": "Maria Garcia", "party_role": "State Rep", "color": "#FF6B6B"},
    {"id": 2, "name": "James Wilson", "party_role": "Community Organizer", "color": "#4ECDC4"},
    {"id": 3, "name": "Dr. Sarah Ahmed", "party_role": "Physician", "color": "#45B7D1"},
    {"id": 4, "name": "Tom Mueller", "party_role": "Labor Leader", "color": "#FFA07A"},
    {"id": 5, "name": "Angela Chen", "party_role": "Tech Entrepreneur", "color": "#98D8C8"},
    {"id": 6, "name": "Robert Jackson", "party_role": "Former Alderman", "color": "#F7DC6F"},
]

# Real IL-9 Candidate Profiles
CANDIDATE_PROFILES = [
    {
        "name": "Daniel Biss",
        "slug": "daniel-biss",
        "title": "Mayor of Evanston",
        "photo": "images/candidates/biss.jpg",
        "campaign_url": "https://www.danielbiss.com",
        "bio": "Mayor of Evanston and former Illinois State Senator. Proven progressive with a legislative track record protecting healthcare, defending immigrants, and advocating for economic justice.",
        "endorsements": [
            "Rep. Jan Schakowsky",
            "Sen. Elizabeth Warren",
            "Illinois AFL-CIO",
            "SEIU Illinois State Council",
            "Chicago Teachers Union",
            "Congressional Progressive Caucus PAC"
        ],
        "key_issues": ["Medicare for All", "Wealth tax on billionaires", "Ban on mass deportations", "Cease-fire in Gaza"]
    },
    {
        "name": "Kat Abugazaleh",
        "slug": "kat-abugazaleh",
        "title": "Former Media Matters Researcher",
        "photo": "images/candidates/katabu.png",
        "campaign_url": "https://www.katforillinois.com/",
        "bio": "Media critic and researcher focused on combating right-wing disinformation. Running an anti-establishment campaign centered on breaking the status quo and transparent grassroots fundraising.",
        "endorsements": [
            "Rep. Ro Khanna",
            "Former Rep. Jamaal Bowman",
            "Sunrise Movement",
            "Peace Action"
        ],
        "key_issues": ["Rejecting corporate PAC money", "Media transparency", "Combating disinformation", "Breaking the status quo"]
    },
    {
        "name": "Laura Fine",
        "slug": "laura-fine",
        "title": "State Senator",
        "photo": "images/candidates/fine.png",
        "campaign_url": "https://www.laurafineforcongress.org/",
        "bio": "Illinois State Senator and champion for families. Recently passed laws banning prior authorization for mental health services and requiring insurance coverage for neonatal intensive care.",
        "endorsements": [
            "Personal PAC",
            "Planned Parenthood"
        ],
        "key_issues": ["Mental health access", "Insurance reform", "Family healthcare", "Toxic metal testing in baby food"]
    },
    {
        "name": "Mike Simmons",
        "slug": "mike-simmons",
        "title": "State Senator",
        "photo": "images/candidates/simmons.jpg",
        "campaign_url": "https://www.mikesimmons.org/",
        "bio": "First openly LGBTQ+ and Ethiopian-American Illinois State Senator. Leader on bold systemic change, passing the Jett Hawkins Act banning hair discrimination and protecting gender-affirming care.",
        "endorsements": [
            "Equality PAC",
            "LGBTQ+ Victory Fund"
        ],
        "key_issues": ["Gender-affirming care", "Public transit expansion", "Affordable housing", "Permanent child tax credits"]
    },
    {
        "name": "Phil Andrew",
        "slug": "phil-andrew",
        "title": "Former FBI Agent",
        "photo": "images/candidates/philandrew.png",
        "campaign_url": "https://www.philandrewforcongress.com/",
        "bio": "Crisis-tested leader with background in law enforcement and hostage negotiation. Gun violence survivor advocating for evidence-based community safety strategies. Pledged to serve only 3-5 terms.",
        "endorsements": [
            "Brady PAC"
        ],
        "key_issues": ["Gun violence prevention", "Community safety", "Political independence", "Refusing PAC money"]
    },
    {
        "name": "Bushra Amiwala",
        "slug": "bushra-amiwala",
        "title": "Skokie School Board Member",
        "photo": "images/candidates/bushra.png",
        "campaign_url": "https://www.bushraforcongress.com/",
        "bio": "Youngest Muslim elected official in the United States. School board member and education advocate focused on tuition-free public college and student debt cancellation.",
        "endorsements": [
            "Former Rep. Marie Newman",
            "Muslim Civic Coalition",
            "Northside Democracy for America"
        ],
        "key_issues": ["Tuition-free college", "Student debt cancellation", "Medicare for All", "Domestic infrastructure over foreign aid"]
    }
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

@app.route('/candidates')
def candidates():
    """Show candidate profiles with live odds and individual charts"""
    # Get latest snapshot for current odds
    snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
    latest_snapshot = snapshots[-1] if snapshots else None

    # Build candidate data with current odds
    candidates_data = []
    for profile in CANDIDATE_PROFILES:
        candidate = profile.copy()
        # Find current odds from latest snapshot
        if latest_snapshot:
            for c in latest_snapshot.get('candidates', []):
                # Normalize names for matching
                snapshot_name = c['name'].replace('Abughazaleh', 'Abugazaleh')
                profile_name = profile['name']
                if snapshot_name == profile_name:
                    candidate['current_odds'] = c['probability']
                    candidate['has_kalshi'] = c.get('hasKalshi', False)
                    break
        if 'current_odds' not in candidate:
            candidate['current_odds'] = 0.0
            candidate['has_kalshi'] = False

        candidates_data.append(candidate)

    # Sort by current odds descending
    candidates_data.sort(key=lambda x: x['current_odds'], reverse=True)

    return render_template('candidates.html', candidates=candidates_data)

@app.route('/fundraising/<candidate_slug>')
def candidate_fundraising(candidate_slug):
    """Show individual candidate fundraising page"""
    # Find candidate profile
    candidate_profile = next((c for c in CANDIDATE_PROFILES if c['slug'] == candidate_slug), None)

    if not candidate_profile:
        return "Candidate not found", 404

    # Get latest snapshot for current odds
    snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
    latest_snapshot = snapshots[-1] if snapshots else None

    # Add current odds
    candidate = candidate_profile.copy()
    if latest_snapshot:
        for c in latest_snapshot.get('candidates', []):
            snapshot_name = c['name'].replace('Abughazaleh', 'Abugazaleh')
            if snapshot_name == candidate['name']:
                candidate['current_odds'] = c['probability']
                candidate['has_kalshi'] = c.get('hasKalshi', False)
                break

    if 'current_odds' not in candidate:
        candidate['current_odds'] = 0.0
        candidate['has_kalshi'] = False

    return render_template('candidate_fundraising.html', candidate=candidate)

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

@app.route('/api/snapshots/count')
def get_snapshot_count():
    """Return total snapshot count without loading all data"""
    try:
        count = count_snapshots_jsonl(HISTORICAL_DATA_PATH)
        return jsonify({"count": count})
    except Exception as e:
        return jsonify({"count": 0})

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
      epsilon: RDP tolerance (default 0.5)
    Returns ~200-400 points instead of 5000+ raw.
    """
    global _chart_cache
    try:
        period = request.args.get('period', 'all')
        epsilon = float(request.args.get('epsilon', '0.5'))
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

        # Detect real gaps (>2 hours) in the RAW data before any processing
        GAP_THRESHOLD_SECS = 7200  # 2 hours
        gaps = []
        for i in range(1, len(parsed)):
            gap_secs = (parsed[i][0] - parsed[i - 1][0]).total_seconds()
            if gap_secs > GAP_THRESHOLD_SECS:
                gaps.append({
                    'start': parsed[i - 1][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'end': parsed[i][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                })

        # ===== EMA SMOOTHING PASS =====
        # Apply exponential moving average per candidate to eliminate jitter.
        # alpha controls responsiveness: lower = smoother (0.15 is very smooth)
        EMA_ALPHA = 0.15

        all_candidates = set()
        for _, snap in parsed:
            for c in snap.get('candidates', []):
                all_candidates.add(c['name'])

        # Track EMA state per candidate
        ema_state = {}  # candidate_name -> current smoothed value

        for i, (dt, snap) in enumerate(parsed):
            for c in snap.get('candidates', []):
                name = c['name']
                raw = c.get('probability', 0)
                if name not in ema_state:
                    ema_state[name] = raw  # First value: no smoothing
                else:
                    ema_state[name] = EMA_ALPHA * raw + (1 - EMA_ALPHA) * ema_state[name]
                c['probability'] = round(ema_state[name], 1)

        # ===== RDP SIMPLIFICATION =====
        # Run RDP per candidate on the smoothed data
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

        # ===== ENSURE MINIMUM TIME DENSITY =====
        # Add points to ensure at least one every 15 minutes (900 seconds)
        kept_sorted = sorted(kept_indices)
        MIN_INTERVAL = 900  # 15 minutes in seconds

        additional_indices = set()
        for i in range(len(kept_sorted) - 1):
            idx1 = kept_sorted[i]
            idx2 = kept_sorted[i + 1]
            dt1 = parsed[idx1][0]
            dt2 = parsed[idx2][0]
            time_gap = (dt2 - dt1).total_seconds()

            # If gap > 15 minutes, add intermediate points
            if time_gap > MIN_INTERVAL:
                num_needed = int(time_gap / MIN_INTERVAL)
                for j in range(1, num_needed + 1):
                    # Find index approximately j * (interval) seconds after dt1
                    target_time = dt1 + timedelta(seconds=j * MIN_INTERVAL)
                    # Find closest index to target_time between idx1 and idx2
                    for k in range(idx1 + 1, idx2):
                        if parsed[k][0] >= target_time:
                            additional_indices.add(k)
                            break

        kept_indices.update(additional_indices)
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


@app.route('/api/fec/candidates')
def get_fec_candidates():
    """
    Fetch FEC campaign finance data for all IL-09 2026 candidates.
    Returns comprehensive financial metrics including burn rates.
    """
    try:
        data = fetch_all_fec_data()

        if not data:
            # Return placeholder data structure if FEC data not yet available
            return jsonify({
                "available": False,
                "message": "FEC data will be available after the January 31st filing deadline",
                "candidates": []
            })

        return jsonify({
            "available": True,
            "updated": datetime.now(timezone.utc).isoformat(),
            "candidates": data
        })
    except Exception as e:
        return jsonify({"error": str(e), "available": False}), 500


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

@app.route('/api/download/snapshots/csv')
def download_snapshots_csv():
    """Download all historical snapshot data as CSV file"""
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if not snapshots:
            return jsonify({"error": "No data available"}), 404

        # Build CSV content
        import io
        output = io.StringIO()
        output.write('timestamp,candidate,probability,hasKalshi\n')

        for snapshot in snapshots:
            timestamp = snapshot.get('timestamp', '')
            for candidate in snapshot.get('candidates', []):
                name = candidate.get('name', '')
                prob = candidate.get('probability', 0)
                has_kalshi = 'true' if candidate.get('hasKalshi', False) else 'false'
                # Escape candidate name if it contains commas or quotes
                name_escaped = f'"{name}"' if ',' in name or '"' in name else name
                output.write(f'{timestamp},{name_escaped},{prob:.1f},{has_kalshi}\n')

        csv_content = output.getvalue()
        output.close()

        # Create response
        from flask import Response
        response = Response(csv_content, mimetype='text/csv')
        response.headers['Content-Disposition'] = 'attachment; filename=il9cast_historical_data.csv'
        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    """Subscribe an email to alerts."""
    import re
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email required'}), 400

    email = data['email'].lower().strip()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({'error': 'Invalid email address'}), 400

    threshold = data.get('threshold', 5.0)

    try:
        token = add_subscriber(email, threshold)
        try:
            send_welcome_email(email, threshold)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Welcome email failed: {e}")
        return jsonify({'success': True, 'message': 'Subscribed! Check your email.'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 409

@app.route('/unsubscribe')
def unsubscribe():
    """Unsubscribe via signed token link."""
    email = request.args.get('email', '').lower().strip()
    token = request.args.get('token', '')

    if not email or not token:
        return render_template('unsubscribe.html', success=False, message='Invalid unsubscribe link.')

    if not verify_unsub_token(email, token):
        return render_template('unsubscribe.html', success=False, message='Invalid unsubscribe link.')

    removed = remove_subscriber(email)
    if removed:
        return render_template('unsubscribe.html', success=True, message=f'{email} has been unsubscribed.')
    else:
        return render_template('unsubscribe.html', success=True, message='You are already unsubscribed.')

@app.route('/api/test-swing-alert')
def test_swing_alert():
    """Test endpoint: send a fake swing alert to all subscribers"""
    fake_swings = [
        {'name': 'Daniel Biss', 'old': 58.2, 'new': 64.7, 'delta': 6.5},
        {'name': 'Jan Schakowsky', 'old': 24.1, 'new': 18.3, 'delta': -5.8}
    ]

    subscribers = read_subscribers()
    count = 0
    for sub in subscribers:
        threshold = sub.get('threshold', 5.0)
        # Filter swings that meet this subscriber's threshold
        subscriber_swings = [s for s in fake_swings if abs(s['delta']) >= threshold]
        if subscriber_swings:
            send_swing_alert_to_subscriber(sub['email'], subscriber_swings)
            count += 1

    return jsonify({'success': True, 'message': f'Test swing alert sent to {count} subscriber(s)'})


# Background task to collect data every 3 minutes
# Reduces over-sampling and ensures clean 3-minute intervals
# Includes spike dampening to prevent chart artifacts

# Maximum percentage-point change allowed per 3-minute interval per candidate
MAX_CHANGE_PER_INTERVAL = 3.0

# In-memory cache of last successful snapshot for spike dampening and API fallback
_last_snapshot = None

def _get_last_snapshot():
    """Get the most recent snapshot for spike dampening comparison."""
    global _last_snapshot
    if _last_snapshot is not None:
        return _last_snapshot
    # Load from file on first run
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if snapshots:
            _last_snapshot = snapshots[-1]
            return _last_snapshot
    except Exception:
        pass
    return None

def _dampen_spikes(aggregated):
    """
    Prevent sudden spikes by capping per-candidate change to MAX_CHANGE_PER_INTERVAL.
    Compares new values against the previous snapshot and clamps large jumps.
    """
    prev = _get_last_snapshot()
    if not prev:
        return aggregated  # No previous data, allow any values

    prev_by_name = {c['name']: c['probability'] for c in prev.get('candidates', [])}

    for c in aggregated:
        if c['name'] in prev_by_name:
            prev_prob = prev_by_name[c['name']]
            delta = c['probability'] - prev_prob
            if abs(delta) > MAX_CHANGE_PER_INTERVAL:
                clamped = prev_prob + (MAX_CHANGE_PER_INTERVAL if delta > 0 else -MAX_CHANGE_PER_INTERVAL)
                print(f"  [Spike dampened] {c['name']}: {c['probability']:.1f}% -> {clamped:.1f}% (was {delta:+.1f}% change)")
                c['probability'] = clamped

    return aggregated

def collect_market_data():
    """Fetch market data and save snapshot automatically"""
    global _last_snapshot
    try:
        print(f"[{datetime.now().isoformat()}] Running automatic data collection...")

        # Fetch Manifold data
        manifold_data = {}
        manifold_ok = False
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
            manifold_ok = True
        except Exception as e:
            print(f"Error fetching Manifold data: {e}")

        # Fetch Kalshi data
        kalshi_data = {}
        kalshi_ok = False
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

                    # Only compute a real midpoint when both sides have orders.
                    # If yes_bid is 0 (no buy-side interest), the midpoint between
                    # 0 and the ask is meaningless — fall back to last_price.
                    if yes_bid > 0 and yes_ask > 0:
                        midpoint = (yes_bid + yes_ask) / 2
                    else:
                        midpoint = last_price

                    # Calculate liquidity-weighted price
                    spread = yes_ask - yes_bid if (yes_bid > 0 and yes_ask > 0) else 0
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
            kalshi_ok = True
        except Exception as e:
            print(f"Error fetching Kalshi data: {e}")

        # If both APIs failed, skip this interval entirely (no bad data)
        if not manifold_ok and not kalshi_ok:
            print(f"[{datetime.now().isoformat()}] Both APIs failed - skipping snapshot to avoid bad data")
            return

        # If only one API failed, log a warning (spike dampening will handle it)
        if not manifold_ok:
            print(f"  [Warning] Manifold API failed - using Kalshi-only data (dampened)")
        if not kalshi_ok:
            print(f"  [Warning] Kalshi API failed - using Manifold-only data (dampened)")

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

            # Spike dampening: cap per-candidate change to prevent chart artifacts
            aggregated = _dampen_spikes(aggregated)

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
                prev_snapshot = _last_snapshot
                append_snapshot_jsonl(HISTORICAL_DATA_PATH, snapshot)
                _last_snapshot = snapshot  # Update in-memory cache
                total_count = count_snapshots_jsonl(HISTORICAL_DATA_PATH)
                print(f"[{datetime.now().isoformat()}] Snapshot saved successfully. Total snapshots: {total_count}")

                # Check for big swings and send alerts
                try:
                    check_swings_and_alert(snapshot, prev_snapshot)
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] Error checking swings: {e}")
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
    from apscheduler.triggers.cron import CronTrigger
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=collect_market_data, trigger="interval", minutes=3)
    scheduler.add_job(func=send_daily_summary, trigger=CronTrigger(hour=8, minute=0, timezone='America/Chicago'))
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

            # Check if it's time for daily summary (8 AM CT = 14:00 UTC)
            try:
                ct_now = datetime.now(timezone.utc) + timedelta(hours=-6)
                if ct_now.hour == 8 and ct_now.minute < 3:
                    send_daily_summary()
            except Exception as e:
                print(f"Error sending daily summary: {e}")

    # Start scheduler thread
    thread = Thread(target=scheduler_thread, daemon=True)
    thread.start()

if __name__ == '__main__':
    # Use debug mode only for local development
    import os
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
