# Munshi Financials — Migration Notes

Migration of **https://www.munshifinancials.com/** from a Hostinger VPS to a new Ubuntu server.

**Status: complete (2026-05-19).** Site is live on the new server with valid TLS.

- **Investigation started:** 2026-05-19
- **Migration completed:** 2026-05-19
- **Source server:** Hostinger VPS, `root@187.127.145.82`
- **Destination server:** AWS EC2 (Mumbai), `aditya@35.154.93.59`, internal hostname `ip-10-0-0-86`. Shared with two other apps (`api.munshifinancials.com` Node service on `:3000`; `app.munshifinancials.com` static SPA from `/opt/munshi/frontend/dist`).
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

`app.secret_key` was hardcoded in `app.py` on the legacy server. **Rotated as part of this migration** — `app.py` now reads `FLASK_SECRET_KEY` from the environment (raises `KeyError` at import if missing). The new server has a fresh 64-byte url-safe random value in `/opt/munshi/website/.env`.

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
| `/opt/munshi/website/.env` (new) / `/root/Munshi/.env` (legacy) | `FIREBASE_API_KEY`, `FIREBASE_STORAGE_BUCKET`, `FLASK_SECRET_KEY` (the new server has a freshly generated value; the legacy server doesn't have this variable since the secret was hardcoded there) |
| `/opt/munshi/website/key.json` (new) / `/root/Munshi/key.json` (legacy) | Google service-account private key for project `munshifinancials-c365a` |

Both files are gitignored and were transferred out-of-band via SCP. They have mode `600` and are owned by the `munshi` user on the new server.

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

## 11. Destination server — final state

| Item | Value |
|---|---|
| Server | AWS EC2 (Mumbai), public IP `35.154.93.59`, internal hostname `ip-10-0-0-86` |
| OS | Ubuntu 24.04.4 LTS (matches source) |
| Python | 3.12.3 |
| Deploy user | `aditya` (login), `munshi` (runtime, via `sudo -u munshi`) |
| App path | `/opt/munshi/website` |
| venv | `/opt/munshi/website/venv` |
| WSGI server | gunicorn 26.0.0, 2 workers, bound `127.0.0.1:5000`, entry point `app:app` |
| systemd unit | `/etc/systemd/system/munshifinancials.service` (active, enabled on boot) |
| nginx site | `/etc/nginx/sites-available/munshifinancials.com` (symlinked into `sites-enabled/`) |
| TLS cert | `/etc/letsencrypt/live/munshifinancials.com/` — covers apex + www, issued by Let's Encrypt E8, expires 2026-08-17 (auto-renews) |
| Coexisting apps on box | `api.munshifinancials.com` (Node on `:3000`), `app.munshifinancials.com` (static SPA) — both untouched |

## 12. Operations performed on the destination server

| When (UTC) | Action |
|---|---|
| 2026-05-19 ~13:15 | SSH connectivity confirmed as `aditya@35.154.93.59` with dedicated ed25519 key |
| 2026-05-19 ~13:16 | Read-only server survey — confirmed OS, Python, tools, existing nginx sites, free port `:5000` |
| 2026-05-19 ~13:17 | `sudo -u munshi git clone` repo into `/opt/munshi/website` (was an empty pre-staged dir) |
| 2026-05-19 ~13:17 | Created venv via `python3 -m venv --without-pip` (system `python3-venv` package was missing); bootstrapped pip into the venv via the official `get-pip.py` (self-contained, no system changes) |
| 2026-05-19 ~13:18 | `pip install -r requirements.txt` and `pip install gunicorn` into the venv |
| 2026-05-19 ~13:19 | SCP'd `.env` and `key.json` to `/tmp` as `aditya`, then `sudo -u munshi cp` into `/opt/munshi/website/`, chmod 600. `/tmp` files removed |
| 2026-05-19 ~13:19 | Smoke-tested gunicorn directly (daemon mode, 1 worker, `curl http://127.0.0.1:5000/` returned 200) |
| 2026-05-19 ~13:20 | Installed `munshifinancials.service` via `sudo tee`, `daemon-reload`, `enable`, `start` — service active |
| 2026-05-19 ~13:22 | Installed nginx site `munshifinancials.com` (HTTP-only initially), `nginx -t`, symlinked into `sites-enabled/`, `systemctl reload nginx` |
| 2026-05-19 ~13:22 | Pre-DNS smoke test via `curl --resolve` from local machine — both apex and www returned 200 with the Flask homepage |
| 2026-05-19 ~13:44 | DNS cutover confirmed (apex A record `35.154.93.59`, `www` CNAME to apex). Client had already flipped the records |
| 2026-05-19 ~13:46 | `sudo certbot --nginx -d munshifinancials.com -d www.munshifinancials.com --non-interactive --agree-tos --email developer@daxido.com --redirect` — cert issued, nginx vhost rewritten to add SSL + HTTP→HTTPS redirect, nginx reloaded |
| 2026-05-19 ~13:47 | End-to-end verification: HTTPS apex 200, HTTPS www 200, HTTP→HTTPS 301, cert SANs cover both names, sibling apps still healthy |

### Deviations from the deployment-instructions PDF

The PDF assumed Flask + PostgreSQL with `wsgi:app` as the entry point. Our app is Flask + Firestore with `app:app`. Forced deviations:

1. **Entry point** — `wsgi:app` → `app:app` in `ExecStart` and the manual smoke-test command (the PDF anticipated this case in §3 and explicitly told us to update).
2. **Database section (PDF §2)** — skipped in full. The app uses Google Firestore; no PostgreSQL is needed. (PostgreSQL is running locally on `:5432` on the destination, but unused by this app.)
3. **gunicorn install** — used the PDF's documented fallback (`pip install gunicorn` separately) because gunicorn is not in `requirements.txt`.
4. **`.env` contents** — populated with `FIREBASE_API_KEY`, `FIREBASE_STORAGE_BUCKET`, `FLASK_SECRET_KEY` instead of database credentials.
5. **`key.json`** (not in PDF) — copied to `/opt/munshi/website/key.json` via SCP + `sudo -u munshi cp`. This file holds the GCP service-account credentials and is the only path by which the app talks to Firestore / Cloud Storage.
6. **`python3-venv` system package missing** — used `python3 -m venv --without-pip` plus `get-pip.py` bootstrap. Self-contained inside the venv; required no apt install or sudo outside the PDF allowlist.
7. **`sudo systemctl status` and `sudo journalctl`** — both showed up in the PDF allowlist but require a password in practice (the sudoers config doesn't include them as NOPASSWD). Substituted with `systemctl is-active` (no sudo needed) and process/port inspection. No impact on the migration.

## 13. Post-migration

- **Hostinger VPS** (`187.127.145.82`) is still running but receives no traffic (DNS no longer points to it). Recommend keeping it powered on for ~24–48 hours as a fallback before decommissioning.
- **TLS auto-renewal** is handled by the system certbot timer / cron on the new server (same mechanism as the existing two sites).
- **Backups (`backup/2026-05-19/`)** retain the source tarball + legacy systemd unit + legacy nginx config — keep this local for at least one renewal cycle (~90 days) in case anything needs to be cross-referenced.
- **DNS TTL** can be raised back to a normal value (3600s) once the site has been stable for ~24 hours.

## 14. Known issues (carried over, NOT fixed in this migration)

These were observed in the app code and intentionally left as-is per scope. None of them are migration-related; they're pre-existing app behaviour preserved during the move.

1. **OTP service is stubbed** — `modules/auth/otp_service.py` returns hardcoded `100001` and stores OTPs unhashed. Anyone can log in via partner/employee OTP flow with `100001`. Needs a follow-up fix.
2. **`startup.sh`** in the repo doesn't match how the app actually starts (systemd does). Cosmetic dead code.
3. **`User=root`** was the legacy systemd setup on Hostinger. The new server correctly runs as `User=munshi` (least privilege).
4. **No exception handling** around the Firebase Identity Toolkit HTTP call in `firebase_login_with_email_password`. Will surface as 500s on transient network errors.
