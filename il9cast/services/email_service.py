"""Auto-extracted from app.py — IL9Cast service module."""

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

BACKUP_EMAIL = os.environ.get('BACKUP_EMAIL', 'rymccomb1@icloud.com')

def send_csv_backup_email():
    """Send CSV backup of all historical data via Resend with attachment."""
    import io, csv, base64
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if not snapshots:
            print(f"[{datetime.now().isoformat()}] CSV backup skipped: no data")
            return False

        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['timestamp', 'candidate', 'probability', 'hasKalshi', 'interpolated'])
        for snapshot in snapshots:
            timestamp = snapshot.get('timestamp', '')
            is_interpolated = 'true' if snapshot.get('interpolated', False) else 'false'
            for candidate in snapshot.get('candidates', []):
                name = candidate.get('name', '')
                prob = _safe_float(candidate.get('probability', 0), 0.0)
                has_kalshi = 'true' if candidate.get('hasKalshi', False) else 'false'
                writer.writerow([timestamp, name, f'{prob:.1f}', has_kalshi, is_interpolated])
        csv_content = output.getvalue()
        output.close()

        snap_count = len(snapshots)
        first_ts = snapshots[0].get('timestamp', 'unknown') if snapshots else 'none'
        last_ts = snapshots[-1].get('timestamp', 'unknown') if snapshots else 'none'
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')

        # Resend attachment: base64-encoded CSV
        csv_b64 = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')

        if not RESEND_API_KEY:
            print(f"[{datetime.now().isoformat()}] CSV backup email skipped (no RESEND_API_KEY)")
            return False

        payload = {
            'from': RESEND_FROM,
            'to': [BACKUP_EMAIL],
            'subject': f'IL9Cast Data Backup - {now_str} ({snap_count} snapshots)',
            'html': (
                f'<h3>IL9Cast Automated Data Backup</h3>'
                f'<p><strong>Snapshots:</strong> {snap_count}</p>'
                f'<p><strong>Range:</strong> {first_ts} → {last_ts}</p>'
                f'<p><strong>CSV Size:</strong> {len(csv_content):,} bytes</p>'
                f'<p>Attached: <code>il9cast_backup_{now_str}.csv</code></p>'
                f'<hr><p style="color:#888;font-size:12px">Sent automatically every 4 hours from IL9Cast</p>'
            ),
            'attachments': [{
                'filename': f'il9cast_backup_{now_str}.csv',
                'content': csv_b64
            }]
        }

        resp = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=30
        )
        if resp.status_code in (200, 201):
            print(f"[{datetime.now().isoformat()}] CSV backup sent to {BACKUP_EMAIL} ({snap_count} snapshots, {len(csv_content):,} bytes)")
            return True
        else:
            print(f"[{datetime.now().isoformat()}] CSV backup email failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] CSV backup email error: {e}")
        return False
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

