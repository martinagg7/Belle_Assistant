"""Vision tool: capture camera frames and describe them with Gemini."""

import os
import time
import logging

import cv2
import requests
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CAMERA_URL     = "http://localhost:8081"  # camera_server (reads from the MJPEG tracker :8080)
PROMPT     = "Describe en español lo que se ve en la imagen en un máximo de 15 palabras. Solo descripción objetiva de lo que aparece, sin tono ni interpretaciones. Si no hay personas, dilo."
PROMPT_WEB = "Describe en español lo que se ve en la imagen en un máximo de 15 palabras. Solo descripción objetiva de lo que aparece, sin tono ni interpretaciones. Si no hay personas, dilo."

client = genai.Client(api_key=GEMINI_API_KEY)


def _capture_frame():
    cap = cv2.VideoCapture("/dev/video0")
    try:
        ret, frame = cap.read()
        return ret, frame
    finally:
        cap.release()


def describe_view() -> str:
    img_bytes = _capture_jpeg()
    if not img_bytes:
        return "No he podido acceder a la cámara."

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                PROMPT,
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            ],
        )
        return response.text.strip()
    except Exception as e:
        return f"No he podido analizar la imagen: {e}"


def describe_view_with_photo() -> tuple:
    """Capture a photo, upload it to Firebase Storage, return (image_url, description)."""
    img_bytes = _capture_jpeg()
    if not img_bytes:
        return ("", "No he podido acceder a la cámara.")

    image_url = _upload_jpeg_to_storage(img_bytes, "vision_requests")

    description = ""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                PROMPT_WEB,
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            ],
        )
        description = response.text.strip()
    except Exception as e:
        description = f"No he podido analizar la imagen: {e}"

    return (image_url, description)


def _capture_jpeg() -> bytes | None:
    """Capture a JPEG from the camera with two paths and automatic fallback.

    A) camera_server (:8081/foto) if the tracker is running.
    B) the robot USB webcam via OpenCV (/dev/video0, /dev/video1).
    Returns the JPEG bytes, or None if neither works.
    """
    # A camera_server if the tracker is running
    try:
        r = requests.get(f"{CAMERA_URL}/foto", timeout=4)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass

    # B USB webcam via OpenCV
    for dev in ("/dev/video0", "/dev/video1"):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        try:
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                for _ in range(5):       # drop the first frames to stabilize
                    cap.read()
                ret, frame = cap.read()
                if ret and frame is not None:
                    ok, buffer = cv2.imencode('.jpg', frame)
                    if ok:
                        print(f"[Cámara] photo captured from {dev}")
                        return buffer.tobytes()
        finally:
            cap.release()

    print("[Cámara] could not capture from /dev/video0 or /dev/video1")
    return None


def _upload_jpeg_to_storage(img_bytes: bytes, carpeta: str) -> str:
    """Upload a JPEG to Firebase Storage at <folder>/<timestamp>.jpg.

    Returns a permanent Firebase download URL, or "" on failure.
    """
    try:
        import uuid
        from urllib.parse import quote
        from firebase_client import get_db
        get_db()  # ensure Firebase Admin is initialized (with storageBucket)
        from firebase_admin import storage as fb_storage

        bucket = fb_storage.bucket()
        blob   = bucket.blob(f"{carpeta}/{int(time.time())}.jpg")

        token = str(uuid.uuid4())
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        blob.upload_from_string(img_bytes, content_type="image/jpeg")

        object_name = quote(blob.name, safe="")
        url = (
            f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}"
            f"/o/{object_name}?alt=media&token={token}"
        )
        logger.info("Vision: photo uploaded: %s", url)
        return url
    except Exception:
        logger.error("Vision: error uploading photo", exc_info=True)
        return ""


def capture_emergency_photo() -> str:
    """Capture a photo at the moment of an emergency and upload it. No AI; speed first."""
    img_bytes = _capture_jpeg()
    if not img_bytes:
        print("[Emergencia] could not capture camera photo")
        return ""
    return _upload_jpeg_to_storage(img_bytes, "emergencias")


if __name__ == "__main__":
    print(describe_view())
