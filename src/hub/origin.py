"""Shared same-origin policy for HTTP writes and WebSocket connections."""
import os
from urllib.parse import urlsplit


def origin_key(value):
    """Return a comparable HTTP origin, rejecting paths and malformed values."""
    try:
        if not value or any(c.isspace() or ord(c) < 32 for c in value):
            return None
        url = urlsplit(value)
        if (url.scheme not in ('http', 'https') or not url.hostname
                or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment):
            return None
        port = url.port
        if port == 0:
            return None
        return url.scheme, url.hostname.lower(), port or (443 if url.scheme == 'https' else 80)
    except ValueError:
        return None


def origin_allowed(connection):
    origin = connection.headers.get('origin')
    # Non-browser clients retain the existing header/session requirements.
    if origin is None:
        return True
    configured = os.getenv('SENTINEL_ORIGIN', '').strip()
    if not configured or configured.lower() == 'auto':
        scheme = {'ws': 'http', 'wss': 'https'}.get(connection.url.scheme, connection.url.scheme)
        expected = f'{scheme}://{connection.url.netloc}'
    else:
        expected = configured
    key = origin_key(origin)
    return key is not None and key == origin_key(expected)
