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
import shutil
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
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'https://il9.org/')
SWING_THRESHOLD = 5.0  # percentage points to trigger alert
_swing_debounce = {}  # candidate_name -> last_alert_time (UTC timestamp)
_daily_summary_sent = None  # date string of last sent daily summary

# ===== JSONL HELPER FUNCTIONS =====

def _acquire_file_lock(lock_path):
    """Acquire an exclusive inter-process file lock and return the lock file handle."""
    import fcntl
    lock_file = open(lock_path, 'a+')
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    return lock_file


def _release_file_lock(lock_file):
    """Release an inter-process file lock."""
    import fcntl
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


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
                if '\x00' in line:
                    preview = line[:120]
                    print(
                        f"[{datetime.now().isoformat()}] Corrupt NUL bytes at line {line_num}. "
                        f"Skipping malformed JSONL row (preview={preview!r})"
                    )
                    continue
                try:
                    snapshot = json.loads(line)
                    snapshots.append(snapshot)
                except json.JSONDecodeError as e:
                    preview = line[:120]
                    print(
                        f"[{datetime.now().isoformat()}] Error parsing line {line_num}: {e}. "
                        f"Skipping malformed JSONL row (preview={preview!r})"
                    )
                    continue
    except (IOError, OSError) as e:
        print(f"[{datetime.now().isoformat()}] Error reading JSONL file: {e}")

    return snapshots


def repair_snapshots_jsonl(filepath):
    """
    Remove malformed JSONL lines from snapshots file.
    Returns dict with total/kept/removed counts and optional backup_path.
    """
    stats = {'total': 0, 'kept': 0, 'removed': 0, 'backup_path': None}
    if not os.path.exists(filepath):
        return stats

    temp_path = filepath + '.repair.tmp'
    lock_path = filepath + '.lock'
    lock_file = None
    try:
        lock_file = _acquire_file_lock(lock_path)
        with open(filepath, 'r') as src, open(temp_path, 'w') as dst:
            for line in src:
                stripped = line.strip()
                if not stripped:
                    continue
                stats['total'] += 1
                if '\x00' in stripped:
                    stats['removed'] += 1
                    continue
                try:
                    json.loads(stripped)
                    dst.write(stripped + '\n')
                    stats['kept'] += 1
                except json.JSONDecodeError:
                    stats['removed'] += 1

        if stats['removed'] > 0:
            backup_path = filepath + f".backup.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            shutil.copy2(filepath, backup_path)
            stats['backup_path'] = backup_path
            os.replace(temp_path, filepath)
            print(
                f"[{datetime.now().isoformat()}] Repaired JSONL snapshots: "
                f"removed {stats['removed']} malformed line(s), kept {stats['kept']}, "
                f"backup saved to {backup_path}"
            )
        elif os.path.exists(temp_path):
            os.remove(temp_path)
    except (IOError, OSError) as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        print(f"[{datetime.now().isoformat()}] Error repairing JSONL file: {e}")
    finally:
        if lock_file is not None:
            _release_file_lock(lock_file)

    return stats


def append_snapshot_jsonl(filepath, snapshot):
    """
    Append a single snapshot to JSONL file safely.
    Uses a file lock + append + fsync to prevent inter-process corruption.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    lock_path = filepath + '.lock'
    lock_file = None
    try:
        lock_file = _acquire_file_lock(lock_path)
        line = json.dumps(snapshot, separators=(',', ':')) + '\n'
        with open(filepath, 'a') as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return True

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error appending to JSONL: {e}")
        raise
    finally:
        if lock_file is not None:
            _release_file_lock(lock_file)


def count_snapshots_jsonl(filepath):
    """Count total valid snapshots in JSONL file without loading all into memory"""
    if not os.path.exists(filepath):
        return 0

    count = 0
    with open(filepath, 'r') as f:
        for line in f:
            stripped = line.strip()
            if stripped and '\x00' not in stripped:
                count += 1
    return count

def count_data_points_jsonl(filepath):
    """Count total data points (candidates across all snapshots) in JSONL file"""
    if not os.path.exists(filepath):
        return 0

    total_data_points = 0
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    snapshot = json.loads(line)
                    candidates = snapshot.get('candidates', [])
                    total_data_points += len(candidates)
                except:
                    pass  # Skip malformed lines
    return total_data_points

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
    unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

    # Plain text version
    text = f"""
