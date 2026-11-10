#!/bin/bash
# ============================================================================
# Pritech Host — Production Deployment Script (Bulletproof)
# Repo   : https://github.com/Izk-123/Pritech-Host.git
# Domain : pritechhost.com
#
# What this provisions, in order:
#   1.  git fetch + reset   → /home/project/pritechhost at origin/main
#   2.  Python venv         → requirements + celery/redis/gunicorn/psycopg2
#   3.  .env                → only created on first run; preserved after
#   4.  Redis               → Celery broker + shared cache (DB 0 / DB 1)
#   5.  PostgreSQL          → role + database, password synced from .env
#   6.  DB connection test  → fail fast if creds are wrong
#   7.  Django setup        → migrate / seed_legal / collectstatic / admin
#   8.  Gunicorn service    → HTTP app server (port = unix socket)
#   9.  Celery worker       → processes emails, provisioning, dunning
#  10.  Celery beat         → schedules the daily lifecycle scan
#  11.  Nginx config        → reverse proxy + static/media
#  12.  Health check        → verifies every public URL responds
#
# Idempotency: safe to re-run.
#   - .env is never overwritten after first creation
#   - git reset --hard origin/main (discards local edits to tracked files)
#   - migrate is fast (no-op when schema is up to date)
#   - seed_legal skips any document that already exists (won't overwrite
#     lawyer-reviewed content unless --force is passed — we never pass it)
#   - systemd unit files are rewritten and services restarted
#
# Requirements on the target box:
#   - Ubuntu 22.04 / 24.04, run as root via SSH
#   - DNS for pritechhost.com + www already points at this server
#   - Ports 80 + 443 reachable (ufw / firewall)
#   - Base packages: git python3 python3-venv python3-pip
#                    postgresql postgresql-contrib nginx openssl curl
# ============================================================================

set -euo pipefail
set +H  # disable history expansion so passwords with '!' don't break

# ─── Configuration ──────────────────────────────────────────────────────────
PROJECT_NAME="pritechhost"
PROJECT_DIR="/home/project/${PROJECT_NAME}"
VENV_DIR="${PROJECT_DIR}/venv"
REPO_URL="https://github.com/Izk-123/Pritech-Host.git"
BRANCH="main"

DOMAIN="pritechhost.com"
WWW_DOMAIN="www.pritechhost.com"
SERVER_IP="204.168.251.91"

DB_NAME="pritechhost_db"
DB_USER="pritechhost_user"

WSGI_APP="pritech_host.wsgi:application"
DJANGO_SETTINGS="pritech_host.settings"
CELERY_APP="pritech_host"
GUNICORN_SOCK="${PROJECT_DIR}/gunicorn.sock"

CELERY_QUEUES="default,emails,provisioning"
CELERY_CONCURRENCY="2"     # keep low on small VPS; raise if you have CPU headroom

# ─── Logging Helpers ────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

printf '\n\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1;35m  Pritech Host Deploy — %s\033[0m\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'

[[ $EUID -eq 0 ]] || die "Run as root."
for cmd in git python3 pip3 psql nginx openssl curl systemctl; do
    command -v "$cmd" &>/dev/null || die "Missing: $cmd — install it first."
done
ok "System dependencies verified."

# ============================================================================
# 1. Fetch Code
# ============================================================================
# `git reset --hard origin/main` is the standard way to make a production
# checkout match the repo exactly. It DISCARDS local modifications to
# tracked files — so if you ever hot-fix something on the box, commit and
# push before re-running this script.
#
# Untracked/git-ignored files are preserved: .env, venv/, media/,
# staticfiles/, db.sqlite3, gunicorn.sock all survive a reset.
# ============================================================================
log "Step 1: Fetching source code"
if [ ! -d "${PROJECT_DIR}/.git" ]; then
    mkdir -p "${PROJECT_DIR}"
    git clone --branch "${BRANCH}" "${REPO_URL}" "${PROJECT_DIR}"
    ok "Repository cloned."
