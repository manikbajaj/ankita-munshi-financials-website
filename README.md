# Munshi Financials — Repository Guide

This repository holds the source for the **www.munshifinancials.com** Flask web application, taken as a snapshot from the live Hostinger VPS on 2026-05-19.

The application is a **multi-role partner-management portal**. Three user roles share the same Flask process:

- **Admin** — internal staff with full control over partners, employees, clients, orders, transactions, invoices, and configuration.
- **Employee** — internal users. Two sub-areas: an operational area (process partner orders) and a *sales* area (create orders, register new partners).
- **Partner** — external users. Manage their own clients, place orders, top up wallet, download invoices.

There is also a **public home page** at `/`.

All data is stored in **Google Firestore** (no SQL database). File uploads (invoices, shared PDFs, partner profile photos) live in **Google Cloud Storage** in the same GCP project.

> The migration of this site from Hostinger to a new Ubuntu server is tracked separately in [MIGRATION_NOTES.md](MIGRATION_NOTES.md).

---

## 1. Tech stack

| Layer | What's used |
|---|---|
| Language | Python 3.12 |
| Web framework | Flask (with Blueprints) |
| WSGI server | gunicorn |
| Reverse proxy | nginx |
| TLS | Let's Encrypt via certbot |
| Process supervision | systemd (unit name `flaskapp.service` on the legacy box) |
| Data store | Google Firestore (via `firebase-admin`) |
| File storage | Google Cloud Storage (via `google-cloud-storage`) |
| User auth | Firebase Identity Toolkit (`signInWithPassword` REST endpoint) |
| Password hashing for OTPs | `bcrypt` (currently bypassed — see §10) |
| Templating | Jinja2 |
| PDF generation | `reportlab` |
| Excel export | `pandas` |
| HTTP client | `requests` |
| Env loading | `python-dotenv` |

`requirements.txt`:

```
Flask
python-dotenv
firebase-admin
google-cloud-firestore
google-cloud-storage
google-auth
bcrypt
Werkzeug
requests
pandas
reportlab
google-cloud-core
```

The virtualenv (`venv/`) is **not** committed — recreate it locally with the steps in §9.

---

## 2. Repository layout

```
/
├── app.py                  Flask entrypoint. Loads .env, initializes FirebaseClient, registers blueprints.
├── requirements.txt        Python dependencies (Flask, firebase-admin, reportlab, pandas, …)
├── startup.sh              Stale dev helper — NOT how the app actually starts (systemd is)
├── .env                    Secrets: FIREBASE_API_KEY, FIREBASE_STORAGE_BUCKET (gitignored)
├── .env.example            Template of required env vars (committed)
├── key.json                Google service-account JSON for GCP project `munshifinancials-c365a` (gitignored)
├── .gitignore              Excludes secrets, venv, __pycache__, backup/
├── README.md               This file
├── MIGRATION_NOTES.md      Cutover plan from Hostinger to new server
├── backup/                 Local-only backup of the legacy server (gitignored)
│   └── 2026-05-19/
│       ├── munshi-source-2026-05-19.tar.gz   Full tarball of /root/Munshi from Hostinger
│       ├── flaskapp.service                  Copy of the systemd unit
│       ├── flaskapp.nginx                    Copy of /etc/nginx/sites-available/flaskapp
│       └── nginx.conf                        Copy of /etc/nginx/nginx.conf (reference)
│
├── modules/                Flask blueprints and helpers
│   ├── __init__.py
│   ├── firebase_client.py        Singleton wrapper over Firestore + GCS + Identity Toolkit
│   ├── admin_routes.py           Admin blueprint (~60 KB, 36 routes)
│   ├── auth/                     Cross-role auth helpers
│   │   ├── login_common.py       Partner lookup by email/phone/partner_code
│   │   ├── otp_service.py        OTP generate/hash/verify  (CURRENTLY BYPASSED — §10)
│   │   └── password_rules.py     Password validators
│   ├── partner/
│   │   └── partner_routes.py     Partner blueprint (login, OTP, dashboard, orders, wallet, invoices)
│   └── employee/
│       ├── employee_routes.py        Operational employee blueprint
│       └── employee_sales_routes.py  Sales-employee blueprint
│
├── templates/              Jinja templates (336 KB)
│   ├── base.html, home.html, error.html
│   ├── admin/              Admin UI (login, dashboard, categories, partner orders, clients, shared PDFs, configuration)
│   ├── employee/           Employee UI (login, OTP, password reset, dashboard, partner_orders, partner_clients)
│   │   └── sales/          Sales sub-area (dashboard, create_order, partners, order_history)
│   └── partner/            Partner UI (login, OTP, password reset, dashboard)
│       └── tabs/           Dashboard tabs (clients, history, new_order, rate_chart, wallet)
│
└── static/                 Static assets (3.1 MB)
    └── img/logo.png        (and other static content)
```

