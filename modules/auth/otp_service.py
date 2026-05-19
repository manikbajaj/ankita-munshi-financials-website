import random
import bcrypt
from datetime import datetime, timezone,timedelta
from modules.firebase_client import FirebaseClient

OTP_EXPIRY_MINUTES = 5
MAX_ATTEMPTS = 3


def generate_otp():
    #return str(random.randint(100000, 999999))
    return 100001


def hash_otp(otp: str) -> str:
    # return bcrypt.hashpw(otp.encode(), bcrypt.gensalt()).decode()
    return otp


def verify_otp_hash(otp: str, hashed: str) -> bool:
    #return bcrypt.checkpw(otp.encode(), hashed.encode())
    return otp


def send_otp(identifier: str, role: str):
    db = FirebaseClient.db()

    otp = generate_otp()

  
    now = datetime.now(timezone.utc)

    doc = {
        "identifier": identifier,
        "role": role,
        "otp_hash": hash_otp(otp),
        "attempts": 0,
        "expires_at": now + timedelta(minutes=OTP_EXPIRY_MINUTES),
        "created_at": now
    }

    db.collection("otp_log").add(doc)

    
    print("\n" + "="*40)
    print(f"✅ OTP FOR {identifier} ({role.upper()}): {otp}")
    print("⏳ Valid for 5 minutes")
    print("="*40 + "\n")

    return True

def verify_otp(identifier: str, otp_input: str, role: str):
    db = FirebaseClient.db()

    q = db.collection("otp_log")\
        .where("identifier", "==", identifier)\
        .where("role", "==", role)\
        .limit(1).stream()

    doc = None
    doc_id = None

    for d in q:
        doc = d.to_dict()
        doc_id = d.id
        break

    if not doc:
        return False, "OTP expired or invalid"

    # ✅ ✅ FIX: MAKE UTC TIMEZONE-AWARE
    now = datetime.now(timezone.utc)

    # ✅ EXPIRY FIX
    expires_at = doc["expires_at"]
    if now > expires_at:
        db.collection("otp_log").document(doc_id).delete()
        return False, "OTP expired"

    
    if doc["attempts"] >= MAX_ATTEMPTS:
        db.collection("otp_log").document(doc_id).delete()
        return False, "Too many attempts"


    if not verify_otp_hash(otp_input, doc["otp_hash"]):
        db.collection("otp_log").document(doc_id).update({
            "attempts": doc["attempts"] + 1
        })
        return False, "Invalid OTP"

    db.collection("otp_log").document(doc_id).delete()
    return True, "OTP verified"