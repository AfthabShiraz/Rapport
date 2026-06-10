"""Overmind SDK wrapper. Failure-safe AND lazy: `import overmind` pulls in the
whole OpenTelemetry stack (observed ~2 min cold on this machine), so it loads
in a background thread at startup. Tracing is a no-op until it's ready, then
flips on transparently; nothing here can break or delay a live call."""
import logging
import threading
import uuid

log = logging.getLogger("overmind")

_ovm = None
_ready = False
_lock = threading.Lock()


class _NoopSpanContext:
    def __init__(self):
        self.trace_id = uuid.uuid4().int & ((1 << 128) - 1)


class _NoopSpan:
    def __init__(self):
        self._ctx = _NoopSpanContext()

    def set_attribute(self, *a, **k):
        pass

    def end(self):
        pass

    def get_span_context(self):
        return self._ctx

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _NoopTracer:
    def start_span(self, name, **k):
        return _NoopSpan()


def _load():
    global _ovm, _ready
    try:
        import overmind as sdk  # slow: pulls in the full OTel stack

        sdk.init(service_name="sales-agent")
        with _lock:
            _ovm = sdk
            _ready = True
        log.info("overmind initialised (background load complete)")
    except Exception as e:
        log.warning("overmind unavailable (%s) — tracing disabled", e)


def init():
    threading.Thread(target=_load, name="overmind-init", daemon=True).start()
    log.info("overmind loading in background — tracing no-op until ready")


def get_tracer():
    if _ready:
        try:
            return _ovm.get_tracer()
        except Exception as e:
            log.warning("overmind.get_tracer failed: %s", e)
    return _NoopTracer()


def start_span(name):
    try:
        return get_tracer().start_span(name)
    except Exception:
        return _NoopSpan()


def start_call_span(agent_id):
    """Root span for a call. Returns (span, trace_id_hex)."""
    span = start_span("sales-call")
    try:
        trace_id = format(span.get_span_context().trace_id, "032x")
    except Exception:
        trace_id = uuid.uuid4().hex
    try:
        if _ready:
            _ovm.set_user(user_id=agent_id, email=f"{agent_id}@demo.local")
    except Exception as e:
        log.warning("overmind.set_user failed: %s", e)
    return span, trace_id


def set_attrs(span, attrs: dict):
    for k, v in attrs.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            pass


def end_span(span):
    try:
        span.end()
    except Exception:
        pass


def evaluate(trace_id, scores: dict):
    if not _ready or not trace_id:
        if not _ready:
            log.warning("overmind not ready — evaluate skipped for %s", trace_id)
        return
    try:
        _ovm.evaluate(trace_id=trace_id, scores=scores)
        log.info("overmind.evaluate submitted for trace %s", trace_id)
    except Exception as e:
        log.warning("overmind.evaluate failed: %s", e)
