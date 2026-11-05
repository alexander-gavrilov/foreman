"""Transports: `get_transport(kind)` returns a Transport instance.

Real transports live in foreman/transports/<kind>.py and expose a class named <Kind>Transport
(OrcaTransport, HerdrTransport); they are imported lazily so a missing one never breaks the others."""
import importlib


def get_transport(kind):
    if kind == "fake":
        from foreman.transports.fake import FakeTransport
        return FakeTransport()
    if kind and kind.isidentifier() and kind not in ("base", "fake"):
        try:
            mod = importlib.import_module("foreman.transports." + kind)
        except ImportError:
            mod = None
        cls = getattr(mod, kind.capitalize() + "Transport", None) if mod else None
        if cls is not None:
            return cls()
    raise ValueError("unknown or unavailable transport: %r" % (kind,))
