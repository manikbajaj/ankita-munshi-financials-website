from flask import Blueprint, request, render_template, redirect, session, flash, jsonify
from datetime import datetime
import uuid, random

from modules.firebase_client import FirebaseClient
from modules.auth.login_common import find_employee
from modules.auth.otp_service import send_otp, verify_otp
from modules.auth.password_rules import hash_password, verify_password

bp = Blueprint("employee", __name__, url_prefix="/employee")

# ==================================================
# AUTH
# ==================================================

def require_employee():
    u = session.get("user")
    return u and u.get("role") == "employee"

def emp_name():
    u=session.get("user") or {}
    return u.get("email") or u.get("uid")

# ==================================================
# LOGIN / OTP / PASSWORD
# ==================================================

@bp.route("/login",methods=["GET","POST"])
def employee_login():

    if request.method=="POST":
        identifier=request.form.get("identifier","").strip()
        eid,emp=find_employee(identifier)

        if not emp:
            flash("Employee not found","danger")
            return redirect("/employee/login")

        if emp.get("status")!="active":
            flash("Employee inactive","danger")
            return redirect("/employee/login")

        if not emp.get("last_password_change"):
            send_otp(identifier,"employee")
            session["otp_stage"]={"uid":eid,"identifier":identifier}
            return redirect("/employee/verify-otp")

        pwd=request.form.get("password") or ""
        if not verify_password(pwd,emp.get("password_hash","")):
            flash("Wrong password","danger")
            return redirect("/employee/login")

        # session["user"]={"uid":eid,"email":emp.get("email"),"role":"employee"}
        # return redirect("/employee/dashboard")
        emp_type = emp.get("employee_type")  # may be None
        session["user"] = {
            "uid": eid,
            "email": emp.get("email"),
            "role": "employee",
            "employee_type": emp_type
        }

        # 🔀 smart redirect
        if emp_type == "sales":
            return redirect("/employee/sales/dashboard")

        return redirect("/employee/dashboard")


    return render_template("employee/login.html")

@bp.route("/verify-otp",methods=["GET","POST"])
def verify_employee_otp():

    data=session.get("otp_stage")
    if not data:
        return redirect("/employee/login")

    if request.method=="POST":
        ok,_=verify_otp(data["identifier"],request.form.get("otp"),"employee")
        if not ok:
            flash("Invalid OTP","danger")
            return redirect("/employee/verify-otp")

        session["set_password_uid"]=data["uid"]
        return redirect("/employee/set-password")

    return render_template("employee/verify_otp.html")

@bp.route("/set-password",methods=["GET","POST"])
def employee_set_password():

    uid=session.get("set_password_uid")
    if not uid:
        return redirect("/employee/login")

    if request.method=="POST":
        FirebaseClient.db().collection("employees").document(uid).update({
            "password_hash":hash_password(request.form.get("password")),
            "last_password_change":datetime.utcnow()
        })

        emp=FirebaseClient.get_document("employees",uid)
        # session["user"]={"uid":uid,"email":emp.get("email"),"role":"employee"}
        # return redirect("/employee/dashboard")
        emp_type = emp.get("employee_type")
        session["user"] = {
            "uid": uid,
            "email": emp.get("email"),
            "role": "employee",
            "employee_type": emp_type
        }

        if emp_type == "sales":
            return redirect("/employee/sales/dashboard")

        return redirect("/employee/dashboard")


    return render_template("employee/set_password.html")

# ==================================================
# DASHBOARD
# ==================================================

@bp.route("/dashboard")
def dashboard():
    if not require_employee(): return redirect("/employee/login")
    return redirect("/employee/partner-orders")

# ==================================================
# HELPERS
# ==================================================

def generate_bill_number():
    return f"BILL{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{random.randint(100,999)}"

def recalc(db,oid,pid):

    paid=0
    q=db.collection("partner_transactions").where("partner_id","==",pid).where("order_id","==",oid)
    for d in q.stream():
        t=d.to_dict()
        if t.get("invalid"): continue
        if t.get("direction")=="debit": paid+=float(t.get("amount") or 0)
        if t.get("direction")=="credit": paid-=float(t.get("amount") or 0)

    ref=db.collection("partner_orders").document(oid)
    o=ref.get().to_dict()
    total=float(o.get("amount_total") or 0)
    due=max(total-paid,0)
    status="unbilled" if paid<=0 else "partial" if due>0 else "paid"

    ref.update({
        "amount_paid":paid,
        "amount_due":due,
        "payment_status":status,
        "updated_at":datetime.utcnow()
    })

