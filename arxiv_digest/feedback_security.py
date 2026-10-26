"""Optional capability signatures for feedback links exposed beyond localhost."""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode


def signature(params: dict[str, str], secret: str) -> str:
    body = urlencode(sorted((key, str(value)) for key, value in params.items() if key != "sig"))
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def signed_params(params: dict, secret: str) -> dict:
    if not secret:
        return params
    result = {key: str(value) for key, value in params.items()}
    result["expires"] = str(int(time.time()) + 30 * 86400)
    result["sig"] = signature(result, secret)
    return result


def verify_params(params: dict[str, str], secret: str) -> bool:
    if not secret:
        return True
    try:
        expires = int(params.get("expires", "0"))
    except ValueError:
        return False
    return expires >= time.time() and hmac.compare_digest(params.get("sig", ""), signature(params, secret))
