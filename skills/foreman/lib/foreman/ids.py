"""Identifiers: session ids (s-xxxxxx) and message ids (m-xxxxxxxx)."""
import secrets


def new_sid(exists=None, _gen=None):
    gen = _gen or (lambda: "s-" + secrets.token_hex(3))
    for _ in range(1000):
        sid = gen()
        if not (exists and exists(sid)):
            return sid
    raise RuntimeError("could not allocate a session id")


def new_msg_id():
    return "m-" + secrets.token_hex(4)