# ==================================================
# SERVICES MASTER (FOR MODAL)
# ==================================================

@bp.route("/services-master")
def services_master():

    if not require_employee(): return jsonify(error="auth"),403
    db=FirebaseClient.db()

    rows=[]
    for d in db.collection("services_master").stream():
        s=d.to_dict()
        rows.append({"id":d.id,"name":s.get("name"),"hsn":s.get("hsn")})

    return jsonify({"services":rows})

# ==================================================
# PARTNER CLIENTS (FOR DROPDOWN)
# ==================================================

@bp.route("/partners/<partner_id>/clients")
def employee_partner_clients(partner_id):

    if not require_employee(): return jsonify(error="auth"),403

    db=FirebaseClient.db()
    out=[]

    q=db.collection("clients").where("partner_id","==",partner_id).where("deleted","==",False).where("is_active","==",True)
    for d in q.stream():
        c=d.to_dict()
        out.append({"id":d.id,"name":c.get("client_name"),"phone":c.get("client_phone")})

    return jsonify({"clients":out})

# ==================================================
# LIST ORDERS (ONLY ASSIGNED)
# ==================================================

@bp.route("/partner-orders")
def partner_orders():

    if not require_employee(): return redirect("/employee/login")

    db=FirebaseClient.db()
    eid=session["user"]["uid"]

    partners={p.id:p.to_dict() for p in db.collection("partners").stream()}
    employees={e.id:e.to_dict() for e in db.collection("employees").stream()}

    orders=[]
    for d in db.collection("partner_orders").where("assigned_employee_id","==",eid).order_by("created_at").stream():
        o=d.to_dict()
        o["id"]=d.id

        pid=o.get("partner_id")
        if pid:
            o["partner_name"]=(partners.get(pid) or {}).get("name")

        orders.append(o)

    return render_template(
        "employee/partner_orders.html",
        orders=orders,
        partners_map=partners,
        employees_map=employees,

        page=1,total_pages=1,total_orders=len(orders),
        search="",filter_partner_id="",filter_status="",filter_payment_status="",filter_employee_id="",
        date_from="",date_to="",

        user=session.get("user"),
        active_tab="partner_orders"
    )

# ==================================================
# SINGLE ORDER
# ==================================================

@bp.route("/partner-orders/<oid>")
def get_order(oid):

    if not require_employee(): return jsonify(error="auth"),403

    db=FirebaseClient.db()
    doc=db.collection("partner_orders").document(oid).get()
    if not doc.exists: return jsonify(error="nf"),404

    o=doc.to_dict()
    if o.get("assigned_employee_id")!=session["user"]["uid"]:
        return jsonify(error="forbidden"),403

    o["id"]=doc.id

    pid=o.get("partner_id")
    if pid:
        p=db.collection("partners").document(pid).get().to_dict()
        o["partner_wallet_balance"]=p.get("wallet_balance") or 0

    pays=[]
    for t in db.collection("partner_transactions").where("order_id","==",oid).stream():
        pays.append({"id":t.id,**t.to_dict()})

    o["payments"]=pays
    return jsonify(success=True,order=o)

# ==================================================
# UPDATE ORDER + BILLING
# ==================================================

