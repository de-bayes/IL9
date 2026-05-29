"""
HTTP performance middleware for IL9Cast (Railway / Gunicorn).

- Security headers (lightweight, CDN-safe)
- gzip for JSON/text responses over a size threshold
- Long-lived cache hints for versioned static assets (?v= mtime)
"""

import gzip

from flask import request

_MIN_GZIP_BYTES = 512
_GZIP_TYPES = frozenset({
    'application/json',
    'application/geo+json',
    'text/html',
    'text/css',
    'text/javascript',
    'application/javascript',
})


def init_performance(app):
    """Register after_request hooks on the Flask app."""

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        return response

    @app.after_request
    def _gzip_json(response):
        if response.direct_passthrough:
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if request.path == '/api/model/precincts' or response.status_code == 304:
            return response
        if response.mimetype not in _GZIP_TYPES:
            return response
        if response.headers.get('Content-Encoding'):
            return response
        accept = (request.headers.get('Accept-Encoding') or '').lower()
        if 'gzip' not in accept:
            return response

        data = response.get_data()
        if len(data) < _MIN_GZIP_BYTES:
            return response

        compressed = gzip.compress(data, compresslevel=6)
        if len(compressed) >= len(data):
            return response

        response.set_data(compressed)
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = len(compressed)
        response.headers['Vary'] = 'Accept-Encoding'
        return response

    @app.after_request
    def _static_immutable_cache(response):
        """Versioned static files (?v=) can be cached for a year."""
        if not request.path.startswith('/static/'):
            return response
        qs = request.query_string.decode('utf-8', errors='ignore')
        if 'v=' not in qs:
            return response
        if response.status_code == 200 and response.mimetype in (
            'text/css', 'application/javascript', 'text/javascript',
            'image/png', 'image/jpeg', 'image/webp', 'image/svg+xml',
            'font/woff2', 'application/font-woff2',
        ):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response
