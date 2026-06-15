import os
import hmac
import hashlib
from fastapi import HTTPException

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

if not GITHUB_WEBHOOK_SECRET:
    raise RuntimeError("GITHUB_WEBHOOK_SECRET not set")


def verify_github_signature(payload: bytes, signature_header: str):
    sha_name, signature = signature_header.split("=")
    if sha_name != "sha256":
        raise HTTPException(status_code=400, detail="Invalid signature type")

    mac = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    )

    if not hmac.compare_digest(mac.hexdigest(), signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