else
    cd "${PROJECT_DIR}"
    git fetch origin "${BRANCH}"
    git reset --hard "origin/${BRANCH}"
    ok "Repository updated to origin/${BRANCH}."
fi
cd "${PROJECT_DIR}"

# ============================================================================
# 2. Python Environment
# ============================================================================
# Beyond requirements.txt, we install celery/redis/gunicorn/psycopg2 explicitly
# so this script is self-contained even if requirements.txt forgets one of
# them. pip is idempotent — re-running with everything installed is a no-op.
# ============================================================================
log "Step 2: Python environment"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    ok "Virtualenv created."
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel --quiet
pip install -r requirements.txt --quiet
pip install celery redis gunicorn psycopg2-binary --quiet
ok "Python dependencies installed."

# ============================================================================
# 3. Environment Variables
# ============================================================================
# .env is the source of truth for all secrets and per-environment config.
# First run  → generate secure DB password + Django SECRET_KEY, write .env.
# Re-runs    → preserve .env verbatim (nobody wants the deploy script to
#              rotate secrets behind their back).
# ============================================================================
log "Step 3: Environment variables"
if [ -f "${PROJECT_DIR}/.env" ]; then
    DB_PASS=$(grep -E '^DB_PASSWORD=' "${PROJECT_DIR}/.env" | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'" || true)
    if [ -z "${DB_PASS}" ]; then
        die "DB_PASSWORD is empty or missing in existing .env. Fix .env first."
    fi
    ok ".env exists — loaded DB_PASSWORD."
else
    # First-time install: generate secure alphanumeric secrets (strip characters
    # that need escaping in shell / SQL / .env parsing).
    DB_PASS="$(openssl rand -base64 32 | tr -d '/+=!@#$%^&*()_+-' | cut -c1-24)"
    DJANGO_SECRET="$(openssl rand -base64 48 | tr -d '/+=!@#$%^&*()_+-' | cut -c1-50)"
    cat > "${PROJECT_DIR}/.env" <<EOF
# ── Django core ─────────────────────────────────────────────────────────────
SECRET_KEY=${DJANGO_SECRET}
DEBUG=False
PRIMARY_DOMAIN=${DOMAIN}
SERVER_IP=${SERVER_IP}
SITE_ID=1
ALLOWED_HOSTS=${DOMAIN},${WWW_DOMAIN},${SERVER_IP},127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://${WWW_DOMAIN}

# ── Database (PostgreSQL) ───────────────────────────────────────────────────
DB_ENGINE=django.db.backends.postgresql
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASS}
DB_HOST=127.0.0.1
DB_PORT=5432

# ── PayChangu ───────────────────────────────────────────────────────────────
PAYCHANGU_SECRET_KEY=sec-test-yourkey-here
PAYCHANGU_WEBHOOK_SECRET=sme.pritechmw
PAYCHANGU_CALLBACK_URL=https://${DOMAIN}/payments/callback/
PAYCHANGU_RETURN_URL=https://${DOMAIN}/payments/return/

# ── Email (cPanel SMTP) ─────────────────────────────────────────────────────
EMAIL_HOST=mail.pritechmw.com
EMAIL_PORT=465
EMAIL_HOST_USER=noreply@pritechmw.com
EMAIL_HOST_PASSWORD=change-me
EMAIL_USE_SSL=True
EMAIL_USE_TLS=False
DEFAULT_FROM_EMAIL=Pritech Host <noreply@pritechmw.com>
SALES_EMAIL=sales@pritechmw.com

# ── Celery / Redis ──────────────────────────────────────────────────────────
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
REDIS_CACHE_URL=redis://127.0.0.1:6379/1

# ── Feature flags ───────────────────────────────────────────────────────────
HESTIA_MOCK=True
DUNNING_ENABLED=True

# ── Cookies / HTTPS (set True once Certbot is confirmed working) ────────────
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
EOF
    chmod 600 "${PROJECT_DIR}/.env"
    ok ".env created with generated secrets."
    warn "SAVE THIS DB PASSWORD: ${DB_PASS}"
