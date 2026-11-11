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
#   7b. Security migration  → add missing security env keys to .env
#   7c. MFA detection       → auto-flip MFA_ENFORCE_FOR_STAFF once admin enrolls
#   8.  Gunicorn service    → HTTP app server (port = unix socket)
#   9.  Celery worker       → processes emails, provisioning, dunning
#  10.  Celery beat         → schedules the daily lifecycle scan
#  11.  Nginx config        → reverse proxy + static/media (HTTP only here)
#  12.  SSL / HTTPS         → idempotent Certbot run (THE FIX for 400s)
#  13.  Post-SSL hardening  → flip SECURE_SSL_REDIRECT + cookies if HTTPS OK
#  14.  Security check      → run Django's check --deploy + summarize
#  15.  Health check        → verifies every public URL responds
#  16.  Done                → prints action items
#
# Idempotency:
#   - .env is never overwritten after first creation
#   - new security env keys are APPENDED if missing (safe re-run)
#   - git reset --hard origin/main (discards local edits to tracked files)
#   - migrate is a fast no-op when schema is up to date
#   - seed_legal skips existing documents
#   - certbot --keep-until-expiring is safe to run daily
#
# Requirements on the target box:
#   - Ubuntu 22.04 / 24.04, run as root via SSH
#   - DNS for the domain + www already points at this server
#   - Ports 80 + 443 reachable from the public internet (ufw/firewall open)
#   - Base packages: git python3 python3-venv python3-pip
#                    postgresql postgresql-contrib nginx openssl curl
#                    certbot python3-certbot-nginx
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
ADMIN_EMAIL="admin@pritechhost.com"

DB_NAME="pritechhost_db"
DB_USER="pritechhost_user"

WSGI_APP="pritech_host.wsgi:application"
DJANGO_SETTINGS="pritech_host.settings"
CELERY_APP="pritech_host"
GUNICORN_SOCK="${PROJECT_DIR}/gunicorn.sock"

CELERY_QUEUES="default,emails,provisioning"
CELERY_CONCURRENCY="2"

# ─── Logging Helpers ────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

printf '\n\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1;35m  Pritech Host Deploy — %s\033[0m\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'

[[ $EUID -eq 0 ]] || die "Run as root."
for cmd in git python3 pip3 psql nginx openssl curl certbot systemctl; do
    command -v "$cmd" &>/dev/null || die "Missing: $cmd — install it first."
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
pip install celery redis gunicorn psycopg2-binary --quiet
ok "Python dependencies installed."

