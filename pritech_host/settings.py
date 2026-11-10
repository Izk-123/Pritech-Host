"""
Django settings for Pritech Host.

Django  : 5.2.17
Python  : 3.10+
Docs    : https://docs.djangoproject.com/en/5.2/topics/settings/

Environment-aware configuration:
  - All sensitive values (DB, SECRET_KEY, API keys) come from `.env`.
  - File lives at project root; DO NOT COMMIT `.env` to source control.
  - Safe defaults let this file run locally even without a `.env`.

Organisation
------------
 1. Paths & environment variables
 2. Core settings & constants
 3. Application definition
 4. Templates
 5. Database
 6. Authentication
 7. Django-allauth (login, signup, Google SSO, password reset)
 8. Internationalisation
 9. Static & media
10. Email (cPanel SMTP)
11. Third-party: PayChangu
12. Third-party: HestiaCP
13. Security headers
14. Logging
15. Cache (Redis-backed, shared across Gunicorn workers)
16. Django Unfold (admin UI)
17. Celery (background workers — email, provisioning, dunning)
18. Celery Beat (scheduled tasks — daily renewal/dunning scan)
19. Misc

Deploy target:
  Domain       : pritechhost.com
  WWW domain   : www.pritechhost.com
  Server IP    : 204.168.251.91
  WSGI app     : pritech_host.wsgi:application
"""

import os
from pathlib import Path

import environ
from celery.schedules import crontab


# ==============================================================================
# 1. PATHS & ENVIRONMENT
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# django-environ defaults.
# Every bool() / list() needs a default so a missing .env key can't silently
# flip a security setting to the wrong value.
env = environ.Env(
    # --- Core ---
    DEBUG=(bool, False),

    # --- Security toggles (all OFF by default; enable via .env once HTTPS
    #     is confirmed working on the live server) ---
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_SECURE=(bool, False),
    CSRF_COOKIE_SECURE=(bool, False),
    SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, False),
    SECURE_HSTS_PRELOAD=(bool, False),

    # --- Feature flags ---
    HESTIA_MOCK=(bool, True),

    # --- Email ---
    EMAIL_USE_SSL=(bool, True),
    EMAIL_USE_TLS=(bool, False),

    # --- Celery / dunning ---
    # Toggle the daily dunning scheduler on/off without redeploying code.
    # Useful during maintenance windows or while triaging false-positives.
    DUNNING_ENABLED=(bool, True),
)

# Load `.env` if it exists. Safe to skip in CI or fresh clones.
ENV_FILE = os.path.join(BASE_DIR, '.env')
if os.path.exists(ENV_FILE):
    environ.Env.read_env(ENV_FILE)


# ==============================================================================
# 2. CORE SETTINGS & CONSTANTS
# ==============================================================================
# For production, set SECRET_KEY in .env. The default below lets `runserver`
# work on a fresh clone but should NEVER survive to production.
SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-me-in-production')

DEBUG = env('DEBUG')

# Canonical domain + server IP. Reused below for ALLOWED_HOSTS, CSRF and
# the PayChangu callback URLs so there's a single source of truth.
PRIMARY_DOMAIN = env('PRIMARY_DOMAIN', default='pritechhost.com')
WWW_DOMAIN     = f'www.{PRIMARY_DOMAIN}'
SERVER_IP      = env('SERVER_IP', default='204.168.251.91')

# Hosts Django will answer for.
# The `.trycloudflare.com` wildcard allows a Cloudflare quick-tunnel to point
# at this same server without editing settings — handy for staging.
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[
    PRIMARY_DOMAIN,
    WWW_DOMAIN,
    SERVER_IP,
    '127.0.0.1',
    'localhost',
    '.trycloudflare.com',
])

# Origins permitted to make state-changing requests (POST/PUT/DELETE).
# Both http:// and https:// are included so the site works in the window
# between deploy and Certbot issuing a certificate.
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[
    f'https://{PRIMARY_DOMAIN}',
    f'https://{WWW_DOMAIN}',
    f'http://{PRIMARY_DOMAIN}',
    f'http://{WWW_DOMAIN}',
    f'http://{SERVER_IP}',
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'https://*.trycloudflare.com',
])


# ==============================================================================
# 3. APPLICATION DEFINITION
# ==============================================================================
INSTALLED_APPS = [
    # ── Admin UI: Django Unfold (must precede django.contrib.admin) ──
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.inlines',

    # ── Django built-ins ──
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',        # required by django-allauth

    # ── Authentication ──
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',

    # ── Local apps ──
    'core',
    'users',
    'billing',
    'hosting',
    'projects',
]

# SRS FR-4: custom user model
AUTH_USER_MODEL = 'users.User'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Allauth must sit AFTER AuthenticationMiddleware
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'pritech_host.urls'
WSGI_APPLICATION = 'pritech_host.wsgi.application'