fi

# ============================================================================
# 4. Redis (Celery broker + shared cache)
# ============================================================================
# Redis DB 0 → Celery broker/result backend (raw task payloads).
# Redis DB 1 → Django cache backend (SiteSettings singleton, legal docs,
#              future per-view caches).
# Splitting DBs avoids polluting the Celery stream with cache keys and lets
# you flush one without touching the other.
# ============================================================================
log "Step 4: Redis broker"
if ! command -v redis-server &>/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server redis-tools -qq
    ok "Redis installed."
else
    ok "Redis already installed."
fi

systemctl enable redis-server > /dev/null 2>&1 || true
systemctl restart redis-server
sleep 1

if ! redis-cli ping 2>/dev/null | grep -q PONG; then
    die "Redis is not responding on 127.0.0.1:6379. Check: journalctl -u redis-server -n 50"
fi
ok "Redis is up (PONG)."

# ============================================================================
# 5. PostgreSQL (force role password to match .env)
# ============================================================================
# If someone manually changed the DB_PASSWORD in .env, we force-ALTER the
# role's password to match. This is the only way to recover from a
# .env-vs-database password drift without manual psql surgery.
# ============================================================================
log "Step 5: PostgreSQL configuration"
# Escape single quotes in case the password contains them.
ESCAPED_PASS=$(printf '%s' "${DB_PASS}" | sed "s/'/''/g")

sudo -u postgres psql <<SQL > /dev/null
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${ESCAPED_PASS}';
   ELSE
      ALTER ROLE ${DB_USER} WITH PASSWORD '${ESCAPED_PASS}';
   END IF;
END
\$\$;
SQL

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 \
    || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" > /dev/null
sudo -u postgres psql -d "${DB_NAME}" -c "GRANT ALL ON SCHEMA public TO ${DB_USER};" > /dev/null
ok "Database role and password synced with .env."

# ============================================================================
# 6. Test Database Connection
# ============================================================================
# Fail fast here rather than 3 steps later during `migrate`. If this test
# fails, the fix is almost always one of:
#   - .env DB_PASSWORD doesn't match what we just set (re-run to re-sync)
#   - pg_hba.conf rejects 127.0.0.1 connections (needs "host all all 127.0.0.1/32 scram-sha-256")
#   - postgres is down (systemctl restart postgresql)
# ============================================================================
log "Step 6: Testing database connection"
if ! PGPASSWORD="${DB_PASS}" psql -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" > /dev/null 2>&1; then
    die "Database connection failed. Check .env DB_PASSWORD and pg_hba.conf."
fi
ok "Database connection successful."

# ============================================================================
# 7. Django Setup
# ============================================================================
# migrate        → apply schema changes
# seed_legal     → create Terms / Privacy / AUP / Refund documents on first
#                  deploy only. Skips any slug that already has an active
#                  document, so this NEVER overwrites lawyer-reviewed content.
# collectstatic  → build staticfiles/ that nginx serves directly
# shell bootstrap→ idempotent superuser + allauth Site domain
# ============================================================================
log "Step 7: Django setup"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS}"

python manage.py migrate --noinput > /dev/null
ok "Database migrations applied."

# Seed legal documents. Idempotent — safe to re-run on every deploy.
#
# NOTE: The seed_legal command ships placeholder text that is a *starting
# point*, not a legally reviewed document. Have a Malawi lawyer review the
# content in /admin/ → Legal Documents before going fully live.
#
# If this command doesn't exist yet (e.g. mid-rollout of the legal feature),
# the deploy still succeeds — the check above just warns.
if [ -f "${PROJECT_DIR}/core/management/commands/seed_legal.py" ]; then
    if python manage.py seed_legal 2>&1 | tee /tmp/seed_legal.out | grep -q "Created"; then
        ok "Legal documents seeded (Terms, Privacy, AUP, Refund)."
        warn "⚠  LEGAL CONTENT IS PLACEHOLDER — have a Malawi lawyer review it."
    else
        ok "Legal documents already present — skipped."
    fi