@bp.route("/partner-orders/<oid>/update",methods=["POST"])
def update_order(oid):

    if not require_employee(): return jsonify(error="auth"),403

    db=FirebaseClient.db()
    ref=db.collection("partner_orders").document(oid)
    o=ref.get().to_dict()

    if o.get("assigned_employee_id")!=session["user"]["uid"]:
        return jsonify(error="forbidden"),403

    form=request.form.to_dict()
    pid=o.get("partner_id")

    update={
        "work_title":form.get("work_title"),
        "description":form.get("description"),
        "status":form.get("status"),
        "internal_comments":form.get("internal_comments"),
        "client_id":form.get("client_id"),
        "updated_at":datetime.utcnow()
    }

    if form.get("eta"):
        update["eta"]=datetime.strptime(form["eta"],"%Y-%m-%d")

    amt_total=float(form.get("amount_total") or o.get("amount_total") or 0)
    paid=float(o.get("amount_paid") or 0)

    mode=form.get("new_payment_mode")
    amt=float(form.get("new_payment_amount") or 0)

    if mode and amt>0:

        tx={
            "partner_id":pid,
            "order_id":oid,
            "type":mode,
            "direction":"debit",
            "source":"order_payment",
            "amount":amt,
            "currency":"INR",
            "payment_date":datetime.utcnow(),
            "created_at":datetime.utcnow(),
            "created_by_name":emp_name(),
            "bill_number":generate_bill_number(),
            "transaction_number":form.get("new_payment_transaction_number") or f"EMP-{uuid.uuid4().hex[:6]}"
        }

        if mode=="wallet":
            pref=db.collection("partners").document(pid)
            bal=float(pref.get().to_dict().get("wallet_balance") or 0)
            if bal<amt: return jsonify(error="wallet_low"),400
            pref.update({"wallet_balance":bal-amt})
            tx["running_balance"]=bal-amt

        db.collection("partner_transactions").add(tx)
        paid+=amt

    due=max(amt_total-paid,0)
    status="unbilled" if paid<=0 else "partial" if due>0 else "paid"

    update.update({
        "amount_total":amt_total,
        "amount_paid":paid,
        "amount_due":due,
        "payment_status":status
    })

    ref.update(update)
    flash("Order updated","success")
    return jsonify(success=True)

# ==================================================
# INVALIDATE / REFUND
# ==================================================

@bp.route("/partner-transactions/<tid>/invalidate",methods=["POST"])
def invalidate_tx(tid):

    if not require_employee(): return jsonify(error="auth"),403
    db=FirebaseClient.db()
    ref=db.collection("partner_transactions").document(tid)
    t=ref.get().to_dict()

    ref.update({"invalid":True,"invalid_at":datetime.utcnow()})
    if t.get("order_id"): recalc(db,t["order_id"],t["partner_id"])
    return jsonify(success=True)

@bp.route("/partner-transactions/<tid>/refund",methods=["POST"])
def refund_tx(tid):

    if not require_employee(): return jsonify(error="auth"),403
    db=FirebaseClient.db()

    t=db.collection("partner_transactions").document(tid).get().to_dict()
    amt=float(request.form.get("amount") or 0)

    db.collection("partner_transactions").add({
        "partner_id":t["partner_id"],
        "order_id":t["order_id"],
        "type":"refund",
        "direction":"credit",
        "amount":amt,
        "created_at":datetime.utcnow(),
        "created_by_name":emp_name()
    })

    recalc(db,t["order_id"],t["partner_id"])
    return jsonify(success=True)

# ==================================================
# INVOICE (ADMIN CLONE)
# ==================================================

@bp.route("/partner-orders/<order_id>/generate-invoice",methods=["POST"])
def generate_invoice(order_id):

    if not require_employee(): return jsonify(error="auth"),403
    
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
        "created_by":emp_name()
    }

    invoice_ref = db.collection("order_invoices").add(invoice)


    # 🔒 LOCK ORDER
    order_ref.update({
        "status": "completed",
        "completed_at": datetime.utcnow(),
        "invoice_id": invoice_ref[1].id 
    })

    return jsonify({"success": True})


    # db=FirebaseClient.db()
    # data=request.json or {}

    # ref=db.collection("partner_orders").document(order_id)
    # o=ref.get().to_dict()

    # if o.get("assigned_employee_id")!=session["user"]["uid"]:
    #     return jsonify(error="forbidden"),403

    # partner=db.collection("partners").document(o["partner_id"]).get().to_dict()

    # services=data.get("services") or []
    # if not services: return jsonify(error="no_services"),400

    # amount_paid=float(o.get("amount_paid") or 0)
    # govt=float(data.get("government_fee") or 0)
    # oop=float(data.get("out_of_pocket") or 0)

    # remaining=amount_paid-govt-oop
    # if remaining<=0: return jsonify(error="fees_exceed"),400

    # base=round(remaining/1.18,2)
    # gst=round(remaining-base,2)

    # invoice={
    #     "order_id":order_id,
    #     "partner_id":o["partner_id"],
    #     "invoice_number":f"INV-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
    #     "invoice_date":datetime.utcnow(),
    #     "services":services,
    #     "total_paid":amount_paid,
    #     "grand_total":amount_paid,
    #     "created_at":datetime.utcnow(),
    #     "created_by":emp_name()
    # }

    # inv=db.collection("order_invoices").add(invoice)

    # ref.update({
    #     "status":"completed",
    #     "completed_at":datetime.utcnow(),
    #     "invoice_id":inv[1].id
    # })

    # return jsonify(success=True)

    # ==================================================