# ==============================================================================
# 4. TEMPLATES
# ==============================================================================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Injects SiteSettings.load() as `site` in every template.
                # Required — removing this breaks every page using {{ site.* }}.
                'core.context_processors.site_settings',
                'core.context_processors.legal_documents',
            ],
        },
    },
]


# ==============================================================================
# 5. DATABASE
# ==============================================================================
# Production uses PostgreSQL (deploy_hosting.sh supplies DB_* env vars).
# Local dev falls back to SQLite when DB_NAME is missing so contributors can
# run `manage.py runserver` without installing PostgreSQL.
if env('DB_NAME', default=''):
    DATABASES = {
        'default': {
            'ENGINE':   env('DB_ENGINE', default='django.db.backends.postgresql'),
            'NAME':     env('DB_NAME'),
            'USER':     env('DB_USER',     default='pritechhost_user'),
            'PASSWORD': env('DB_PASSWORD', default=''),
            'HOST':     env('DB_HOST',     default='127.0.0.1'),
            'PORT':     env('DB_PORT',     default='5432'),
            'CONN_MAX_AGE': 600,   # reuse connections for 10 minutes
            'OPTIONS': {
                # Fail fast when Postgres is down instead of hanging forever.
                'connect_timeout': 10,
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME':   BASE_DIR / 'db.sqlite3',
        }
    }


# ==============================================================================
# 6. AUTHENTICATION
# ==============================================================================
AUTHENTICATION_BACKENDS = [
    # Django's default: handles superuser login in /admin/
    'django.contrib.auth.backends.ModelBackend',
    # Allauth backend: handles email + Google login on the public site
    'allauth.account.auth_backends.AuthenticationBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ==============================================================================
# 7. DJANGO-ALLAUTH (login, signup, Google SSO, password reset)
# ==============================================================================
# SITE_ID selects the Site row allauth uses to build OAuth callbacks.
# The Site row's `domain` field MUST equal PRIMARY_DOMAIN, otherwise Google
# rejects every login with `redirect_uri_mismatch`.
SITE_ID = int(env('SITE_ID', default='1'))

# Auth by email, not username
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_UNIQUE_EMAIL = True

# 'optional' lets people log in before verifying. Set to 'mandatory' in .env
# once you want to force email verification before login.
ACCOUNT_EMAIL_VERIFICATION = env('ACCOUNT_EMAIL_VERIFICATION', default='optional')

# Redirect targets
LOGIN_REDIRECT_URL           = '/dashboard/'
ACCOUNT_LOGOUT_REDIRECT_URL  = '/'
ACCOUNT_SIGNUP_REDIRECT_URL  = '/dashboard/'

# Allow GET-based logout links (home page / nav bar).
ACCOUNT_LOGOUT_ON_GET = True

# Google provider
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
    }
}

# Auto-link a Google signup to an existing local user with the same email.
# Lets customers who checked out as guests sign in with Google later without
# creating a duplicate account.
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True


# ==============================================================================
# 8. INTERNATIONALISATION
# ==============================================================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Africa/Blantyre'
USE_I18N      = True
USE_TZ        = True


# ==============================================================================
# 9. STATIC & MEDIA FILES
# ==============================================================================
# Nginx serves STATIC_ROOT and MEDIA_ROOT directly, so this only affects the
# `collectstatic` target path. Keep them in sync with nginx.conf.
STATIC_URL       = '/static/'
STATIC_ROOT      = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Uploads: cap request bodies at 25 MB (matches client_max_body_size in nginx).
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024


# ==============================================================================
# 10. EMAIL (cPanel SMTP)
# ==============================================================================
# cPanel mail servers typically use port 465 (SSL) or 587 (TLS).
# Port 465 + SSL is what your .env ships with; if deliverability becomes
# flaky, switch to 587 + EMAIL_USE_TLS=True in .env.
EMAIL_BACKEND       = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST          = env('EMAIL_HOST',     default='mail.pritechmw.com')
EMAIL_PORT          = env.int('EMAIL_PORT', default=465)
EMAIL_HOST_USER     = env('EMAIL_HOST_USER', default='noreply@pritechmw.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_SSL       = env('EMAIL_USE_SSL')
EMAIL_USE_TLS       = env('EMAIL_USE_TLS')       # mutually exclusive with SSL

# Every transactional email goes out as this. Reply-To always goes to sales,
# which forwards to whoever is on shift.
DEFAULT_FROM_EMAIL = env(
    'DEFAULT_FROM_EMAIL',
    default=f'Pritech Host <{EMAIL_HOST_USER}>',
)
SALES_EMAIL  = env('SALES_EMAIL',  default='sales@pritechmw.com')
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Abort SMTP after 20s so a dead mail server can't hang a Celery worker.
# (The webhook never blocks on SMTP — email sending is a Celery task.)
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=20)


