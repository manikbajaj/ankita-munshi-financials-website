# Munshi Financials — Migration Notes

Live, evolving log of findings and actions for migrating
**https://www.munshifinancials.com/** from Hostinger to a new Ubuntu server.

- **Investigation started:** 2026-05-19
- **Operator:** developer@daxido.com
- **Source server:** Hostinger VPS, `root@187.127.145.82` (SSH password auth)
- **Destination server:** *not yet provided*
- **GitHub repo:** https://github.com/manikbajaj/ankita-munshi-financials-website.git (do **NOT** push yet)
- **Local working dir:** `c:\Brojects\munshifinancials\`
- **Local backup dir:** `c:\Brojects\munshifinancials\backup\2026-05-19\`

---

## 1. Source server — system facts

| Item | Value |
|---|---|
| Hostname | `srv1552755` |
| OS | Ubuntu 24.04.4 LTS (Noble Numbat), kernel 6.8.0-106-generic, x86_64 |
| Uptime at audit | 46 days |
| Load | idle (0.00, 0.00, 0.00) |
| Other linux users | `ubuntu` (default, otherwise empty) |
| Security agent present | `monarx-agent` listening on `127.0.0.1:65529` |

## 2. Listening ports

| Port | Bind | Process | Notes |
|---|---|---|---|
| 22 | 0.0.0.0 / [::] | sshd | SSH |
| 80 | 0.0.0.0 | nginx | HTTP (redirects to HTTPS) |
| 443 | 0.0.0.0 | nginx | HTTPS, terminating TLS for the Flask app |
| 8000 | 127.0.0.1 | gunicorn (3 workers / 2 + master) | Flask app — local only, behind nginx |
| 53 | 127.0.0.53 / 127.0.0.54 | systemd-resolve | DNS stub resolver |
| 65529 | 127.0.0.1 | monarx-agent | Security agent (local only) |

Only ports `22`, `80`, `443` are exposed publicly.

## 3. Application stack

- **Framework:** Flask
- **Python:** 3.12.3 (system) — app uses its own venv at `/root/Munshi/venv`
- **WSGI server:** gunicorn, 2 workers, bound `127.0.0.1:8000`, 600 s timeout
- **App path:** `/root/Munshi/`
- **WSGI callable:** `app:app` (module `app.py`, Flask object `app`)
- **No local database.** Data layer is **Google Firestore** via `firebase-admin`. Cloud storage via `google-cloud-storage`.
- **GCP project:** `munshifinancials-c365a` (from `key.json`)

### `/root/Munshi/` layout

```
.env                  129 B   secrets (Firebase API key, storage bucket name)
app.py                1002 B  Flask entrypoint, registers 4 blueprints
key.json              2406 B  Google service-account credentials (DO NOT COMMIT)
requirements.txt      163 B   Flask, python-dotenv, firebase-admin, google-cloud-*, bcrypt, Werkzeug, requests, pandas, reportlab
startup.sh            45 B    `gunicorn --bind=0.0.0.0 --timeout 600 app:app`  (NOT the actual launch path — systemd is)
modules/              Blueprints: admin_routes.py (60 KB), auth/, employee/, partner/, firebase_client.py
static/               3.1 MB  static assets
templates/            336 KB  Jinja templates (home.html, error.html, …)
venv/                 262 MB  Python virtualenv (rebuild on new server — NOT migrated)
__pycache__/          discardable
```

### Flask blueprints registered in `app.py`

- `modules.admin_routes` → `admin_bp`
- `modules.partner.partner_routes` → `partner_bp`
- `modules.employee.employee_routes` → `employee_bp`
- `modules.employee.employee_sales_routes` → `employee_sales_bp`

`app.secret_key` is hardcoded in `app.py` as `"super-secret-key-change-this"` — **rotate before going live on the new server.**

## 4. Process supervision

- Managed by **systemd**, unit `/etc/systemd/system/flaskapp.service`, currently `active (running)`.

```ini
[Unit]
Description=Flask App
After=network.target

[Service]
User=root
WorkingDirectory=/root/Munshi
ExecStart=/root/Munshi/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

- Service enabled at boot. Memory ~185 MB peak. CPU lifetime ~16 min.
- **No root crontab.** `/etc/cron.d/` contains only OS-level jobs (`certbot`, `e2scrub_all`, `monarx-update`, `sysstat`) — none app-specific.

## 5. nginx

- **Site config:** `/etc/nginx/sites-available/flaskapp` (symlinked into `sites-enabled`).
- **Top-level config:** `/etc/nginx/nginx.conf` is mostly stock Ubuntu default.

### Behaviour encoded by the site config

- Apex `munshifinancials.com` 301-redirects (over HTTPS) → `http://www.munshifinancials.com$request_uri` (note: the canonical host is **www**, and the redirect target is plain HTTP — letting the next server hop upgrade to HTTPS).
- `www.munshifinancials.com` is the real vhost: `proxy_pass http://127.0.0.1:8000` with `Host` and `X-Real-IP` headers set.
- Port 80 vhosts for both names return 301 → HTTPS (managed by certbot).
- TLS via Let's Encrypt files (see §6).

## 6. TLS / certbot

- Let's Encrypt cert at `/etc/letsencrypt/live/munshifinancials.com/` covers **both** `munshifinancials.com` and `www.munshifinancials.com`.
- Auto-renewal handled by `/etc/cron.d/certbot`.
- **Not migrated** — destination server will issue its own cert with certbot once DNS is cut over.

## 7. Secrets inventory (DO NOT COMMIT)

| File | Contains |
|---|---|
| `/root/Munshi/.env` | `FIREBASE_API_KEY`, `FIREBASE_STORAGE_BUCKET` |
| `/root/Munshi/key.json` | Google service-account private key for project `munshifinancials-c365a` |
| `app.py` (line setting `app.secret_key`) | Hardcoded Flask session secret — should be moved to `.env` and rotated |

These three items must be transferred out-of-band to the destination server (never committed to GitHub).

## 8. Backup plan / what we are taking

**Backed up to local** (`c:\Brojects\munshifinancials\backup\2026-05-19\`):

- `Munshi-source.tar.gz` — `/root/Munshi/` minus `venv/` and `__pycache__/`
- `flaskapp.service` — copy of the systemd unit
- `flaskapp.nginx` — copy of `/etc/nginx/sites-available/flaskapp`
- `nginx.conf` — copy of the top-level nginx config (reference only)

**NOT backed up** (intentional):

- `venv/` — rebuild from `requirements.txt` on destination
- `/etc/letsencrypt/` — destination will mint a fresh cert via certbot
- Database dumps — there is no database to dump

## 9. Operations performed on Hostinger (so far)

| When (UTC) | Action | Risk |
|---|---|---|
| 2026-05-19 | SSH login as root, read-only recon (`ls`, `cat`, `ps`, `ss`, `systemctl status`, `dpkg -l`) | None — read only |

*Nothing has been changed on the live server. Site is still live and serving traffic.*

## 10. Next steps (still pending)

1. Create source tarball on Hostinger `/tmp/` and pull to local; delete tarball from server.
2. Pull config files (systemd unit, nginx vhost, nginx.conf) to local.
3. Initialize local git repo, scrub secrets via `.gitignore`. **Do not push.**
4. Wait for destination server SSH + (now optional) PostgreSQL info.
5. On destination: install Python 3.12 + venv tooling, restore source, recreate venv from requirements, install `.env` + `key.json` out-of-band, write systemd unit, write nginx vhost (alongside existing sites, not replacing them), reload nginx, run certbot, smoke-test.
6. Coordinate DNS cutover.