# ============================================================================
# 3. Environment Variables
# ============================================================================
# .env is the source of truth for secrets and per-environment config.
# First run  → generate DB password + Django SECRET_KEY, write .env.
# Re-runs    → preserve .env verbatim (never rotate secrets automatically).
#
# SECURITY DEFAULTS on first run:
#   AXES_ENABLED=True            (brute-force protection is on by default)
#   RATELIMIT_ENABLE=True        (rate limits are on by default)
#   MFA_ENFORCE_FOR_STAFF=False  (flipped True by step 7c once admin enrolls)
#   SECURE_SSL_REDIRECT=False    (flipped True by step 13 once HTTPS verified)
#   SESSION_COOKIE_SECURE=False  (flipped True by step 13)
#   CSRF_COOKIE_SECURE=False     (flipped True by step 13)
#   SECURE_HSTS_SECONDS=0        (operator turns this on manually after 30d)
# ============================================================================
log "Step 3: Environment variables"
if [ -f "${PROJECT_DIR}/.env" ]; then
    DB_PASS=$(grep -E '^DB_PASSWORD=' "${PROJECT_DIR}/.env" | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'" || true)
    if [ -z "${DB_PASS}" ]; then
        die "DB_PASSWORD is empty or missing in existing .env. Fix .env first."
    fi
    ok ".env exists — loaded DB_PASSWORD."
else
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
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://${WWW_DOMAIN},http://${DOMAIN},http://${WWW_DOMAIN},http://${SERVER_IP}

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

# ── Google OAuth ────────────────────────────────────────────────────────────
# Fill these in after creating a Google Cloud OAuth client.
# Leave empty to disable Google SSO (staff login still works).
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# ── Celery / Redis ──────────────────────────────────────────────────────────
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
REDIS_CACHE_URL=redis://127.0.0.1:6379/1

# ── Feature flags ───────────────────────────────────────────────────────────
HESTIA_MOCK=True
DUNNING_ENABLED=True

# ── Security features (SRS NFR-9 to NFR-11) ─────────────────────────────────
# Axes brute-force lockout: 5 failures → 1-hour lockout on IP+username.
AXES_ENABLED=True

# Rate limiting: per-view limits on payment, ticket, cookie endpoints.
RATELIMIT_ENABLE=True

# MFA enforcement: flipped True automatically by deploy step 7c once
# the admin has enrolled in 2FA. Until then, staff can log in with
# password only — which is fine because the admin hasn't got a TOTP
# secret yet.
MFA_ENFORCE_FOR_STAFF=False

# ── HTTPS hardening (auto-managed by deploy step 13) ───────────────────────
# These flip to True automatically once the script verifies that
# https://${DOMAIN}/ returns 200 with the correct certificate.
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False

# HSTS is intentionally 0 during the initial deploy. Do NOT enable
# until HTTPS has been stable for at least 7 days — see the settings.py
# rollout schedule. The operator enables this manually via:
#   sed -i 's/^SECURE_HSTS_SECONDS=.*/SECURE_HSTS_SECONDS=3600/' .env
# then increments gradually: 3600 → 86400 → 31536000.
SECURE_HSTS_SECONDS=0
EOF
    chmod 600 "${PROJECT_DIR}/.env"
    ok ".env created with generated secrets."
    warn "SAVE THIS DB PASSWORD: ${DB_PASS}"
fi

# ============================================================================
# 4. Redis
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
# 5. PostgreSQL
# ============================================================================
log "Step 5: PostgreSQL configuration"
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
log "Step 6: Testing database connection"
if ! PGPASSWORD="${DB_PASS}" psql -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" > /dev/null 2>&1; then
    die "Database connection failed. Check .env DB_PASSWORD and pg_hba.conf."
fi
ok "Database connection successful."

# ============================================================================
# 7. Django Setup
# ============================================================================
log "Step 7: Django setup"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS}"

python manage.py migrate --noinput > /dev/null
ok "Database migrations applied."

if [ -f "${PROJECT_DIR}/core/management/commands/seed_legal.py" ]; then
    if python manage.py seed_legal 2>&1 | grep -q "Created"; then
        ok "Legal documents seeded."
        warn "⚠  LEGAL CONTENT IS PLACEHOLDER — have a Malawi lawyer review it."
    else
        ok "Legal documents already present — skipped."
    fi
else
    warn "seed_legal command not found — skipping legal document seeding."
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
# 7b. Security env keys — ADD MISSING ONES ON RE-RUN
# ============================================================================
# Existing deployments predate the security features. Rather than telling
# the operator to manually add 6 env vars, we append them if missing.
# `sed -i` on an existing key is only used in later steps (MFA, SSL) —
# here we only ADD what's missing so we never override manual changes.
# ============================================================================
log "Step 7b: Ensuring security env keys are present"

add_env_if_missing() {
    local key="$1"
    local default="$2"
    if ! grep -q "^${key}=" "${PROJECT_DIR}/.env"; then
        echo "${key}=${default}" >> "${PROJECT_DIR}/.env"
        ok "  Added missing env key: ${key}=${default}"
    fi
}

add_env_if_missing "AXES_ENABLED"             "True"
add_env_if_missing "RATELIMIT_ENABLE"         "True"
add_env_if_missing "MFA_ENFORCE_FOR_STAFF"    "False"
add_env_if_missing "SECURE_HSTS_SECONDS"      "0"
add_env_if_missing "SECURE_HSTS_INCLUDE_SUBDOMAINS" "False"
add_env_if_missing "SECURE_HSTS_PRELOAD"      "False"
add_env_if_missing "GOOGLE_CLIENT_ID"         ""
add_env_if_missing "GOOGLE_CLIENT_SECRET"     ""

ok "Security env keys verified."