# ==============================================================================
# 11. THIRD-PARTY: PAYCHANGU
# ==============================================================================
PAYCHANGU_SECRET_KEY     = env('PAYCHANGU_SECRET_KEY',     default='')
PAYCHANGU_WEBHOOK_SECRET = env('PAYCHANGU_WEBHOOK_SECRET', default='')
PAYCHANGU_BASE_URL       = env('PAYCHANGU_BASE_URL', default='https://api.paychangu.com')

PAYCHANGU_CALLBACK_URL = env(
    'PAYCHANGU_CALLBACK_URL',
    default=f'https://{PRIMARY_DOMAIN}/payments/callback/',
)
PAYCHANGU_RETURN_URL = env(
    'PAYCHANGU_RETURN_URL',
    default=f'https://{PRIMARY_DOMAIN}/payments/return/',
)

# Timeout for outbound PayChangu API calls (seconds).
PAYCHANGU_TIMEOUT = env.int('PAYCHANGU_TIMEOUT', default=15)


# ==============================================================================
# 12. THIRD-PARTY: HESTIACP
# ==============================================================================
# When HESTIA_MOCK=True, the provisioning service logs what it *would* do
# instead of calling the real Hestia API. Perfect for dev and CI.
HESTIA_MOCK    = env('HESTIA_MOCK')
HESTIA_HOST    = env('HESTIA_HOST',    default='https://your-hestia-server.com:8083')
HESTIA_API_KEY = env('HESTIA_API_KEY', default='')


# ==============================================================================
# 13. SECURITY HEADERS
# ==============================================================================
# Behind Nginx, `X-Forwarded-Proto: https` is set for HTTPS requests. Django
# uses this to know the original request was secure, so `request.is_secure()`
# works and SECURE_SSL_REDIRECT doesn't cause a redirect loop.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Sane defaults — always on, regardless of environment.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY      = 'same-origin'
X_FRAME_OPTIONS             = 'SAMEORIGIN'   # needed by some allauth popup flows

# Cookie hardening — safe to enable always (never readable from JS).
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY    = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE    = 'Lax'

# Env-controlled so the site works during initial HTTP testing.
# Set to True in .env once Certbot has issued a working certificate.
SECURE_SSL_REDIRECT   = env('SECURE_SSL_REDIRECT')
SESSION_COOKIE_SECURE = env('SESSION_COOKIE_SECURE')
CSRF_COOKIE_SECURE    = env('CSRF_COOKIE_SECURE')

# HSTS: off by default. Enable with SECURE_HSTS_SECONDS=31536000 in .env
# *after* HTTPS is stable. Enabling prematurely locks browsers into HTTPS.
SECURE_HSTS_SECONDS            = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env('SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD            = env('SECURE_HSTS_PRELOAD')


# ==============================================================================
# 14. LOGGING
# ==============================================================================
# systemd captures stdout/stderr — watch in real time with:
#   journalctl -u gunicorn-pritechhost -f
#   journalctl -u celery-pritechhost   -f
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        # Application loggers — webhook, provisioning, billing, dunning
        'core': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'hosting': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'billing': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


# ==============================================================================
# 15. CACHE (Redis-backed, shared across Gunicorn workers)
# ==============================================================================
# SiteSettings.load() reads/writes django.core.cache. With the default
# per-process LocMemCache, each Gunicorn worker has its *own* cache — meaning
# an admin edit invalidates only one worker and the other two serve stale
# site content for up to 10 minutes.
#
# Pointing the cache at Redis (DB 1, keeping DB 0 for Celery) gives a single
# shared cache across all workers, so admin edits are visible immediately.
REDIS_CACHE_URL = env('REDIS_CACHE_URL', default='redis://127.0.0.1:6379/1')

if REDIS_CACHE_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_CACHE_URL,
            'TIMEOUT': 600,          # default TTL = 10 minutes
            'OPTIONS': {
                'db': '1',           # explicit for clarity
            },
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }


