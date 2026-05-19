from flask import Blueprint, request, render_template, redirect, session, flash,send_file
from modules.auth.login_common import find_partner
from modules.auth.otp_service import send_otp, verify_otp
from modules.auth.password_rules import hash_password, verify_password, is_strong_password
from modules.firebase_client import FirebaseClient
from datetime import datetime
from google.cloud.firestore import Query
from flask import jsonify,current_app
import base64
from datetime import timezone, datetime
from google.cloud.firestore_v1 import FieldFilter
from reportlab.lib.utils import ImageReader
from modules.firebase_client import FirebaseClient
import os

FirebaseClient.initialize()  # VERY IMPORTANT



from werkzeug.utils import secure_filename
import uuid
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from io import BytesIO
import os

bp = Blueprint("partner", __name__, url_prefix="/partner")


# =====================================================
# AUTH MIDDLEWARE
# =====================================================
def require_partner():
    user = session.get("user")
    if not user or user.get("role") != "partner":
        flash("Please login first", "danger")
        return False
    return True


# =====================================================
# LOGIN → START OTP FLOW
# =====================================================
@bp.route("/start-login", methods=["POST"])
def start_login():
    identifier = request.form.get("phone_or_asp")

    pid, partner = find_partner(identifier)
    if not partner:
        flash("Partner not found", "danger")
        return redirect("/")

    if partner.get("status") != "active":
        flash("Partner not active", "danger")
        return redirect("/")

    send_otp(identifier, "partner")

    session["otp_stage"] = {
        "uid": pid,
        "identifier": identifier,
        "role": "partner"
    }

    return redirect("/partner/verify-otp")


# =====================================================
# VERIFY OTP
# =====================================================
@bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp_view():
    if request.method == "POST":
        otp = request.form.get("otp")
        data = session.get("otp_stage")

        if not data:
            flash("Session expired. Try again.", "danger")
            return redirect("/partner/login")

        ok, msg = verify_otp(data["identifier"], otp, "partner")
        if not ok:
            flash(msg, "danger")
            return redirect("/partner/verify-otp")

        session["set_password_uid"] = data["uid"]
        return redirect("/partner/set-password")

    return render_template("partner/verify_otp.html")


# =====================================================
# SET PASSWORD (FIRST LOGIN OR RESET)
# =====================================================
@bp.route("/set-password", methods=["GET", "POST"])
def set_password():
    uid = session.get("set_password_uid")
    if not uid:
        return redirect("/partner/login")

    if request.method == "POST":
        pwd = request.form.get("password")

        ok, msg = is_strong_password(pwd)
        if not ok:
            flash(msg, "danger")
            return redirect("/partner/set-password")

        FirebaseClient.db().collection("partners") \
            .document(uid).update({
                "password_hash": hash_password(pwd),
                "last_password_change": datetime.utcnow()
            })

        flash("Password set successfully", "success")
        return redirect("/partner/login")

    return render_template("partner/set_password.html")


# =====================================================
# FORGOT PASSWORD
# =====================================================
@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form.get("identifier")

        pid, partner = find_partner(identifier)

        if not partner:
            flash("Partner not found", "danger")
            redirect("/partner/forgot-password")

        if partner.get("status") != "active":
            flash("Partner not active", "danger")
            return redirect("/partner/forgot-password")

        send_otp(identifier, "partner")

        session["otp_stage"] = {
            "uid": pid,
            "identifier": identifier,
            "role": "partner",
            "purpose": "forgot_password"
        }

        flash("OTP sent to reset password", "success")
        return redirect("/partner/verify-otp")

    return render_template("partner/forgot_password.html")


# =====================================================
# LOGIN
# =====================================================
# @bp.route("/login", methods=["GET", "POST"])
# def login():
#     if request.method == "POST":
#         identifier = request.form.get("identifier")
#         password = request.form.get("password") or ""

#         pid, partner = find_partner(identifier)
#         if not partner:
#             flash("Invalid login", "danger")
#             return redirect("/partner/login")

#         if partner.get("status") != "active":
#             flash("Partner not active", "danger")
#             return redirect("/partner/login")

#         # FIRST TIME LOGIN
#         if not partner.get("password_hash"):
#             send_otp(identifier, "partner")

#             session["otp_stage"] = {
#                 "uid": pid,
#                 "identifier": identifier,
#                 "role": "partner",
#                 "purpose": "first_login"
#             }
#             flash("OTP sent for first-time password setup", "success")
#             return redirect("/partner/verify-otp")

