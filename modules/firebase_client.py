# modules/firebase_client.py

import os
import requests
from typing import Optional, Dict, Any
from datetime import timedelta

import firebase_admin
from firebase_admin import credentials, firestore

from google.cloud import storage
from google.oauth2 import service_account


class FirebaseClient:
    """
    Central Firebase + Firestore + Storage Client
    ---------------------------------------------
    - Single initialization
    - Env-driven config
    - Safe for prod
    """

    # =========================
    # 🔧 CONFIG (ENV-DRIVEN)
    # =========================
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", "key.json")
    STORAGE_BUCKET_NAME = os.getenv("FIREBASE_STORAGE_BUCKET")
    FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

    # =========================
    # 🔒 INTERNAL SINGLETONS
    # =========================
    _db = None
    _storage_client = None
    _cred_path = None
    _project_id = None

    # =========================
    # 🔐 INITIALIZATION
    # =========================
    @classmethod
    def initialize(cls, cred_path: Optional[str] = None):
        """
        Initialize Firebase Admin SDK once.
        Must be called at app startup.
        """
        cls._cred_path = cred_path or cls.FIREBASE_CREDENTIALS

        if not os.path.exists(cls._cred_path):
            raise FileNotFoundError(f"Firebase key file not found: {cls._cred_path}")

        if not firebase_admin._apps:
            cred = credentials.Certificate(cls._cred_path)
            firebase_admin.initialize_app(cred)

        cls._db = firestore.client()

        # Load project id once
        sa = service_account.Credentials.from_service_account_file(cls._cred_path)
        cls._project_id = sa.project_id

        if not cls.STORAGE_BUCKET_NAME:
            raise RuntimeError("FIREBASE_STORAGE_BUCKET env variable missing")

        return cls._db

    # =========================
    # 🔥 FIRESTORE
    # =========================
    @classmethod
    def db(cls):
        if cls._db is None:
            raise RuntimeError("Firebase not initialized. Call FirebaseClient.initialize() first.")
        return cls._db

    # =========================
    # 📦 STORAGE CLIENT
    # =========================
    @classmethod
    def storage(cls) -> storage.Client:
        """
        Google Cloud Storage client
        Uses same Firebase service account
        """
        if cls._storage_client is None:
            if not cls._cred_path:
                raise RuntimeError("Firebase not initialized")

            creds = service_account.Credentials.from_service_account_file(cls._cred_path)

            cls._storage_client = storage.Client(
                credentials=creds,
                project=creds.project_id
            )

        return cls._storage_client

    @classmethod
    def bucket(cls):
        """
        Default Firebase storage bucket
        """
        return cls.storage().bucket(cls.STORAGE_BUCKET_NAME)

    # =========================
    # 📄 FIRESTORE HELPERS
    # =========================
    @classmethod
    def get_document(cls, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            snap = cls.db().collection(collection).document(doc_id).get()
            return snap.to_dict() if snap.exists else None
        except Exception as e:
            print(f"[Firestore:get_document] {e}")
            return None

    @classmethod
    def set_document(cls, collection: str, doc_id: str, data: Dict[str, Any]) -> bool:
        try:
            cls.db().collection(collection).document(doc_id).set(data)
            return True
        except Exception as e:
            print(f"[Firestore:set_document] {e}")
            return False

    @classmethod
    def update_document(cls, collection: str, doc_id: str, data: Dict[str, Any]) -> bool:
        try:
            cls.db().collection(collection).document(doc_id).update(data)
            return True
        except Exception as e:
            print(f"[Firestore:update_document] {e}")
            return False

    # =========================
    # 🔐 AUTH (EMAIL/PASSWORD)
    # =========================
    @classmethod
    def firebase_login_with_email_password(cls, email: str, password: str) -> dict:
        if not cls.FIREBASE_API_KEY:
            raise RuntimeError("FIREBASE_API_KEY missing")

        url = (
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
            f"?key={cls.FIREBASE_API_KEY}"
        )

        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }

        return requests.post(url, json=payload, timeout=10).json()

    # =========================
    # 📄 FILE UPLOAD HELPERS
    # =========================
    @classmethod
    def upload_bytes(
        cls,
        blob_path: str,
        data: bytes,
        content_type: str
    ) -> str:
        """
        Upload raw bytes to storage
        Returns gs:// path
        """
        blob = cls.bucket().blob(blob_path)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{cls.STORAGE_BUCKET_NAME}/{blob_path}"

    @classmethod
    def upload_file(
        cls,
        blob_path: str,
        file_obj,
        content_type: str
    ) -> str:
        blob = cls.bucket().blob(blob_path)
        blob.upload_from_file(file_obj, content_type=content_type)
        blob.make_public()
        return blob.public_url

    # =========================
    # 🧾 INVOICE STORAGE
    # =========================
    @classmethod
    def upload_invoice_pdf(cls, pdf_bytes: bytes, txn_id: str) -> str:
        return cls.upload_bytes(
            blob_path=f"invoices/{txn_id}.pdf",
            data=pdf_bytes,
            content_type="application/pdf"
        )

    # =========================
    # 🔐 SIGNED URL (SECURE)
    # =========================
    @classmethod
    def generate_signed_url(cls, gcs_path: str, minutes: int = 5) -> Optional[str]:
        """
        Convert gs://bucket/path → temporary HTTPS URL
        """
        if not gcs_path.startswith("gs://"):
            return None

        _, path = gcs_path.split("gs://", 1)
        bucket_name, blob_path = path.split("/", 1)

        blob = cls.storage().bucket(bucket_name).blob(blob_path)

        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=minutes),
            method="GET"
        )
    @classmethod
    def upload_private_file(cls, blob_path: str, file_obj, content_type: str) -> str:
        """
        Upload file as PRIVATE
        Returns gs:// path (not public URL)
        """
        blob = cls.bucket().blob(blob_path)
        blob.upload_from_file(file_obj, content_type=content_type)

        # ❌ DO NOT blob.make_public()
        return f"gs://{cls.STORAGE_BUCKET_NAME}/{blob_path}"
    