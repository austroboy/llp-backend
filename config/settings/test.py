"""Test settings — used by pytest. Overrides slow / external deps."""
from .base import *  # noqa: F401, F403

DEBUG = False
ALLOWED_HOSTS = ["*"]

# In-memory cache; no Redis required for unit tests
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tests",
    }
}

# Disable Channels Redis in tests
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Eager Celery — tasks run synchronously in tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Faster password hashing for tests
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# No external AI calls in tests
ANTHROPIC_API_KEY = "test-key"
GEMINI_API_KEY = "test-key"
ENABLE_RESPONSE_CACHE = False
ENABLE_VERIFIER_LOOP = False
