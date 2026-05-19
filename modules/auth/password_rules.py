import re
import bcrypt


def is_strong_password(password: str):
    if len(password) < 8:
        return False, "Min 8 characters required"
    if not re.search(r"[A-Z]", password):
        return False, "One uppercase required"
    if not re.search(r"[0-9]", password):
        return False, "One number required"
    if not re.search(r"[!@#$%^&*]", password):
        return False, "One special character required"
    return True, "Strong password"


def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str):
    return bcrypt.checkpw(password.encode(), hashed.encode())
