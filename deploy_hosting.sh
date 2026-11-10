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
#  11.  Nginx config        → reverse proxy + static/media (HTTP only here)
#  12.  SSL / HTTPS         → idempotent Certbot run (THE FIX for 400s)
#  13.  Post-SSL hardening  → flip SECURE_SSL_REDIRECT + cookies if HTTPS OK
#  14.  Health check        → verifies every public URL responds
#
# The SSL step (12) is idempotent:
#   - no cert yet           → issues one and patches nginx to add listen 443
#   - cert exists, valid    → re-installs (fixes any missing listen 443 block)
#   - cert close to expiry  → renews
# This means re-running the deploy ALWAYS guarantees that ${DOMAIN} has a
# proper HTTPS listener — preventing the "400 Bad Request" failure mode where
# https://${DOMAIN}/ falls through to another site's default_server on 443.
#
# Idempotency notes:
#   - .env is never overwritten after first creation
#   - git reset --hard origin/main (discards local edits to tracked files)
#   - migrate is a fast no-op when schema is up to date
#   - seed_legal skips any document that already exists
#   - certbot --keep-until-expiring is safe to run daily (no-op when fresh)
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
# `git reset --hard origin/main` makes the checkout match the repo exactly.
# It DISCARDS local modifications to tracked files — commit and push any
# hot-fixes before re-running.
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
# NOTE: SECURE_SSL_REDIRECT / SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE
# start as False on purpose. Step 13 flips them to True ONLY after
# verifying HTTPS actually works. This prevents the site from being
# completely unusable if Certbot fails on the first deploy.
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

# ── Celery / Redis ──────────────────────────────────────────────────────────
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
REDIS_CACHE_URL=redis://127.0.0.1:6379/1

# ── Feature flags ───────────────────────────────────────────────────────────
HESTIA_MOCK=True
DUNNING_ENABLED=True

# ── Security toggles (auto-managed by deploy Step 13) ───────────────────────
# These will be flipped to True automatically once HTTPS is verified working.
# Do NOT set them manually to True unless certbot has succeeded.
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
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
# Certbot in Step 12 will patch this block to add `listen 443 ssl` and
# a proper http→https redirect. We deliberately do NOT put those here so
# that a fresh deploy is a valid HTTP-only state before any cert exists.
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

# Verify config — and explicitly surface any server_name conflicts
# (other sites on this shared server also claiming our domain or IP).
if ! nginx -t 2>/tmp/nginx-t.log; then
    cat /tmp/nginx-t.log
    die "Nginx config failed — see above."
fi
if grep -q "conflicting server name" /tmp/nginx-t.log; then
    warn "nginx reported server_name conflicts (other sites claiming ${DOMAIN} or ${SERVER_IP}):"
    grep "conflicting server name" /tmp/nginx-t.log | sort -u | sed 's/^/    /'
    warn "  This can cause HTTPS requests to hit the wrong backend."
    warn "  Fix by editing server_name in the offending site's config."
fi

systemctl reload nginx
ok "Nginx reloaded."

# ============================================================================
# 12. SSL / HTTPS Provisioning (idempotent Certbot)
# ============================================================================
# THIS is the step that prevents the "400 Bad Request on https://" class of
# failures. Without a `listen 443 ssl` block for ${DOMAIN}, nginx serves
# requests to https://${DOMAIN}/ using whatever OTHER site happens to be
# the default_server on port 443 — and that site's Django app rejects the
# Host header (400 DisallowedHost).
#
# Certbot --nginx --keep-until-expiring is idempotent:
#   - No cert yet                → issues one, patches nginx
#   - Cert exists, fresh         → re-installs (fixes missing listen 443)
#   - Cert close to expiry       → renews and re-installs
#
# We do NOT fail the whole deploy if certbot fails. The site can still
# serve HTTP, and Step 13 keeps SECURE_SSL_REDIRECT=False so HTTP remains
# usable while the operator investigates.
# ============================================================================
log "Step 12: SSL / HTTPS provisioning"

# --- 12a. Pre-flight: nginx answering on loopback? ---
if ! curl -sfI -H "Host: ${DOMAIN}" "http://127.0.0.1/" > /dev/null 2>&1; then
    warn "nginx is not answering ${DOMAIN} on loopback — Certbot will probably fail."
    warn "  Check: systemctl status nginx && journalctl -u nginx -n 30"
fi

# --- 12b. Report existing cert state ---
if certbot certificates 2>/dev/null | grep -q "Certificate Name: ${DOMAIN}"; then
    ok "Existing certificate found for ${DOMAIN} — will reinstall/renew."
else
    ok "No certificate yet — will issue one."
fi

