from pathlib import Path
from datetime import timedelta
from decouple import config, Csv
import sentry_sdk

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=Csv())

ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
MISTRAL_API_KEY = config('MISTRAL_API_KEY', default='')
GEMINI_API_KEY = config('GEMINI_API_KEY', default='')
FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY', default='')

# Blank in an environment with no Sentry project configured (e.g. CI) just
# means the SDK stays uninitialized — no crash reporting, not a hard error.
SENTRY_DSN = config('SENTRY_DSN', default='')
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment='development' if DEBUG else 'production',
        # No IPs/request headers/user data sent — this app handles chat
        # conversations, and that's more than crash reporting needs.
        send_default_pii=False,
        # Routine application logs (logger.info/.warning) stay local via the
        # LOGGING config below; Sentry only sees unhandled exceptions and
        # explicit logger.exception()/error() calls, which the SDK captures
        # as issues regardless of this flag.
        enable_logs=False,
        traces_sample_rate=1.0,
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
    )

# Used to build absolute URLs for generated media (e.g. AI-generated images)
# from contexts with no HTTP request object, like the WebSocket consumer.
# .strip() guards against a stray trailing newline/space from a pasted
# platform env var value -- hit this for real once already: it survived
# .rstrip('/') (rstrip only strips '/', not whitespace) all the way into a
# redirect Location header as a literal %0A, which Chrome then rejected
# outright with ERR_INVALID_REDIRECT.
BACKEND_URL = config('BACKEND_URL', default='http://localhost:8000').strip()
# Used to build links that must point at the frontend (password reset,
# email confirmation) rather than this backend.
FRONTEND_URL = config('FRONTEND_URL', default='http://localhost:3000').strip()

RESEND_API_KEY = config('RESEND_API_KEY', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@sparqup.fr')

GOOGLE_OAUTH_CLIENT_ID = config('GOOGLE_OAUTH_CLIENT_ID', default='')
GOOGLE_OAUTH_CLIENT_SECRET = config('GOOGLE_OAUTH_CLIENT_SECRET', default='')
GITHUB_OAUTH_CLIENT_ID = config('GITHUB_OAUTH_CLIENT_ID', default='')
GITHUB_OAUTH_CLIENT_SECRET = config('GITHUB_OAUTH_CLIENT_SECRET', default='')

AUTH_USER_MODEL = 'users.CustomUser'

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'channels',
    'corsheaders',
    'encrypted_model_fields',
    'storages',
    'pgvector.django',
    'core',
    'users',
    'assistants',
    'projects',
    'threads',
    'chat_messages',
    'librarian',
    'project_files',
    'keys',
    'mcp_server',
    'mcp_client',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.permissions_policy_middleware',
]