# ============================================================================
# 7c. MFA ENFORCEMENT — auto-flip based on admin enrollment
# ============================================================================
# MFA_ENFORCE_FOR_STAFF=True locks every staff member out of the site
# until they enroll in TOTP. If we enable it on first deploy, the admin
# is instantly locked out — because they haven't enrolled yet.
#
# So we detect on every deploy:
#   • Admin has enrolled    → set MFA_ENFORCE_FOR_STAFF=True (enforce!)
#   • Admin has NOT enrolled → set MFA_ENFORCE_FOR_STAFF=False (bootstrap)
#   • allauth.mfa broken   → leave as-is, warn loudly
#
# Enrollment is done once via:
#   https://pritechhost.com/accounts/2fa/totp/activate/
# (the URL is exempt from the middleware so the admin can reach it)
# ============================================================================
log "Step 7c: MFA enrollment detection"

MFA_STATUS=$(python manage.py shell <<'PY' 2>/dev/null | tail -1
try:
    from allauth.mfa.models import Authenticator
    from users.models import User
    admin = User.objects.filter(is_superuser=True).first()
    if not admin:
        print("no_admin")
    elif Authenticator.objects.filter(user=admin).exists():
        print("enrolled")
    else:
        print("not_enrolled")
except ImportError:
    print("mfa_unavailable")
except Exception:
    print("error")
PY
)

case "$MFA_STATUS" in
    enrolled)
        if grep -q '^MFA_ENFORCE_FOR_STAFF=False' "${PROJECT_DIR}/.env"; then
            sed -i 's/^MFA_ENFORCE_FOR_STAFF=False/MFA_ENFORCE_FOR_STAFF=True/' "${PROJECT_DIR}/.env"
            ok "Admin has MFA enrolled → MFA_ENFORCE_FOR_STAFF=True (enforced)."
        else
            ok "Admin has MFA enrolled → MFA already enforced."
        fi
        ;;
    not_enrolled)
        sed -i 's/^MFA_ENFORCE_FOR_STAFF=True/MFA_ENFORCE_FOR_STAFF=False/' "${PROJECT_DIR}/.env"
        warn "Admin has NOT enrolled in MFA → enforcement OFF."
        warn "  ⚠ ACTION: log in at https://${DOMAIN}/admin/, then visit"
        warn "            https://${DOMAIN}/accounts/2fa/totp/activate/"
        warn "            to enroll. Re-run this script to enforce MFA."
        ;;
    mfa_unavailable)
        warn "allauth.mfa not installed → MFA enforcement unavailable."
        warn "  Install with: pip install 'django-allauth[mfa]'"
        ;;
    no_admin)
        warn "No superuser found — skipping MFA detection."
        ;;
    *)
        warn "MFA status could not be determined (status=${MFA_STATUS})."
        warn "  Check that allauth.mfa is installed and DB migrations succeeded."
        ;;
