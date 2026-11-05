"""Transports: `get_transport(kind)` returns a Transport instance.

Real transports live in foreman/transports/<kind>.py and expose a class named <Kind>Transport
(OrcaTransport, HerdrTransport, ClaudeMsgTransport for kind "claude-msg"); they are imported lazily so a
missing one never breaks the others."""
import importlib


def get_transport(kind):
    if kind == "fake":
        from foreman.transports.fake import FakeTransport
        return FakeTransport()
    name = kind.replace("-", "_") if isinstance(kind, str) else kind  # "claude-msg" -> claude_msg
    if name and name.isidentifier() and name not in ("base", "fake") and not name.startswith("_"):
        try:
            mod = importlib.import_module("foreman.transports." + name)
        except ImportError:
            mod = None
        cls_name = "".join(part.capitalize() for part in name.split("_")) + "Transport"
        cls = getattr(mod, cls_name, None) if mod else None
        if cls is not None:
            return cls()
    raise ValueError("unknown or unavailable transport: %r" % (kind,))