ROOT_URLCONF = 'backend_sparqhub_django.urls'
ASGI_APPLICATION = 'backend_sparqhub_django.asgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DATABASE_NAME', default='mydb'),
        'USER': config('DATABASE_USER', default='postgres'),
        'PASSWORD': config('DATABASE_PASSWORD'),
        'HOST': config('DATABASE_HOST', default='db'),
        'PORT': config('DATABASE_PORT', default='5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Generated images (image_providers) go to Cloudflare R2 (S3-compatible) when
# configured; local disk otherwise (dev, CI, or before R2 is set up) — local
# disk is ephemeral on most hosting platforms, wiped on every deploy/restart.
AWS_ACCESS_KEY_ID = config('R2_ACCESS_KEY_ID', default='')
AWS_SECRET_ACCESS_KEY = config('R2_SECRET_ACCESS_KEY', default='')
AWS_STORAGE_BUCKET_NAME = config('R2_BUCKET_NAME', default='')
AWS_S3_ENDPOINT_URL = config('R2_ENDPOINT_URL', default='')
AWS_S3_CUSTOM_DOMAIN = config('R2_PUBLIC_DOMAIN', default='')
AWS_S3_REGION_NAME = 'auto'
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_ADDRESSING_STYLE = 'virtual'
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = False

STORAGES = {
    'default': {
        'BACKEND': 'storages.backends.s3.S3Storage' if AWS_ACCESS_KEY_ID else 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Note: DATA_UPLOAD_MAX_MEMORY_SIZE (Django default 2.5MB) does not apply
# here — that setting excludes multipart file upload data, only bounding
# non-file POST fields.
PROJECT_FILE_MAX_SIZE_BYTES = config('PROJECT_FILE_MAX_SIZE_BYTES', default=20 * 1024 * 1024, cast=int)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

_REDIS_URL = config('REDIS_URL', default='redis://redis:6379/0')

CELERY_BROKER_URL = _REDIS_URL
CELERY_RESULT_BACKEND = _REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            # channels_redis long-polls for messages via BZPOPMIN with its own
            # brpop_timeout (5s) — a normal, expected wait, not an error. With
            # no explicit socket_timeout/retry_on_timeout, any transient read
            # hiccup on that connection (observed repeatedly against Render's
            # free-tier Key Value instance) raises redis.exceptions.TimeoutError
            # uncaught, which crashes the entire ASGI WebSocket connection.
            'hosts': [{
                'address': _REDIS_URL,
                'socket_timeout': 20,
                'socket_keepalive': True,
                'retry_on_timeout': True,
            }],
        },
    },
}

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', cast=Csv())
CSRF_TRUSTED_ORIGINS = config('CORS_ALLOWED_ORIGINS', cast=Csv())

# Blank (the default) means host-only cookies — correct for local dev, where
# the frontend/backend hosts (localhost:3000/8000) share no parent domain to
# scope to. In prod, where frontend and backend live on sibling subdomains of
# the same domain (e.g. app.example.com / api.example.com), this must be set
# to the shared parent (e.g. ".example.com") — otherwise a cookie set by the
# backend is host-only to its own domain and never sent to the frontend's,
# even though both are "same-site" for SameSite-cookie purposes.
COOKIE_DOMAIN = config('COOKIE_DOMAIN', default='') or None

CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_DOMAIN = COOKIE_DOMAIN
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG

# Render terminates TLS at its edge and forwards plain HTTP internally, so
# request.is_secure() (and therefore SECURE_SSL_REDIRECT) would never see a
# request as secure without being told which header carries the original
# scheme — this is Render's (and most PaaS's) standard proxy header.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = not DEBUG

# 2 years, matching the value Vercel already sets automatically for the
# frontend. No preload: submitting to browsers' HSTS preload list is close
# to irreversible (removal takes months to propagate), and would force every
# current and future subdomain of sparqup.fr onto HTTPS forever — not a
# decision to make implicitly via a header default.
SECURE_HSTS_SECONDS = 63072000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False

SIMPLE_JWT = {
    'AUTH_COOKIE': 'access_token',
    'AUTH_COOKIE_SECURE': not DEBUG,
    'AUTH_COOKIE_HTTP_ONLY': True,
    'AUTH_COOKIE_SAMESITE': 'Lax',
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# Shared with the WebSocket chat path's own rate check (core/rate_limit.py,
# used from chat_messages/consumers.py) — a single source of truth so the
# HTTP (SendMessageAPIView) and WS budgets for "sending a chat message"
# don't silently drift apart.
CHAT_MESSAGES_PER_MINUTE = 30

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'users.authentication.CookieJWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'EXCEPTION_HANDLER': 'core.exceptions.api_exception_handler',
    # ScopedRateThrottle only throttles a view that opts in via its own
    # throttle_scope attribute — safe to set globally, it's a no-op on every
    # view that doesn't set one. Keys by request.user.pk when authenticated,
    # falling back to IP only for pre-auth endpoints (login/register/
    # password-reset) — the only cases where no user identity exists yet to
    # key on instead, so a shared IP (NAT, VPN, mobile carrier) can't be
    # mistaken for a single abusive user on every other throttled endpoint.
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.ScopedRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'auth': '10/min',
        'chat': f'{CHAT_MESSAGES_PER_MINUTE}/min',
        'uploads': '20/hour',
    },
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
