# Munshi Financials — Migration Notes

Migration of **https://www.munshifinancials.com/** from a Hostinger VPS to a new Ubuntu server.

- **Investigation started:** 2026-05-19
- **Source server:** Hostinger VPS, `root@187.127.145.82`
- **Destination server:** TBD
- **GitHub repo:** https://github.com/manikbajaj/ankita-munshi-financials-website.git
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
| 8000 | 127.0.0.1 | gunicorn (master + 2 workers) | Flask app — local only, behind nginx |
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

- Apex `munshifinancials.com` 301-redirects (over HTTPS) → `http://www.munshifinancials.com$request_uri` (the canonical host is **www**, and the redirect target is plain HTTP — the next hop upgrades to HTTPS).
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

These items must be transferred out-of-band to the destination server (never committed to GitHub).

## 8. Backup contents

**Backed up to local** (`c:\Brojects\munshifinancials\backup\2026-05-19\`):

- `munshi-source-2026-05-19.tar.gz` — `/root/Munshi/` minus `venv/` and `__pycache__/`
- `flaskapp.service` — copy of the systemd unit
- `flaskapp.nginx` — copy of `/etc/nginx/sites-available/flaskapp`
- `nginx.conf` — copy of the top-level nginx config (reference only)

**NOT backed up** (intentional):

- `venv/` — rebuild from `requirements.txt` on destination
- `/etc/letsencrypt/` — destination will mint a fresh cert via certbot
- Database dumps — no database exists

## 9. Operations performed on Hostinger

| When (UTC) | Action | Risk |
|---|---|---|
| 2026-05-19 11:29 | SSH login as root, read-only recon (`ls`, `cat`, `ps`, `ss`, `systemctl status`, `dpkg -l`) | None — read only |
| 2026-05-19 11:37 | Created tarball at `/tmp/munshi-source-2026-05-19.tar.gz` of `/root/Munshi/` (3.07 MB, 67 entries, venv & __pycache__ excluded) | None — read of `/root/Munshi`, write inside `/tmp` only |
| 2026-05-19 11:38 | Downloaded tarball + `flaskapp.service` + `flaskapp` nginx site + `nginx.conf` to local | None — read only over SCP |
| 2026-05-19 11:39 | `rm /tmp/munshi-source-2026-05-19.tar.gz` — confirmed removed | Reversible — tarball regeneratable |

*Live server has not been modified. Site is still live and serving traffic.*

### Backup integrity check

- SHA-256 of tarball matches on both sides: `dae28840547e6ab9c95e8074b27427d308ec157df9322a64b7ab9a16f9ff8ecc`
- Local extraction inspected: 67 entries, no `venv/`, no `__pycache__/`.

## 10. Local git repo state

- Initialized at `c:\Brojects\munshifinancials\` on branch `main`.
- First commit `f30f9d9` — "Initial import of Munshi Financials site from Hostinger (snapshot 2026-05-19)".
- 54 files staged. `git check-ignore` confirms `.env`, `key.json`, and `backup/` are excluded.
- Remote `origin` set to https://github.com/manikbajaj/ankita-munshi-financials-website.git.

## 11. Destination-server deployment plan

When SSH access to the destination Ubuntu server is available:

1. **Audit** existing nginx vhosts and listening ports — pick a free internal port for gunicorn (8000 may be in use by another app on the shared server).
2. **Install** Python 3.12, `python3-venv`, `git`. Confirm `nginx` and `certbot` are present.
3. **Clone repo** into a location consistent with the box's existing layout (e.g. `/var/www/munshi` or `/srv/munshi` — not `/root/` like the old box).
4. **Create venv** and `pip install -r requirements.txt`.
5. **Place secrets out-of-band:** copy `.env` and `key.json` into the deploy dir via SCP (never via git).
6. **Rotate** the hardcoded `app.secret_key` in `app.py` (or move it to `.env`) before first start.
7. **Write systemd unit** — rename from `flaskapp.service` to a specific name (e.g. `munshi.service`) to avoid colliding with other apps that may use the generic name.
8. **Create new nginx site file** in `/etc/nginx/sites-available/munshi`, symlink into `sites-enabled/`, `nginx -t`, then `systemctl reload nginx` (does not interrupt other vhosts).
9. **Issue TLS cert** with `certbot --nginx -d munshifinancials.com -d www.munshifinancials.com` once DNS is pointed at the new server (use `--staging` first to dry-run, or temporarily test via `curl --resolve` with the new IP before DNS cutover).
10. **Smoke-test** the new server via `curl --resolve munshifinancials.com:443:<NEW_IP>` against representative routes before flipping DNS.
11. **DNS cutover** — update A/AAAA records for `munshifinancials.com` and `www.munshifinancials.com` to the new IP. Lower TTL beforehand to speed up propagation.
12. **Post-cutover monitoring** — watch nginx access logs and `systemctl status munshi` on the new server for the first 24 h.
13. **Decommission** the Hostinger VPS only after the new server is stable.