Welcome to IL9Cast Alerts!

You'll now receive:

⚡ Big Swing Alerts
Get notified immediately when any candidate moves {threshold:.1f}%+ in the prediction markets

📊 Daily Summary
Every morning at 8 AM CT: current standings and 24-hour changes

View Live Markets: {SITE_BASE_URL}markets

---
Unsubscribe: {unsub_url}
    """

    # HTML version
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
            <tr><td align="center" style="padding: 40px 20px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                    <!-- Logo -->
                    <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                        <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                            <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                        </h1>
                        <p style="margin: 0 0 20px 0; color: #888; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;">Alert System Activated</p>
                    </td></tr>

                    <!-- Welcome -->
                    <tr><td style="padding: 32px 40px 24px 40px; text-align: center;">
                        <h2 style="margin: 0 0 8px 0; color: #F0EFEB; font-family: Georgia, 'Times New Roman', serif; font-size: 22px; font-weight: 400;">Welcome</h2>
                        <p style="margin: 0; color: #888; font-size: 14px; line-height: 1.6;">You're now subscribed to IL-9 primary race alerts.</p>
                    </td></tr>

                    <!-- Features -->
                    <tr><td style="padding: 0 40px 12px 40px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                            <tr><td style="padding: 20px 24px;">
                                <h3 style="margin: 0 0 6px 0; color: #31B0B5; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Big Swing Alerts</h3>
                                <p style="margin: 0; color: #999; font-size: 14px; line-height: 1.5;">Notified when any candidate moves {threshold:.1f}%+ in prediction markets</p>
                            </td></tr>
                        </table>
                    </td></tr>
                    <tr><td style="padding: 0 40px 24px 40px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                            <tr><td style="padding: 20px 24px;">
                                <h3 style="margin: 0 0 6px 0; color: #31B0B5; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Daily Summary</h3>
                                <p style="margin: 0; color: #999; font-size: 14px; line-height: 1.5;">Every morning at 8 AM CT: standings and 24-hour changes</p>
                            </td></tr>
                        </table>
                    </td></tr>

                    <!-- CTA -->
                    <tr><td style="padding: 8px 40px 32px 40px; text-align: center;">
                        <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                    </td></tr>

                    <!-- Footer -->
                    <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                        <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                    </td></tr>
                </table>
            </td></tr>
        </table>
    </body>
    </html>
    """
    send_email(email, 'Welcome to IL9Cast Alerts', html, text)

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

