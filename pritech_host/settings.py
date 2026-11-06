"""
Django settings for Pritech Host.

Environment-aware configuration:
  - All sensitive values (DB, SECRET_KEY, API keys) come from `.env`.
  - File lives at project root; DO NOT COMMIT `.env` to source control.
  - Safe defaults let this file run locally even without a `.env`.

Deploy target:
  Domain       : pritechhost.com
  WWW domain   : www.pritechhost.com
  Server IP    : 204.168.251.91
  WSGI app     : pritech_host.wsgi:application
"""

import os
from pathlib import Path
import environ


# ==============================================================================
# 1. PATHS & ENVIRONMENT
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialise django-environ with sensible defaults. Every bool() needs a default
# so a missing `.env` key doesn't silently evaluate to the wrong boolean.
env = environ.Env(
    DEBUG=(bool, False),
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_SECURE=(bool, False),
    CSRF_COOKIE_SECURE=(bool, False),
    SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, False),
    SECURE_HSTS_PRELOAD=(bool, False),
    HESTIA_MOCK=(bool, True),
    ACCOUNT_EMAIL_VERIFICATION_STRICT=(bool, False),
)

# Load `.env` if it exists. Safe to skip in CI or fresh clones.
ENV_FILE = os.path.join(BASE_DIR, '.env')
if os.path.exists(ENV_FILE):
    environ.Env.read_env(ENV_FILE)


# ==============================================================================
# 2. CORE SETTINGS & CONSTANTS
# ==============================================================================
SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-me-in-production')

DEBUG = env('DEBUG')

# Canonical domain + server IP. Used below for ALLOWED_HOSTS and CSRF.
PRIMARY_DOMAIN = env('PRIMARY_DOMAIN', default='pritechhost.com')
WWW_DOMAIN     = f'www.{PRIMARY_DOMAIN}'
SERVER_IP      = env('SERVER_IP', default='204.168.251.91')

# Hosts Django will answer for. Wildcard `.trycloudflare.com` lets you tunnel
# into this same config for local testing without touching the file.
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[
    PRIMARY_DOMAIN,
    WWW_DOMAIN,
    SERVER_IP,
    '127.0.0.1',
    'localhost',
    '.trycloudflare.com',
])

# Origins permitted to make state-changing requests (POST/PUT/DELETE).
# Both http:// and https:// are listed so the site works during the window
# between deploy and Certbot running.
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
            ],
        },
    },
]


# ==============================================================================
# 5. DATABASE
# ==============================================================================
# Production uses PostgreSQL; local dev falls back to SQLite when DB_NAME is
# missing so contributors can run `manage.py runserver` without installing PG.
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
AUTH_USER_MODEL = 'users.User'

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
# `SITE_ID` selects the Site row that allauth uses to build OAuth callbacks.
# The Site row's `domain` field MUST equal PRIMARY_DOMAIN; otherwise Google
# will reject every login with `redirect_uri_mismatch`.
SITE_ID = int(env('SITE_ID', default='1'))

# Auth by email, not username
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_UNIQUE_EMAIL = True

# 'optional' lets people log in before verifying. Set to 'mandatory' once
# you want to enforce email verification.
ACCOUNT_EMAIL_VERIFICATION = env('ACCOUNT_EMAIL_VERIFICATION', default='optional')

# Redirect targets
LOGIN_REDIRECT_URL = '/dashboard/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'
ACCOUNT_SIGNUP_REDIRECT_URL = '/dashboard/'

# Allauth 0.63+ needs explicit URLs to host its own login pages
ACCOUNT_LOGOUT_ON_GET = True   # allow GET logout links on the home page

# Google provider
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
    }
}

# Automatically link a Google signup to an existing local user with the same
# email. This lets customers who checked out first sign in with Google later.
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
# Nginx serves STATIC_ROOT and MEDIA_ROOT directly, so this only concerns
# the `collectstatic` target path. Keep them in sync with the nginx config.
STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Uploads: cap request bodies at 25 MB (matches `client_max_body_size` in nginx).
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024


