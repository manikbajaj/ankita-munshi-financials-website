from modules.firebase_client import FirebaseClient


def find_partner(identifier):
    db = FirebaseClient.db()

    q = db.collection("partners")\
        .where("email", "==", identifier)\
        .limit(1).stream()

    for d in q:
        return d.id, d.to_dict()

    q = db.collection("partners")\
        .where("phone", "==", identifier)\
        .limit(1).stream()

    for d in q:
        return d.id, d.to_dict()

    q = db.collection("partners")\
        .where("partner_code", "==", identifier)\
        .limit(1).stream()

    for d in q:
        return d.id, d.to_dict()

    return None, None


def find_employee(identifier):
    db = FirebaseClient.db()
    identifier = identifier.strip()

    # EMAIL
    q = db.collection("employees")\
        .where("email", "==", identifier.lower())\
        .limit(1).stream()

    for d in q:
        return d.id, d.to_dict()

    # PHONE
    q = db.collection("employees")\
        .where("phone", "==", identifier)\
        .limit(1).stream()

    for d in q:
        return d.id, d.to_dict()

    # EMPLOYEE CODE
    q = db.collection("employees")\
        .where("employee_id", "==", identifier)\
        .limit(1).stream()

    for d in q:
        return d.id, d.to_dict()

    return None, None