#!/usr/bin/env python3
"""Apply performance architecture patches to app.py (idempotent)."""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
t = APP.read_text()

if "init_performance" not in t:
    t = t.replace(
        "app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400  # Cache static files for 1 day (safe due to ?v= cache-buster)\n",
        "app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400  # Cache static files for 1 day (safe due to ?v= cache-buster)\n\n"
        "from performance import init_performance\n"
        "init_performance(app)\n",
        1,
    )

if "get_jsonl_raw_lines" not in t:
    block = '''
_jsonl_lines_cache = {'size': None, 'lines': None}
_jsonl_lines_lock = _threading.Lock()


def _jsonl_data_size():
    """Byte size of the active snapshots file (gz preferred)."""
    gz_path = HISTORICAL_DATA_PATH + '.gz'
    try:
        if os.path.exists(gz_path):
            return os.path.getsize(gz_path)
        if os.path.exists(HISTORICAL_DATA_PATH):
            return os.path.getsize(HISTORICAL_DATA_PATH)
    except OSError:
        pass
    return 0


def get_jsonl_raw_lines():
    """Return all JSONL lines, cached until the underlying file grows."""
    size = _jsonl_data_size()
    with _jsonl_lines_lock:
        if _jsonl_lines_cache['size'] == size and _jsonl_lines_cache['lines'] is not None:
            return _jsonl_lines_cache['lines']
    try:
        f = _open_jsonl(HISTORICAL_DATA_PATH)
        if f is None:
            return []
        with f:
            lines = [line.strip() for line in f if line.strip()]
    except (IOError, OSError):
        return []
    with _jsonl_lines_lock:
        _jsonl_lines_cache['size'] = size
        _jsonl_lines_cache['lines'] = lines
    return lines


'''
    t = t.replace(
        "_chart_compute_locks_lock = _threading.Lock()  # guards _chart_compute_locks dict\n\n\n"
        "def _get_compute_lock(cache_key):",
        "_chart_compute_locks_lock = _threading.Lock()  # guards _chart_compute_locks dict\n" + block + "def _get_compute_lock(cache_key):",
        1,
    )

if "source_lines = get_jsonl_raw_lines()" not in t:
    t = t.replace(
        """    if raw_lines is not None:
        source_lines = raw_lines
    else:
        try:
            f = _open_jsonl(HISTORICAL_DATA_PATH)
            if f is None:
                return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}
            with f:
                source_lines = [line.strip() for line in f if line.strip()]
        except (IOError, OSError):
            return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}""",
        """    if raw_lines is not None:
        source_lines = raw_lines
    else:
        source_lines = get_jsonl_raw_lines()
        if not source_lines:
            return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}""",
        1,
    )

if "raw_lines = get_jsonl_raw_lines()" not in t:
    t = t.replace(
        """    # Read raw lines once, re-parse for each period (faster than deepcopy)
    try:
        f = _open_jsonl(HISTORICAL_DATA_PATH)
        if f is None:
            return
        with f:
            raw_lines = [line.strip() for line in f if line.strip()]
    except (IOError, OSError):
        return
    if not raw_lines:""",
        """    raw_lines = get_jsonl_raw_lines()
    if not raw_lines:""",
        1,
    )

if "def healthz" not in t:
    t = t.replace(
        "# Routes\n@app.route('/')\ndef landing():",
        """# Routes
@app.route('/healthz')
def healthz():
    \"\"\"Lightweight health check for Railway (no template render).\"\"\"
    return jsonify({'status': 'ok'}), 200


@app.route('/')
def landing():""",
        1,
    )

if "api_model_precincts" not in t:
    t = t.replace(
        """@app.route('/odds')
def odds():
    return render_template('odds.html')

@app.route('/model/methodology')""",
        """@app.route('/odds')
def odds():
    return render_template('odds.html')


@app.route('/api/model/precincts')
def api_model_precincts():
    \"\"\"
    Serve precinct GeoJSON with gzip when supported (~1.6 MB -> ~250 KB).
    Cached immutably — geometry does not change on the archive site.
    \"\"\"
    model_dir = os.path.join(app.static_folder, 'model')
    gz_path = os.path.join(model_dir, 'il9_precinct_model.geojson.gz')
    plain_path = os.path.join(model_dir, 'il9_precinct_model.geojson')
    accept = request.headers.get('Accept-Encoding', '').lower()
    if 'gzip' in accept and os.path.exists(gz_path):
        resp = send_file(gz_path, mimetype='application/geo+json')
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        resp.headers['Vary'] = 'Accept-Encoding'
        return resp
    if os.path.exists(plain_path):
        resp = send_file(plain_path, mimetype='application/geo+json')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
    return jsonify({'error': 'Precinct GeoJSON not found'}), 404


@app.route('/model/methodology')""",
        1,
    )

if "_threading.Thread(target=_prewarm_chart_cache" in t:
    t = t.replace(
        "# Pre-warm chart cache so first visitor gets instant response\n"
        "_threading.Thread(target=_prewarm_chart_cache, daemon=True).start()",
        """# Pre-warm chart cache at startup (archive: one-time cost, all chart requests hit cache).
try:
    _prewarm_chart_cache()
except Exception as _prewarm_err:
    print(f"[{datetime.now().isoformat()}] Chart cache prewarm skipped: {_prewarm_err}")""",
        1,
    )

APP.write_text(t)
print("app.py patched OK")