View Live Markets: {SITE_BASE_URL}markets
    """

    # Build HTML rows
    rows = ''
    for s in swings:
        arrow = '▲' if s['delta'] > 0 else '▼'
        color = '#31B686' if s['delta'] > 0 else '#e74c3c'
        rows += f"""
                                <tr>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #F0EFEB; font-weight: 500;">{s['name']}</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #888;">{s['old']:.1f}%</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #31B0B5; font-weight: 600;">{s['new']:.1f}%</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: {color}; font-weight: 700; font-size: 16px;">
                                        {arrow} {abs(s['delta']):.1f}%
                                    </td>
                                </tr>"""

    subject = f"⚡ IL9Cast Alert: {swings[0]['name']} {'+' if swings[0]['delta'] > 0 else ''}{swings[0]['delta']:.1f}%"
    if len(swings) > 1:
        subject = f"⚡ IL9Cast Alert: {len(swings)} candidates moved significantly"

    token = make_unsub_token(email)
    unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
            <tr><td align="center" style="padding: 40px 20px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                    <!-- Logo -->
                    <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                        <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                            <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                        </h1>
                        <p style="margin: 0 0 20px 0; color: #31B0B5; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; font-weight: 700;">Market Movement Detected</p>
                    </td></tr>

                    <!-- Data Table -->
                    <tr><td style="padding: 28px 40px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                            <thead>
                                <tr style="background-color: #1A1A1E;">
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Candidate</th>
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Before</th>
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">After</th>
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Change</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows}
                            </tbody>
                        </table>
                    </td></tr>

                    <!-- CTA -->
                    <tr><td style="padding: 0 40px 32px 40px; text-align: center;">
                        <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                    </td></tr>

                    <!-- Footer -->
                    <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                        <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                    </td></tr>
                </table>
            </td></tr>
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
            color = '#31B686' if delta > 0 else ('#e74c3c' if delta < 0 else '#888')
            if delta != 0:
                change_str = f'{arrow} {abs(delta):.1f}%'
            else:
                change_str = '—'
        else:
            color = '#888'
            change_str = 'New'

        rows += f"""
                                            <tr>
                                                <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #F0EFEB; font-weight: 500;">{name}</td>
                                                <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #31B0B5; font-weight: 700; font-size: 16px;">{prob:.1f}%</td>
                                                <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: {color}; font-weight: 600;">{change_str}</td>
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

View Live Markets: {SITE_BASE_URL}markets
    """

    for sub in subscribers:
        email = sub['email']
        token = make_unsub_token(email)
        unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
        <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
                <tr><td align="center" style="padding: 40px 20px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                        <!-- Logo -->
                        <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                            <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                                <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                            </h1>
                            <p style="margin: 0 0 20px 0; color: #888; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;">Daily Summary &middot; {date_str}</p>
                        </td></tr>

                        <!-- Data Table -->
                        <tr><td style="padding: 28px 40px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                                <thead>
                                    <tr>
                                        <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Candidate</th>
                                        <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Current</th>
                                        <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">24h Change</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows}
                                </tbody>
                            </table>
                        </td></tr>

                        <!-- CTA -->
                        <tr><td style="padding: 0 40px 32px 40px; text-align: center;">
                            <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                        </td></tr>

                        <!-- Footer -->
                        <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                            <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                        </td></tr>
                    </table>
                </td></tr>
            </table>
        </body>
        </html>
        """
        send_email(email, f'IL9Cast Daily Summary - {date_str}', html, text)

    print(f"[{datetime.now().isoformat()}] Daily summary sent to {len(subscribers)} subscriber(s)")


# ===== FEC API FUNCTIONS =====