#         # NORMAL PASSWORD LOGIN
#         if not verify_password(password, partner["password_hash"]):
#             flash("Wrong password", "danger")
#             return redirect("/partner/login")

#         # SUCCESS LOGIN
#         session["user"] = {
#             "uid": pid,
#             "role": "partner",
#             "email": partner.get("email")
#         }

#         return redirect("/partner/dashboard")

#     return render_template("partner/login.html")
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier")
        password = request.form.get("password")

        pid, partner = find_partner(identifier)
        if not partner:
            flash("Invalid login", "danger")
            return redirect("/partner/login")

        if partner.get("status") != "active":
            flash("Partner not active", "danger")
            return redirect("/partner/login")

        # 🔹 FIRST SUBMIT (no password yet)
        if password is None:
            # first-time → OTP
            if not partner.get("password_hash"):
                send_otp(identifier, "partner")
                session["otp_stage"] = {
                    "uid": pid,
                    "identifier": identifier,
                    "role": "partner",
                    "purpose": "first_login"
                }
                flash("OTP sent for first-time setup", "success")
                return redirect("/partner/verify-otp")

            # existing user → show password box
            return render_template(
                "partner/login.html",
                show_password=True
            )

        # 🔹 PASSWORD LOGIN
        if not verify_password(password, partner["password_hash"]):
            flash("Wrong password", "danger")
            return redirect("/partner/login")

        session["user"] = {
            "uid": pid,
            "role": "partner",
            "email": partner.get("email")
        }
        return redirect("/partner/dashboard")

    return render_template("partner/login.html", show_password=False)



# =====================================================
# DASHBOARD — LAZY LOAD BY TAB
# =====================================================
def resolve_user_name(db, uid):
    if not uid:
        return None

    # check admins
    admin_doc = db.collection("admins").document(uid).get()
    if admin_doc.exists:
        a = admin_doc.to_dict()
        return a.get("name")

    # check employees
    emp_doc = db.collection("employees").document(uid).get()
    if emp_doc.exists:
        e = emp_doc.to_dict()
        return e.get("name")

    return None

# @bp.route("/dashboard")
# def dashboard():
#     if not require_partner():
#         return redirect("/partner/login")

#     db = FirebaseClient.db()
#     uid = session["user"]["uid"]

#     partner = db.collection("partners").document(uid).get().to_dict()
#     if not partner:
#         flash("Partner not found", "danger")
#         return redirect("/partner/login")

#     active_tab = request.args.get("tab", "wallet")
#     order_no = (request.args.get("order_no") or "").strip()
#     employee = (request.args.get("employee") or "").strip()


#     # ----------------------------------------------------------------------------------
#     # TAB: WALLET → Paginated Firestore (cursor-based) + limit(25)
#     # ----------------------------------------------------------------------------------
#     transactions = []
#     next_cursor = None

#     if active_tab == "wallet":
#         page_size = 10
#         cursor_id = request.args.get("cursor")

#         q = (
#             db.collection("partner_transactions")
#             .where("partner_id", "==", uid)
#             .order_by("created_at", direction=Query.DESCENDING)
#             .limit(page_size)
#         )

#         if cursor_id:
#             cursor_doc = db.collection("partner_transactions").document(cursor_id).get()
#             if cursor_doc.exists:
#                 q = q.start_after(cursor_doc)

#         results = q.stream()
#         docs = list(results)

#         # for d in docs:
#         #     item = d.to_dict()
#         #     item["id"] = d.id
#         #     transactions.append(item)

#         user_name_cache = {}

#         for d in docs:
#             item = d.to_dict()
#             item["id"] = d.id

#             uid = item.get("created_by_id")

#             if uid:
#                 if uid not in user_name_cache:
#                     user_name_cache[uid] = resolve_user_name(db, uid)
#                 item["name"] = user_name_cache[uid]
#             else:
#                 item["name"] = None

#             transactions.append(item)


#                 # save next cursor (if list not empty)
#             if docs:
#                     next_cursor = docs[-1].id

#     # ----------------------------------------------------------------------------------
#     # TAB: ORDERS → Only load active orders (limit 25)
#     # ----------------------------------------------------------------------------------
#     active_orders = []
#     if active_tab == "new_order":
#         q = (
#         db.collection("partner_orders")
#         .where("partner_id", "==", uid)
#         .where("status", "!=", "completed")
#         .order_by("status")
#         .order_by("created_at", direction=Query.DESCENDING)
#         .limit(50)  # fetch more for filtering
#     )

