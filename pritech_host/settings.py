"""
Django settings for the Pritech Host project.

Django  : 5.2.17
Python  : 3.10+

Environment-aware configuration
-------------------------------
All sensitive values come from `.env` (see deploy_hosting.sh).
Do NOT commit `.env` to source control.

Development vs. Production
--------------------------
The SAME file serves both. The deltas are driven by env vars, not
by separate files, so there's no risk of "I ran the wrong settings".

    Variable                  | Dev default  | Prod default
    ------------------------- | ------------ | ------------
    DEBUG                     | True         | False
    AXES_ENABLED              | False        | True
    MFA_ENFORCE_FOR_STAFF     | False        | True (after enrollment)
    RATELIMIT_ENABLE          | False        | True
    REDIS_CACHE_URL           | ''           | redis://...
    CELERY_BROKER_URL         | ''           | redis://...
    SECURE_SSL_REDIRECT       | False        | True (after HTTPS)
    SESSION_COOKIE_SECURE     | False        | True (after HTTPS)
    CSRF_COOKIE_SECURE        | False        | True (after HTTPS)
    SECURE_HSTS_SECONDS       | 0            | 31536000 (after 30d stable)

A fresh clone runs `runserver` on http://127.0.0.1:8000 with:
  - SQLite (no PostgreSQL needed)
  - LocMemCache (no Redis needed)
  - Celery in EAGER mode (no worker needed; tasks run inline)

The moment `.env` supplies a real Redis URL, cache and Celery switch
to async — same code path as prod.

Organisation
------------
 1. Paths & environment variables
 2. Core settings & constants
 3. Application definition
 4. Templates
 5. Database
 6. Authentication
 7. Django-allauth (login, signup, Google SSO, MFA)
 8. Brute-force protection (django-axes)
 9. Rate limiting (django-ratelimit)
10. Internationalisation
11. Static & media
12. Email (cPanel SMTP)
13. Third-party: PayChangu
14. Third-party: HestiaCP
15. Security headers
16. Logging
17. Cache (Redis in prod / LocMemCache in dev)
18. Django Unfold (admin UI)
19. Celery (background workers, with EAGER fallback for dev)
20. Celery Beat (scheduled tasks — daily renewal/dunning scan)
21. Misc

Deploy target
-------------
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
# Every bool() / list() needs a default so a missing .env key can't
# silently flip a setting to the wrong value.
env = environ.Env(
    # --- Core ---
    DEBUG=(bool, False),

    # --- Feature flags ---
    HESTIA_MOCK=(bool, True),
    DUNNING_ENABLED=(bool, True),

    # --- Email protocol ---
    EMAIL_USE_SSL=(bool, True),
    EMAIL_USE_TLS=(bool, False),

    # --- Security toggles -----------------------------------------------
    # All default to their DEV-safe values. deploy_hosting.sh (or you,
    # manually) flips them for prod via .env once HTTPS is confirmed.
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_SECURE=(bool, False),
    CSRF_COOKIE_SECURE=(bool, False),
    SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, False),
    SECURE_HSTS_PRELOAD=(bool, False),

    # --- Security layers (SRS NFR-9 to NFR-13) -------------------------
    AXES_ENABLED=(bool, False),
    MFA_ENFORCE_FOR_STAFF=(bool, False),
    RATELIMIT_ENABLE=(bool, False),
)

# Load .env if it exists — safe to skip in CI or fresh clones.
ENV_FILE = os.path.join(BASE_DIR, '.env')
if os.path.exists(ENV_FILE):
    environ.Env.read_env(ENV_FILE)


# ==============================================================================
# 2. CORE SETTINGS & CONSTANTS
# ==============================================================================
SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-me-in-production')
DEBUG = env('DEBUG')

PRIMARY_DOMAIN = env('PRIMARY_DOMAIN', default='pritechhost.com')
WWW_DOMAIN     = f'www.{PRIMARY_DOMAIN}'
SERVER_IP      = env('SERVER_IP', default='204.168.251.91')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[
    PRIMARY_DOMAIN, WWW_DOMAIN, SERVER_IP, '127.0.0.1', 'localhost',
    '.trycloudflare.com',
])

CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[
    f'https://{PRIMARY_DOMAIN}', f'https://{WWW_DOMAIN}',
    f'http://{PRIMARY_DOMAIN}',  f'http://{WWW_DOMAIN}',
    f'http://{SERVER_IP}',
    'http://127.0.0.1:8000', 'http://localhost:8000',
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
    'django.contrib.sites',

    # ── Authentication ──
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.mfa',                 # TOTP 2FA (SRS NFR-9)

    # ── Security ──
    'axes',                        # brute-force lockout (SRS NFR-10)

    # ── Local apps ──
    'core',
    'users',
    'billing',
    'hosting',
    'projects',
]

AUTH_USER_MODEL = 'users.User'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',

    # ── Security middleware ─────────────────────────────────────────────
    # Axes tracks failed logins AFTER the user is identified. Order it
    # after AccountMiddleware so allauth's session state is settled.
    'axes.middleware.AxesMiddleware',

    # MFA enforcement — redirects un-enrolled staff to /accounts/2fa/.
    # No-op when MFA_ENFORCE_FOR_STAFF=False (dev default).
    'core.middleware.StaffMFAEnforcementMiddleware',
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
                'core.context_processors.site_settings',
                'core.context_processors.legal_documents',
            ],
        },
    },
]


# ==============================================================================
# 5. DATABASE
# ==============================================================================
# Prod uses PostgreSQL (deploy_hosting.sh supplies DB_* env vars).
# Dev falls back to SQLite when DB_NAME is missing.
if env('DB_NAME', default=''):
    DATABASES = {
        'default': {
            'ENGINE':   env('DB_ENGINE', default='django.db.backends.postgresql'),
            'NAME':     env('DB_NAME'),
            'USER':     env('DB_USER',     default='pritechhost_user'),
            'PASSWORD': env('DB_PASSWORD', default=''),
            'HOST':     env('DB_HOST',     default='127.0.0.1'),
            'PORT':     env('DB_PORT',     default='5432'),
            'CONN_MAX_AGE': 600,
            'OPTIONS': {'connect_timeout': 10},
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
    # Axes backend FIRST so it can intercept failed logins and count
    # them toward the lockout threshold BEFORE ModelBackend rejects.
    # It's harmless when AXES_ENABLED=False (it just delegates).
    'axes.backends.AxesStandaloneBackend',

    # Django's default: handles admin login + superuser checks.
    'django.contrib.auth.backends.ModelBackend',

    # Allauth backend: email + Google login on the public site.
    'allauth.account.auth_backends.AuthenticationBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ==============================================================================
# 7. DJANGO-ALLAUTH (login, signup, Google SSO, MFA)
# ==============================================================================
SITE_ID = int(env('SITE_ID', default='1'))

ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_UNIQUE_EMAIL = True

ACCOUNT_EMAIL_VERIFICATION = env('ACCOUNT_EMAIL_VERIFICATION', default='optional')

LOGIN_REDIRECT_URL          = '/dashboard/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'
ACCOUNT_SIGNUP_REDIRECT_URL = '/dashboard/'
ACCOUNT_LOGOUT_ON_GET       = True

# ── Google OAuth ──────────────────────────────────────────────────────────
# The APP dict is how allauth gets OAuth credentials. Without it,
# allauth reads from the SocialApp admin row instead. Using the env
# vars means you can rotate the client secret via .env without
# touching the database.
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': env('GOOGLE_CLIENT_ID',     default=''),
            'secret':    env('GOOGLE_CLIENT_SECRET', default=''),
            'key':       '',
        },
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
    }
}

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

# --- MFA (SRS NFR-9) -----------------------------------------------------
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_ALLOW_UNVERIFIED_EMAIL = True
# MFA_TOTP_ISSUER = "Pritech Host"   # shows in the authenticator app


# ==============================================================================
# 8. BRUTE-FORCE PROTECTION — django-axes (SRS NFR-10)
# ==============================================================================
# Axes locks an account/IP pair after N failed login attempts.
# In DEV it's off (AXES_ENABLED=False in .env) so test retries don't
# lock you out. In PROD it's on — 5 failures → 1-hour lockout.
# ------------------------------------------------------------------------------

AXES_ENABLED             = env('AXES_ENABLED')
AXES_FAILURE_LIMIT       = 5
AXES_COOLOFF_TIME        = 1                # hours
AXES_LOCK_OUT_AT_FAILURE = True
AXES_RESET_ON_SUCCESS    = True

# Lock by username AND IP independently. Blocks:
#   - one account brute-forced from many IPs (username-based lock)
#   - one IP brute-forcing many accounts (IP-based lock)
AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']

# Get the real client IP behind Nginx (X-Forwarded-For chain).
AXES_IPWARE_PROXY_COUNT           = 1
AXES_IPWARE_META_PRECEDENCE_ORDER = ['HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR']

# False = protect both /admin/login/ and /accounts/login/.
# True  = protect only admin login. We want both protected → False.
AXES_ONLY_ADMIN_SITE = False

AXES_RESET_COOL_OFF_ON_VERIFIED_REQUEST = False


# ==============================================================================
# 9. RATE LIMITING — django-ratelimit (SRS NFR-11)
# ==============================================================================
# Applied per-view via @ratelimit decorators in core/views.py.
# Dev default is OFF; prod default is ON.
# ------------------------------------------------------------------------------

RATELIMIT_ENABLE    = env('RATELIMIT_ENABLE')
RATELIMIT_USE_CACHE = 'default'
RATELIMIT_VIEW      = None


# ==============================================================================
# 10. INTERNATIONALISATION
# ==============================================================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Africa/Blantyre'
USE_I18N      = True
USE_TZ        = True


# ==============================================================================
# 11. STATIC & MEDIA FILES
# ==============================================================================
STATIC_URL       = '/static/'
STATIC_ROOT      = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024


# ==============================================================================
# 12. EMAIL (cPanel SMTP)
# ==============================================================================
EMAIL_BACKEND       = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST          = env('EMAIL_HOST',     default='mail.pritechmw.com')
EMAIL_PORT          = env.int('EMAIL_PORT', default=465)
EMAIL_HOST_USER     = env('EMAIL_HOST_USER', default='noreply@pritechmw.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_SSL       = env('EMAIL_USE_SSL')
EMAIL_USE_TLS       = env('EMAIL_USE_TLS')

DEFAULT_FROM_EMAIL = env(
    'DEFAULT_FROM_EMAIL',
    default=f'Pritech Host <{EMAIL_HOST_USER}>',
)
SALES_EMAIL  = env('SALES_EMAIL',  default='sales@pritechmw.com')
SERVER_EMAIL = DEFAULT_FROM_EMAIL

EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=20)


# ==============================================================================
# 13. THIRD-PARTY: PAYCHANGU
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
PAYCHANGU_TIMEOUT = env.int('PAYCHANGU_TIMEOUT', default=15)


# ==============================================================================
# 14. THIRD-PARTY: HESTIACP
# ==============================================================================
HESTIA_MOCK    = env('HESTIA_MOCK')
HESTIA_HOST    = env('HESTIA_HOST',    default='https://your-hestia-server.com:8083')
HESTIA_API_KEY = env('HESTIA_API_KEY', default='')


# ==============================================================================
# 15. SECURITY HEADERS (SRS NFR-12, NFR-13)
# ==============================================================================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# SAMEORIGIN (not DENY) because Google Sign-In uses an iframe for
# OAuth postMessage callbacks. DENY breaks Google SSO entirely.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY      = 'same-origin'
X_FRAME_OPTIONS             = 'SAMEORIGIN'

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY    = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE    = 'Lax'

# HTTPS-dependent flags — off in dev, auto-flipped by deploy_hosting.sh
# step 13 once it verifies HTTPS responds with 200.
SECURE_SSL_REDIRECT   = env('SECURE_SSL_REDIRECT')
SESSION_COOKIE_SECURE = env('SESSION_COOKIE_SECURE')
CSRF_COOKIE_SECURE    = env('CSRF_COOKIE_SECURE')

# HSTS — enable ONLY after HTTPS has been stable for 30+ days.
# Setting this non-zero tells browsers to ALWAYS use HTTPS for a year.
# If you enable it prematurely and lose the cert, the site becomes
# unreachable for every returning visitor — they can't even click
# "proceed anyway". Roll out gradually:
#   1 week HTTPS stable    → SECURE_HSTS_SECONDS=3600
#   2 weeks HTTPS stable   → SECURE_HSTS_SECONDS=86400
#   30 days HTTPS stable   → SECURE_HSTS_SECONDS=31536000
# Then add INCLUDE_SUBDOMAINS=True. PRELOAD is a separate 6-month
# commitment to browsers — only add after 30 days of HSTS=31536000.
SECURE_HSTS_SECONDS            = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env('SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD            = env('SECURE_HSTS_PRELOAD')


# ==============================================================================
# 16. LOGGING
# ==============================================================================
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
        'django':          {'handlers': ['console'], 'level': 'INFO',    'propagate': False},
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'axes':            {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'allauth':         {'handlers': ['console'], 'level': 'INFO',    'propagate': False},
        'core':            {'handlers': ['console'], 'level': 'INFO',    'propagate': False},
        'hosting':         {'handlers': ['console'], 'level': 'INFO',    'propagate': False},
        'billing':         {'handlers': ['console'], 'level': 'INFO',    'propagate': False},
    },
}


# ==============================================================================
# 17. CACHE
# ==============================================================================
# The 400/500 "no Redis" problem, fixed
# --------------------------------------
# Previous versions defaulted REDIS_CACHE_URL to a real Redis URL. That
# meant a fresh Windows dev clone crashed on every page with
# "Error 10061: No connection could be made". Now the default is '',
# so dev falls back to LocMemCache and works with zero setup.
#
# Prod sets REDIS_CACHE_URL in .env (via deploy_hosting.sh). Redis DB 1
# is used so a FLUSHDB on the cache never touches the Celery broker
# queue (which lives in Redis DB 0).
# ------------------------------------------------------------------------------

REDIS_CACHE_URL = env('REDIS_CACHE_URL', default='')

if REDIS_CACHE_URL:
    # Production / when Redis is explicitly configured
    CACHES = {
        'default': {
            'BACKEND':  'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_CACHE_URL,
            'TIMEOUT':  600,
            'OPTIONS':  {'db': '1'},
        }
    }
else:
    # Development default — per-process in-memory cache. Each Django
    # process (there's one under runserver) has its own copy, so no
    # cross-process sharing. That's fine: the only consumers are
    # SiteSettings.load() and legal_documents(), both of which are
    # fine with per-process caching.
    CACHES = {
        'default': {
            'BACKEND':  'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'pritechhost-dev',
        }
    }


# ==============================================================================
# 18. DJANGO UNFOLD (Admin UI)
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
            {
                'title': 'Security',
                'items': [
                    {'title': 'Login Attempts', 'icon': 'shield', 'link': '/admin/axes/accessattempt/'},
                    {'title': 'Access Log',     'icon': 'receipt', 'link': '/admin/axes/accesslog/'},
                ],
            },
        ],
    },
}


# ==============================================================================
# 19. CELERY (background workers — email, provisioning, dunning)
# ==============================================================================
# The "no worker" fallback, explained
# ------------------------------------
# Real Celery needs a broker (Redis) and a running worker process. That
# is overkill for `python manage.py runserver` when you're just testing
# the UI. So:
#
#   • CELERY_BROKER_URL empty → EAGER mode:
#       * `.delay(...)` runs the task synchronously in the caller thread
#       * emails are sent during the HTTP request (slow, but you see errors)
#       * payment webhooks send confirmation emails before returning 200
#       * no worker process needed, no Redis needed
#
#   • CELERY_BROKER_URL set (prod) → normal async:
#       * `.delay(...)` enqueues to Redis
#       * worker process picks up tasks
#       * webhook returns in ~50 ms while emails send in the background
#
# The end result is the SAME API (`task.delay(...)`), different runtime.
# ------------------------------------------------------------------------------

CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='')

if not CELERY_BROKER_URL:
    # ── DEVELOPMENT: run tasks inline ──
    # `memory://` is a valid broker that doesn't require external services,
    # but we also set ALWAYS_EAGER so tasks don't wait for a worker.
    CELERY_BROKER_URL          = 'memory://'
    CELERY_RESULT_BACKEND      = 'cache+memory://'
    CELERY_TASK_ALWAYS_EAGER   = True
    # Propagate task exceptions to the caller so failures surface in the
    # web request traceback (much easier to debug than a swallowed log).
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_TASK_STORE_EAGER_RESULT = False   # don't bloat memory with results
else:
    # ── PRODUCTION: real async broker ──
    CELERY_RESULT_BACKEND         = env(
        'CELERY_RESULT_BACKEND',
        default=CELERY_BROKER_URL,
    )
    CELERY_TASK_ALWAYS_EAGER      = False
    CELERY_TASK_EAGER_PROPAGATES  = False

# Serialisation — JSON only, never pickle
CELERY_ACCEPT_CONTENT    = ['json']
CELERY_TASK_SERIALIZER   = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE          = TIME_ZONE

# Reliability
CELERY_TASK_ACKS_LATE                     = True
CELERY_WORKER_PREFETCH_MULTIPLIER         = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Safety rails
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TIME_LIMIT      = 300
CELERY_TASK_TRACK_STARTED   = True

# Routing — task name must match the fully-qualified path of @shared_task
CELERY_TASK_ROUTES = {
    'core.tasks.send_transactional_email_task':     {'queue': 'emails'},
    'hosting.tasks.provision_hosting_account_task': {'queue': 'provisioning'},
    'billing.tasks.check_subscription_lifecycle':   {'queue': 'default'},
}
CELERY_TASK_DEFAULT_QUEUE = 'default'


# ==============================================================================
# 20. CELERY BEAT (periodic tasks — daily renewal/dunning scan)
# ==============================================================================
CELERY_BEAT_SCHEDULE = {}

if env('DUNNING_ENABLED'):
    CELERY_BEAT_SCHEDULE['check-subscription-lifecycle'] = {
        'task': 'billing.tasks.check_subscription_lifecycle',
        'schedule': crontab(hour=0, minute=30),   # 00:30 Africa/Blantyre
    }


# ==============================================================================
# 21. MISC
# ==============================================================================
DEFAULT_AUTO_FIELD     = 'django.db.models.BigAutoField'
PASSWORD_RESET_TIMEOUT = 86400   # 24 hours