def fetch_all_fec_data():
    """
    Returns hardcoded FEC data for all IL-09 2026 candidates with profiles.
    Data is static until new FEC reports are filed (expected April 2026).
    Source: FEC filings as of Dec 31, 2025 (retrieved Feb 1, 2026).

    Burn rate formulas (corrected):
    - 2-week burn: last 2 weeks expenditures × 2 = monthly
    - 1-month burn: last 1 month expenditures = monthly
    - 1.5-month burn: last 1.5 months expenditures × 0.66 = monthly
    """
    return [
        {
            "name": "Daniel Biss",
            "total_raised": 1984528.24,
            "total_spent": 608223.67,
            "cash_on_hand": 1376304.57,
            "total_donors": 5590,
            "small_dollar_amount": 97977.94,
            "individual_total": 1913366.89,
            "coverage_end_date": "2025-12-31T00:00:00",
            "committee_id": "C00905307",
            "burn_2week": 0,
            "burn_1month": 0,
            "burn_1_5month": 0.0,
            "cash_runway_months": 0,
            "spent_pct_of_raised": 30.648274876652803,
            "avg_contribution": 355.01399642218246,
            "small_dollar_pct": 5.120708449177775
        },
        {
            "name": "Kat Abugazaleh",
            "total_raised": 2705175.67,
            "total_spent": 1894222.66,
            "cash_on_hand": 810953.01,
            "total_donors": 39569,
            "small_dollar_amount": 1898015.77,
            "individual_total": 2702469.35,
            "coverage_end_date": "2025-12-31T00:00:00",
            "committee_id": "C00900449",
            "burn_2week": 0,
            "burn_1month": 0,
            "burn_1_5month": 0.0,
            "cash_runway_months": 0,
            "spent_pct_of_raised": 70.02216828306754,
            "avg_contribution": 68.36603578558973,
            "small_dollar_pct": 70.23264741189386
        },
        {
            "name": "Laura Fine",
            "total_raised": 1921415.34,
            "total_spent": 481445.18,
            "cash_on_hand": 1439970.16,
            "total_donors": 4874,
            "small_dollar_amount": 59768.25,
            "individual_total": 1899148.18,
            "coverage_end_date": "2025-12-31T00:00:00",
            "committee_id": "C00904326",
            "burn_2week": 0,
            "burn_1month": 0,
            "burn_1_5month": 0.0,
            "cash_runway_months": 0,
            "spent_pct_of_raised": 25.056799015667274,
            "avg_contribution": 394.21734509643005,
            "small_dollar_pct": 3.147108299890533
        },
        {
            "name": "Mike Simmons",
            "total_raised": 324880.07,
            "total_spent": 189728.52,
            "cash_on_hand": 135151.55,
            "total_donors": 1384,
            "small_dollar_amount": 42440.7,
            "individual_total": 310380.07,
            "coverage_end_date": "2025-12-31T00:00:00",
            "committee_id": "C00910976",
            "burn_2week": 0,
            "burn_1month": 0,
            "burn_1_5month": 0.0,
            "cash_runway_months": 0,
            "spent_pct_of_raised": 58.399556488645175,
            "avg_contribution": 234.73993497109828,
            "small_dollar_pct": 13.673783886961555
        },
        {
            "name": "Phil Andrew",
            "total_raised": 1210786.43,
            "total_spent": 249372.83,
            "cash_on_hand": 961413.6,
            "total_donors": 2367,
            "small_dollar_amount": 42544.0,
            "individual_total": 800978.51,
            "coverage_end_date": "2025-12-31T00:00:00",
            "committee_id": "C00911024",
            "burn_2week": 0,
            "burn_1month": 0,
            "burn_1_5month": 0.0,
            "cash_runway_months": 0,
            "spent_pct_of_raised": 20.595938624782903,
            "avg_contribution": 511.5278538234051,
            "small_dollar_pct": 5.311503301131013
        },
        {
            "name": "Bushra Amiwala",
            "total_raised": 663802.8,
            "total_spent": 185643.0,
            "cash_on_hand": 478159.8,
            "total_donors": 4356,
            "small_dollar_amount": 168958.8,
            "individual_total": 657402.8,
            "coverage_end_date": "2025-09-30T00:00:00",
            "committee_id": "C00906842",
            "burn_2week": 0,
            "burn_1month": 0,
            "burn_1_5month": 0,
            "cash_runway_months": 0,
            "spent_pct_of_raised": 27.966588872478393,
            "avg_contribution": 152.38815426997246,
            "small_dollar_pct": 25.70095533514612
        }
    ]


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
    Optional one-time purge: remove snapshots before Jan 30, 2026.
    Disabled by default to preserve historical volume data.
    Set ENABLE_PRE_JAN30_PURGE=true to run this migration.
    """
    purge_enabled = os.environ.get('ENABLE_PRE_JAN30_PURGE', '').strip().lower() in {'1', 'true', 'yes'}
    if not purge_enabled:
        print(f"[{datetime.now().isoformat()}] Pre-Jan30 purge disabled; preserving historical snapshots")
        return

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

    with open(marker, 'w') as f:
        f.write('done')

# Initialize data on module load
print(f"[{datetime.now().isoformat()}] Using historical data path: {HISTORICAL_DATA_PATH}")
initialize_data()
purge_old_data()
repair_snapshots_jsonl(HISTORICAL_DATA_PATH)

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
            "Illinois Federation of Teachers",
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
        "bio": "Illinois State Senator and champion for families. Recently passed laws banning prior authorization for mental health services, requiring insurance coverage for emergency neonatal intensive care, and mandating toxic metal testing in baby food.",
        "endorsements": [
            "Rep. Brad Schneider (IL-10)",
            "Rep. Lois Frankel (FL-22)",
            "Rep. Norma Torres (CA-25)",
            "State Rep. Tracy Katz Muhl (IL-57)",
            "State Sen. Laura Murphy (IL-28)",
            "Chicago Tribune",
            "Maine Township Democrats"
        ],
        "key_issues": ["Mental health access", "Insurance reform", "Family healthcare", "Toxic metal testing in baby food"]
    },
    {
        "name": "Mike Simmons",
        "slug": "mike-simmons",
        "title": "State Senator",
        "photo": "images/candidates/simmons.jpg",
        "campaign_url": "https://www.mikesimmons.org/",
        "bio": "First openly LGBTQ+ and Ethiopian-American Illinois State Senator. Passed the Jett Hawkins Act banning hair discrimination and championed the Patient and Provider Protection Act protecting gender-affirming care.",
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
        "bio": "Former FBI special agent and hostage negotiator with 21 years of service. Gun violence survivor shot by Laurie Dann in 1988, advocating for evidence-based community safety strategies.",
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

@app.route('/model/methodology')
def model_methodology():
    # Serve the corrected methodology source file directly to avoid binary asset swaps in PRs.
    pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'IL9Cast_Methodology_CORRECTED.pdf')
    return send_file(pdf_path, mimetype='application/pdf')

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

@app.route('/updates')
def updates():
    return render_template('updates.html')

@app.route('/case-study/bid-ask-spreads')
def case_study_bid_ask():
    return render_template('case_study_bid_ask.html')

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

@app.route('/sitemap.xml')
def sitemap():
    """Serve sitemap.xml for search engines"""
    from flask import Response
    return Response(render_template('sitemap.xml'), mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    """Serve robots.txt for search engine crawlers"""
    from flask import Response
    return Response(render_template('robots.txt'), mimetype='text/plain')

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

    # Get FEC data for this candidate
    all_fec_data = fetch_all_fec_data()
    fec_data = None
    for fec_candidate in all_fec_data:
        # Normalize names for matching
        fec_name = fec_candidate['name']
        candidate_name = candidate['name']
        if fec_name == candidate_name or fec_name.replace('Abugazaleh', 'Abughazaleh') == candidate_name:
            fec_data = fec_candidate
            break

    # Add FEC data to candidate object
    if fec_data:
        candidate.update(fec_data)

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
    """Proxy Kalshi API to avoid CORS — uses /events endpoint (public, no auth required)"""
    try:
        response = requests.get('https://api.elections.kalshi.com/trade-api/v2/events/KXIL9D-26')
        response.raise_for_status()
        data = response.json()
        # Reshape to match the old /markets response format the frontend expects
        markets = data.get('markets', [])
        for m in markets:
            # The old API had candidate name in 'subtitle'; new API uses yes_sub_title
            if not m.get('subtitle'):
                m['subtitle'] = m.get('yes_sub_title') or m.get('custom_strike', {}).get('Candidate', '')
        result = jsonify({"markets": markets})
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


@app.route('/api/admin/repair-snapshots', methods=['POST'])
def repair_snapshots_admin():
    """Run JSONL repair on demand and return recovery metadata."""
    try:
        stats = repair_snapshots_jsonl(HISTORICAL_DATA_PATH)
        return jsonify({
            'success': True,
            'stats': stats,
            'historical_data_path': HISTORICAL_DATA_PATH
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/fix-kalshi-gap', methods=['POST'])
def fix_kalshi_gap():
    """One-time fix: remove last N min of Manifold-only data. Default 50 min."""
    try:
        minutes = int(request.args.get('minutes', 50))
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if not snapshots:
            return jsonify({"error": "no snapshots"}), 400

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=minutes)

        good = []
        removed = 0
        for s in snapshots:
            ts = parse_snapshot_timestamp(s.get('timestamp', ''))
            if ts and ts < cutoff:
                good.append(s)
            else:
                removed += 1

        temp_path = HISTORICAL_DATA_PATH + '.fix_tmp'
        with open(temp_path, 'w') as f:
            for s in good:
                f.write(json.dumps(s) + '\n')
        os.replace(temp_path, HISTORICAL_DATA_PATH)

        return jsonify({
            "success": True,
            "removed": removed,
            "kept": len(good),
            "cutoff_minutes": minutes
        })
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
    """Return total snapshot count and data points without loading all data"""
    try:
        snapshot_count = count_snapshots_jsonl(HISTORICAL_DATA_PATH)
        data_points = count_data_points_jsonl(HISTORICAL_DATA_PATH)
        return jsonify({
            "count": snapshot_count,
            "snapshots": snapshot_count,
            "data_points": data_points
        })
    except Exception as e:
        return jsonify({"count": 0, "snapshots": 0, "data_points": 0})

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

@app.route('/api/broadcast', methods=['POST'])
def broadcast_email():
    """Send a one-time broadcast email to all subscribers. Requires secret key."""
    data = request.get_json(force=True) if request.data else {}
    secret = data.get('secret', '')
    if secret != EMAIL_SECRET_SALT:
        return jsonify({'error': 'Unauthorized'}), 403

    subscribers = read_subscribers()
    if not subscribers:
        return jsonify({'success': True, 'message': 'No subscribers'})

    count = 0
    for sub in subscribers:
        email = sub['email']
        token = make_unsub_token(email)
        unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

        text = """