#         for d in q.stream():
#             x = d.to_dict()
#             x["id"] = d.id
#             active_orders.append(x)

#     # 🔍 PYTHON SEARCH (CASE-INSENSITIVE PREFIX)
#         if order_no:
#              active_orders = [
#             o for o in active_orders
#             if o.get("order_number", "").startswith(order_no)
#         ]

#     if employee:
#         emp = employee.lower()
#         active_orders = [
#             o for o in active_orders
#             if o.get("created_by_name_lc", "").startswith(emp)
#         ]



#     # ------------------------------------a----------------------------------------------
#     # TAB: CLIENTS → Only load first 25 active clients
#     # ----------------------------------------------------------------------------------
#     clients = []
#     if active_tab == "new_order":
#         q = (
#             db.collection("clients")
#             .where("partner_id", "==", uid)
#             .where("is_active", "==", "true")
#             .limit(25)
#             )
#         for d in q.stream():
#             c = d.to_dict()
#             c["id"] = d.id
#             clients.append(c)

#     return render_template(
#         "partner/dashboard.html",
#         partner=partner,
#         active_tab=active_tab,

#         # Wallet
#         transactions=transactions,
#         next_cursor=next_cursor,

#         # Orders
#         active_orders=active_orders,

#         # Clients
#         clients=clients
#     )