# --- 12c. Run Certbot (non-interactive, idempotent) ---
# Flags explained:
#   --non-interactive      → never prompt (would hang under systemd/CI)
#   --keep-until-expiring  → don't renew a fresh cert, but DO reinstall
#                            to fix a missing listen 443 block
#   --expand               → if we add domains in the future, expand the cert
#   --redirect             → patch nginx to 301 http → https (idempotent)
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

# --- 12d. Verify the installed cert actually covers our domain ---
CERT_CN=$(echo | openssl s_client -connect "${DOMAIN}:443" -servername "${DOMAIN}" 2>/dev/null \
          | openssl x509 -noout -subject 2>/dev/null \
          | sed -n 's/^subject=\?CN *= *//p' || echo "")

if [[ "${CERT_CN}" == *"${DOMAIN}"* ]]; then
    ok "HTTPS cert verified (subject=${CERT_CN})"

    # --- 12e. Verify SANs include www variant ---
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
    warn "  Run: certbot certificates — HTTPS may not work correctly yet."
fi

# --- 12f. Detect other sites on this server whose certs will fail renewal ---
# On a shared VPS, other projects' certs can have stale DNS pointing to
# different servers. Their renewal timer will fail daily. We can't fix them
# (they belong to other projects), but the operator should know.
FAILED_EXTERNAL=""
for conf in /etc/letsencrypt/renewal/*.conf; do
    [[ -f "$conf" ]] || continue
    cert_name=$(basename "$conf" .conf)
    [[ "$cert_name" == "$DOMAIN" ]] && continue
    [[ "$cert_name" == "$WWW_DOMAIN" ]] && continue

    # Get the first non-www domain from this cert config
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
    warn "  To silence, either fix their DNS or remove with:"
    warn "    certbot delete --cert-name <name>"
fi

# ============================================================================
# 13. Post-SSL Hardening
# ============================================================================
# Set SECURE_SSL_REDIRECT and the secure-cookie flags to reflect ACTUAL
# HTTPS status, not aspiration:
#   - HTTPS verified working    → set all three to True (browser-side safety)
#   - HTTPS not working         → set all three to False (site serves via HTTP)
#
# This is what saves the deploy from bricking the site when Certbot fails
# on first run — the .env template starts with all flags False; we only
# flip them on after confirming HTTPS responds with 200.
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
# 14. Health Check
# ============================================================================
# Verifies the site responds through nginx → gunicorn → Django, and that
# the seeded legal pages are actually 200. Uses a relaxed matcher for HTTP
# so it works both pre-Certbot (200 direct) and post-Certbot (301 → HTTPS).
# ============================================================================
log "Step 14: Health check"

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

# Homepage: 200 (HTTP direct) or 301/302 (redirect to HTTPS)
check_url "http"  "/"               "200 301 302"
check_url "https" "/"               "200"

# Legal pages — proves seed_legal worked
check_url "https" "/legal/terms/"   "200"
check_url "https" "/legal/privacy/" "200"
check_url "https" "/legal/aup/"     "200"
check_url "https" "/legal/refund/"  "200"

# Admin — proves Django + session stack works
check_url "https" "/admin/login/"   "200"

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

# Report SSL status in the final summary
if curl -sfI --max-time 5 "https://${DOMAIN}/" >/dev/null 2>&1; then
    ok "HTTPS is active and verified."
    SSL_STATUS="ACTIVE"
else
    warn "HTTPS is NOT active — site serves via HTTP only."
    warn "  Fix: certbot --nginx -d ${DOMAIN} -d ${WWW_DOMAIN} --agree-tos --redirect --email ${ADMIN_EMAIL}"
    SSL_STATUS="NOT ACTIVE"
fi

cat <<EOF

  Test :     curl -I https://${DOMAIN}
  Admin:     https://${DOMAIN}/admin/
  Login:     admin@pritechhost.com / ChangeMe123!
  HTTPS:     ${SSL_STATUS}

  Logs:
    Gunicorn : journalctl -u gunicorn-${PROJECT_NAME} -f
    Celery   : journalctl -u celery-${PROJECT_NAME} -f
    Beat     : journalctl -u celery-beat-${PROJECT_NAME} -f
    Nginx    : journalctl -u nginx -f
    Certbot  : journalctl -u certbot -f

  ⚠  ACTION REQUIRED:
     1. Change the admin password immediately (ChangeMe123! is public).
     2. Have a Malawi lawyer review the seeded legal documents in
        /admin/ → Legal Documents before going live. The seeded text
        is a structural starting point, not legal advice.
     3. Fill in real EMAIL_HOST_PASSWORD and PAYCHANGU_* values in
        ${PROJECT_DIR}/.env, then:
          systemctl restart gunicorn-${PROJECT_NAME} celery-${PROJECT_NAME} celery-beat-${PROJECT_NAME}

  Certbot auto-renewal runs daily via systemd timer:
    systemctl list-timers | grep certbot

EOF
