"""Print a body + Upstash-Signature so you can curl a stage endpoint the way QStash would.

    eval "$(python scripts/sign_qstash.py <run_id> research)"
    curl -X POST $API/internal/runs/<run_id>/stages/research -H "Upstash-Signature: $SIG" -d "$BODY"
Uses QSTASH_CURRENT_SIGNING_KEY and BACKEND_URL from .env.
"""
import base64
import hashlib
import json
import os
import shlex
import sys
import time

import jwt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.services.queue import stage_url  # noqa: E402

run_id, stage = sys.argv[1], sys.argv[2]
s = get_settings()
if not s.qstash_current_signing_key:
    sys.exit("Set QSTASH_CURRENT_SIGNING_KEY in .env (any string works for local testing)")

body = json.dumps({"run_id": run_id, "stage": stage}, separators=(",", ":")).encode()
now = int(time.time())
claims = {
    "iss": "Upstash",
    "sub": stage_url(run_id, stage),
    "body": base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("="),
    "iat": now, "nbf": now, "exp": now + 300,
}
# Shell-quoted so `eval "$(python scripts/sign_qstash.py ...)"` sets $BODY and $SIG exactly.
print("BODY=" + shlex.quote(body.decode()))
print("SIG=" + shlex.quote(jwt.encode(claims, s.qstash_current_signing_key, algorithm="HS256")))