@bp.route("/dashboard")
def dashboard():
    if not require_partner():
        return redirect("/partner/login")

    db = FirebaseClient.db()
    partner_uid = session["user"]["uid"]

    partner_doc = db.collection("partners").document(partner_uid).get()
    if not partner_doc.exists:
        flash("Partner not found", "danger")
        return redirect("/partner/login")

    partner = partner_doc.to_dict()
    active_tab = request.args.get("tab", "wallet")
    clients_tab = []
    order_no = (request.args.get("order_no") or "").strip()
    payment_filter = (request.args.get("payment_mode") or "").strip()
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    df = dt = None
    try:
     if date_from:
         df = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)

     if date_to:
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    except Exception:
     df = dt = None


    # =========================
    # WALLET FILTER PARAMS
    # =========================
    txn_no = (request.args.get("txn_no") or "").strip().lower()
    employee = (request.args.get("employee") or "").strip().lower()
    note = (request.args.get("note") or "").strip().lower()
    txn_type = (request.args.get("txn_type") or "").strip().lower()


    transactions = []

    # =========================
    # 🔐 WALLET TAB (PARTNER SAFE)
    # =========================
    if active_tab == "wallet":
        q = (
            db.collection("partner_transactions")
            .where("partner_id", "==", partner_uid)  # 🔐 SECURITY FIX
            .order_by("created_at", direction=Query.DESCENDING)
            .limit(100)
        )

        docs = list(q.stream())
        name_cache = {}

        for d in docs:
            t = d.to_dict()
            t["id"] = d.id

            emp_id = t.get("created_by_id")
            emp_name = ""

            if emp_id:
                if emp_id not in name_cache:
                    name_cache[emp_id] = resolve_user_name(db, emp_id)
                emp_name = name_cache[emp_id] or ""

            t["employee_name"] = emp_name
            transactions.append(t)

        # -------------------------
        # 🔍 PYTHON FILTERING
        # -------------------------
        filtered = []
        for t in transactions:
            if txn_no and txn_no not in (t.get("transaction_number") or "").lower():
                continue

            if employee and (
                employee not in (t.get("employee_name") or "").lower()
                and employee not in (t.get("created_by_id") or "").lower()
            ):
                continue

            if note and note not in (t.get("note") or "").lower():
                continue
                # ✅ NEW: TRANSACTION TYPE FILTER
            if txn_type and txn_type != (t.get("type") or "").lower():
                    continue

            filtered.append(t)

        transactions = filtered

    # =========================
    # 🕒 ONGOING ORDERS (NEW_ORDER TAB)
    # =========================
    active_orders = []

    if active_tab == "new_order":
        q = (
            db.collection("partner_orders")
            .where("partner_id", "==", partner_uid)
            .where("status", "!=", "completed")
            .order_by("status")
            .order_by("created_at", direction=Query.DESCENDING)
            .limit(25)
        )

        for d in q.stream():
            o = d.to_dict()
            o["id"] = d.id
            if o.get("status") == "completed":
                continue
            active_orders.append(o)
    # =========================
    # 👥 CLIENTS (FOR DROPDOWN)
    # =========================
    clients = []
    
    if active_tab == "new_order":
        q = (
            db.collection("clients")
            .where("partner_id", "==", partner_uid)
            .where("is_active", "==", True)
            .limit(1000)
        )
    
        for d in q.stream():
            c = d.to_dict()
            c["id"] = d.id
            clients.append(c)
    
    # =========================
    # 📜 HISTORY TAB (COMPLETED + INVOICED)
    # =========================
    history_rows = []

    if active_tab == "history":
        orders = (
            db.collection("partner_orders")
            .where("partner_id", "==", partner_uid)
            .where("status", "==", "completed")
            .stream()
        )

        for order in orders:
            o = order.to_dict()
            # Order number filter
            if order_no and not (o.get("order_number") or "").startswith(order_no):
                continue

            
            order_id = order.id

            invoice_q = (
                db.collection("order_invoices")
                .where("order_id", "==", order_id)
                .limit(1)
                .stream()
            )

            invoice = next(invoice_q, None)
            if not invoice:
                continue

            inv = invoice.to_dict()
            inv_date = inv.get("invoice_date")
            if isinstance(inv_date, datetime):
                 if df and inv_date < df:
                     continue
                 if dt and inv_date > dt:
                     continue



            txns = (
                db.collection("partner_transactions")
                .where("order_id", "==", order_id)
                .stream()
            )
            has_wallet = False
            has_direct = False

            for t in txns:
                td = t.to_dict()

                # sirf actual payments
                if td.get("direction") != "debit":
                    continue
                
                if td.get("type") == "wallet":
                    has_wallet = True

                if td.get("type") == "direct":
                    has_direct = True



            if has_wallet and has_direct:
                payment_mode = "Mixed"
            elif has_wallet:
                payment_mode = "Wallet"
            else:
                payment_mode = "Direct"

            if payment_filter and payment_filter != payment_mode:
                 continue


            history_rows.append({
                "order_number": o.get("order_number"),
                "work_title": o.get("work_title"),
                "client_name": o.get("client_name", "—"),
                "invoice_date": inv.get("invoice_date"),
                "invoice_number": inv.get("invoice_number"),
                "amount": o.get("amount_total"),
                "payment_mode": payment_mode
            })

        history_rows.sort(key=lambda x: x["invoice_date"], reverse=True)

    # =========================
    # 👥 CLIENTS TAB
    # =========================

    if active_tab == "clients":
        q = (
            db.collection("clients")
            .where("partner_id", "==", partner_uid)
            .where("is_active", "==", True)
            .limit(1000)
        )
        for d in q.stream():
            c = d.to_dict()
            c["id"] = d.id
            clients_tab.append(c)
        # -------- FILTERS (PYTHON SIDE) --------
        fname = (request.args.get("client_name") or "").lower()
        phone = (request.args.get("phone") or "").lower()
        email = (request.args.get("email") or "").lower()
        filtered = []
        for c in clients_tab:
            if fname and fname not in (c.get("client_name") or "").lower():
                continue
            if phone and phone not in (c.get("client_phone") or "").lower():
                continue
            if email and email not in (c.get("client_email") or "").lower():
                continue
            filtered.append(c)

    rate_chart_pdf = None

    if active_tab == "rate_chart":
        gcs_path = (
        f"gs://{FirebaseClient.STORAGE_BUCKET_NAME}"
        "/system/configuration/official_document.pdf"
                   )
        rate_chart_pdf = FirebaseClient.generate_signed_url(
        gcs_path,
        minutes=15
            )


    return render_template(
        "partner/dashboard.html",
        partner=partner,
        active_tab=active_tab,
        transactions=transactions,
        active_orders=active_orders,
        clients=clients,
        clients_tab=clients_tab,
        history_rows=history_rows,
        rate_chart_pdf=rate_chart_pdf
    )


# =====================================================
# CREATE ORDER
# =====================================================

# @bp.route("/orders/create", methods=["POST"])
# def create_order():
#     if "user" not in session or session["user"]["role"] != "partner":
#         return redirect("/partner/login")

#     db = FirebaseClient.db()
#     uid = session["user"]["uid"]

#     work_title = (request.form.get("work_title") or "").strip()
#     if not work_title:
#         flash("Work title is required", "danger")
#         return redirect("/partner/dashboard?tab=new_order")

#     order_number, date_code = generate_order_number(db, uid)

