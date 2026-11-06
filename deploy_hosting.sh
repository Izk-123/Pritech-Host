#!/bin/bash
# ============================================================================
# Production Deployment Script: Pritech Host
# Repo: https://github.com/Izk-123/Pritech-Host.git
# Domain: pritechhost.com
# Stack: Django + Gunicorn + Nginx + PostgreSQL
# ============================================================================

set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────────────────
PROJECT_NAME="pritechhost"
PROJECT_DIR="/home/project/${PROJECT_NAME}"
VENV_DIR="${PROJECT_DIR}/venv"
REPO_URL="https://github.com/Izk-123/Pritech-Host.git"
BRANCH="main"

DOMAIN="pritechhost.com"
WWW_DOMAIN="www.pritechhost.com"
SERVER_IP="204.168.251.91" # Update if your VPS IP changes

DB_NAME="pritechhost_db"
DB_USER="pritechhost_user"
DB_PASS="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
DJANGO_SECRET="$(openssl rand -base64 48 | tr -d '/+=' | cut -c1-50)"

WSGI_APP="pritech_host.wsgi:application"
DJANGO_SETTINGS="pritech_host.settings"

# ─── Logging Helpers ────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

log "======================================================="
log " Deploying Pritech Host to Production"
log "   Domain    : ${DOMAIN}"
log "   Directory : ${PROJECT_DIR}"
log "   Repo      : ${REPO_URL}"
log "======================================================="
sleep 2

# ─── 0. System Dependencies Check ───────────────────────────────────────────
log "Step 0: Verifying system dependencies"
for cmd in git python3 pip3 psql nginx certbot; do
    if ! command -v $cmd &>/dev/null; then
        die "Missing system dependency: $cmd. Please install it (apt install $cmd) and re-run."
    fi
done
ok "All system dependencies present."

# ─── 1. Clone or Update Repository ──────────────────────────────────────────
log "Step 1: Fetching repository"
if [ ! -d "${PROJECT_DIR}/.git" ]; then
    mkdir -p "${PROJECT_DIR}"
    git clone --branch "${BRANCH}" "${REPO_URL}" "${PROJECT_DIR}"
    ok "Repository cloned."
else
    cd "${PROJECT_DIR}"
    git fetch origin "${BRANCH}"
    git reset --hard "origin/${BRANCH}"
    ok "Repository updated to latest origin/${BRANCH}."
fi
cd "${PROJECT_DIR}"

# ─── 2. Python Virtual Environment & Dependencies ───────────────────────────
log "Step 2: Setting up Python environment"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    ok "Virtual environment created."
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel --quiet
pip install -r requirements.txt --quiet
pip install gunicorn psycopg2-binary --quiet
ok "Python dependencies installed."

# ─── 3. Environment Variables (.env) ────────────────────────────────────────
log "Step 3: Configuring .env file"
if [ ! -f "${PROJECT_DIR}/.env" ]; then
    cat > "${PROJECT_DIR}/.env" <<EOF
SECRET_KEY=${DJANGO_SECRET}
DEBUG=False
ALLOWED_HOSTS=${DOMAIN},${WWW_DOMAIN},${SERVER_IP},127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://${WWW_DOMAIN}
SITE_ID=1

# --- Database ---
DB_ENGINE=django.db.backends.postgresql
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASS}
DB_HOST=127.0.0.1
DB_PORT=5432

# --- PayChangu Integration ---
PAYCHANGU_SECRET_KEY=sec-test-yourkey-here
PAYCHANGU_WEBHOOK_SECRET=sme.pritechmw
PAYCHANGU_CALLBACK_URL=https://${DOMAIN}/payments/callback/
PAYCHANGU_RETURN_URL=https://${DOMAIN}/payments/return/

# --- Email SMTP (Update with your cPanel password) ---
EMAIL_HOST_PASSWORD=your_cpanel_email_password_here

# --- HestiaCP Integration ---
HESTIA_MOCK=True
EOF
    chmod 600 "${PROJECT_DIR}/.env"
    ok ".env created successfully."
else
    ok ".env already exists — keeping existing secrets."
    # Ensure ALLOWED_HOSTS is correct in case it was created previously
    sed -i "s/^ALLOWED_HOSTS=.*/ALLOWED_HOSTS=${DOMAIN},${WWW_DOMAIN},${SERVER_IP},127.0.0.1,localhost/" "${PROJECT_DIR}/.env"
    sed -i "s|^CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://${WWW_DOMAIN}|" "${PROJECT_DIR}/.env"
    ok ".env updated with correct domains."
