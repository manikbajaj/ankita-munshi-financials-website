from flask import Blueprint, render_template, session, redirect, flash, request
from datetime import datetime
import random, string, uuid
from werkzeug.utils import secure_filename
from modules.firebase_client import FirebaseClient
#from modules.employee.employee_routes import require_sales_employee
from google.cloud.firestore import Query

bp = Blueprint("employee_sales", __name__, url_prefix="/employee/sales")

# ================= UTIL =================

def generate_partner_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def get_fortes():
    fortes=[]
    for f in FirebaseClient.db().collection("forte").stream():
        d=f.to_dict()
        fortes.append(d.get("name"))
    return sorted(fortes)

def generate_order_number(db, partner_id):
    today = datetime.utcnow()
    date_code = today.strftime("%m%d%y")

    partner = db.collection("partners").document(partner_id).get().to_dict()
    partner_code = partner.get("partner_code")

    q = db.collection("partner_orders")\
        .where("partner_id","==",partner_id)\
        .where("date_code","==",date_code)\
        .order_by("created_at",direction=Query.DESCENDING)\
        .limit(1)

    docs=list(q.stream())

    seq="0001"
    if docs:
        last=docs[0].to_dict()["order_number"].split("-")[-1]
        seq=str(int(last)+1).zfill(4)

    return f"ORD-{partner_code}-{date_code}-{seq}",date_code

def require_employee():
    user = session.get("user")
    if not user or user.get("role") != "employee":
        return False
    return True



# def require_sales_employee():
#     if not require_employee():
#         return False

#     emp = FirebaseClient.get_document("employees", session["user"]["uid"])
#     return emp and emp.get("employee_type") == "sales"

def require_sales_employee():
    user = session.get("user")
    return user and user.get("role") == "employee" and user.get("employee_type") == "sales"



# ================= DASHBOARD =================

@bp.route("/dashboard")
def dashboard():
    if not require_sales_employee():
        return redirect("/employee/login")

    uid=session["user"]["uid"]
    db=FirebaseClient.db()

    search=request.args.get("search","").lower()
    partner=request.args.get("partner","")
    status=request.args.get("status","")

    partners_map={}
    for p in db.collection("partners").stream():
        d=p.to_dict()
        phone=d.get("phone","")
        masked="******"+phone[-4:] if len(phone)>=4 else "******"
        partners_map[p.id]=f"{d.get('partner_code')} — {d.get('name')} — {masked}"

    q=db.collection("partner_orders")\
        .where("created_by_id","==",uid)\
        .order_by("created_at",direction=Query.DESCENDING)

    orders=[]
    total_sales=0

    for d in q.stream():
        o=d.to_dict()

        o["partner_display"]=partners_map.get(o.get("partner_id"),"")
        o["created_at_fmt"]=o.get("created_at").strftime("%d %b %Y")
        o["amount_total"]=o.get("amount_total",0)

        if partner and o["partner_display"]!=partner:
            continue

        if status and o.get("status")!=status:
            continue

        if search:
            blob=(o["order_number"]+" "+o["partner_display"]).lower()
            if search not in blob:
                continue

        total_sales+=o["amount_total"]
        orders.append(o)

    unique_partners=sorted(set(o["partner_display"] for o in orders if o["partner_display"]))

    stats={
        "total_orders":len(orders),
        "total_sales":total_sales,
        "completed":len([o for o in orders if o["status"]=="completed"]),
        "pending":len([o for o in orders if o["status"]!="completed"])
    }

    return render_template(
        "employee/sales/dashboard.html",
        orders=orders,
        partners=unique_partners,
        stats=stats,
        fortes=get_fortes(),
        active_tab="dashboard"
    )

# ================= CREATE ORDER =================