#     # ===============================
#     # 📎 MULTIPLE ATTACHMENTS (FIX)
#     # ===============================
#     attachments = []
#     files = request.files.getlist("attachments")

#     for f in files:
#         if not f or f.filename == "":
#             continue

#         filename = secure_filename(f.filename)
#         ext = filename.rsplit(".", 1)[-1].lower()

#         if ext not in ("pdf", "jpg", "jpeg", "png", "doc", "docx"):
#             continue

#         blob_path = f"order_attachments/{uid}/{uuid.uuid4().hex}_{filename}"

#         public_url = FirebaseClient.upload_file(
#             blob_path=blob_path,
#             file_obj=f,
#             content_type=f.content_type
#         )

#         attachments.append({
#             "file_name": filename,
#             "file_url": public_url,
#             "uploaded_at": datetime.utcnow()
#         })

#     # ===============================
#     # 🧾 CREATE ORDER DOC
#     # ===============================
#     order_doc = {
#         "order_number": order_number,
#         "date_code": date_code,
#         "partner_id": uid,

#         "work_title": work_title,
#         "client_id": request.form.get("client_id") or None,
#         "client_name": request.form.get("client_name") or None,
#         "client_phone": request.form.get("client_phone") or None,
#         "client_email": request.form.get("client_email") or None,

#         "description": request.form.get("description") or None,
#         "attachments": attachments,   # ✅ NOW FILLED
#         "is_manual": True,

#         "status": "pending",
#         "eta": None,

#         "amount_total": 0,
#         "amount_paid": 0,

#         "created_at": datetime.utcnow(),
#     }

#     db.collection("partner_orders").add(order_doc)

#     flash(f"Order {order_number} submitted successfully", "success")
#     return redirect("/partner/dashboard?tab=new_order")

@bp.route("/orders/create", methods=["POST"])
def create_order():
    if "user" not in session or session["user"]["role"] != "partner":
        return redirect("/partner/login")

    db = FirebaseClient.db()
    uid = session["user"]["uid"]

    work_title = (request.form.get("work_title") or "").strip()
    if not work_title:
        flash("Work title is required", "danger")
        return redirect("/partner/dashboard?tab=new_order")

    order_number, date_code = generate_order_number(db, uid)

    # 🔹 Fetch partner info (for folder names)
    partner = db.collection("partners").document(uid).get().to_dict()
    partner_folder = f"{partner.get('name','partner')}_{partner.get('partner_code')}"
    partner_folder = secure_filename(partner_folder)

    # 🔹 Client info (optional)
    client_id = request.form.get("client_id")
    client_name = request.form.get("client_name") or "no_client"
    client_folder = secure_filename(f"{client_name}_{client_id or 'NA'}")

    # ===============================
    # 📎 PRIVATE ATTACHMENTS
    # ===============================
    attachments = []
    files = request.files.getlist("attachments")

    for f in files:
        if not f or f.filename == "":
            continue

        filename = secure_filename(f.filename)
        ext = filename.rsplit(".", 1)[-1].lower()

        if ext not in ("pdf", "jpg", "jpeg", "png", "doc", "docx"):
            continue

        blob_path = (
            f"order_attachments/"
            f"{partner_folder}/"
            f"{client_folder}/"
            f"{order_number}/"
            f"{uuid.uuid4().hex}_{filename}"
        )

        gcs_path = FirebaseClient.upload_private_file(
            blob_path=blob_path,
            file_obj=f,
            content_type=f.content_type
        )

        attachments.append({
            "file_name": filename,
            "gcs_path": gcs_path,     # 🔐 PRIVATE
            "uploaded_at": datetime.utcnow()
        })

    # ===============================
    # 🧾 CREATE ORDER DOC
    # ===============================
    order_doc = {
        "order_number": order_number,
        "date_code": date_code,
        "partner_id": uid,

        "work_title": work_title,
        "client_id": client_id or None,
        "client_name": client_name or None,
        "client_phone": request.form.get("client_phone") or None,
        "client_email": request.form.get("client_email") or None,

        "description": request.form.get("description") or None,
        "attachments": attachments,  # 🔐 PRIVATE FILES
        "is_manual": True,

        "status": "pending",
        "eta": None,

        "amount_total": 0,
        "amount_paid": 0,

        "created_at": datetime.utcnow(),
    }

    db.collection("partner_orders").add(order_doc)

    flash(f"Order {order_number} submitted successfully", "success")
    return redirect("/partner/dashboard?tab=new_order")



