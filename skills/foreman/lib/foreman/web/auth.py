"""Web authentication and device token management (architecture §9).

Pairing:
- One-time 8-char code from a 32-letter unambiguous alphabet valid for 10 minutes.
- Stored hashed in secrets.json (mode 0600).
- Exchanged for a random device token stored hashed with label and created_at.
- Device token is validated via cookie `fm_token` (HttpOnly; SameSite=Strict; Path=/)
  or `Authorization: Bearer <token>`.
- Rate limiting: at most 5 failed pairing attempts per minute per client IP.
"""
from datetime import timedelta
import hashlib
import os
import secrets
import threading
import time

from foreman import config, ids, paths, store, timeutil

PAIRING_TTL_SECONDS = 600  # 10 minutes
PAIRING_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # 32 unambiguous chars (no 0, 1, i, l, o)

# Rate limiting for failed pairing attempts: client_ip -> list of failure timestamps
_failed_pairing_lock = threading.Lock()
_failed_pairing_attempts = {}


def check_pairing_rate_limit(client_ip):
    """Return True if client_ip is within the rate limit (< 5 failures in past 60s), False if exceeded."""
    if not client_ip:
        return True
    now = time.time()
    cutoff = now - 60.0
    with _failed_pairing_lock:
        recent = [t for t in _failed_pairing_attempts.get(client_ip, []) if t >= cutoff]
        _failed_pairing_attempts[client_ip] = recent
        return len(recent) < 5


def record_failed_pairing(client_ip):
    """Record a failed pairing attempt for client_ip."""
    if not client_ip:
        return
    now = time.time()
    cutoff = now - 60.0
    with _failed_pairing_lock:
        recent = [t for t in _failed_pairing_attempts.get(client_ip, []) if t >= cutoff]
        recent.append(now)
        _failed_pairing_attempts[client_ip] = recent


def reset_pairing_rate_limits():
    """Clear all rate limit records (for testing)."""
    with _failed_pairing_lock:
        _failed_pairing_attempts.clear()



def hash_token(token):
    if not isinstance(token, str):
        token = str(token)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_base_url():
    cfg = config.load()
    base = cfg.get("web", {}).get("base_url")
    if base:
        return base.rstrip("/")
    binds = cfg.get("web", {}).get("bind", ["127.0.0.1"])
    host = binds[0] if isinstance(binds, list) and binds else "127.0.0.1"
    port = cfg.get("web", {}).get("port", 8765)
    return "http://%s:%d" % (host, port)


def create_pairing_code(label=None):
    """Generate a one-time 8-char code from a 32-letter unambiguous alphabet valid for 10 minutes,
    store hashed in secrets.json (0600). Returns (code, pair_url)."""
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    now_dt = timeutil.now()
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = (now_dt + timedelta(seconds=PAIRING_TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    code_hash = hash_token(code)

    def apply(sec):
        if not isinstance(sec, dict):
            sec = {}
        codes = sec.setdefault("web_pairing_codes", [])
        # Prune expired codes
        active = []
        for c in codes:
            exp_dt = timeutil.parse(c.get("expires_at", ""))
            if exp_dt and exp_dt >= now_dt:
                active.append(c)
        active.append({
            "code_hash": code_hash,
            "created_at": now,
            "expires_at": expires_at,
            "label": label or "web",
        })
        sec["web_pairing_codes"] = active
        return sec

    store.update_json(paths.secrets_file(), apply, default={}, mode=0o600)
    base_url = _get_base_url()
    return code, "%s/pair?code=%s" % (base_url, code)


def exchange_pairing_code(code, label=None):
    """Exchange a one-time pairing code for a persistent device token.
    Returns (success: bool, token_or_error: str, device_id_or_none: str|None)."""
    if not code or not isinstance(code, str):
        return False, "invalid pairing code", None

    code_clean = code.strip().lower()
    chash = hash_token(code_clean)
    now_dt = timeutil.now()
    now_iso = timeutil.now_iso()

    result = {}

    def apply(sec):
        if not isinstance(sec, dict):
            sec = {}
        codes = sec.setdefault("web_pairing_codes", [])
        devices = sec.setdefault("web_devices", [])

        # Find matching unexpired code
        matched_idx = None
        for i, entry in enumerate(codes):
            if entry.get("code_hash") == chash:
                exp_dt = timeutil.parse(entry.get("expires_at", ""))
                if exp_dt and exp_dt >= now_dt:
                    matched_idx = i
                    break

        if matched_idx is None:
            result["success"] = False
            result["error"] = "invalid or expired pairing code"
            return sec

        code_entry = codes.pop(matched_idx)
        dev_token = secrets.token_urlsafe(32)
        dev_id = "d-" + secrets.token_hex(3)
        dev_label = label or code_entry.get("label") or "web"

        devices.append({
            "id": dev_id,
            "token_hash": hash_token(dev_token),
            "label": dev_label,
            "created_at": now_iso,
        })
        result["success"] = True
        result["token"] = dev_token
        result["device_id"] = dev_id
        return sec

    store.update_json(paths.secrets_file(), apply, default={}, mode=0o600)
    if result.get("success"):
        return True, result["token"], result["device_id"]
    return False, result.get("error", "pairing failed"), None


def validate_token(token):
    """Validate a device token against secrets.json."""
    if not token or not isinstance(token, str):
        return False
    sec = store.read_json(paths.secrets_file(), {})
    if not isinstance(sec, dict):
        return False
    devices = sec.get("web_devices", [])
    thash = hash_token(token)
    return any(d.get("token_hash") == thash for d in devices)


def list_devices():
    """List paired devices: [{"id": ..., "label": ..., "created_at": ...}]."""
    sec = store.read_json(paths.secrets_file(), {})
    if not isinstance(sec, dict):
        return []
    devices = sec.get("web_devices", [])
    return [{"id": d["id"], "label": d.get("label", ""), "created_at": d.get("created_at", "")}
            for d in devices if d.get("id")]


def revoke_device(device_id):
    """Revoke a paired device by id. Returns True if revoked, False if not found."""
    found = [False]

    def apply(sec):
        if not isinstance(sec, dict):
            sec = {}
        devices = sec.setdefault("web_devices", [])
        before = len(devices)
        devices[:] = [d for d in devices if d.get("id") != device_id]
        if len(devices) < before:
            found[0] = True
        return sec

    store.update_json(paths.secrets_file(), apply, default={}, mode=0o600)
    return found[0]
