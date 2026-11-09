#!/bin/bash
# ============================================================================
# Pritech Host — Production Deployment Script (Bulletproof)
# Repo   : https://github.com/Izk-123/Pritech-Host.git
# Domain : pritechhost.com
#
# Provisions:
#   • system packages (git, python, postgres, nginx, redis)
#   • python venv + requirements + celery/redis/gunicorn/psycopg2
#   • PostgreSQL role + database (password synced from .env)
#   • Django migrate / collectstatic / admin bootstrap
#   • systemd : gunicorn, celery worker, celery beat
#   • nginx reverse proxy
# ============================================================================

set -euo pipefail
set +H  # Disable history expansion so passwords with '!' don't break

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
for cmd in git python3 pip3 psql nginx openssl systemctl; do
    command -v "$cmd" &>/dev/null || die "Missing: $cmd"
done
ok "System dependencies verified."

# ============================================================================
# 1. Fetch Code
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
log "Step 2: Python environment"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    ok "Virtualenv created."
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel --quiet
pip install -r requirements.txt --quiet
# Celery + Redis client + gunicorn + postgres driver.
# (Also add these to requirements.txt in the repo so future deploys are
#  deterministic — pip install is idempotent so re-running is safe.)
pip install celery redis gunicorn psycopg2-binary --quiet
ok "Python dependencies installed."

# ============================================================================
# 3. Environment Variables
# ============================================================================
log "Step 3: Environment variables"
if [ -f "${PROJECT_DIR}/.env" ]; then
    # Read existing DB password safely (strip CR / surrounding quotes).
    DB_PASS=$(grep -E '^DB_PASSWORD=' "${PROJECT_DIR}/.env" | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'" || true)
    if [ -z "${DB_PASS}" ]; then
        die "DB_PASSWORD is empty or missing in existing .env. Fix .env first."
    fi
    ok ".env exists — loaded DB_PASSWORD."
else
    # First-time install: generate secure secrets.
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
DATABASE_URL=postgres://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
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

# ── Feature flags ───────────────────────────────────────────────────────────
HESTIA_MOCK=True

# ── Cookies / HTTPS (set True once SSL terminates correctly) ────────────────
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
EOF
    chmod 600 "${PROJECT_DIR}/.env"
    ok ".env created with generated secrets."
    warn "SAVE THIS DB PASSWORD: ${DB_PASS}"
fi

# ============================================================================
# 4. Redis (Celery broker)
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

# Create DB if missing
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 \
    || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" > /dev/null
sudo -u postgres psql -d "${DB_NAME}" -c "GRANT ALL ON SCHEMA public TO ${DB_USER};" > /dev/null
ok "Database role and password synced with .env."

# ============================================================================
# 6. Test Database Connection
# ============================================================================
log "Step 6: Testing database connection"
if ! PGPASSWORD="${DB_PASS}" psql -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" > /dev/null 2>&1; then
    die "Database connection failed. Check .env DB_PASSWORD and pg_hba.conf."
fi
ok "Database connection successful."

# ============================================================================
# 7. Django Setup
# ============================================================================
log "Step 7: Running Django setup"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS}"

python manage.py migrate --noinput > /dev/null
ok "Database migrations applied."

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
# 10. Celery Beat Service (scheduler for future periodic tasks)
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

    location /static/ { alias ${PROJECT_DIR}/staticfiles/; expires 30d; access_log off; }
    location /media/  { alias ${PROJECT_DIR}/media/;      expires 7d;  }

    location / {
        proxy_pass http://unix:${GUNICORN_SOCK};
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;

        # Webhook endpoints can wait a few seconds, but not 60+. We rely on
        # Celery to keep them fast; this is just a safety net.
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/${PROJECT_NAME} /etc/nginx/sites-enabled/${PROJECT_NAME}
nginx -t || die "Nginx config failed."
systemctl reload nginx
ok "Nginx reloaded."

# ============================================================================
# 12. Done
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

echo ""
echo "  Test :   curl -I https://${DOMAIN}"
echo "  Admin:   https://${DOMAIN}/admin/"
echo "  Login:   admin@pritechhost.com / ChangeMe123!"
echo "  Celery:  journalctl -u celery-${PROJECT_NAME} -f"
echo "  ⚠ CHANGE THE ADMIN PASSWORD IMMEDIATELY!"