# =====================================================
# GENERATE ORDER NUMBER (Optimized)
# =====================================================
def generate_order_number(db, partner_id):
    today = datetime.utcnow()
    date_code = today.strftime("%m%d%y")

    # 🔹 Fetch partner code (ASP / partner_code)
    partner_doc = db.collection("partners").document(partner_id).get()
    if not partner_doc.exists:
        raise ValueError("Partner not found")

    partner = partner_doc.to_dict()
    partner_code = partner.get("partner_code")

    if not partner_code:
        raise ValueError("Partner code missing")

    # 🔹 Find last order for same partner + same date
    q = (
        db.collection("partner_orders")
        .where("partner_id", "==", partner_id)
        .where("date_code", "==", date_code)
        .order_by("created_at", direction=Query.DESCENDING)
        .limit(1)
    )

    docs = list(q.stream())

    if docs:
        last_order_number = docs[0].to_dict().get("order_number", "")
        last_seq = last_order_number.split("-")[-1]
        seq = str(int(last_seq) + 1).zfill(4)
    else:
        seq = "0001"

    order_number = f"ORD-{partner_code}-{date_code}-{seq}"

    return order_number, date_code

# =====================================================
# CLIENT LIVE SEARCH ENDPOINT (AJAX)
# =====================================================
# =====================================================
# CLIENT LIVE SEARCH ENDPOINT (CASE-INSENSITIVE PREFIX)
# =====================================================
# @bp.route("/search-clients")
# def search_clients():
#     # Auth check
#     if not session.get("user") or session["user"]["role"] != "partner":
#         return jsonify({"error": "auth"}), 403

#     db = FirebaseClient.db()
#     uid = session["user"]["uid"]

#     # Always lowercase for matching
#     query_text = (request.args.get("q") or "").strip()

#     if not query_text:
#         return jsonify([])

#     try:
#         # CASE-INSENSITIVE PREFIX QUERY
#         q = (
#             db.collection("clients")
#             .where("partner_id", "==", uid)
#             .order_by("client_name")
#             .where("client_name", ">=", query_text)
#             .where("client_name", "<=", query_text + u"\uf8ff")
#             .limit(25)
#         )

#         results = []
#         for d in q.stream():
#             c = d.to_dict()
#             results.append({
#                 "id": d.id,
#                 "client_name": c.get("client_name"),      # original casing
#                 "client_phone": c.get("client_phone"),
#                 "client_email": c.get("client_email"),
#             })

#         return jsonify(results)

#     except Exception as e:
#         print("🔥 CLIENT SEARCH ERROR:", e)
#         return jsonify({"error": "server", "details": str(e)}), 500

@bp.route("/search-clients")
def search_clients():
    if not session.get("user") or session["user"]["role"] != "partner":
        return jsonify({"error": "auth"}), 403

    db = FirebaseClient.db()
    partner_uid = session["user"]["uid"]

    qtext = (request.args.get("q") or "").strip().lower()
    if not qtext:
        return jsonify([])

    results = []

    q = (
        db.collection("clients")
        .where("partner_id", "==", partner_uid)
        .limit(100)  # safe buffer
    )

    for d in q.stream():
        c = d.to_dict()

        haystack = (
            (c.get("client_name") or "")
            + (c.get("client_phone") or "")
            + (c.get("client_email") or "")
        ).lower()

        if qtext in haystack:
            results.append({
                "id": d.id,
                "client_name": c.get("client_name"),
                "client_phone": c.get("client_phone"),
                "client_email": c.get("client_email"),
            })

        if len(results) >= 25:
            break

    return jsonify(results)


@bp.route("/profile", methods=["POST"])
def partner_profile():
    if not require_partner():
        return redirect("/partner/login")

    db = FirebaseClient.db()
    uid = session["user"]["uid"]

    partner_ref = db.collection("partners").document(uid)
    partner_doc = partner_ref.get()

    if not partner_doc.exists:
        flash("Partner not found", "danger")
        return redirect("/partner/dashboard")

    # -------- Collect + sanitize input --------
    name = (request.form.get("name") or "").strip()
    address = (request.form.get("address") or "").strip()
    city = (request.form.get("city") or "").strip()
    state = (request.form.get("state") or "").strip()
    pincode = (request.form.get("pincode") or "").strip()

    # -------- Basic validations --------
    if pincode and not pincode.isdigit():
        flash("Pincode must be numeric", "danger")
        return redirect("/partner/dashboard")

    if pincode and len(pincode) != 6:
        flash("Pincode must be 6 digits", "danger")
        return redirect("/partner/dashboard")

    update_data = {
        "name": name,
        "address": address,
        "city": city,
        "state": state,
        "pincode": pincode,
        "updated_at": datetime.utcnow()
    }

    # ❗ IMPORTANT: allow empty string overwrite, remove ONLY None
    update_data = {k: v for k, v in update_data.items() if v is not None}

    if len(update_data) <= 1:  # only updated_at present
        flash("No changes detected", "info")
        return redirect("/partner/dashboard")

    partner_ref.update(update_data)

    flash("Profile updated successfully", "success")
    return redirect("/partner/dashboard")