# ==============================================================================
# 16. DJANGO UNFOLD (Admin UI)
# ==============================================================================
UNFOLD = {
    'SITE_TITLE':  'Pritech Host Admin',
    'SITE_HEADER': 'Pritech Host',
    'SITE_SYMBOL': 'dns',
    'SIDEBAR': {
        'show_search': True,
        'navigation': [
            {
                'title': 'Hosting',
                'items': [
                    {'title': 'Plans',            'icon': 'inventory_2', 'link': '/admin/billing/plan/'},
                    {'title': 'Subscriptions',    'icon': 'sync_alt',    'link': '/admin/billing/subscription/'},
                    {'title': 'Projects',         'icon': 'folder',      'link': '/admin/projects/project/'},
                    {'title': 'Hosting Accounts', 'icon': 'dns',         'link': '/admin/hosting/hostingaccount/'},
                ],
            },
            {
                'title': 'Billing',
                'items': [
                    {'title': 'Invoices', 'icon': 'receipt_long', 'link': '/admin/billing/invoice/'},
                    {'title': 'Payments', 'icon': 'payments',     'link': '/admin/billing/payment/'},
                ],
            },
            {
                'title': 'Support',
                'items': [
                    {'title': 'Tickets', 'icon': 'confirmation_number', 'link': '/admin/core/ticket/'},
                ],
            },
            {
                'title': 'Users',
                'items': [
                    {'title': 'Customers', 'icon': 'people', 'link': '/admin/users/user/'},
                    {'title': 'Profiles',  'icon': 'badge',  'link': '/admin/users/customerprofile/'},
                ],
            },
        ],
    },
}


# ==============================================================================
# 17. CELERY (background workers — email, provisioning, dunning)
# ==============================================================================
# Celery offloads email delivery, Hestia provisioning and the daily
# renewal/dunning scan from the request/response cycle. The PayChangu
# webhook returns 200 in ~50 ms instead of waiting 10–60 s for SMTP +
# Hestia API.
#
# Run at least one worker:
#   celery -A pritech_host worker -l info -Q default,emails,provisioning
# ------------------------------------------------------------------------------

CELERY_BROKER_URL     = env('CELERY_BROKER_URL',     default='redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/0')

# --- Serialisation ----------------------------------------------------------
# JSON only — never allow pickle in production (security + worker compat).
CELERY_ACCEPT_CONTENT    = ['json']
CELERY_TASK_SERIALIZER   = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE          = TIME_ZONE

# --- Reliability ------------------------------------------------------------
# Re-deliver the task to another worker if the current one dies mid-task.
CELERY_TASK_ACKS_LATE = True

# Fetch only one task per worker child. With ACKS_LATE this gives us true
# at-least-once delivery — a crashed worker won't silently swallow tasks.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# If Redis isn't up yet when the worker boots (e.g. systemd boot order races),
# keep retrying instead of crashing out.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- Safety rails -----------------------------------------------------------
# Soft limit raises SoftTimeLimitExceeded (task can clean up), hard limit kills.
# 240 s soft / 300 s hard is generous for a single email or a Hestia API call,
# but the dunning scan iterates subscriptions — a slow loop is caught here
# before it can hold a worker slot for hours.
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TIME_LIMIT      = 300
CELERY_TASK_TRACK_STARTED   = True

# --- Routing ----------------------------------------------------------------
# Task names MUST match the fully-qualified path of the @shared_task in the
# codebase. Renaming a task module means updating this dict too.
CELERY_TASK_ROUTES = {
    'core.tasks.send_transactional_email_task':     {'queue': 'emails'},
    'hosting.tasks.provision_hosting_account_task': {'queue': 'provisioning'},
    'billing.tasks.check_subscription_lifecycle':   {'queue': 'default'},
}
CELERY_TASK_DEFAULT_QUEUE = 'default'


# ==============================================================================
# 18. CELERY BEAT (periodic tasks — daily renewal/dunning scan)
# ==============================================================================
# Runs `check_subscription_lifecycle` once per day at 00:30 Africa/Blantyre.
# That task drives the full renewal + dunning timeline:
#
#   -30d  early reminder email
#   -14d  generate renewal invoice + reminder with pay link
#    -7d  reminder
#    -3d  urgent reminder
#    -1d  final reminder
#     0d  expiry notice
#    +7d  suspension warning (24h notice)
#    +8d  SUSPEND Hestia account
#   +30d  final warning (deletion in 15 days)
#   +45d  delete Hestia account, cancel subscription
#
# Toggle the whole schedule off via DUNNING_ENABLED=False in .env — useful
# during a maintenance window, or while triaging a customer complaint where
# you don't want more reminder emails going out.
# ------------------------------------------------------------------------------

CELERY_BEAT_SCHEDULE = {}

if env('DUNNING_ENABLED'):
    CELERY_BEAT_SCHEDULE['check-subscription-lifecycle'] = {
        'task': 'billing.tasks.check_subscription_lifecycle',
        # 00:30 Africa/Blantyre = 22:30 UTC (CAT is UTC+2).
        # Runs at 00:30 local, so any 24h clock day-boundary weirdness
        # is already settled by the time staff check the dashboard at 08:00.
        'schedule': crontab(hour=0, minute=30),
    }


# ==============================================================================
# 19. MISC
# ==============================================================================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Used by Django's built-in password reset flow (allauth handles ours).
PASSWORD_RESET_TIMEOUT = 86400   # 24 hours
