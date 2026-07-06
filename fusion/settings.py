"""
Django settings for fusion project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env as early as possible
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# DEBUG must be defined before Sentry and other production checks
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

# ==================== SENTRY (only in production) ====================
SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN and not DEBUG:
    import sentry_sdk
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=False,
    )

# ==================== SECURITY ====================
_DEV_SECRET_KEY = 'dev-only-insecure-secret-key-for-local-testing'
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = _DEV_SECRET_KEY
    else:
        raise ValueError("SECRET_KEY is missing from .env file!")

PASSWORD_RESET_DOMAIN = os.getenv('PASSWORD_RESET_DOMAIN', '').strip()

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if h.strip()]
if PASSWORD_RESET_DOMAIN and PASSWORD_RESET_DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(PASSWORD_RESET_DOMAIN)

if not DEBUG:
    if SECRET_KEY == _DEV_SECRET_KEY:
        raise ValueError("Production requires a unique SECRET_KEY — do not use the dev fallback.")
    if '*' in ALLOWED_HOSTS:
        raise ValueError("ALLOWED_HOSTS must not contain '*' in production.")


def _build_csrf_trusted_origins(hosts, debug=False):
    """Mirror ALLOWED_HOSTS into CSRF_TRUSTED_ORIGINS so login POSTs aren't rejected."""
    origins = [
        'https://fusionbeta.com',
        'https://www.fusionbeta.com',
        'https://resetpassword.fusionbeta.com',
    ]
    for host in hosts:
        if not host or host == '*':
            continue
        if debug:
            origins.extend([
                f'http://{host}',
                f'https://{host}',
                f'http://{host}:8000',
                f'https://{host}:8000',
            ])
        else:
            origins.append(f'https://{host}')
    return list(dict.fromkeys(origins))


CSRF_TRUSTED_ORIGINS = _build_csrf_trusted_origins(ALLOWED_HOSTS, DEBUG)
CSRF_FAILURE_VIEW = 'core.views.csrf_failure'

# ==================== PRODUCTION SECURITY (Fixed for Railway) ====================
if not DEBUG:
    # Railway + HTTPS fixes
    SECURE_SSL_REDIRECT = False
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True
    USE_X_FORWARDED_PORT = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False

# Session hardening (all environments)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 14 days
SESSION_SAVE_EVERY_REQUEST = True

# Content-Security-Policy: allow trusted CDNs used in base.html (Tailwind CDN needs unsafe-eval).
SECURITY_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.tailwindcss.com https://unpkg.com https://cdnjs.cloudflare.com 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' https://cdnjs.cloudflare.com 'unsafe-inline'; "
    "font-src 'self' https://cdnjs.cloudflare.com data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# ==================== DATABASE ====================
if all([os.getenv('DB_NAME'), os.getenv('DB_USER'), os.getenv('DB_PASSWORD'), os.getenv('DB_HOST')]):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME'),
            'USER': os.getenv('DB_USER'),
            'PASSWORD': os.getenv('DB_PASSWORD'),
            'HOST': os.getenv('DB_HOST'),
            'PORT': os.getenv('DB_PORT', '5432'),
            'OPTIONS': {'sslmode': 'require'},
        }
    }
else:
    # Local development fallback
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ==================== APPLICATION DEFINITION ====================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'axes',
    'anymail',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'core.middleware.SecurityHeadersMiddleware',
    'axes.middleware.AxesMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.TimezoneMiddleware',
]

ROOT_URLCONF = 'fusion.urls'

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
                'core.context_processors.unread_notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'fusion.wsgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ==================== BRUTE-FORCE PROTECTION (django-axes) ====================
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
AXES_RESET_ON_SUCCESS = True

# Rate limiting + security event cache (used by core.ratelimit)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'fusion-security-cache',
    }
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Chicago'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Auth settings
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
LOGIN_URL = 'login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==================== AUTHENTICATION BACKENDS ====================
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'axes.backends.AxesStandaloneBackend',
]

# ==================== LOGGING ====================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module}.{funcName}:{lineno} - {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'fusion': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': True,
        },
        'fusion.security': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'axes': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# ==================== EMAIL ====================
# Configured via environment variables for security (works in Railway + local dev).
# - With RESEND_API_KEY set: uses Resend via django-anymail (recommended for production).
# - In development (no RESEND_API_KEY and DEBUG=True): console backend (emails printed to terminal).
# - Legacy SMTP: set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend + HOST/USER/PASS.
# Users enter their *email* on the password reset page (already set up that way in templates).
RESEND_API_KEY = (os.getenv('RESEND_API_KEY') or '').strip()

if os.getenv('EMAIL_BACKEND'):
    EMAIL_BACKEND = os.getenv('EMAIL_BACKEND')
elif RESEND_API_KEY:
    EMAIL_BACKEND = 'anymail.backends.resend.EmailBackend'
else:
    EMAIL_BACKEND = (
        'django.core.mail.backends.console.EmailBackend' if DEBUG
        else 'django.core.mail.backends.smtp.EmailBackend'
    )

DEFAULT_FROM_EMAIL = (os.getenv('DEFAULT_FROM_EMAIL') or 'onboarding@resend.dev').strip()

if RESEND_API_KEY:
    ANYMAIL = {
        'RESEND_API_KEY': RESEND_API_KEY,
    }

if EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
    EMAIL_HOST = os.getenv('EMAIL_HOST')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
    EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
    EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')