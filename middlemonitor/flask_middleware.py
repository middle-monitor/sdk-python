"""
Optional Flask integration.

Full instrumentation (one span per request + 5xx submission, mirroring the Go
SDK's Echo/Gin/HTTP middlewares):

    from middlemonitor.flask_middleware import instrument_flask

    instrument_flask(app)

Error-only hook (5xx submission to the Errors API, no tracing):

    from middlemonitor.flask_middleware import capture_exception_errors

    app.after_request(capture_exception_errors)

instrument_flask supersedes capture_exception_errors; use one or the other.
"""
import time
from typing import Any

from . import get_global_client, get_message_from_exception_body
from .client_ip import resolve_client_ip
from .config import LogLevel, should_sample_log


def _http_status_to_level(status: int) -> LogLevel:
    """Maps a response status onto the levels the log sampling rules are written in:
    5xx is the service failing, 4xx is the caller at fault."""
    if status >= 500:
        return LogLevel.ERROR
    if status >= 400:
        return LogLevel.WARN
    return LogLevel.INFO


def _response_body(response: Any) -> bytes:
    """Reads the body only when it is buffered: get_data() on a streaming response
    (SSE, file download) raises and would consume the generator."""
    if getattr(response, "direct_passthrough", False):
        return b""
    try:
        return response.get_data()
    except RuntimeError:
        return b""


def _log_http_request(client: Any, method: str, route: str, status: int,
                      duration_ms: int, has_error: bool, cause: str = "",
                      client_ip: str = "") -> None:
    """Records one entry per instrumented request, so the Logs view carries
    per-service request volume — the half of the correlation that host CPU/memory
    metrics cannot provide on their own.

    Gated by the log sampling rules, which is what keeps this from becoming an
    access-log firehose: with the defaults only failed requests get through (4xx as
    WARN, 5xx as ERROR), never the 2xx traffic."""
    level = _http_status_to_level(status)
    if not should_sample_log(client.config, route, level, status, has_error):
        return
    message = f"{method} {route} {status}"
    # The cause is already extracted for the Errors view; carrying it here is what
    # makes a 5xx line readable without opening the trace.
    if cause and cause != f"HTTP {status}":
        message = f"{message}: {cause}"
    attrs = {
        "http.method": method,
        "http.route": route,
        "http.status_code": str(status),
        "duration_ms": str(duration_ms),
    }
    # Absent unless config.client_ip allows it — see resolve_client_ip.
    if client_ip:
        attrs["client.ip"] = client_ip
    try:
        client.log(level, message, attrs)
    except Exception:
        pass


def capture_exception_errors(response: Any) -> Any:
    """
    Flask after_request hook: on 5xx, capture response body, extract "error" from JSON,
    and submit to Middle-Monitor Errors API so the UI shows the detailed message.
    """
    if response.status_code < 500:
        return response
    try:
        from flask import request
    except ImportError:
        return response
    client = get_global_client()
    if not client:
        return response
    if client.config.disable_http_error_reporting:
        return response
    body = _response_body(response)
    message = get_message_from_exception_body(body, response.status_code)
    request_body = None
    if request.get_data():
        data = request.get_data()
        if len(data) <= 2000:
            request_body = data
        else:
            request_body = data[:2000]
    try:
        client.submit_application_error(
            name="http",
            message=message,
            file="handler",
            line=0,
            status_code=response.status_code,
            method=request.method,
            url=request.url,
            request_body=request_body,
        )
    except Exception:
        pass
    return response


def instrument_flask(app: Any) -> Any:
    """Register before/after/teardown hooks on the app: one SERVER span per request
    (W3C trace context extracted from headers, span active during the handler),
    error status on 4xx/5xx, an error span for 5xx on never-sampled routes, and the
    5xx submission of capture_exception_errors."""
    from flask import g, request

    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry.propagate import extract
    from opentelemetry.trace import SpanKind, Status, StatusCode

    from .config import should_sample_trace

    @app.before_request
    def _mm_start_request():
        g._mm_start = time.monotonic()
        client = get_global_client()
        if client is None or client.otel_client.tracer is None:
            return
        route = request.path
        if not should_sample_trace(client.config, route, False):
            return
        parent = extract(dict(request.headers))
        span = client.otel_client.tracer.start_span(
            f"{request.method} {route}",
            context=parent,
            kind=SpanKind.SERVER,
            attributes={
                "http.method": request.method,
                "http.route": route,
                "http.url": request.url,
            },
        )
        g._mm_span = span
        g._mm_token = otel_context.attach(trace.set_span_in_context(span))

    @app.after_request
    def _mm_finish_span(response):
        client = get_global_client()
        if client is None:
            return response
        status = response.status_code
        has_error = status >= 400
        is_server_error = status >= 500
        span = getattr(g, "_mm_span", None)
        if span is not None:
            span.set_attribute("http.status_code", status)
            span.set_attribute("error", has_error)
            if has_error:
                span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
            else:
                span.set_status(Status(StatusCode.OK))
        elif (
            is_server_error
            and client.otel_client.tracer is not None
            and should_sample_trace(client.config, request.path, True)
        ):
            # Never-sampled route (e.g. /health) that failed: still export an error span
            error_span = client.otel_client.tracer.start_span(
                f"{request.method} {request.path}",
                context=extract(dict(request.headers)),
                kind=SpanKind.SERVER,
                attributes={
                    "http.method": request.method,
                    "http.route": request.path,
                    "http.url": request.url,
                    "http.status_code": status,
                    "error": True,
                },
            )
            error_span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
            error_span.end()

        # Cause of a 5xx, resolved once for both the Errors view and the request
        # log so the two never disagree on what failed.
        cause = ""
        if is_server_error:
            cause = get_message_from_exception_body(_response_body(response), status)

        response = capture_exception_errors(response)
        start = getattr(g, "_mm_start", None)
        duration_ms = int((time.monotonic() - start) * 1000) if start else 0
        _log_http_request(client, request.method, request.path, status,
                          duration_ms, has_error, cause,
                          resolve_client_ip(client.config, request.headers, request.remote_addr))
        return response

    @app.teardown_request
    def _mm_end_span(exc):
        token = getattr(g, "_mm_token", None)
        if token is not None:
            otel_context.detach(token)
            g._mm_token = None
        span = getattr(g, "_mm_span", None)
        if span is not None:
            if exc is not None:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.end()
            g._mm_span = None

    return app
