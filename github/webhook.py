import os
import hmac
import hashlib
import re
from fastapi import HTTPException

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

if not GITHUB_WEBHOOK_SECRET:
    raise RuntimeError("GITHUB_WEBHOOK_SECRET not set")


def verify_github_signature(payload: bytes, signature_header: str):
    if not isinstance(signature_header, str) or not re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature_header):
        raise HTTPException(status_code=400, detail="Missing or malformed webhook signature")
    signature = signature_header.split("=", 1)[1].lower()

    mac = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    )

    if not hmac.compare_digest(mac.hexdigest(), signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