---

## 3. URL routing

Every blueprint mounts under a fixed prefix. The home page is the only top-level route.

| Prefix | Module | What it serves |
|---|---|---|
| `/` | `app.py` | Public home page (`templates/home.html`) |
| `/admin/*` | [modules/admin_routes.py](modules/admin_routes.py) | Internal admin UI and JSON endpoints |
| `/employee/*` | [modules/employee/employee_routes.py](modules/employee/employee_routes.py) | Employee operational UI |
| `/employee/sales/*` | [modules/employee/employee_sales_routes.py](modules/employee/employee_sales_routes.py) | Sales sub-area for employees |
| `/partner/*` | [modules/partner/partner_routes.py](modules/partner/partner_routes.py) | External partner portal |

Selected admin routes (full list in [admin_routes.py](modules/admin_routes.py)):

- `GET/POST /admin/admin-login`, `GET /admin/dashboard`, `GET /admin/logout`
- `POST /admin/partners/add | edit/<id> | delete/<id> | toggle/<id>`, `GET /admin/partners/export/excel`
- `POST /admin/employees/add | edit/<id> | delete/<id>`, `GET /admin/employees/export/excel`
- `GET/POST /admin/categories`
- `GET/POST /admin/shared-pdfs`
- `GET /admin/partner-orders`, `GET /admin/partner-orders/<order_id>`, `POST /admin/partner-orders/<order_id>/update`
- `POST /admin/partner-transactions/topup | <txn>/invalidate | <txn>/refund | manual-refund`
- `POST /admin/partner-orders/<order_id>/generate-invoice` (PDF, uploaded to GCS, recorded in Firestore)
- `GET /admin/services-master`, `GET /admin/partner-clients`
- `POST /admin/clients/add | edit/<id> | toggle/<id> | delete/<id>`, `GET /admin/partners/<partner_id>/clients`
- `GET /admin/configuration`, `POST /admin/configuration/service/add | service/delete/<id> | pdf/upload`

Partner authentication flow (`/partner/...`): `start-login` → `verify-otp` → `set-password` (first time) / `login` (return user) → `dashboard`. Forgot-password reuses the same OTP path.

---

## 4. Request flow on the legacy Hostinger box

```
  Public                                 Hostinger VPS (Ubuntu 24.04)
  internet                               -----------------------------------------------
  ─────►  TCP :443 / :80  ─►  nginx  ─►  127.0.0.1:8000  ─►  gunicorn (2 workers)  ─►  Flask app
  client                     │              │
                             │              └─ runs from /root/Munshi/venv
                             │
                             └─ TLS via /etc/letsencrypt/live/munshifinancials.com/
                                Apex munshifinancials.com → 301 to www.munshifinancials.com
                                www.munshifinancials.com   → proxy to gunicorn
```

Process supervision: `systemctl start/stop/status flaskapp` on the legacy box. The actual ExecStart was:

```
/root/Munshi/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
```

`startup.sh` in the repo (`gunicorn --bind=0.0.0.0 --timeout 600 app:app`) was *not* the active launch command — it appears to be left over from an earlier setup. Do not rely on it on the new server.

---

## 5. Data layer — Firestore

