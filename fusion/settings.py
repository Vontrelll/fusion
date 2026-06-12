"""
Django settings for fusion project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env as early as possible
load_dotenv()

import sentry_sdk

# ==================== SENTRY (error tracking) ====================
# Only initialize if SENTRY_DSN is provided (recommended for production).
# Never commit real DSNs. Set via environment variable.
SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=True,   # includes some request data; adjust per your privacy policy
    )
#-----------------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

# DEBUG must be defined early because it is used in SECRET_KEY and other logic
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

# ==================== SECURITY ====================
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        # Safe development / test fallback only (never use in production)
        SECRET_KEY = 'dev-only-insecure-secret-key-for-local-testing'
    else:
        raise ValueError("SECRET_KEY is missing from .env file! Please add it.")

# Database credentials check (only required for Postgres/Supabase deployments)
DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if h.strip()]

# ==================== APPLICATION DEFINITION ====================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'axes',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
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

# Database
# ==================== DATABASE ====================
# Uses Postgres/Supabase when DB_* env vars are provided (production).
# Falls back to local SQLite for development and running tests when those vars are absent.
if DB_NAME and DB_USER and DB_PASSWORD and DB_HOST:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': DB_NAME,
            'USER': DB_USER,
            'PASSWORD': DB_PASSWORD,
            'HOST': DB_HOST,
            'PORT': os.getenv('DB_PORT', '5432'),
            'OPTIONS': {
                'sslmode': 'require',
            },
        }
    }
else:
    # Local development / test fallback (matches the existing db.sqlite3 in the repo)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Chicago'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# For production static file serving
if not DEBUG:
    STATIC_ROOT = BASE_DIR / 'staticfiles'

# Auth settings
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
LOGIN_URL = 'login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==================== PRODUCTION SECURITY ====================
if not DEBUG:
    # These settings only activate in production (DEBUG=False)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000          # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
else:
    # Development settings - explicitly disable secure cookies
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False


# ==================== AUTHENTICATION BACKENDS ====================
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'axes.backends.AxesStandaloneBackend',
]

# django-axes: DIFFERENT STANDARDS FOR ADMIN vs APP USERS
# - Strict protection for the Django admin site (low failure limit, locks quickly).
# - Lenient / additional attempts for normal app users (parents & owners) because we use
#   a custom login view. AXES_ONLY_ADMIN_SITE=True means axes ONLY protects the admin login
#   at /secret-admin-.../ and does NOT lock or rate-limit the app's /login/ for end users.
# This gives owners/parents "additional login attempts" while keeping the secret admin page secure.
AXES_ONLY_ADMIN_SITE = True
AXES_FAILURE_LIMIT = 3          # strict for admin
AXES_COOLOFF_TIME = 10          # minutes lock for admin after failures
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]



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
        'fusion.deletion': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}
}
# ==================== EMAIL ====================
# Development: emails are printed to the console (great for testing password resets, etc.).
# Production: set the following environment variables and the backend will switch to SMTP.
# Recommended services: SendGrid, Mailgun, Amazon SES, Postmark, etc.
#
# Required for production:
#   EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS (or EMAIL_USE_SSL)
#   DEFAULT_FROM_EMAIL
#
# Example .env for prod:
#   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
#   EMAIL_HOST=smtp.sendgrid.net
#   EMAIL_PORT=587
#   EMAIL_HOST_USER=apikey
#   EMAIL_HOST_PASSWORD=your-real-sendgrid-key
#   EMAIL_USE_TLS=True
#   DEFAULT_FROM_EMAIL="Fusion <no-reply@yourdomain.com>"

if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    # Production email (falls back to console if no EMAIL_HOST configured, to avoid total breakage)
    EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
    EMAIL_HOST = os.getenv('EMAIL_HOST', '')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
    DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Fusion <no-reply@example.com>')