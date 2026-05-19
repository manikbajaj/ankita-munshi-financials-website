import io
import random
import string
import math
import uuid
import zipfile
import requests
from io import BytesIO
from datetime import datetime

import pandas as pd
from flask import (
    Blueprint, request, render_template, session,
    flash, redirect, url_for, current_app,
    send_file, jsonify
)

from modules.firebase_client import FirebaseClient



bp = Blueprint("admin", __name__, url_prefix="/admin")


# =========================
# ✅ HELPERS
# =========================

def generate_partner_code(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


def generate_employee_id():
    try:
        docs = FirebaseClient.db().collection("employees").stream()
        max_num = 0
        for d in docs:
            eid = d.to_dict().get("employee_id", "")
            if eid.upper().startswith("EMP"):
                num = ''.join(ch for ch in eid if ch.isdigit())
                if num:
                    max_num = max(max_num, int(num))
        return f"EMP{(max_num + 1):04d}"
    except Exception as e:
        current_app.logger.exception("employee id error: %s", e)
        return "EMP0001"


def require_admin():
    user = session.get("user")
    if not user or user.get("role") != "admin":
        flash("Login as admin required", "danger")
        return False
    return True


# =========================
# ✅ ADMIN LOGIN
# =========================
@bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        # ✅ STEP 1: AUTHENTICATE VIA FIREBASE AUTH
        try:
            result = FirebaseClient.firebase_login_with_email_password(email, password)
        except Exception as e:
            print("Firebase auth error:", e)
            flash("Authentication server error", "danger")
            return redirect(url_for("admin.admin_login"))

        if result.get("error"):
            flash("Invalid email or password", "danger")
            return redirect(url_for("admin.admin_login"))

        uid = result.get("localId")

        # ✅ STEP 2: CHECK IF THIS UID IS ADMIN
        admin_doc = FirebaseClient.get_document("admins", uid)

        if not admin_doc:
            flash("You are not authorized as admin", "danger")
            return redirect(url_for("admin.admin_login"))

        # ✅ STEP 3: CREATE SESSION
        session["user"] = {
            "uid": uid,
            "email": email,
            "role": "admin",
            "name": admin_doc.get("name")
        }

        return redirect(url_for("admin.dashboard"))

    return render_template("admin/admin_login.html")


# =========================
# ✅ DASHBOARD (Partner / Employee)
# =========================
@bp.route("/dashboard")
def dashboard():
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()

    active_tab = request.args.get("tab", "partners")
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 7
    search = (request.args.get("search") or "").strip()

    # 🔹 PARTNER FILTERS FROM QUERY
    partner_filters = {
        "city": (request.args.get("city") or "").strip(),
        "state": (request.args.get("state") or "").strip(),
        "status": (request.args.get("status") or "").strip(),
        "profession": (request.args.get("profession") or "").strip(),
        "email": (request.args.get("email") or "").strip(),
        "partner_code": (request.args.get("partner_code") or "").strip(),
        "phone": (request.args.get("phone") or "").strip(),
    }

    partners_docs = []
    employees_docs = []

    # ✅ FETCH PARTNERS
    for d in db.collection("partners").order_by("created_at").stream():
        p = d.to_dict()
        p["id"] = d.id
        partners_docs.append(p)

    # ✅ FETCH EMPLOYEES
    for d in db.collection("employees").order_by("created_at").stream():
        e = d.to_dict()
        e["id"] = d.id
        employees_docs.append(e)

    # ✅ PARTNER MATCH FUNCTION
    def partner_match(p):
        if search:
            s = search.lower()
            if not (
                s in (p.get("name", "") or "").lower()
                or s in (p.get("email", "") or "").lower()
            ):
                return False

        if partner_filters["city"] and partner_filters["city"].lower() not in (p.get("city", "") or "").lower():
            return False
        if partner_filters["state"] and partner_filters["state"].lower() not in (p.get("state", "") or "").lower():
            return False
        if partner_filters["status"] and partner_filters["status"].lower() != (p.get("status", "") or "").lower():
            return False
        if partner_filters["profession"] and partner_filters["profession"].lower() not in (
            (p.get("profession", "") or "") + (p.get("profession_manual", "") or "")
        ).lower():
            return False
        if partner_filters["email"] and partner_filters["email"].lower() not in (p.get("email", "") or "").lower():
            return False
        if partner_filters["partner_code"] and partner_filters["partner_code"] not in (p.get("partner_code", "") or ""):
            return False
        if partner_filters["phone"] and partner_filters["phone"] not in (p.get("phone", "") or ""):
            return False

        return True

    # ✅ APPLY FILTERS TO PARTNERS
    filtered_partners = [p for p in partners_docs if partner_match(p)]

    # ✅ SIMPLE SEARCH FILTER FOR EMPLOYEES
    if search:
        filtered_employees = [
            e for e in employees_docs
            if search.lower() in ((e.get("name", "") + e.get("email", "")).lower())
        ]
    else:
        filtered_employees = employees_docs

    # ✅ ENSURE PARTNER CODES
    for p in filtered_partners:
        if not p.get("partner_code"):
            code = generate_partner_code()
            db.collection("partners").document(p["id"]).update({"partner_code": code})
            p["partner_code"] = code

    # ✅ ENSURE EMPLOYEE IDS
    for e in filtered_employees:
        if not e.get("employee_id"):
            eid = generate_employee_id()
            db.collection("employees").document(e["id"]).update({"employee_id": eid})
            e["employee_id"] = eid

    # ✅ TOTAL COUNTS (AFTER FILTER)
    total_partners = len(filtered_partners)
    total_employees = len(filtered_employees)
    active_partners = sum(1 for p in filtered_partners if (p.get("status") or "").lower() == "active")
    inactive_partners = total_partners - active_partners

    active_employees = sum(1 for e in filtered_employees if (e.get("status") or "").lower() == "active")
    inactive_employees = total_employees - active_employees

    # ✅ TOTAL PAGES
    total_pages_partners = max(1, math.ceil(total_partners / per_page))
    total_pages_employees = max(1, math.ceil(total_employees / per_page))

    # ✅ PAGE SLICE
    start = (page - 1) * per_page
    partners_page = filtered_partners[start:start + per_page]
    employees_page = filtered_employees[start:start + per_page]

    # ✅ SEND CORRECT TOTAL_PAGES / TOTAL_COUNT BASED ON TAB
    if active_tab == "partners":
        total_pages = total_pages_partners
        total_count = total_partners
        active_count = active_partners
        inactive_count = inactive_partners
    else:
        total_pages = total_pages_employees
        total_count = total_employees
        active_count = active_employees
        inactive_count = inactive_employees

    return render_template(
        "admin/dashboard.html",
        partners=partners_page,
        employees=employees_page,
        active_tab=active_tab,
        page=page,
        total_pages=total_pages,
        total_partners=total_partners,
        total_employees=total_employees,
        user=session.get("user"),
        active_count=active_count,
        inactive_count=inactive_count,
        partner_filters=partner_filters,
        search=search,
        total_count=total_count,
    )


# =========================
# ✅ EXPORTS
# =========================

def _make_csv_response(rows, filename):
    si = io.StringIO()
    if rows:
        cols = list(rows[0].keys())
        si.write(",".join(cols) + "\n")
        for r in rows:
            si.write(",".join([f'"{r.get(c, "")}"' for c in cols]) + "\n")

    mem = io.BytesIO()
    mem.write(si.getvalue().encode("utf-8"))
    mem.seek(0)

    return send_file(mem, as_attachment=True, download_name=filename, mimetype="text/csv")


@bp.route("/partners/export/excel")
def export_partners_excel():
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()   # ✅ CENTRALIZED DB

    rows = []
    for d in db.collection("partners").stream():
        p = d.to_dict()
        p["id"] = d.id
        rows.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "email": p.get("email"),
            "phone": p.get("phone"),
            "city": p.get("city"),
            "state": p.get("state"),
            "status": p.get("status"),
            "partner_code": p.get("partner_code"),
            "created_at": str(p.get("created_at"))
        })

    try:
        df = pd.DataFrame(rows)
        mem = io.BytesIO()
        with pd.ExcelWriter(mem, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="partners")
        mem.seek(0)
        return send_file(
            mem,
            as_attachment=True,
            download_name="partners.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception:
        return _make_csv_response(rows, "partners.csv")


@bp.route("/employees/export/excel")
def export_employees_excel():
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()   # ✅ CENTRALIZED DB

    rows = []
    for d in db.collection("employees").stream():
        e = d.to_dict()
        e["id"] = d.id
        rows.append({
            "id": e.get("id"),
            "name": e.get("name"),
            "email": e.get("email"),
            "phone": e.get("phone"),
            "employee_id": e.get("employee_id"),
            "status": e.get("status"),
            "employee_type": e.get("employee_type"),
            "created_at": str(e.get("created_at"))
        })

    try:
        df = pd.DataFrame(rows)
        mem = io.BytesIO()
        with pd.ExcelWriter(mem, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="employees")
        mem.seek(0)
        return send_file(
            mem,
            as_attachment=True,
            download_name="employees.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception:
        return _make_csv_response(rows, "employees.csv")


# =========================
# ✅ PARTNERS CRUD
# =========================

@bp.route("/partners/add", methods=["POST"])
def add_partner():
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    data = request.form.to_dict()

    doc = {
        "name": data.get("name"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "city": data.get("city"),
        "state": data.get("state"),
        "address": data.get("address"),
        "profession_manual": data.get("profession_manual"),
        "status": data.get("status", "pending"),
        "created_at": datetime.utcnow(),
        "partner_code": generate_partner_code()
    }

    FirebaseClient.db().collection("partners").add(doc)
    return jsonify({"success": True})


@bp.route("/partners/edit/<id>", methods=["POST"])
def edit_partner(id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    FirebaseClient.db().collection("partners").document(id).update(request.form.to_dict())
    return jsonify({"success": True})


@bp.route("/partners/delete/<id>", methods=["POST"])
def delete_partner(id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    FirebaseClient.db().collection("partners").document(id).update({"deleted": True})
    return jsonify({"success": True})


# (Optional) Partner status toggle – used from JS if you implement it
@bp.route("/partners/toggle/<id>", methods=["POST"])
def toggle_partner_status(id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    ref = FirebaseClient.db().collection("partners").document(id)
    doc = ref.get()
    if not doc.exists:
        return jsonify({"success": False, "error": "not_found"}), 404

    data = doc.to_dict()
    status = (data.get("status") or "pending").lower()
    # Simple toggle: pending -> active, active -> inactive, inactive -> active
    if status == "active":
        new_status = "inactive"
    else:
        new_status = "active"

    ref.update({"status": new_status})
    return jsonify({"success": True, "status": new_status})


# =========================
# ✅ EMPLOYEES CRUD
# =========================

@bp.route("/employees/add", methods=["POST"])
def add_employee():
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    data = request.form.to_dict()
    data["created_at"] = datetime.utcnow()
    res = FirebaseClient.db().collection("employees").add(data)

    eid = generate_employee_id()
    FirebaseClient.db().collection("employees").document(res[1].id).update({"employee_id": eid})

    return jsonify({"success": True, "employee_id": eid})


@bp.route("/employees/edit/<id>", methods=["POST"])
def edit_employee(id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    FirebaseClient.db().collection("employees").document(id).update(request.form.to_dict())
    return jsonify({"success": True})


@bp.route("/employees/delete/<id>", methods=["POST"])
def delete_employee(id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    FirebaseClient.db().collection("employees").document(id).update({"deleted": True})
    return jsonify({"success": True})


# =========================
# ✅ CATEGORIES
# =========================

@bp.route("/categories", methods=["GET", "POST"])
def admin_categories():
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        db.collection("categories").add({
            "name": name,
            "created_at": datetime.utcnow()
        })
        return redirect(url_for("admin.admin_categories"))

    cats = []
    for d in db.collection("categories").stream():
        cats.append({"id": d.id, **d.to_dict()})

    return render_template("admin/categories.html", categories=cats)


# =========================
# ✅ SHARED PDFS (stub)
# =========================

@bp.route("/shared-pdfs", methods=["GET", "POST"])
def admin_shared_pdfs():
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()

    if request.method == "POST":
        # Your upload logic here
        f = request.files.get("pdf")
        name = (request.form.get("name") or "").strip()
        # TODO: implement storage + save to Firestore
        flash("Upload logic not implemented yet", "warning")
        return redirect(url_for("admin.admin_shared_pdfs"))

    rows = []
    for d in db.collection("shared_pdfs").stream():
        rows.append({"id": d.id, **d.to_dict()})

    return render_template("admin/shared_pdfs.html", pdfs=rows)

###############################################################
def recalculate_order_totals(db, order_id, partner_id):
    """
    Recalculate order.amount_paid, amount_due, payment_status
    using ONLY valid transactions.
    """
    paid = 0.0

    q = (db.collection("partner_transactions")
           .where("partner_id", "==", partner_id)
           .where("order_id", "==", order_id))

    for d in q.stream():
        t = d.to_dict()

        if t.get("invalid"):
            continue

        if t.get("direction") == "debit" and t.get("type") in ("wallet", "direct"):
            paid += float(t.get("amount") or 0)

        if t.get("direction") == "credit" and t.get("type") == "refund":
            paid -= float(t.get("amount") or 0)

    order_ref = db.collection("partner_orders").document(order_id)
    order_doc = order_ref.get()
    if not order_doc.exists:
        return

    order = order_doc.to_dict()
    total = float(order.get("amount_total") or 0)
    due = max(total - paid, 0)

    if paid <= 0:
        status = "unbilled"
    elif due > 0:
        status = "partial"
    else:
        status = "paid"

    order_ref.update({
        "amount_paid": paid,
        "amount_due": due,
        "payment_status": status,
        "updated_at": datetime.utcnow(),
    })


def generate_bill_number():
    """Generate a unique bill number: datetime + random suffix."""
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    suffix = random.randint(100, 999)
    return f"BILL{ts}-{suffix}"


@bp.route("/partner-orders")
def partner_orders():
    """
    Admin view: Partner Orders list + basic edit.
    """
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()

    page = max(int(request.args.get("page", 1)), 1)
    per_page = 12

    # Filters
    search = (request.args.get("search") or "").strip().lower()
    filter_partner_id = (request.args.get("partner_id") or "").strip()
    filter_status = (request.args.get("status") or "").strip()
    filter_payment_status = (request.args.get("payment_status") or "").strip()
    filter_employee_id = (request.args.get("employee_id") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()

    # Parse dates
    df = None
    dt = None
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d")
    except Exception:
        df = None
        dt = None

    # Preload partners & employees
    partners_map = {}
    for d in db.collection("partners").stream():
        p = d.to_dict()
        p["id"] = d.id
        partners_map[d.id] = p

    employees_map = {}
    for d in db.collection("employees").stream():
        e = d.to_dict()
        e["id"] = d.id
        employees_map[d.id] = e

    # Fetch orders (full scan + python filter for now)
    orders_docs = []
    for d in db.collection("partner_orders").order_by("created_at").stream():
        o = d.to_dict()
        o["id"] = d.id

        # Attach partner / employee display names if missing
        pid = o.get("partner_id")
        if pid and not o.get("partner_name"):
            o["partner_name"] = (partners_map.get(pid) or {}).get("name")

        eid = o.get("assigned_employee_id")
        if eid and not o.get("assigned_employee_name"):
            o["assigned_employee_name"] = (employees_map.get(eid) or {}).get("name")

        orders_docs.append(o)

    def order_match(o):
        # Simple text search
        if search:
            s = search
            text = " ".join([
                str(o.get("work_title") or ""),
                str(o.get("client_name") or ""),
                str(o.get("client_phone") or ""),
                str(o.get("client_email") or ""),
                str(o.get("id") or ""),
            ]).lower()
            if s not in text:
                return False

        if filter_partner_id and o.get("partner_id") != filter_partner_id:
            return False

        if filter_status and (o.get("status") or "").lower() != filter_status.lower():
            return False

        if filter_payment_status and (o.get("payment_status") or "").lower() != filter_payment_status.lower():
            return False

        if filter_employee_id and o.get("assigned_employee_id") != filter_employee_id:
            return False

        ca = o.get("created_at")
        if isinstance(ca, datetime):
            if df and ca < df:
                return False
            if dt and ca > dt:
                return False

        return True

    filtered_orders = [o for o in orders_docs if order_match(o)]
    total_orders = len(filtered_orders)
    total_pages = max(1, math.ceil(total_orders / per_page))
    start = (page - 1) * per_page
    orders_page = filtered_orders[start:start + per_page]

    return render_template(
        "admin/partner_orders.html",
        active_tab="partner_orders",
        user=session.get("user"),
        orders=orders_page,
        page=page,
        total_pages=total_pages,
        total_orders=total_orders,
        partners_map=partners_map,
        employees_map=employees_map,
        search=(request.args.get("search") or ""),
        filter_partner_id=filter_partner_id,
        filter_status=filter_status,
        filter_payment_status=filter_payment_status,
        filter_employee_id=filter_employee_id,
        date_from=date_from,
        date_to=date_to,
    )


@bp.route("/partner-orders/<order_id>", methods=["GET"])
def get_partner_order(order_id):
    """
    Fetch a single order as JSON for the edit modal.
    Attach: partner display name, wallet balance, payments history (partner_transactions).
    """
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    doc = db.collection("partner_orders").document(order_id).get()
    if not doc.exists:
        return jsonify({"error": "not_found"}), 404

    data = doc.to_dict()
 

    data["id"] = doc.id
    data["final_documents"] = data.get("final_documents", [])
    data["invoice_id"] = data.get("invoice_id")
    if data.get("invoice_id"):
        inv = db.collection("order_invoices").document(data["invoice_id"]).get()
        if inv.exists:
         data["invoice"] = inv.to_dict()

    partner_id = data.get("partner_id")
    partner_wallet_balance = 0
    partner_name = None
    if partner_id:
        pdoc = db.collection("partners").document(partner_id).get()
        if pdoc.exists:
            p = pdoc.to_dict()
            partner_name = p.get("name")
            partner_wallet_balance = p.get("wallet_balance") or 0
            data["partner_state"] = p.get("state")

    if partner_name:
        data["partner_name"] = partner_name

    # Load payments for this order (from partner_transactions)
    payments = []
    if partner_id:
        # Make sure you have composite index if required
        q = (db.collection("partner_transactions")
               .where("partner_id", "==", partner_id)
               .where("order_id", "==", order_id)
               .order_by("created_at"))
        for tdoc in q.stream():
            t = tdoc.to_dict()
            payments.append({
                "id": tdoc.id,
                "amount": t.get("amount") or 0,
                "type": t.get("type"),               # wallet, direct, refund, topup
                "direction": t.get("direction"),     # debit, credit
                "source": t.get("source"),
                "bill_number": t.get("bill_number"),
                "transaction_number": t.get("transaction_number"),
                "payment_date": (
                    t.get("payment_date").isoformat()
                    if isinstance(t.get("payment_date"), datetime) else None
                ),
                "created_at": (
                    t.get("created_at").isoformat()
                    if isinstance(t.get("created_at"), datetime) else None
                ),
                "created_by_name": t.get("created_by_name"),
                "invalid": t.get("invalid") or False,
                "invalid_reason": t.get("invalid_reason"),
                "refunded": t.get("refunded") or False,
                "original_txn_id": t.get("original_txn_id"),

            })

    data["payments"] = payments
    data["partner_wallet_balance"] = partner_wallet_balance

    return jsonify({"success": True, "order": data})


@bp.route("/partner-orders/<order_id>/update", methods=["POST"])
def update_partner_order(order_id):
    """
    Basic edit + billing:
      - work details
      - assignment & status
      - internal comments & final_documents
      - billing:
          * amount_total (editable)
          * optional new payment (wallet or direct)
          * auto recompute amount_paid, amount_due, payment_status
          * record each payment in partner_transactions with bill_number (for order payments)
    """
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    form = request.form.to_dict()

    # Load current order
    order_ref = db.collection("partner_orders").document(order_id)
    order_doc = order_ref.get()
    if not order_doc.exists:
        return jsonify({"error": "not_found"}), 404

    order = order_doc.to_dict()
    partner_id = order.get("partner_id")
    if not partner_id:
        return jsonify({"error": "missing_partner"}), 400

    # current amounts
    current_total = float(order.get("amount_total") or 0)
    # compute paid from transactions (trusted source)
    paid_calc = 0.0
    # Sum only valid, non-invalid, non-refunded debit transactions applied to this order
    q = (db.collection("partner_transactions")
           .where("partner_id", "==", partner_id)
           .where("order_id", "==", order_id))
    for tdoc in q.stream():
        t = tdoc.to_dict()
        if t.get("invalid"):
            continue
        if t.get("direction") == "debit" and t.get("type") in ("wallet", "direct"):
            paid_calc += float(t.get("amount") or 0)
        # credits (refunds) reduce paid
        if t.get("direction") == "credit" and t.get("type") == "refund":
            paid_calc -= float(t.get("amount") or 0)

    current_paid = float(paid_calc)

    # --- 1. Update basic fields ---
    update_data = {
        "work_title": form.get("work_title"),
        "description": form.get("description"),
        "status": form.get("status"),
        "internal_comments": form.get("internal_comments"),
        "updated_at": datetime.utcnow(),
    }
        # --- ETA ---
    eta_raw = form.get("eta")
    if eta_raw:
        try:
            update_data["eta"] = datetime.strptime(eta_raw, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "invalid_eta"}), 400
    else:
        update_data["eta"] = None

    #update_data["client_id"] = form.get("client_id")
    client_id = form.get("client_id")
    update_data["client_id"] = client_id
    
    # 🔥 IMPORTANT: store client snapshot for grid display
    if client_id:
        cdoc = db.collection("clients").document(client_id).get()
        if cdoc.exists:
            c = cdoc.to_dict()
            update_data["client_name"] = c.get("client_name")
            update_data["client_phone"] = c.get("client_phone")
            update_data["client_email"] = c.get("client_email")
        else:
            update_data["client_name"] = None
            update_data["client_phone"] = None
            update_data["client_email"] = None
    else:
        update_data["client_name"] = None
        update_data["client_phone"] = None
        update_data["client_email"] = None
    

    # Assignment
    assigned_employee_id = form.get("assigned_employee_id") or None
    if assigned_employee_id:
        emp_doc = db.collection("employees").document(assigned_employee_id).get()
        if emp_doc.exists:
            e = emp_doc.to_dict()
            update_data["assigned_employee_id"] = assigned_employee_id
            update_data["assigned_employee_name"] = e.get("name")
            update_data["assigned_at"] = datetime.utcnow()
    else:
        update_data["assigned_employee_id"] = None
        update_data["assigned_employee_name"] = None

    # Final documents
    final_docs = form.get("final_documents")
    if final_docs is not None:
        update_data["final_documents"] = [
            x.strip() for x in final_docs.split(",") if x.strip()
        ]

    # --- 2. Billing: Amount Total editable ---
    new_amount_total = form.get("amount_total")
    if new_amount_total is not None and new_amount_total != "":
        try:
            new_amount_total = float(new_amount_total)
        except ValueError:
            return jsonify({"error": "invalid_amount_total"}), 400
    else:
        new_amount_total = current_total

    # --- 3. Optional manual adjust to Amount Paid (admin) ---
    # If admin wants to manually adjust amount_paid, we record that as an admin 'direct' transaction
    manual_paid_adjust = form.get("manual_amount_paid")  # optional field (not in UI currently)
    if manual_paid_adjust:
        try:
            manual_amount = float(manual_paid_adjust)
        except ValueError:
            return jsonify({"error": "invalid_manual_paid"}), 400
        if manual_amount != 0:
            # Create partner_transactions record for manual adjustment (admin-created)
            tx = {
                "partner_id": partner_id,
                "order_id": order_id,
                "type": "direct",
                "direction": "debit",
                "source": "manual_admin_adjust",
                "amount": manual_amount,
                "currency": "INR",
                "payment_date": datetime.utcnow(),
                "created_at": datetime.utcnow(),
                "created_by_id": session.get("user", {}).get("id"),
                "created_by_name": session.get("user", {}).get("name") or session.get("user", {}).get("email"),
                "transaction_number": form.get("manual_transaction_number") or f"MANUAL-{uuid.uuid4().hex[:8].upper()}",
                "bill_number": generate_bill_number(),
            }
            db.collection("partner_transactions").document().set(tx)
            # recalc current_paid
            current_paid += manual_amount

    # --- 4. Billing: New Payment (optional) ---
    new_payment_mode = form.get("new_payment_mode")  # "", "wallet", "direct"
    new_payment_amount_raw = form.get("new_payment_amount")
    new_payment_amount = 0.0
    if new_payment_amount_raw:
        try:
            new_payment_amount = float(new_payment_amount_raw)
        except ValueError:
            return jsonify({"error": "invalid_payment_amount"}), 400

    new_bill_number = order.get("last_bill_number")

    user = session.get("user") or {}
    created_by_id = user.get("id") or user.get("uid")
    created_by_name = user.get("name") or user.get("email") or "Admin"

    if new_payment_mode and new_payment_amount > 0:
        # ❌ Prevent overpayment
        amount_due_now = max(new_amount_total - current_paid, 0)

        if new_payment_amount > amount_due_now:
            flash(f"Payment exceeds amount due (Due: ₹{amount_due_now})", "danger")
            return jsonify({"success": False}), 400

        # parse payment date (for direct) else use now
        pay_date_str = form.get("new_payment_date") or ""
        if pay_date_str:
            try:
                payment_date = datetime.strptime(pay_date_str, "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "invalid_payment_date"}), 400
        else:
            payment_date = datetime.utcnow()

        tx_data = {
            "partner_id": partner_id,
            "order_id": order_id,
            "type": new_payment_mode,       # "wallet" or "direct"
            "direction": "debit",
            "source": "order_payment",
            "amount": new_payment_amount,
            "currency": "INR",
            "payment_date": payment_date,
            "created_at": datetime.utcnow(),
            "created_by_id": created_by_id,
            "created_by_name": created_by_name,
            "invalid": False,
            "invalid_reason": None,
            "refunded": False,
        }

        # Generate bill number (only for order payments)
        bill_number = generate_bill_number()
        tx_data["bill_number"] = bill_number
        new_bill_number = bill_number

        if new_payment_mode == "wallet":
            # Use wallet balance (update partner wallet_balance)
            partner_ref = db.collection("partners").document(partner_id)
            pdoc = partner_ref.get()
            if not pdoc.exists:
                return jsonify({"error": "partner_not_found"}), 404

            partner = pdoc.to_dict()
            wallet_balance = float(partner.get("wallet_balance") or 0)

            if wallet_balance < new_payment_amount:
                return jsonify({"error": "insufficient_wallet_balance"}), 400

            new_balance = wallet_balance - new_payment_amount
            tx_data["running_balance"] = new_balance
            tx_data["transaction_number"] = f"WALLET-{uuid.uuid4().hex[:10].upper()}"

            # NOTE: In production, wrap partner wallet update and transaction write in a Firestore transaction
            partner_ref.update({"wallet_balance": new_balance})

        elif new_payment_mode == "direct":
            txn_no = form.get("new_payment_transaction_number") or ""
            if not txn_no.strip():
                txn_no = f"MANUAL-{uuid.uuid4().hex[:8].upper()}"
            tx_data["transaction_number"] = txn_no

        # Write partner_transactions doc
        db.collection("partner_transactions").document().set(tx_data)

        # add to paid
        current_paid = current_paid + new_payment_amount

    # --- 5. Final recalculation ---
    new_amount_paid = current_paid
    new_amount_due = max(new_amount_total - new_amount_paid, 0)

    if new_amount_paid <= 0:
        payment_status = "unbilled"
    elif new_amount_due > 0:
        payment_status = "partial"
    else:
        payment_status = "paid"

    update_data["amount_total"] = new_amount_total
    update_data["amount_paid"] = new_amount_paid
    update_data["amount_due"] = new_amount_due
    update_data["payment_status"] = payment_status
    update_data["last_bill_number"] = new_bill_number
    order_ref.update(update_data)

    flash("Order updated successfully", "success")
    stay_open = form.get("stay_open") == "1"

    return jsonify({
    "success": True
})


# -------------------------
# Transaction endpoints
# -------------------------
@bp.route("/partner-transactions/topup", methods=["POST"])
def partner_topup():
    """
    Create wallet topup (partner wallet credit). This records a 'topup' (credit),
    but does NOT generate an order invoice (billing is only for orders).
    """
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    data = request.form.to_dict()
    partner_id = data.get("partner_id")
    if not partner_id:
        return jsonify({"error": "missing_partner"}), 400

    try:
        amount = float(data.get("amount") or 0)
    except ValueError:
        return jsonify({"error": "invalid_amount"}), 400

    if amount <= 0:
        return jsonify({"error": "invalid_amount"}), 400

    # create topup tx (credit)
    tx = {
        "partner_id": partner_id,
        "order_id": None,
        "type": "topup",
        "direction": "credit",
        "source": "wallet_topup",
        "amount": amount,
        "currency": "INR",
        "payment_date": datetime.utcnow(),
        "created_at": datetime.utcnow(),
        "created_by_id": session.get("user", {}).get("id"),
        "created_by_name": session.get("user", {}).get("name") or session.get("user", {}).get("email"),
        "transaction_number": f"TOPUP-{uuid.uuid4().hex[:10].upper()}",
        "running_balance": None,  # we will compute and set below
    }

    partner_ref = db.collection("partners").document(partner_id)
    pdoc = partner_ref.get()
    if not pdoc.exists:
        return jsonify({"error": "partner_not_found"}), 404

    partner = pdoc.to_dict()
    wallet_balance = float(partner.get("wallet_balance") or 0)
    new_balance = wallet_balance + amount
    tx["running_balance"] = new_balance

    # NOTE: wrap in transaction in prod
    partner_ref.update({"wallet_balance": new_balance})
    db.collection("partner_transactions").document().set(tx)

    return jsonify({"success": True, "new_balance": new_balance})

@bp.route("/partner-transactions/<txn_id>/invalidate", methods=["POST"])
def invalidate_transaction(txn_id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    t_ref = db.collection("partner_transactions").document(txn_id)
    tdoc = t_ref.get()
    if not tdoc.exists:
        return jsonify({"error": "txn_not_found"}), 404

    data = request.form.to_dict()
    reason = data.get("reason") or "Marked invalid by admin"

    t_ref.update({
        "invalid": True,
        "invalid_reason": reason,
        "invalid_by": session.get("user", {}).get("id"),
        "invalid_by_name": session.get("user", {}).get("name") or session.get("user", {}).get("email"),
        "invalid_at": datetime.utcnow(),
    })

    t = tdoc.to_dict()
    order_id = t.get("order_id")
    partner_id = t.get("partner_id")

    # 🔁 NEW: recalc order totals
    if order_id and partner_id:
        recalculate_order_totals(db, order_id, partner_id)

    flash("Transaction marked invalid", "warning")
    return jsonify({"success": True})

@bp.route("/partner-transactions/<txn_id>/refund", methods=["POST"])
def refund_transaction(txn_id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    t_ref = db.collection("partner_transactions").document(txn_id)
    tdoc = t_ref.get()
    if not tdoc.exists:
        return jsonify({"error": "txn_not_found"}), 404

    original = tdoc.to_dict()

    # ❌ NEW: block refund on invalid txn
    if original.get("invalid"):
        return jsonify({"error": "cannot_refund_invalid_transaction"}), 400

    form = request.form.to_dict()
    try:
        amount = float(form.get("amount") or 0)
    except ValueError:
        return jsonify({"error": "invalid_amount"}), 400

    if amount <= 0:
        return jsonify({"error": "invalid_amount"}), 400

    refund_to_wallet = form.get("refund_to_wallet") in ("1", "true", "True", "yes", "on")
    reason = form.get("reason") or "Refund issued"

    partner_id = original.get("partner_id")
    order_id = original.get("order_id")

    tx = {
        "partner_id": partner_id,
        "order_id": order_id,
        "type": "refund",
        "direction": "credit",
        "source": "refund",
        "amount": amount,
        "currency": "INR",
        "payment_date": datetime.utcnow(),
        "created_at": datetime.utcnow(),
        "created_by_id": session.get("user", {}).get("id"),
        "created_by_name": session.get("user", {}).get("name") or session.get("user", {}).get("email"),
        "transaction_number": f"REFUND-{uuid.uuid4().hex[:10].upper()}",
        "original_txn_id": txn_id,
        "refund_to_wallet": refund_to_wallet,
        "refund_reason": reason,
    }

    if refund_to_wallet:
        pref = db.collection("partners").document(partner_id)
        pdoc = pref.get()
        bal = float(pdoc.to_dict().get("wallet_balance") or 0)
        pref.update({"wallet_balance": bal + amount})
        tx["running_balance"] = bal + amount

    t_ref.update({
        "refunded": True,
        "refunded_at": datetime.utcnow(),
        "refunded_amount": amount,
    })

    db.collection("partner_transactions").document().set(tx)

    # 🔁 NEW: recalc order totals
    if order_id and partner_id:
        recalculate_order_totals(db, order_id, partner_id)

    flash("Refund processed successfully", "success")
    return jsonify({"success": True})

@bp.route("/partner-transactions/manual-refund", methods=["POST"])
def manual_refund():
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    form = request.form.to_dict()

    # --- amount ---
    try:
        amount = float(form.get("amount") or 0)
    except ValueError:
        return jsonify({"error": "invalid_amount"}), 400

    if amount <= 0:
        return jsonify({"error": "invalid_amount"}), 400

    # --- partner ---
    partner_id = form.get("partner_id")
    if not partner_id:
        return jsonify({"error": "missing_partner"}), 400

    refund_to_wallet = form.get("refund_to_wallet") in ("1", "true", "True")
    reason = form.get("reason") or "Manual refund"

    tx = {
        "partner_id": partner_id,
        "order_id": None,                 # 👈 very important
        "type": "refund",
        "direction": "credit",
        "source": "manual_refund",
        "amount": amount,
        "currency": "INR",
        "created_at": datetime.utcnow(),
        "created_by_id": session.get("user", {}).get("id"),
        "created_by_name": session.get("user", {}).get("email"),
        "refund_reason": reason,
    }

    # --- wallet credit ---
    if refund_to_wallet:
        pref = db.collection("partners").document(partner_id)
        pdoc = pref.get()
        if not pdoc.exists:
            return jsonify({"error": "partner_not_found"}), 404

        bal = float(pdoc.to_dict().get("wallet_balance") or 0)
        pref.update({"wallet_balance": bal + amount})
        tx["running_balance"] = bal + amount

    db.collection("partner_transactions").document().set(tx)

    return jsonify({"success": True})

@bp.route("/partner-orders/<order_id>/generate-invoice", methods=["POST"])
def generate_order_invoice(order_id):

    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403

    db = FirebaseClient.db()
    data = request.json or {}

    order_ref = db.collection("partner_orders").document(order_id)
    order_doc = order_ref.get()

    if not order_doc.exists:
        return jsonify({"error": "order_not_found"}), 404

    order = order_doc.to_dict()

    if order.get("status") == "completed":
        return jsonify({"error": "invoice_already_generated"}), 400

    partner_id = order.get("partner_id")
    if not partner_id:
        return jsonify({"error": "missing_partner"}), 400

    partner_doc = db.collection("partners").document(partner_id).get()
    if not partner_doc.exists:
        return jsonify({"error": "partner_not_found"}), 404

    partner = partner_doc.to_dict()

    services = data.get("services", [])
    if not services:
        return jsonify({"error": "no_services"}), 400

    # ===============================
    # 🔢 MASTER PAID AMOUNT LOGIC
    # ===============================
    amount_paid = float(order.get("amount_paid") or 0)
    govt_fee = float(data.get("government_fee") or 0)
    oop_fee = float(data.get("out_of_pocket") or 0)

    remaining = amount_paid - govt_fee - oop_fee
    if remaining <= 0:
        return jsonify({"error": "fees_exceed_paid_amount"}), 400

    GST_RATE = 0.18

    # Reverse GST calculation
    service_base_total = round(remaining / (1 + GST_RATE), 2)
    gst_total = round(remaining - service_base_total, 2)

    # GST split 
    state = (partner.get("state") or "").strip().lower()

    PUNJAB_ALIASES = ("punjab", "pun", "pan", "pb", "pjab")

    is_punjab = state == "punjab" or state.startswith(PUNJAB_ALIASES)

    if is_punjab:
        cgst = round(gst_total / 2, 2)
        sgst = round(gst_total / 2, 2)
        igst = 0
    else:
        cgst = 0
        sgst = 0
        igst = round(gst_total, 2)
    # ===============================
    # 🔁 SERVICE AUTO-NORMALIZATION
    # ===============================
    input_service_sum = sum(float(s.get("amount") or 0) for s in services)
    if round(input_service_sum, 2) != round(service_base_total, 2):
        return jsonify({
        "error": "service_amount_mismatch",
        "expected": service_base_total,
        "received": input_service_sum
    }), 400
    for s in services:
        if not s.get("service_name") or not s.get("amount"):
         return jsonify({"error": "invalid_service_row"}), 400


    if input_service_sum <= 0:
        return jsonify({"error": "invalid_service_amounts"}), 400

    normalized_services = []
    for s in services:
        ratio = float(s["amount"]) / input_service_sum
        base_amt = round(service_base_total * ratio, 2)

        normalized_services.append({
            "service_id": s.get("service_id"),
            "service_name": s.get("service_name"),
            "hsn": s.get("hsn"),
            "base_amount": base_amt
        })

    invoice = {
        "order_id": order_id,
        "partner_id": partner_id,
        "invoice_number": f"INV-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "invoice_date": datetime.utcnow(),
        "invoice_type": "b2b" if partner.get("gst_number") else "b2c",

        "fees": {
            "government_fee": govt_fee,
            "out_of_pocket": oop_fee,
            "service_base_total": service_base_total
        },

        "services": normalized_services,

        "tax": {
            "cgst": cgst,
            "sgst": sgst,
            "igst": igst
        },

        "total_paid": amount_paid,
        "grand_total": amount_paid,

        "created_at": datetime.utcnow(),
        "created_by": session.get("user", {}).get("email")
    }

    invoice_ref = db.collection("order_invoices").add(invoice)


    # 🔒 LOCK ORDER
    order_ref.update({
        "status": "completed",
        "completed_at": datetime.utcnow(),
        "invoice_id": invoice_ref[1].id 
    })

    return jsonify({"success": True})

@bp.route("/services-master", methods=["GET"])
def services_master():

    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 403

    db = FirebaseClient.db()

    services = []
    docs = db.collection("services_master").stream()

    for d in docs:
        s = d.to_dict()
        services.append({
            "id": d.id,
            "name": s.get("name"),
            "hsn": s.get("hsn")
        })

    return jsonify({"services": services})


# ======================================================
# PARTNER CLIENTS LIST + FILTERS
# ======================================================
@bp.route("/partner-clients")
def partner_clients():
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()

    partner_id = (request.args.get("partner_id") or "").strip()
    name = (request.args.get("client_name") or "").strip().lower()
    phone = (request.args.get("phone") or "").strip()
    email = (request.args.get("email") or "").strip().lower()
    is_active = request.args.get("is_active")

    q = db.collection("clients").where("deleted", "==", False)

    if partner_id:
        q = q.where("partner_id", "==", partner_id)

    if is_active in ("true", "false"):
        q = q.where("is_active", "==", is_active == "true")

    # ---------------- LOAD PARTNERS ----------------
    partners_map = {}
    for p in db.collection("partners").stream():
        d = p.to_dict()
        if d.get("status") != "active":
            continue
        partners_map[p.id] = d



    # ---------------- LOAD EMPLOYEES ----------------
    employees_map = {
        e.id: e.to_dict()
        for e in db.collection("employees").stream()
    }

    clients = []
    for d in q.stream():
        c = d.to_dict()
        c["id"] = d.id

        if name and name not in (c.get("client_name") or "").lower():
            continue
        if phone and phone not in (c.get("client_phone") or ""):
            continue
        if email and email not in (c.get("client_email") or "").lower():
            continue

        # -------- RESOLVE ADDED BY --------
        added_by = c.get("added_by") or {}
        uid = added_by.get("user_id")

        resolved_email = "-"
        resolved_name = "-"

        # admin case
        if added_by.get("email"):
            resolved_email = added_by["email"]
            resolved_name = resolved_email.split("@")[0]

        # employee case
        elif uid and uid in employees_map:
            emp = employees_map[uid]
            resolved_email = emp.get("email", "-")
            resolved_name = emp.get("name") or resolved_email.split("@")[0]

        c["added_by_display"] = {
            "name": resolved_name,
            "email": resolved_email
        }

        clients.append(c)

    return render_template(
        "admin/partner_clients.html",
        active_tab="partner_clients",
        clients=clients,
        partners_map=partners_map,
        filters=request.args,
        user=session.get("user")
    )

# ======================================================
# ADD CLIENT
# ======================================================
@bp.route("/clients/add", methods=["POST"])
def add_client():
    if not require_admin():
        return jsonify(success=False, error="Unauthorized"), 401

    db = FirebaseClient.db()
    user = session.get("user") or {}

    client_name = (request.form.get("client_name") or "").strip()
    client_phone = (request.form.get("client_phone") or "").strip()
    partner_id = (request.form.get("partner_id") or "").strip()

    if not client_name or not client_phone or not partner_id:
        return jsonify(success=False, error="Client name, phone and partner are required")

    # -------- auto name from email --------
    email = user.get("email") or ""
    display_name = email.split("@")[0] if email else "admin"

    doc = {
        "client_name": client_name,
        "client_phone": client_phone,
        "client_email": (request.form.get("client_email") or "").strip(),
        "partner_id": partner_id,
        "notes": request.form.get("notes"),

        "is_active": True,
        "deleted": False,

        "added_by": {
            "user_id": user.get("uid"),
            "name": display_name,
            "email": email,
        },
        "added_type": user.get("role"),

        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    # Firestore auto-creates collection on first insert
    db.collection("clients").add(doc)

    flash("Client added successfully", "success")
    return jsonify(success=True)


# ======================================================
# EDIT CLIENT
# ======================================================
@bp.route("/clients/edit/<client_id>", methods=["POST"])
def edit_client(client_id):
    if not require_admin():
        return jsonify(success=False), 401

    db = FirebaseClient.db()
    ref = db.collection("clients").document(client_id)
    snap = ref.get()

    if not snap.exists:
        return jsonify(success=False, error="Client not found")

    ref.update({
    "client_name": request.form.get("client_name"),
    "client_phone": request.form.get("client_phone"),
    "client_email": request.form.get("client_email"),
    "partner_id": request.form.get("partner_id"),  # 🔥 THIS
    "notes": request.form.get("notes"),
    "updated_at": datetime.utcnow(),
})


    flash("Client updated successfully", "success")
    return jsonify(success=True)


# ======================================================
# TOGGLE ACTIVE / INACTIVE
# ======================================================
@bp.route("/clients/toggle/<client_id>", methods=["POST"])
def toggle_client(client_id):
    if not require_admin():
        return jsonify(success=False), 401

    db = FirebaseClient.db()
    ref = db.collection("clients").document(client_id)
    snap = ref.get()

    if not snap.exists:
        return jsonify(success=False, error="Client not found")

    current = snap.to_dict().get("is_active", True)

    ref.update({
        "is_active": not current,
        "updated_at": datetime.utcnow(),
    })

    flash(
        "Client activated" if not current else "Client deactivated",
        "success" if not current else "warning"
    )

    return jsonify(success=True)


# ======================================================
# DELETE CLIENT (SOFT DELETE)
# ======================================================
@bp.route("/clients/delete/<client_id>", methods=["POST"])
def delete_client(client_id):
    if not require_admin():
        return jsonify(success=False), 401

    db = FirebaseClient.db()
    ref = db.collection("clients").document(client_id)
    snap = ref.get()

    if not snap.exists:
        return jsonify(success=False, error="Client not found")

    ref.update({
        "deleted": True,
        "deleted_at": datetime.utcnow(),
    })

    flash("Client removed successfully", "success")
    return jsonify(success=True)

@bp.route("/partners/<partner_id>/clients")
def get_partner_clients(partner_id):
    if not require_admin():
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    clients = []

    q = (
        db.collection("clients")
        .where("partner_id", "==", partner_id)
        .where("deleted", "==", False)
        .where("is_active", "==", True)
    )

    for d in q.stream():
        c = d.to_dict()
        clients.append({
            "id": d.id,
            "name": c.get("client_name"),
            "phone": c.get("client_phone"),
        })

    return jsonify({"clients": clients})

@bp.route("/partner-orders/<order_id>/download-files")
def download_order_files(order_id):
    if not require_admin():
        return redirect(url_for("admin.admin_login"))

    db = FirebaseClient.db()
    doc = db.collection("partner_orders").document(order_id).get()

    if not doc.exists:
        flash("Order not found", "danger")
        return redirect(request.referrer or "/admin/partner-orders")

    order = doc.to_dict()
    files = order.get("attachments") or []

    if not files:
        flash("No files attached to this order", "warning")
        return redirect(request.referrer or "/admin/partner-orders")

    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, f in enumerate(files, start=1):
            gcs_path = f.get("gcs_path")
            filename = f.get("file_name") or f"file_{idx}"

            if not gcs_path:
                continue
            signed_url = FirebaseClient.generate_signed_url(gcs_path, minutes=10)
            if not signed_url:
                current_app.logger.warning(f"Skipping file (no signed url): {filename}")
                continue

            try:
                resp = requests.get(signed_url, timeout=15)
                if resp.status_code == 200:
                    zf.writestr(filename, resp.content)
                else:
                    current_app.logger.warning(
                        f"Download failed {filename} | status={resp.status_code}"
                    )
            except Exception as e:
                current_app.logger.warning(f"File skip: {filename} | {e}")

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"order_{order_id}_attachments.zip",
        mimetype="application/zip"
    )


#Configuration

@bp.route("/configuration")
def configuration():
    if not require_admin():
        return redirect("/admin/login")

    db = FirebaseClient.db()

    # 🔹 Service Master
    services = []
    q = db.collection("services_master").order_by("added_on")
    for d in q.stream():
        s = d.to_dict()
        s["id"] = d.id
        services.append(s)

    return render_template(
        "admin/configuration.html",
        active_tab="configuration",
        services=services
    )

# ==========================
# ADD SERVICE
# ==========================
@bp.route("/configuration/service/add", methods=["POST"])
def add_service():
    if not require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 403

    name = (request.form.get("name") or "").strip()
    hsn = (request.form.get("hsn") or "").strip()

    if not name or not hsn:
        return jsonify({"success": False, "error": "missing fields"})

    db = FirebaseClient.db()

    db.collection("services_master").add({
        "name": name,
        "hsn": hsn,
        "added_on": datetime.utcnow()
    })

    return jsonify({"success": True})


# ==========================
# DELETE SERVICE
# ==========================
@bp.route("/configuration/service/delete/<service_id>", methods=["POST"])
def delete_service(service_id):
    if not require_admin():
        return jsonify({"success": False}), 403

    db = FirebaseClient.db()
    db.collection("services_master").document(service_id).delete()

    return jsonify({"success": True})


@bp.route("/configuration/pdf/upload", methods=["POST"])
def upload_config_pdf():
    if not require_admin():
        return redirect("/admin/login")

    if "config_pdf" not in request.files:
        flash("No file selected", "danger")
        return redirect("/admin/configuration")

    file = request.files["config_pdf"]
    if not file or file.filename == "":
        flash("Invalid file", "danger")
        return redirect("/admin/configuration")

    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files allowed", "danger")
        return redirect("/admin/configuration")

    # 🔹 FIXED PATH (overwrite always)
    blob_path = "system/configuration/official_document.pdf"

    FirebaseClient.upload_file(
        blob_path=blob_path,
        file_obj=file,
        content_type="application/pdf"
    )

    flash("PDF uploaded successfully", "success")
    return redirect("/admin/configuration")


@bp.route("/configuration/pdf/view")
def view_config_pdf():
    if not require_admin():
        return redirect("/admin/login")

    gcs_path = "gs://{}/system/configuration/official_document.pdf".format(
        FirebaseClient.STORAGE_BUCKET_NAME
    )

    url = FirebaseClient.generate_signed_url(
        gcs_path=gcs_path,
        minutes=5
    )

    if not url:
        flash("PDF not available", "warning")
        return redirect("/admin/configuration")

    return redirect(url)

@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/admin/admin-login")