# PARTNER CLIENTS PAGE (EMPLOYEE)
# ==================================================

@bp.route("/partner-clients")
def partner_clients_page():

    if not require_employee():
        return redirect("/employee/login")

    db = FirebaseClient.db()
    emp_id = session["user"]["uid"]

    # 🔹 collect only partners from employee assigned orders
    partner_ids = set()
    for o in db.collection("partner_orders").where("assigned_employee_id","==",emp_id).stream():
        pid = o.to_dict().get("partner_id")
        if pid:
            partner_ids.add(pid)

    partners_map = {}
    for pid in partner_ids:
        pdoc = db.collection("partners").document(pid).get()
        if pdoc.exists:
            partners_map[pid] = pdoc.to_dict()

    # ---------------- filters ----------------
    partner_id = (request.args.get("partner_id") or "").strip()
    name = (request.args.get("client_name") or "").strip().lower()
    phone = (request.args.get("phone") or "").strip()
    email = (request.args.get("email") or "").strip().lower()
    is_active = request.args.get("is_active")

    q = db.collection("clients").where("deleted","==",False)

    if partner_id:
        q = q.where("partner_id","==",partner_id)

    if is_active in ("true","false"):
        q = q.where("is_active","==",is_active=="true")

    # ---------------- employees map (for added_by) ----------------
    employees_map = {
        e.id: e.to_dict()
        for e in db.collection("employees").stream()
    }

    clients = []

    for d in q.stream():
        c = d.to_dict()
        c["id"] = d.id

        # 🔐 only clients belonging to employee partners
        if c.get("partner_id") not in partners_map:
            continue

        if name and name not in (c.get("client_name") or "").lower():
            continue
        if phone and phone not in (c.get("client_phone") or ""):
            continue
        if email and email not in (c.get("client_email") or "").lower():
            continue

        # resolve added_by
        added = c.get("added_by") or {}
        uid = added.get("user_id")

        resolved_email = "-"
        resolved_name = "-"

        if added.get("email"):
            resolved_email = added["email"]
            resolved_name = resolved_email.split("@")[0]

        elif uid and uid in employees_map:
            emp = employees_map[uid]
            resolved_email = emp.get("email","-")
            resolved_name = emp.get("name") or resolved_email.split("@")[0]

        c["added_by_display"] = {
            "name": resolved_name,
            "email": resolved_email
        }

        clients.append(c)

    return render_template(
        "employee/partner_clients.html",
        active_tab="partner_clients",
        clients=clients,
        partners_map=partners_map,
        filters=request.args,
        user=session.get("user")
    )

@bp.route("/clients/add", methods=["POST"])
def add_client():
    if not require_employee():
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

@bp.route("/clients/toggle/<client_id>", methods=["POST"])
def toggle_client(client_id):
    if not require_employee():
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

# ==================================================
# FORGOT PASSWORD
# ==================================================

@bp.route("/forgot-password", methods=["GET", "POST"])
def employee_forgot_password():

    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()

        eid, emp = find_employee(identifier)

        if not emp:
            flash("Employee not found", "danger")
            return redirect("/employee/forgot-password")

        if emp.get("status") != "active":
            flash("Employee inactive", "danger")
            return redirect("/employee/forgot-password")

        # 🔐 send OTP
        send_otp(identifier, "employee")

        session["otp_stage"] = {
            "uid": eid,
            "identifier": identifier
        }

        return redirect("/employee/verify-otp")

    return render_template("employee/forgot_password.html")
@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/employee/login")