else
    warn "seed_legal command not found — skipping legal document seeding."
    warn "   (Run 'git pull' to fetch the legal feature branch, or continue"
    warn "    without legal pages until the next deploy.)"
fi

python manage.py collectstatic --noinput > /dev/null
ok "Static files collected."

python manage.py shell <<'PY' || true
from users.models import User
from django.contrib.sites.models import Site

if not User.objects.filter(is_superuser=True).exists():
    User.objects.create_superuser('admin', 'admin@pritechhost.com', 'ChangeMe123!')
    print("  ✔ Superuser created: admin@pritechhost.com / ChangeMe123!")
else:
    print("  ✔ Superuser already exists.")

Site.objects.update_or_create(
    id=1, defaults={'domain': 'pritechhost.com', 'name': 'Pritech Host'}
)
print("  ✔ Allauth Site domain updated to pritechhost.com")
PY

# ============================================================================
# 8. Gunicorn Service
# ============================================================================
log "Step 8: Configuring Gunicorn"
cat > /etc/systemd/system/gunicorn-${PROJECT_NAME}.service <<EOF
[Unit]
Description=Gunicorn for Pritech Host
After=network.target postgresql.service redis-server.service
Requires=postgresql.service

[Service]
User=root
Group=root
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin"
Environment="DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS}"
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${VENV_DIR}/bin/gunicorn --workers 3 --bind unix:${GUNICORN_SOCK} ${WSGI_APP}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable gunicorn-${PROJECT_NAME} > /dev/null
systemctl restart gunicorn-${PROJECT_NAME}
ok "Gunicorn configured and restarted."

# ============================================================================
# 9. Celery Worker Service
# ============================================================================
# Processes tasks from the three queues declared in settings.CELERY_TASK_ROUTES.
# `-Ofair` prevents one slow task from blocking the whole prefetch batch.
# `--without-gossip --without-mingle --without-heartbeat` trims 3 unnecessary
# background threads on a single-worker VPS.
# ============================================================================
log "Step 9: Configuring Celery worker"
cat > /etc/systemd/system/celery-${PROJECT_NAME}.service <<EOF
[Unit]
Description=Celery Worker for Pritech Host
After=network.target redis-server.service postgresql.service
Requires=redis-server.service

[Service]
User=root
Group=root
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin"
Environment="DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS}"
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${VENV_DIR}/bin/celery -A ${CELERY_APP} worker \\
    --loglevel=info \\
    --queues=${CELERY_QUEUES} \\
    --concurrency=${CELERY_CONCURRENCY} \\
    --without-gossip --without-mingle --without-heartbeat \\
    -Ofair
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable celery-${PROJECT_NAME} > /dev/null
systemctl restart celery-${PROJECT_NAME}
ok "Celery worker configured and restarted."

# ============================================================================
# 10. Celery Beat Service (periodic scheduler)
# ============================================================================
# Drives the daily renewal/dunning scan
# (billing.tasks.check_subscription_lifecycle) at 00:30 Africa/Blantyre.
# Can be silenced by setting DUNNING_ENABLED=False in .env.
# ============================================================================
log "Step 10: Configuring Celery beat"
cat > /etc/systemd/system/celery-beat-${PROJECT_NAME}.service <<EOF
[Unit]
Description=Celery Beat for Pritech Host
After=network.target redis-server.service

[Service]
User=root
Group=root
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin"
Environment="DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS}"
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${VENV_DIR}/bin/celery -A ${CELERY_APP} beat --loglevel=info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable celery-beat-${PROJECT_NAME} > /dev/null
systemctl restart celery-beat-${PROJECT_NAME}
ok "Celery beat configured and restarted."