# ==============================================================================
# 10. EMAIL (cPanel SMTP)
# ==============================================================================
# cPanel mail servers typically use port 465 (SSL) or 587 (TLS). Your existing
# `.env` uses 465; if deliverability becomes flaky, switch to 587 + TLS.
EMAIL_BACKEND       = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST          = env('EMAIL_HOST',     default='mail.pritechmw.com')
EMAIL_PORT          = env.int('EMAIL_PORT', default=465)
EMAIL_HOST_USER     = env('EMAIL_HOST_USER', default='noreply@pritechmw.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_SSL       = env.bool('EMAIL_USE_SSL', default=True)
EMAIL_USE_TLS       = env.bool('EMAIL_USE_TLS', default=False)

# Every transactional email goes out as this. Reply-To always goes to sales,
# which forwards to whoever is on shift.
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=f'Pritech Host <{EMAIL_HOST_USER}>')
SALES_EMAIL        = env('SALES_EMAIL', default='sales@pritechmw.com')
SERVER_EMAIL       = DEFAULT_FROM_EMAIL

# Abort SMTP after 10s so a dead mail server doesn't hang a webhook.
EMAIL_TIMEOUT = 10


# ==============================================================================
# 11. THIRD-PARTY: PAYCHANGU
# ==============================================================================
PAYCHANGU_SECRET_KEY      = env('PAYCHANGU_SECRET_KEY', default='')
PAYCHANGU_WEBHOOK_SECRET  = env('PAYCHANGU_WEBHOOK_SECRET', default='')
PAYCHANGU_BASE_URL        = env('PAYCHANGU_BASE_URL', default='https://api.paychangu.com')
PAYCHANGU_CALLBACK_URL    = env('PAYCHANGU_CALLBACK_URL',
                                default=f'https://{PRIMARY_DOMAIN}/payments/callback/')
PAYCHANGU_RETURN_URL      = env('PAYCHANGU_RETURN_URL',
                                default=f'https://{PRIMARY_DOMAIN}/payments/return/')

# Request timeout for outbound PayChangu calls (seconds).
PAYCHANGU_TIMEOUT = env.int('PAYCHANGU_TIMEOUT', default=15)


# ==============================================================================
# 12. THIRD-PARTY: HESTIACP
# ==============================================================================
# When HESTIA_MOCK=True the provisioning service logs what it *would* do
# instead of hitting a real server. Perfect for dev and CI.
HESTIA_MOCK    = env('HESTIA_MOCK')
HESTIA_HOST    = env('HESTIA_HOST',    default='https://your-hestia-server.com:8083')
HESTIA_API_KEY = env('HESTIA_API_KEY', default='')


# ==============================================================================
# 13. SECURITY HEADERS
# ==============================================================================
# Behind Nginx, `X-Forwarded-Proto: https` is set for HTTPS requests. Django
# uses this header to know the original request was secure, so `request.is_secure()`
# works correctly and `SECURE_SSL_REDIRECT` doesn't loop.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Sane defaults. Enable more once Certbot has issued a certificate — see below.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY      = 'same-origin'
X_FRAME_OPTIONS             = 'SAMEORIGIN'   # needed by some allauth pages

# These are env-controlled so the site works during initial HTTP testing.
# Once HTTPS is confirmed working, set them to True in .env.
SECURE_SSL_REDIRECT    = env('SECURE_SSL_REDIRECT')
SESSION_COOKIE_SECURE  = env('SESSION_COOKIE_SECURE')
CSRF_COOKIE_SECURE     = env('CSRF_COOKIE_SECURE')

# Cookies should never be readable from JavaScript.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY    = True

# HSTS: off by default. Enable with e.g. SECURE_HSTS_SECONDS=31536000 in .env
# once HTTPS is stable. Enabling prematurely locks browsers into HTTPS.
SECURE_HSTS_SECONDS           = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env('SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD            = env('SECURE_HSTS_PRELOAD')


# ==============================================================================
# 14. LOGGING
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
        # systemd captures stdout/stderr; run `journalctl -u gunicorn-pritechhost -f`
        # to watch these in real time.
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
        # Application loggers — webhook, provisioning, billing
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
# 15. DJANGO UNFOLD (Admin UI)
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
# 16. MISC
# ==============================================================================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Used by Django's built-in password reset flow (allauth handles ours).
PASSWORD_RESET_TIMEOUT = 86400   # 24 hours
