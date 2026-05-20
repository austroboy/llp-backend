"""Production settings — AWS deployment."""
from .base import *  # noqa: F401, F403

DEBUG = False

# All security knobs on
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Force S3 storage
USE_S3_STORAGE = True

# Stricter rate limits
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # type: ignore[name-defined]
    "anon": "30/min",
    "user": "60/min",
}

# Structured JSON logs in prod
LOG_FORMAT = "json"
for handler in LOGGING["handlers"].values():  # type: ignore[name-defined]
    handler["formatter"] = "json"