The `FirebaseClient` singleton in [modules/firebase_client.py](modules/firebase_client.py) is the only thing in the codebase that talks to Google. Everywhere else calls `FirebaseClient.db()` or `FirebaseClient.bucket()`.

### Firestore collections referenced by the code

| Collection | Used by | Purpose |
|---|---|---|
| `admins` | admin_routes | Admin user records |
| `employees` | admin, employee, sales routes | Employee records (incl. role flags) |
| `partners` | admin, partner, employee, sales | Partner accounts |
| `clients` | admin, employee, partner | End-clients owned by partners |
| `partner_orders` | admin, employee, partner | Orders placed by partners |
| `partner_transactions` | admin, employee, partner | Wallet top-ups, refunds, charges |
| `order_invoices` | admin, partner | Generated PDF invoice metadata |
| `services_master` | admin, employee, partner | Catalogue of services partners can order |
| `categories` | admin | Service categories |
| `shared_pdfs` | admin | Shared resource PDFs (rate charts etc.) |
| `forte` | employee_sales | Skill/specialisation tags used when registering partners |
| `otp_log` | auth/otp_service | OTP issue/verify audit trail |

No schema file exists; structure is implicit in the route handlers. To inspect actual documents, use the Firestore console for project `munshifinancials-c365a`.

### Cloud Storage layout

The default bucket is `munshifinancials-c365a.firebasestorage.app` (configured via `FIREBASE_STORAGE_BUCKET`).

- `invoices/<txn_id>.pdf` — invoice PDFs, **private** (read via `generate_signed_url`, 5-minute expiry default).
- Partner profile photos and shared admin PDFs upload via `upload_file` (made public) and `upload_private_file` (private). See [firebase_client.py](modules/firebase_client.py) for the helpers.

---

## 6. Authentication

The app does **not** use Flask-Login or any auth library. Auth is hand-rolled and uses Flask `session` (`session.get("user")`, etc.) gated by `require_admin()` / equivalent helpers in each blueprint.