fi

# ─── 4. PostgreSQL Database Setup ───────────────────────────────────────────
log "Step 4: Configuring PostgreSQL"
sudo -u postgres psql <<SQL > /dev/null
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
   ELSE
      ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASS}';
   END IF;
END
\$\$;
SQL

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" > /dev/null
sudo -u postgres psql -d "${DB_NAME}" -c "GRANT ALL ON SCHEMA public TO ${DB_USER};" > /dev/null
ok "Database and user ready."

# ─── 5. Django Setup ────────────────────────────────────────────────────────
log "Step 5: Running Django setup"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS}"

python manage.py migrate --noinput > /dev/null
ok "Database migrations applied."

python manage.py collectstatic --noinput > /dev/null
ok "Static files collected."

# Create Superuser if none exists
python manage.py shell <<'PY' || true
from users.models import User
if not User.objects.filter(is_superuser=True).exists():
    User.objects.create_superuser('admin', 'admin@pritechhost.com', 'ChangeMe123!')
    print("  ✔ Created superuser: admin@pritechhost.com / ChangeMe123!")
else:
    print("  ✔ Superuser already exists.")
PY

# ─── 6. Gunicorn Systemd Service ────────────────────────────────────────────
log "Step 6: Creating Gunicorn systemd service"
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
ExecStart=${VENV_DIR}/bin/gunicorn --workers 3 --bind unix:${PROJECT_DIR}/gunicorn.sock ${WSGI_APP}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable gunicorn-${PROJECT_NAME} > /dev/null
systemctl restart gunicorn-${PROJECT_NAME}
ok "Gunicorn service created and started."

# ─── 7. Nginx Configuration ─────────────────────────────────────────────────
log "Step 7: Configuring Nginx"
cat > /etc/nginx/sites-available/${PROJECT_NAME} <<EOF
server {
    listen 80;
    server_name ${DOMAIN} ${WWW_DOMAIN} ${SERVER_IP};

    client_max_body_size 25M;

    location /static/ {
        alias ${PROJECT_DIR}/staticfiles/;
        expires 30d;
        access_log off;
    }

    location /media/ {
        alias ${PROJECT_DIR}/media/;
        expires 7d;
    }

    location / {
        proxy_pass http://unix:${PROJECT_DIR}/gunicorn.sock;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
    }
}
EOF

ln -sf /etc/nginx/sites-available/${PROJECT_NAME} /etc/nginx/sites-enabled/${PROJECT_NAME}
nginx -t || die "Nginx configuration test failed!"
systemctl reload nginx
ok "Nginx configured and reloaded."

# ─── 8. SSL Certificate (Certbot) ───────────────────────────────────────────
log "Step 8: Setting up SSL Certificate"
if getent hosts "${DOMAIN}" > /dev/null; then
    certbot --nginx -d "${DOMAIN}" -d "${WWW_DOMAIN}" --non-interactive --agree-tos --register-unsafely-without-email --redirect || warn "Certbot failed. You can run it manually later."
    ok "SSL Certificates installed."
else
    warn "DNS for ${DOMAIN} does not point to this server yet."
    warn "Run this command after updating DNS: sudo certbot --nginx -d ${DOMAIN} -d ${WWW_DOMAIN}"
fi

# ─── 9. Final Status ────────────────────────────────────────────────────────
log "======================================================="
log " Deployment Complete!"
log "======================================================="
if systemctl is-active --quiet gunicorn-${PROJECT_NAME}; then
    ok "Gunicorn is running."
else
    warn "Gunicorn failed to start. Check logs: journalctl -u gunicorn-${PROJECT_NAME} -n 50"
fi

echo -e "\n\033[1;32mNext Steps:\033[0m"
echo "1. Update .env with your real PayChangu and Email passwords:"
echo "   nano ${PROJECT_DIR}/.env"
echo "2. Restart the app after editing .env:"
echo "   systemctl restart gunicorn-${PROJECT_NAME}"
echo "3. Update PayChangu Webhook to:"
echo "   https://${DOMAIN}/payments/webhook/"
echo "4. Update Google OAuth Redirect URIs to:"
echo "   https://${DOMAIN}/accounts/google/login/callback/"
echo "5. Login to Admin: https://${DOMAIN}/admin/"
echo "   Superuser: admin@pritechhost.com / ChangeMe123!"
echo "   (CHANGE THIS PASSWORD IMMEDIATELY)"
echo ""