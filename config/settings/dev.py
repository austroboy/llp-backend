"""Development settings — local machine."""
from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Show full SQL on demand
LOGGING["loggers"]["django.db.backends"]["level"] = "WARNING"  # type: ignore[name-defined]

# Looser CORS in dev
CORS_ALLOW_ALL_ORIGINS = True

# Use console email backend
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Django Debug Toolbar — optional
try:
    import debug_toolbar  # noqa: F401
    INSTALLED_APPS += ["debug_toolbar"]  # type: ignore[name-defined]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")  # type: ignore[name-defined]
    INTERNAL_IPS = ["127.0.0.1"]
except ImportError:
    pass