# ============================================================================
# 11. Nginx Configuration
# ============================================================================
log "Step 11: Configuring Nginx"
cat > /etc/nginx/sites-available/${PROJECT_NAME} <<EOF
server {
    listen 80;
    server_name ${DOMAIN} ${WWW_DOMAIN} ${SERVER_IP};
    client_max_body_size 25M;

    # Static files: served straight from disk by nginx — no Python hop.
    location /static/ { alias ${PROJECT_DIR}/staticfiles/; expires 30d; access_log off; }
    location /media/  { alias ${PROJECT_DIR}/media/;      expires 7d;  }

    # Application: proxy everything else to Gunicorn via unix socket.
    location / {
        proxy_pass http://unix:${GUNICORN_SOCK};
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;

        # Webhook handlers return in <100ms thanks to Celery; 60s is a
        # generous safety net that still fails fast on a stuck backend.
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/${PROJECT_NAME} /etc/nginx/sites-enabled/${PROJECT_NAME}
nginx -t || die "Nginx config failed."
systemctl reload nginx
ok "Nginx reloaded."

# ============================================================================
# 12. Health check
# ============================================================================
# Confirm the app is actually serving through the whole nginx → gunicorn →
# Django stack. We hit 127.0.0.1 directly with a Host header so we don't
# depend on DNS or a working certificate yet.
#
# Expected codes:
#   200      → served directly (no HTTPS redirect configured yet, or SSL off)
#   301/302  → HTTPS redirect is set up (good — certbot usually does this)
#   404      → URL exists in urls.py but view raised Http404 (legal doc not seeded?)
#   5xx      → backend is broken; check journalctl -u gunicorn-pritechhost
#   000      → nginx or gunicorn is down entirely
# ============================================================================
log "Step 12: Health check"

check_url() {
    local path="$1"
    local status
    status=$(curl -sk -o /dev/null -w "%{http_code}" \
        -H "Host: ${DOMAIN}" "http://127.0.0.1${path}" 2>/dev/null || echo "000")
    case "$status" in
        200)      ok   "${path} → 200" ;;
        301|302)  ok   "${path} → ${status} (redirect to HTTPS)" ;;
        404)      warn "${path} → 404 (check legal seeding or URL config)" ;;
        5*)       warn "${path} → ${status} (server error — check journalctl)" ;;
        000)      warn "${path} → no response (nginx or gunicorn down)" ;;
        *)        warn "${path} → ${status}" ;;
    esac
}

check_url "/"
check_url "/legal/terms/"
check_url "/legal/privacy/"
check_url "/legal/aup/"
check_url "/legal/refund/"

# ============================================================================
# Done
# ============================================================================
printf '\n\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1;35m  Deployment Complete\033[0m\n'
printf '\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'

for svc in gunicorn-${PROJECT_NAME} celery-${PROJECT_NAME} celery-beat-${PROJECT_NAME} redis-server; do
    if systemctl is-active --quiet "$svc"; then
        ok "$svc is running."
    else
        warn "$svc is NOT running. Check: journalctl -u $svc -n 50"
    fi
done

cat <<EOF

  Test :     curl -I https://${DOMAIN}
  Admin:     https://${DOMAIN}/admin/
  Login:     admin@pritechhost.com / ChangeMe123!

  Logs:
    Gunicorn : journalctl -u gunicorn-${PROJECT_NAME} -f
    Celery   : journalctl -u celery-${PROJECT_NAME} -f
    Beat     : journalctl -u celery-beat-${PROJECT_NAME} -f
    Nginx    : journalctl -u nginx -f

  ⚠  ACTION REQUIRED:
     1. Change the admin password immediately (ChangeMe123! is public).
     2. Have a Malawi lawyer review the seeded Terms / Privacy / AUP /
        Refund documents in /admin/ → Legal Documents before going live.
        The seed content is a structural starting point, not legal advice.
     3. Fill in real EMAIL_HOST_PASSWORD and PAYCHANGU_* values in .env,
        then: systemctl restart gunicorn-${PROJECT_NAME} celery-${PROJECT_NAME} celery-beat-${PROJECT_NAME}
     4. Run: certbot --nginx -d ${DOMAIN} -d ${WWW_DOMAIN} --agree-tos --redirect --email admin@${DOMAIN}
     5. After Certbot: set SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True in .env, then restart the three app services.
EOF