esac

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
# 10. Celery Beat Service
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
# 11. Nginx Configuration (HTTP only)
# ============================================================================
log "Step 11: Configuring Nginx"
cat > /etc/nginx/sites-available/${PROJECT_NAME} <<EOF
server {
    listen 80;
    server_name ${DOMAIN} ${WWW_DOMAIN};
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
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/${PROJECT_NAME} /etc/nginx/sites-enabled/${PROJECT_NAME}

if ! nginx -t 2>/tmp/nginx-t.log; then
    cat /tmp/nginx-t.log
    die "Nginx config failed — see above."
fi
if grep -q "conflicting server name" /tmp/nginx-t.log; then
    warn "nginx reported server_name conflicts (other sites claiming ${DOMAIN} or ${SERVER_IP}):"
    grep "conflicting server name" /tmp/nginx-t.log | sort -u | sed 's/^/    /'
    warn "  This can cause HTTPS requests to hit the wrong backend."
fi

systemctl reload nginx
ok "Nginx reloaded."

# ============================================================================
# 12. SSL / HTTPS Provisioning (idempotent Certbot)
# ============================================================================
log "Step 12: SSL / HTTPS provisioning"

if ! curl -sfI -H "Host: ${DOMAIN}" "http://127.0.0.1/" > /dev/null 2>&1; then
    warn "nginx is not answering ${DOMAIN} on loopback — Certbot will probably fail."
    warn "  Check: systemctl status nginx && journalctl -u nginx -n 30"
fi

if certbot certificates 2>/dev/null | grep -q "Certificate Name: ${DOMAIN}"; then
    ok "Existing certificate found for ${DOMAIN} — will reinstall/renew."
else
    ok "No certificate yet — will issue one."
fi

if certbot --nginx \
        -d "${DOMAIN}" \
        -d "${WWW_DOMAIN}" \
        --agree-tos \
        --redirect \
        --non-interactive \
        --keep-until-expiring \
        --expand \
        --email "${ADMIN_EMAIL}" \
        2>&1 | tee /tmp/certbot-deploy.log | tail -3 \
        | grep -qE "Congratulations|not yet due for renewal|Successfully deployed|reinstalled" ; then
    ok "Certbot completed."
else
    warn "Certbot reported issues. Check /tmp/certbot-deploy.log for details."
    warn "  Common causes: DNS not propagated, port 80 blocked, LE rate limit."
fi

CERT_CN=$(echo | openssl s_client -connect "${DOMAIN}:443" -servername "${DOMAIN}" 2>/dev/null \
          | openssl x509 -noout -subject 2>/dev/null \
          | sed -n 's/^subject=\?CN *= *//p' || echo "")

if [[ "${CERT_CN}" == *"${DOMAIN}"* ]]; then
    ok "HTTPS cert verified (subject=${CERT_CN})"

    CERT_SAN=$(echo | openssl s_client -connect "${DOMAIN}:443" -servername "${DOMAIN}" 2>/dev/null \
               | openssl x509 -noout -ext subjectAltName 2>/dev/null || echo "")
    if echo "${CERT_SAN}" | grep -q "DNS:${WWW_DOMAIN}"; then
        ok "Cert SAN includes ${WWW_DOMAIN}."
    else
        warn "Cert does NOT cover ${WWW_DOMAIN}. Add it with:"
        warn "  certbot --nginx -d ${DOMAIN} -d ${WWW_DOMAIN} --expand"
    fi
else
    warn "Cert verification failed (subject was '${CERT_CN}', expected '${DOMAIN}')."
fi

# Detect other sites on this server whose certs will fail renewal
FAILED_EXTERNAL=""
for conf in /etc/letsencrypt/renewal/*.conf; do
    [[ -f "$conf" ]] || continue
    cert_name=$(basename "$conf" .conf)
    [[ "$cert_name" == "$DOMAIN" ]] && continue
    [[ "$cert_name" == "$WWW_DOMAIN" ]] && continue

    primary_domain=$(grep -oP '^domains\s*=\s*\K[^,\s]+' "$conf" 2>/dev/null | head -1 || echo "$cert_name")
    dns_ip=$(dig +short "$primary_domain" A 2>/dev/null | head -1 || true)

    if [[ -z "$dns_ip" ]]; then
        FAILED_EXTERNAL+="  ${primary_domain} → no A record (renewal will fail)\n"
    elif [[ "$dns_ip" != "$SERVER_IP" ]]; then
        FAILED_EXTERNAL+="  ${primary_domain} → DNS: ${dns_ip} (not this server; renewal will fail)\n"
    fi
done

if [[ -n "$FAILED_EXTERNAL" ]]; then
    warn "Other domains on this server have broken cert renewals:"
    printf "%b" "$FAILED_EXTERNAL" | sed 's/^/    /'
    warn "  Their renewals fail daily but don't affect ${DOMAIN}."
    warn "  Silence with: certbot delete --cert-name <name>"
fi

# ============================================================================
# 13. Post-SSL Hardening
# ============================================================================
log "Step 13: Post-SSL hardening"

if curl -sfI --max-time 5 "https://${DOMAIN}/" >/dev/null 2>&1; then
    ok "HTTPS responds — enabling SECURE_SSL_REDIRECT + secure cookies."
    sed -i 's/^SECURE_SSL_REDIRECT=.*/SECURE_SSL_REDIRECT=True/'       "${PROJECT_DIR}/.env"
    sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=True/'   "${PROJECT_DIR}/.env"
    sed -i 's/^CSRF_COOKIE_SECURE=.*/CSRF_COOKIE_SECURE=True/'         "${PROJECT_DIR}/.env"
else
    warn "HTTPS is NOT responding — leaving redirects OFF so HTTP still works."
    warn "  Investigate with: certbot certificates"
    warn "                    nginx -T | grep -A2 'server_name ${DOMAIN}'"
    sed -i 's/^SECURE_SSL_REDIRECT=.*/SECURE_SSL_REDIRECT=False/'       "${PROJECT_DIR}/.env"
    sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=False/'   "${PROJECT_DIR}/.env"
    sed -i 's/^CSRF_COOKIE_SECURE=.*/CSRF_COOKIE_SECURE=False/'         "${PROJECT_DIR}/.env"
fi

systemctl restart gunicorn-${PROJECT_NAME} celery-${PROJECT_NAME} celery-beat-${PROJECT_NAME}
ok "Services restarted to pick up the new .env values."

# ============================================================================
# 14. Django check --deploy
# ============================================================================
# Django's `check --deploy` audits the settings for production
# readiness. It emits WARNING-level issues for insecure defaults like
# `DEBUG=True`, missing HTTPS redirects, or weak SECRET_KEY.
#
# With our config:
#   • After step 13 succeeds → 0 warnings
#   • Before HTTPS is verified → warnings about cookies/redirects
#   • Always: SECURE_HSTS_SECONDS warning if HSTS is 0
#     (this is INTENTIONAL — see the settings.py rollout schedule)
#
# We don't fail on warnings — the operator decides.
# ============================================================================
log "Step 14: Django deployment check"

DEPLOY_CHECK_LOG=/tmp/django-check-deploy.log
if python manage.py check --deploy > "${DEPLOY_CHECK_LOG}" 2>&1; then
    WARNING_COUNT=$(grep -c "WARNINGS:" "${DEPLOY_CHECK_LOG}" || echo "0")
    if [[ "${WARNING_COUNT}" -gt 0 ]] && ! grep -q "0 silenced" <(grep "System check identified" "${DEPLOY_CHECK_LOG}" || echo ""); then
        warn "check --deploy reported configuration warnings:"
        grep -E "^\s*[?]|WARNINGS:" "${DEPLOY_CHECK_LOG}" | sed 's/^/    /' | head -10
        warn "  Full log: ${DEPLOY_CHECK_LOG}"
    else
        ok "check --deploy passed (0 issues)."
    fi
else
    warn "check --deploy failed — investigate before going live:"
    sed 's/^/    /' "${DEPLOY_CHECK_LOG}" | tail -20
fi

# Expect a warning about SECURE_HSTS_SECONDS=0 until the operator
# enables HSTS after 30+ days of stable HTTPS. That's intentional.
if grep -q "SECURE_HSTS_SECONDS" "${DEPLOY_CHECK_LOG}" 2>/dev/null; then
    warn "  ℹ  SECURE_HSTS_SECONDS=0 warning is INTENTIONAL."
    warn "     Enable HSTS manually after 30 days of stable HTTPS. See"
    warn "     settings.py section 15 for the safe rollout schedule."
fi

# ============================================================================
# 15. Health Check
# ============================================================================
log "Step 15: Health check"

check_url() {
    local scheme="$1"
    local path="$2"
    shift 2
    local expect_string="$*"
    local status
    status=$(curl -sk -o /dev/null -w "%{http_code}" \
        --max-time 5 "${scheme}://${DOMAIN}${path}" 2>/dev/null || echo "000")

    for expect in $expect_string; do
        if [[ "$status" == "$expect" ]]; then
            ok "${scheme}://${DOMAIN}${path} → ${status}"
            return 0
        fi
    done

    case "$status" in
        000) warn "${scheme}://${DOMAIN}${path} → no response (nginx/gunicorn down?)" ;;
        *)   warn "${scheme}://${DOMAIN}${path} → ${status} (expected one of: ${expect_string})" ;;
    esac
}