- **Admin login** — POSTs email/password to Firebase Identity Toolkit via `FirebaseClient.firebase_login_with_email_password`, which calls `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=<FIREBASE_API_KEY>`. The Firebase API key is therefore embedded in the running app's env and is required to be valid.
- **Partner / employee onboarding** — phone/email lookup → OTP → set password → subsequent password logins. OTP is logged in the `otp_log` Firestore collection.
- **Flask session secret** — currently hardcoded in [app.py:18](app.py#L18) as `"super-secret-key-change-this"`. **This must be rotated** (move into `.env` as e.g. `FLASK_SECRET_KEY` and read with `os.getenv`) before going live anywhere new. Rotating it invalidates existing sessions.

---

## 7. Environment variables

| Variable | Required | Source on Hostinger | Notes |
|---|---|---|---|
| `FIREBASE_API_KEY` | yes | `.env` | Google API key used for Identity Toolkit email/password login |
| `FIREBASE_STORAGE_BUCKET` | yes | `.env` | Default bucket: `munshifinancials-c365a.firebasestorage.app` |
| `FIREBASE_CREDENTIALS` | no | defaults to `key.json` in CWD | Path to the GCP service-account JSON |

[.env.example](.env.example) holds the template (no values).

---

## 8. Secrets — what they are and where to keep them

Three things must **never** be committed to GitHub, and must be transferred out-of-band when moving the app to a new server:

| File | Purpose | How to provision on a new server |
|---|---|---|
| `.env` | Holds `FIREBASE_API_KEY` and `FIREBASE_STORAGE_BUCKET` | `scp` from the local backup into the deploy dir |
| `key.json` | Service-account JSON for GCP project `munshifinancials-c365a`. Grants Firestore + Storage access. | `scp` from the local backup into the deploy dir alongside `app.py` |
| (Flask) `app.secret_key` in `app.py` | Signs Flask session cookies | Should be moved into `.env` and replaced with a fresh strong random value before the next deploy |

The legacy `.env` and `key.json` are recoverable from the encrypted tarball — see §11 below.

---

## 9. Running locally (development)

```powershell
# from c:\Brojects\munshifinancials
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Place .env and key.json in the project root (NOT in git).
# Then:
python app.py
# Flask debug server starts on http://127.0.0.1:5000
```

Or with gunicorn (matches production behaviour more closely; requires WSL or Linux):

```bash
gunicorn -w 2 -b 127.0.0.1:8000 app:app
```

The Flask app uses `app.run(debug=True)` when run directly via `python app.py` — do **not** use this in production.

---

## 10. Known issues and gotchas

These are observations from reading the code as it was captured on 2026-05-19. Fix before the new deployment:

1. **OTP is currently stubbed out for testing.** In [modules/auth/otp_service.py](modules/auth/otp_service.py):
   - `generate_otp()` returns the hardcoded integer `100001` (real `random.randint(100000, 999999)` is commented out).
   - `hash_otp(otp)` returns the OTP verbatim (no bcrypt hash; real call is commented out).
   - `verify_otp_hash(otp, hashed)` returns the OTP itself instead of a boolean.
   This means in production today **any partner or employee can log in with OTP `100001`**, and stored OTPs are plaintext. This must be reverted to the bcrypt-based implementation before going live on the new server.
2. **Hardcoded Flask `app.secret_key`** — see §6.
3. **`startup.sh` is misleading** — binds gunicorn to `0.0.0.0` and is not what systemd runs. Treat as dead code or update it to mirror the systemd unit.
4. **`User=root` in the systemd unit** — the legacy box runs gunicorn as root. The new deployment should run the app under a dedicated non-root user (e.g. `munshi`) for least privilege.
5. **`return requests.post(...).json()` in `firebase_login_with_email_password`** — no exception handling around network failures. Wrap before relying on it for new code.

---

## 11. Accessing the local backup

The backup directory `backup/2026-05-19/` is **gitignored** and stays on the local machine only.

```powershell
# List backup contents
Get-ChildItem c:\Brojects\munshifinancials\backup\2026-05-19\

# Verify tarball integrity (must match the SHA-256 in MIGRATION_NOTES.md §9)
Get-FileHash c:\Brojects\munshifinancials\backup\2026-05-19\munshi-source-2026-05-19.tar.gz -Algorithm SHA256

# Inspect tarball without extracting
tar -tzf c:\Brojects\munshifinancials\backup\2026-05-19\munshi-source-2026-05-19.tar.gz

# Extract elsewhere (do NOT extract over the repo root — it would re-introduce .env and key.json under git's working tree)
mkdir c:\tmp\munshi-restore
tar -xzf c:\Brojects\munshifinancials\backup\2026-05-19\munshi-source-2026-05-19.tar.gz -C c:\tmp\munshi-restore
```

The tarball is the **only** copy of `/root/Munshi/.env` and `/root/Munshi/key.json` outside the live Hostinger server. Keep this directory backed up to whatever encrypted secondary storage you use.

The three config files in the same directory (`flaskapp.service`, `flaskapp.nginx`, `nginx.conf`) are **reference** for the new server's nginx + systemd configuration — do not copy them verbatim, since the new server may share nginx with other apps. See [MIGRATION_NOTES.md §11](MIGRATION_NOTES.md) for the new-server deployment outline.

---

## 12. Source server (legacy) at a glance

| | |
|---|---|
| Host | Hostinger VPS, `187.127.145.82`, hostname `srv1552755` |
| OS | Ubuntu 24.04.4 LTS |
| App location | `/root/Munshi/` |
| Process | `flaskapp.service` (systemd) → gunicorn on `127.0.0.1:8000` |
| Web server | nginx, vhost at `/etc/nginx/sites-available/flaskapp` |
| TLS | Let's Encrypt cert at `/etc/letsencrypt/live/munshifinancials.com/` |
| Domains served | `munshifinancials.com` (301 → www), `www.munshifinancials.com` (proxies to app) |
| Other tenants on host | none — only the default `ubuntu` user; security agent `monarx-agent` runs locally |

See [MIGRATION_NOTES.md](MIGRATION_NOTES.md) for the full migration record.
