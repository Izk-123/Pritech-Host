#!/bin/bash
# ============================================================================
# Pritech Host — Production Deployment Script (Bulletproof)
# Repo   : https://github.com/Izk-123/Pritech-Host.git
# Domain : pritechhost.com
# ============================================================================

set -euo pipefail
set +H  # Disable history expansion (so passwords with '!' don't break)

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
GUNICORN_SOCK="${PROJECT_DIR}/gunicorn.sock"

# ─── Logging Helpers ────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

printf '\n\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1;35m  Pritech Host Deploy — %s\033[0m\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'

[[ $EUID -eq 0 ]] || die "Run as root."
for cmd in git python3 pip3 psql nginx openssl; do
    command -v $cmd &>/dev/null || die "Missing: $cmd"
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
# 2. Virtual Environment
# ============================================================================
log "Step 2: Python environment"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    ok "Virtualenv created."
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel --quiet
pip install -r requirements.txt --quiet
pip install gunicorn psycopg2-binary --quiet
ok "Dependencies installed."

# ============================================================================
# 3. Environment Variables (The Core Fix)
# ============================================================================
log "Step 3: Environment variables"
if [ -f "${PROJECT_DIR}/.env" ]; then
    # Read existing DB_PASSWORD safely, stripping any carriage returns or quotes
    DB_PASS=$(grep -E '^DB_PASSWORD=' "${PROJECT_DIR}/.env" | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'" || true)
    
    if [ -z "${DB_PASS}" ]; then
        die "DB_PASSWORD is empty or missing in existing .env. Fix .env first."
    fi
    ok ".env exists — loaded DB_PASSWORD from file."
else
    # First-time install: generate secure alphanumeric secrets
    DB_PASS="$(openssl rand -base64 32 | tr -d '/+=!@#$%^&*()_+-' | cut -c1-24)"
    DJANGO_SECRET="$(openssl rand -base64 48 | tr -d '/+=!@#$%^&*()_+-' | cut -c1-50)"
    cat > "${PROJECT_DIR}/.env" <<EOF
SECRET_KEY=${DJANGO_SECRET}
DEBUG=False
PRIMARY_DOMAIN=${DOMAIN}
SERVER_IP=${SERVER_IP}
SITE_ID=1
ALLOWED_HOSTS=${DOMAIN},${WWW_DOMAIN},${SERVER_IP},127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://${WWW_DOMAIN}

DB_ENGINE=django.db.backends.postgresql
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASS}
DB_HOST=127.0.0.1
DB_PORT=5432

PAYCHANGU_SECRET_KEY=sec-test-yourkey-here
PAYCHANGU_WEBHOOK_SECRET=sme.pritechmw
PAYCHANGU_CALLBACK_URL=https://${DOMAIN}/payments/callback/
PAYCHANGU_RETURN_URL=https://${DOMAIN}/payments/return/

EMAIL_HOST=mail.pritechmw.com
EMAIL_PORT=465
EMAIL_HOST_USER=noreply@pritechmw.com
EMAIL_HOST_PASSWORD=change-me
EMAIL_USE_SSL=True
EMAIL_USE_TLS=False
DEFAULT_FROM_EMAIL=Pritech Host <noreply@pritechmw.com>
SALES_EMAIL=sales@pritechmw.com

HESTIA_MOCK=True

SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
EOF
    chmod 600 "${PROJECT_DIR}/.env"
    ok ".env created with generated secrets."
    warn "SAVE THIS DB PASSWORD: ${DB_PASS}"
fi

# ============================================================================
# 4. PostgreSQL (Force Sync Password)
# ============================================================================
log "Step 4: PostgreSQL configuration"
# Escape single quotes in case user manually added them
ESCAPED_PASS=$(printf '%s' "${DB_PASS}" | sed "s/'/''/g")

# Create role or force-update its password to match .env
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

# Grant permissions
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" > /dev/null
sudo -u postgres psql -d "${DB_NAME}" -c "GRANT ALL ON SCHEMA public TO ${DB_USER};" > /dev/null
ok "Database role and password forced to match .env."

# ============================================================================
# 5. Test Database Connection
# ============================================================================
log "Step 5: Testing database connection"
if ! PGPASSWORD="${DB_PASS}" psql -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" > /dev/null 2>&1; then
    die "Database connection failed. Check your .env DB_PASSWORD and pg_hba.conf."
fi
ok "Database connection successful."

# ============================================================================
# 6. Django Setup
# ============================================================================
log "Step 6: Running Django setup"
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

Site.objects.update_or_create(id=1, defaults={'domain': 'pritechhost.com', 'name': 'Pritech Host'})
print("  ✔ Allauth Site domain updated to pritechhost.com")
PY

# ============================================================================
# 7. Gunicorn Service
# ============================================================================
log "Step 7: Configuring Gunicorn"
cat > /etc/systemd/system/gunicorn-${PROJECT_NAME}.service <<EOF
[Unit]
Description=Gunicorn for Pritech Host
After=network.target postgresql.service

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
# 8. Nginx Configuration
# ============================================================================
log "Step 8: Configuring Nginx"
cat > /etc/nginx/sites-available/${PROJECT_NAME} <<EOF
server {
    listen 80;
    server_name ${DOMAIN} ${WWW_DOMAIN} ${SERVER_IP};
    client_max_body_size 25M;
    location /static/ { alias ${PROJECT_DIR}/staticfiles/; expires 30d; access_log off; }
    location /media/  { alias ${PROJECT_DIR}/media/;      expires 7d;  }
    location / {
        proxy_pass http://unix:${GUNICORN_SOCK};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
    }
}
EOF

ln -sf /etc/nginx/sites-available/${PROJECT_NAME} /etc/nginx/sites-enabled/${PROJECT_NAME}
nginx -t || die "Nginx config failed."
systemctl reload nginx
ok "Nginx reloaded."

# ============================================================================
# 9. Done
# ============================================================================
printf '\n\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1;35m  Deployment Complete\033[0m\n'
printf '\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
systemctl is-active --quiet gunicorn-${PROJECT_NAME} && ok "Gunicorn is running." \
    || warn "Gunicorn failed to start. Check: journalctl -u gunicorn-${PROJECT_NAME} -n 50"
echo ""
echo "  Test:   curl -I https://${DOMAIN}"
echo "  Admin:  https://${DOMAIN}/admin/"
echo "  Login:  admin@pritechhost.com / ChangeMe123!"
echo "  ⚠ CHANGE THE ADMIN PASSWORD IMMEDIATELY!"