check_url "http"  "/"               "200 301 302"
check_url "https" "/"               "200"
check_url "https" "/legal/terms/"   "200"
check_url "https" "/legal/privacy/" "200"
check_url "https" "/legal/aup/"     "200"
check_url "https" "/legal/refund/"  "200"
check_url "https" "/admin/login/"   "200"

# Security headers — spot-check that they're actually being emitted
HDRS=$(curl -skI --max-time 5 "https://${DOMAIN}/" 2>/dev/null)
for hdr in "X-Content-Type-Options: nosniff" \
           "X-Frame-Options: SAMEORIGIN" \
           "Referrer-Policy: same-origin"; do
    if echo "$HDRS" | grep -qi "${hdr%:*}:"; then
        ok "Header present: ${hdr}"
    else
        warn "Header MISSING: ${hdr}"
    fi
done

# ============================================================================
# Done
# ============================================================================
printf '\n\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1;35m  Deployment Complete\033[0m\n'
printf '\033[1;35m══════════════════════════════════════════════════════════\033[0m\n'

for svc in gunicorn-${PROJECT_NAME} celery-${PROJECT_NAME} celery-beat-${PROJECT_NAME} redis-server nginx; do
    if systemctl is-active --quiet "$svc"; then
        ok "$svc is running."
    else
        warn "$svc is NOT running. Check: journalctl -u $svc -n 50"
    fi