@bp.route("/profile/photo", methods=["POST"])
def upload_profile_photo():
    if not require_partner():
        return redirect("/partner/login")

    uid = session["user"]["uid"]
    partner_ref = FirebaseClient.db().collection("partners").document(uid)

    # ===============================
    # 🗑️ REMOVE PHOTO
    # ===============================
    cropped = request.form.get("cropped_image")
    if cropped == "REMOVE":
        partner_ref.update({
            "profile_photo_url": None,
            "updated_at": datetime.utcnow()
        })
        flash("Profile photo removed", "success")
        return redirect("/partner/dashboard")

    # ===============================
    # 🖼️ BASE64 IMAGE (Cropper.js)
    # ===============================
    if cropped and cropped.startswith("data:image"):
        try:
            header, encoded = cropped.split(",", 1)
            image_bytes = base64.b64decode(encoded)

            if len(image_bytes) > 2 * 1024 * 1024:
                flash("Image must be under 2MB", "danger")
                return redirect("/partner/dashboard")

            storage_client = FirebaseClient.storage()
            bucket = storage_client.bucket(
                os.getenv("FIREBASE_STORAGE_BUCKET")
            )

            unique = int(datetime.utcnow().timestamp())
            blob_path = f"partner_profiles/{uid}_{unique}.jpg"
            blob = bucket.blob(blob_path)

            blob.upload_from_string(
                image_bytes,
                content_type="image/jpeg",
                predefined_acl="publicRead"
            )

            photo_url = f"https://storage.googleapis.com/{bucket.name}/{blob_path}"
            print("🔥 FINAL PHOTO URL:", photo_url)


            partner_ref.update({
                "profile_photo_url": photo_url,
                "updated_at": datetime.utcnow()
            })

            flash("Profile photo updated successfully", "success")
            return redirect("/partner/dashboard")

        except Exception as e:
            print("🔥 PHOTO BASE64 ERROR:", e)
            flash("Failed to upload photo", "danger")
            return redirect("/partner/dashboard")

    # ===============================
    # 📂 FALLBACK: FILE UPLOAD (OLD)
    # ===============================
    if "photo" not in request.files:
        flash("No file selected", "danger")
        return redirect("/partner/dashboard")

    file = request.files["photo"]
    if not file or file.filename == "":
        flash("Invalid file", "danger")
        return redirect("/partner/dashboard")

    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext not in ("jpg", "jpeg", "png"):
        flash("Only JPG or PNG images allowed", "danger")
        return redirect("/partner/dashboard")

    storage_client = FirebaseClient.storage()
    bucket = storage_client.bucket(
        os.getenv("FIREBASE_STORAGE_BUCKET")
    )

    blob_path = f"partner_profiles/{uid}.{ext}"
    blob = bucket.blob(blob_path)

    blob.upload_from_file(
        file,
        content_type=file.content_type,
        predefined_acl="publicRead"
    )

    photo_url = f"https://storage.googleapis.com/{bucket.name}/{blob_path}"

    partner_ref.update({
        "profile_photo_url": photo_url,
        "updated_at": datetime.utcnow()
    })

    flash("Profile photo updated successfully", "success")
    return redirect("/partner/dashboard")

