"""Path and environment configuration for IL9Cast."""
import os
import warnings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_data_path(filename='historical_snapshots.jsonl'):
    """Resolve the correct data directory, checking for actual data files."""
    configured_dir = os.environ.get('DATA_DIR', '').strip()
    if configured_dir:
        return os.path.join(configured_dir, filename)

    local_data = os.path.join(BASE_DIR, 'data')
    for candidate_dir in ['/app/data', '/data', local_data]:
        candidate_path = os.path.join(candidate_dir, filename)
        gz_path = candidate_path + '.gz'
        if os.path.exists(gz_path) or os.path.exists(candidate_path):
            return candidate_path

    return os.path.join(local_data, filename)


HISTORICAL_DATA_PATH = resolve_data_path('historical_snapshots.jsonl')
SEED_DATA_PATH = os.path.join(BASE_DIR, 'data', 'seed_snapshots.json')
LEGACY_JSON_PATH = os.path.join(BASE_DIR, 'data', 'historical_snapshots.json')
REPO_CSV_PATH = os.path.join(BASE_DIR, 'il9cast_historical_data.csv')
SUBSCRIBERS_PATH = resolve_data_path('email_subscribers.jsonl')

RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', 'alerts@il9.org')
RESEND_FROM = f"IL9Cast <{RESEND_FROM_EMAIL}>"
EMAIL_SECRET_SALT = os.environ.get('EMAIL_SECRET_SALT')
if not EMAIL_SECRET_SALT:
    warnings.warn('EMAIL_SECRET_SALT is not set! Email tokens will be insecure.', stacklevel=1)
    EMAIL_SECRET_SALT = 'il9cast-change-me'

ADMIN_API_TOKEN = os.environ.get('ADMIN_API_TOKEN')
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'https://il9.org/')