done

if curl -sfI --max-time 5 "https://${DOMAIN}/" >/dev/null 2>&1; then
    ok "HTTPS is active and verified."
    SSL_STATUS="ACTIVE"
else
    warn "HTTPS is NOT active — site serves via HTTP only."
    SSL_STATUS="NOT ACTIVE"
fi

# Report MFA enforcement state
MFA_STATE=$(grep '^MFA_ENFORCE_FOR_STAFF=' "${PROJECT_DIR}/.env" | cut -d= -f2)
if [[ "${MFA_STATE}" == "True" ]]; then
    MFA_LINE="✔ ENFORCED (staff must use TOTP)"
else
    MFA_LINE="⚠ NOT ENFORCED — enroll admin at /accounts/2fa/totp/activate/, then re-run"
fi

cat <<EOF

  Test :     curl -I https://${DOMAIN}
  Admin:     https://${DOMAIN}/admin/
  Login:     admin@pritechhost.com / ChangeMe123!
  HTTPS:     ${SSL_STATUS}
  2FA:       ${MFA_LINE}

  Logs:
    Gunicorn : journalctl -u gunicorn-${PROJECT_NAME} -f
    Celery   : journalctl -u celery-${PROJECT_NAME} -f
    Beat     : journalctl -u celery-beat-${PROJECT_NAME} -f
    Nginx    : journalctl -u nginx -f
    Certbot  : journalctl -u certbot -f

  ⚠  ACTION REQUIRED:

     1. CHANGE THE ADMIN PASSWORD IMMEDIATELY.
        ChangeMe123! is public. Log in and change it before anything else.

     2. ENROLL ADMIN IN 2FA, THEN RE-RUN THIS SCRIPT.
        1. Log in at https://${DOMAIN}/admin/
        2. Visit https://${DOMAIN}/accounts/2fa/totp/activate/
        3. Scan the QR code with Google Authenticator / Authy / 1Password
        4. Save the recovery codes somewhere safe
        5. Re-run this script — it will auto-enable MFA enforcement

     3. HAVE A MALAWI LAWYER REVIEW THE LEGAL DOCUMENTS.
        /admin/ → Legal Documents contains placeholder Terms, Privacy,
        AUP, and Refund Policy. They are a structural starting point,
        not legal advice.

     4. FILL IN REAL PAYCHANGU CREDENTIALS.
        PAYCHANGU_SECRET_KEY and PAYCHANGU_WEBHOOK_SECRET in .env are
        placeholders. Copy the real values from the PayChangu dashboard
        → Settings → API Keys, then:
          systemctl restart gunicorn-${PROJECT_NAME} celery-${PROJECT_NAME}

     5. FILL IN REAL EMAIL CREDENTIALS.
        EMAIL_HOST_PASSWORD in .env is `change-me`. Use the real cPanel
        mailbox password, then restart the same three services.

     6. CONFIGURE GOOGLE OAUTH (optional).
        GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env are empty.
        See https://console.cloud.google.com/apis/credentials.

  Certbot auto-renewal runs daily via systemd timer:
    systemctl list-timers | grep certbot

  When you're ready to enable HSTS (after 30 days of stable HTTPS):
    sed -i 's/^SECURE_HSTS_SECONDS=.*/SECURE_HSTS_SECONDS=3600/' ${PROJECT_DIR}/.env
    # wait one week, then 86400, then 31536000

EOF