@bp.route("/invoice/<invoice_number>/download")
def download_invoice(invoice_number):
    if not require_partner():
        return redirect("/partner/login")

    db = FirebaseClient.db()
    partner_uid = session["user"]["uid"]

    # =============================
    # FETCH INVOICE (SAFE)
    # =============================
    inv_q = (
        db.collection("order_invoices")
        .where("invoice_number", "==", invoice_number)
        .where("partner_id", "==", partner_uid)
        .limit(1)
        .stream()
    )

    invoice_doc = next(inv_q, None)
    if not invoice_doc:
        flash("Invoice not found", "danger")
        return redirect("/partner/dashboard?tab=history")

    inv = invoice_doc.to_dict()

    # =============================
    # FETCH PARTNER (BILL TO)
    # =============================
    partner = (
        db.collection("partners")
        .document(partner_uid)
        .get()
        .to_dict()
    )

    # =============================
    # PDF SETUP
    # =============================
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left = 25 * mm
    right = width - 25 * mm
    y = height - 30 * mm

    # =============================
    # MUNSHI COMPANY (HARDCODED)
    # =============================
    M_NAME = "Munshi Financials"
    M_ADDR = "Your Address"
    M_CITY = "Jalandhar, Punjab"
    M_GST = "ASJAKSLS-12123"

    # =============================
    # LOGO (100% FIXED)
    # =============================
    logo_path = os.path.join(
        current_app.root_path, "static", "img", "logo.png"
    )
    print(os.path.exists(logo_path))

    if os.path.exists(logo_path):
        logo = ImageReader(logo_path)
        c.drawImage(
    logo,
    left,
    y - 35,
    width=40 * mm,
    height=40 * mm,
    mask='auto'      # 🔥 VERY IMPORTANT
)

    # =============================
    # MUNSHI HEADER (RIGHT)
    # =============================
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(right, y, M_NAME)
    y -= 14

    c.setFont("Helvetica", 9)
    c.drawRightString(right, y, M_ADDR); y -= 12
    c.drawRightString(right, y, M_CITY); y -= 12
    c.drawRightString(right, y, f"GSTIN: {M_GST}")

    # =============================
    # TITLE
    # =============================
    y -= 30
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, y, "TAX INVOICE")

    # =============================
    # INVOICE META
    # =============================
    y -= 30
    c.setFont("Helvetica", 10)
    c.drawString(left, y, f"Invoice No: {inv['invoice_number']}")
    c.drawRightString(
        right,
        y,
        f"Invoice Date: {inv['invoice_date'].strftime('%d %b %Y')}"
    )

    # =============================
    # BILL TO (PARTNER ONLY)
    # =============================
    y -= 30
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Bill To")
    y -= 14

    c.setFont("Helvetica", 10)
    c.drawString(left, y, partner.get("name", "—")); y -= 14
    c.drawString(left, y, partner.get("address", "—")); y -= 14

    city_line = f"{partner.get('city','')} {partner.get('state','')} - {partner.get('pincode','')}"
    c.drawString(left, y, city_line); y -= 14

    c.drawString(
        left,
        y,
        f"GSTIN: {partner.get('partner_gst') or 'NA'}"
    )

    # =============================
    # SERVICES TABLE
    # =============================
    y -= 30
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Service")
    c.drawString(left + 90 * mm, y, "HSN")
    c.drawRightString(right, y, "Amount (Rs.)")

    y -= 8
    c.line(left, y, right, y)
    y -= 14

    c.setFont("Helvetica", 10)
    for svc in inv.get("services", []):
        c.drawString(left, y, svc.get("service_name", ""))
        c.drawString(left + 90 * mm, y, svc.get("hsn", "NA"))
        c.drawRightString(right, y, f"{svc.get('base_amount',0):.2f}")
        y -= 14

    # =============================
    # TOTALS
    # =============================
    y -= 10
    c.line(left, y, right, y)
    y -= 20

    fees = inv.get("fees", {})
    tax = inv.get("tax", {})

    def row(label, val):
        nonlocal y
        c.drawString(left + 80 * mm, y, label)
        c.drawRightString(right, y, f"{val:.2f}")
        y -= 14

    row("Service Fee", fees.get("service_base_total", 0))
    row("Government Fee", fees.get("government_fee", 0))
    row("Out of Pocket", fees.get("out_of_pocket", 0))
    row("CGST", tax.get("cgst", 0))
    row("SGST", tax.get("sgst", 0))
    row("IGST", tax.get("igst", 0))

    y -= 6
    c.setFont("Helvetica-Bold", 11)
    row("Grand Total", inv.get("grand_total", 0))

    # =============================
    # FOOTER
    # =============================
    c.setFont("Helvetica", 9)
    c.drawCentredString(
        width / 2,
        15 * mm,
        f"Generated on {datetime.now().strftime('%d %b %Y %H:%M')} | Page 1 of 1"
    )

    c.showPage()
    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Invoice_{invoice_number}.pdf",
        mimetype="application/pdf"
    )

# =====================================================
# LOGOUT
# =====================================================
@bp.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "success")
    return redirect("/")
