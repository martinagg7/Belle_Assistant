"""firebase_client.py

Initializes the Firebase Admin SDK once and exposes get_db().
Every module that needs Firestore imports it from here.
"""
import os
import firebase_admin
from firebase_admin import credentials, firestore

_db = None


def get_db():
    global _db
    if _db is None:
        cred_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'belle-service-account.json')
        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, {
                'storageBucket': 'robotics-488810.firebasestorage.app'
            })
        _db = firestore.client()
    return _db