@bp.route("/orders/create",methods=["GET"])
def create_order_page():
    if not require_sales_employee():
        return redirect("/employee/login")

    partners=[]

    for p in FirebaseClient.db().collection("partners").stream():
        d=p.to_dict()
        phone=d.get("phone","")
        masked="******"+phone[-4:] if len(phone)>=4 else "******"

        partners.append({
            "id":p.id,
            "display":f"{d.get('partner_code')} — {d.get('name')} — {masked}"
        })

    return render_template(
        "employee/sales/create_order.html",
        partners=partners,
        fortes=get_fortes(),
        active_tab="create_order"
    )

@bp.route("/orders/create",methods=["POST"])
def create_order():
    if not require_sales_employee():
        return redirect("/employee/login")

    db=FirebaseClient.db()
    emp=FirebaseClient.get_document("employees",session["user"]["uid"])

    partner_id=request.form.get("partner_id")
    work_title=request.form.get("work_title")
    extra_description=request.form.get("extra_description")

    order_number,date_code=generate_order_number(db,partner_id)

    attachments=[]

    for f in request.files.getlist("attachments"):
        if f and f.filename:
            filename=secure_filename(f.filename)
            blob_path=f"sales_orders/{partner_id}/{order_number}/{uuid.uuid4().hex}_{filename}"

            gcs=FirebaseClient.upload_private_file(blob_path,f,f.content_type)

            attachments.append({
                "file_name":filename,
                "gcs_path":gcs,
                "uploaded_at":datetime.utcnow()
            })

    db.collection("partner_orders").add({
        "order_number":order_number,
        "date_code":date_code,
        "partner_id":partner_id,
        "work_title":work_title,
        "extra_description":extra_description,
        "attachments":attachments,
        "status":"pending",
        "amount_total":0,
        "amount_paid":0,
        "created_by_id":session["user"]["uid"],
        "created_by_name":emp.get("name"),
        "created_at":datetime.utcnow()
    })

    flash("Order created","success")
    return redirect("/employee/sales/dashboard")

# ================= PARTNERS =================

@bp.route("/partners")
def partners_page():
    if not require_sales_employee():
        return redirect("/employee/login")

    uid=session["user"]["uid"]
    db=FirebaseClient.db()

    search=request.args.get("search","").lower()
    status=request.args.get("status","")

    partners=[]

    for p in db.collection("partners").where("created_by_id","==",uid).stream():
        d=p.to_dict()

        phone=d.get("phone","")
        masked="******"+phone[-4:] if len(phone)>=4 else "******"

        db_status=d.get("status","").lower()
        if status and db_status!=status:
            continue

        blob=(d.get("partner_code","")+" "+d.get("name","")+" "+d.get("city","")).lower()
        if search and search not in blob:
            continue

        partners.append({
            "partner_code":d.get("partner_code"),
            "name":d.get("name"),
            "phone":masked,
            "city":d.get("city"),
            "status":db_status,
            "created_at":d.get("created_at").strftime("%d %b %Y")
        })

    return render_template(
        "employee/sales/partners.html",
        partners=partners,
        fortes=get_fortes(),
        active_tab="partners"
    )

# ================= ADD PARTNER =================

@bp.route("/partners/add",methods=["POST"])
def sales_add_partner():
    if not require_sales_employee():
        return redirect("/employee/login")

    emp=FirebaseClient.get_document("employees",session["user"]["uid"])
    data=request.form

    FirebaseClient.db().collection("partners").add({
        "name":data.get("name"),
        "email":data.get("email"),
        "phone":data.get("phone"),
        "gst_number":data.get("gst_number"),
        "fortes":request.form.getlist("fortes"),
        "city":data.get("city"),
        "state":data.get("state"),
        "address":data.get("address"),
        "profession_manual":data.get("profession_manual"),
        "status":"pending",
        "partner_code":generate_partner_code(),
        "created_by_id":session["user"]["uid"],
        "created_by_name":emp.get("name"),
        "created_at":datetime.utcnow()
    })

    flash("Partner added","success")
    return redirect("/employee/sales/dashboard")

@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/employee/login")