Thanks for subscribing to IL9Cast!

We're working on some exciting new features, including the possibility of building a precinct-by-precinct model for the IL-9 primary.

Stay tuned - more updates coming soon.

View Live Markets: """ + SITE_BASE_URL + """markets
"""

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
        <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
                <tr><td align="center" style="padding: 40px 20px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                        <!-- Logo -->
                        <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                            <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                                <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                            </h1>
                            <p style="margin: 0 0 20px 0; color: #888; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;">A Quick Update</p>
                        </td></tr>

                        <!-- Message -->
                        <tr><td style="padding: 32px 40px;">
                            <p style="margin: 0 0 16px 0; color: #F0EFEB; font-size: 16px; line-height: 1.7;">Thanks for subscribing to IL9Cast.</p>
                            <p style="margin: 0 0 16px 0; color: #ccc; font-size: 15px; line-height: 1.7;">We're working on some new things behind the scenes, including the possibility of building a <strong style="color: #31B0B5;">precinct-by-precinct model</strong> for the IL-9 primary.</p>
                            <p style="margin: 0; color: #ccc; font-size: 15px; line-height: 1.7;">Stay tuned &mdash; more updates coming soon.</p>
                        </td></tr>

                        <!-- CTA -->
                        <tr><td style="padding: 0 40px 32px 40px; text-align: center;">
                            <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                        </td></tr>

                        <!-- Footer -->
                        <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                            <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                        </td></tr>
                    </table>
                </td></tr>
            </table>
        </body>
        </html>
        """
        if send_email(email, 'IL9Cast - New Things Coming', html, text):
            count += 1

    return jsonify({'success': True, 'message': f'Broadcast sent to {count} subscriber(s)'})


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
            kalshi_response = requests.get('https://api.elections.kalshi.com/trade-api/v2/events/KXIL9D-26', timeout=10)
            kalshi_response.raise_for_status()
            kalshi_markets = kalshi_response.json().get('markets', [])

            for market in kalshi_markets:
                display_name = market.get('yes_sub_title') or market.get('subtitle') or market.get('title', '')
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
                        # Multiply by spread (not a fixed constant) so the shift is
                        # proportional to the spread width and can never leave [bid, ask].
                        price_shift = offset_from_mid * spread * spread_factor
                        liquidity_price = max(yes_bid, min(yes_ask, midpoint + price_shift))

                    kalshi_data[name] = {
                        'last_price': last_price,
                        'midpoint': midpoint,
                        'liquidity': liquidity_price,
                        'yes_bid': yes_bid,
                        'yes_ask': yes_ask,
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

                kalshi_bid = kalshi_info.get('yes_bid', 0)
                kalshi_ask = kalshi_info.get('yes_ask', 0)
                has_two_sided_book = kalshi_bid > 0 and kalshi_ask > 0
                has_unlocked_spread = kalshi_ask > kalshi_bid

                # Treat Kalshi as inactive when the order book is one-sided/locked.
                # A non-zero last trade with no current bid can be stale and can
                # otherwise overstate thinly traded candidates.
                has_kalshi = has_two_sided_book and has_unlocked_spread and (kalshi_last > 0 or kalshi_mid > 0)

                if has_kalshi:
                    last_outside_spread = (
                        kalshi_bid > 0 and kalshi_ask > 0 and
                        (kalshi_last > kalshi_ask or kalshi_last < kalshi_bid)
                    )
                    if last_outside_spread:
                        # Throttled: reduce last_price weight, boost spread-based components
                        aggregate = (0.40 * manifold_prob) + (0.20 * kalshi_last) + (0.28 * kalshi_mid) + (0.12 * kalshi_liq)
                        print(f"  [Spread throttle] {candidate_key}: last={kalshi_last:.1f} outside [{kalshi_bid:.1f}, {kalshi_ask:.1f}]")
                    else:
                        # Normal weights
                        aggregate = (0.40 * manifold_prob) + (0.42 * kalshi_last) + (0.12 * kalshi_mid) + (0.06 * kalshi_liq)
                else:
                    if (kalshi_last > 0 or kalshi_mid > 0) and (not has_two_sided_book or not has_unlocked_spread):
                        print(
                            f"  [Kalshi ignored] {candidate_key}: "
                            f"non-actionable book bid={kalshi_bid:.1f}, ask={kalshi_ask:.1f}, "
                            f"last={kalshi_last:.1f}"
                        )
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
    # Use an inter-process file lock so only one worker runs the scheduler.
    from threading import Thread
    import time
    import fcntl
    import os

    _scheduler_lock_file = None

    def _acquire_scheduler_lock():
        """Acquire an exclusive lock; return True only for the elected worker."""
        global _scheduler_lock_file
        lock_path = os.environ.get('IL9_SCHEDULER_LOCK_PATH', '/tmp/il9_scheduler.lock')
        _scheduler_lock_file = open(lock_path, 'w')
        try:
            fcntl.flock(_scheduler_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _scheduler_lock_file.write(str(os.getpid()))
            _scheduler_lock_file.flush()
            return True
        except BlockingIOError:
            return False

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

            # Check if it's time for daily summary (8 AM Central Time)
            try:
                from zoneinfo import ZoneInfo
                ct_now = datetime.now(ZoneInfo('America/Chicago'))
                if ct_now.hour == 8 and ct_now.minute < 3:
                    send_daily_summary()
            except Exception as e:
                print(f"Error sending daily summary: {e}")

    # Start scheduler thread only in the elected worker.
    if _acquire_scheduler_lock():
        print(f"[{datetime.now().isoformat()}] Scheduler lock acquired in pid={os.getpid()}")
        thread = Thread(target=scheduler_thread, daemon=True)
        thread.start()
    else:
        print(f"[{datetime.now().isoformat()}] Scheduler disabled in pid={os.getpid()} (lock held by another worker)")

if __name__ == '__main__':
    # Use debug mode only for local development
    import os
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
