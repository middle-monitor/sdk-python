import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from middlemonitor.client import (
    OTelClient,
    get_global_client,
    get_message_from_exception_body,
    init_global_client,
)
from middlemonitor.config import Config, LogLevel, new_config
import middlemonitor.client as client_module


def reset_global_client():
    client_module._global_client = None


@pytest.fixture(autouse=True)
def clean_global(request):
    reset_global_client()
    yield
    reset_global_client()


def make_config(endpoint: str = "http://localhost:19999") -> Config:
    return new_config(endpoint, "svc", "tok")


# ── get_message_from_exception_body ─────────────────────────────────────────


class TestGetMessageFromExceptionBody:
    def test_empty_body(self):
        assert get_message_from_exception_body(b"") == "HTTP 500"
        assert get_message_from_exception_body("") == "HTTP 500"
        assert get_message_from_exception_body(None) == "HTTP 500"

    def test_json_with_error_field(self):
        body = json.dumps({"error": "database down"}).encode()
        assert get_message_from_exception_body(body) == "database down"

    def test_json_string_body(self):
        body = json.dumps({"error": "bad request"})
        assert get_message_from_exception_body(body) == "bad request"

    def test_json_without_error_field(self):
        body = json.dumps({"message": "ok"}).encode()
        assert get_message_from_exception_body(body, 503) == "HTTP 503"

    def test_invalid_json(self):
        assert get_message_from_exception_body(b"not json", 502) == "HTTP 502"

    def test_unicode_decode_error(self):
        # bytes that cannot be decoded as UTF-8
        bad = bytes([0xFF, 0xFE])
        result = get_message_from_exception_body(bad, 500)
        assert result == "HTTP 500"

    def test_json_error_field_none(self):
        body = json.dumps({"error": None}).encode()
        assert get_message_from_exception_body(body) == "HTTP 500"

    def test_custom_status_code(self):
        assert get_message_from_exception_body(b"", 503) == "HTTP 503"


# ── OTelClient ──────────────────────────────────────────────────────────────


class TestOTelClientInit:
    def test_init_creates_providers(self):
        cfg = make_config()
        client = OTelClient(cfg)
        assert not client.initialized
        client.init()
        assert client.initialized
        assert client.tracer_provider is not None
        assert client.logger_provider is not None
        assert client.tracer is not None
        assert client.logger is not None

    def test_init_idempotent(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        first_tp = client.tracer_provider
        client.init()  # second call should be no-op
        assert client.tracer_provider is first_tp

    def test_endpoint_strips_v1_traces(self):
        cfg = make_config("http://localhost:4318/v1/traces")
        client = OTelClient(cfg)
        client.init()
        assert client.initialized

    def test_endpoint_strips_v1_logs(self):
        cfg = make_config("http://localhost:4318/v1/logs")
        client = OTelClient(cfg)
        client.init()
        assert client.initialized

    def test_no_token_no_auth_header(self):
        cfg = make_config()
        cfg.token = None
        client = OTelClient(cfg)
        client.init()
        assert client.initialized

    def test_with_token_sets_auth(self):
        cfg = make_config()
        cfg.token = "mytoken"
        client = OTelClient(cfg)
        client.init()
        assert client.initialized


class TestOTelClientLog:
    def test_log_auto_inits(self):
        cfg = make_config()
        client = OTelClient(cfg)
        assert not client.initialized
        client.log(LogLevel.INFO, "hello")
        assert client.initialized

    def test_log_with_attrs(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        client.log(LogLevel.WARN, "warning", {"key": "value"})

    def test_log_sync(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        client.log_sync(LogLevel.ERROR, "sync error")

    def test_flush_logs_no_provider(self):
        cfg = make_config()
        client = OTelClient(cfg)
        assert client.logger_provider is None
        client.flush_logs()  # should not panic

    def test_flush_logs_with_provider(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        client.flush_logs(timeout=1.0)

    def test_build_log_record_all_levels(self):
        cfg = make_config()
        client = OTelClient(cfg)
        for level in LogLevel:
            record = client._build_log_record(level, "msg")
            assert record is not None

    def test_build_log_record_with_attrs(self):
        cfg = make_config()
        client = OTelClient(cfg)
        record = client._build_log_record(LogLevel.DEBUG, "msg", {"k": "v"})
        assert record is not None

    def test_build_log_record_no_attrs(self):
        cfg = make_config()
        client = OTelClient(cfg)
        record = client._build_log_record(LogLevel.INFO, "msg", None)
        assert record is not None


class TestOTelClientReportError:
    def test_report_error_auto_inits(self):
        cfg = make_config()
        client = OTelClient(cfg)
        assert not client.initialized
        err = Exception("auto init test")
        client.report_error(err)
        assert client.initialized

    def test_report_error_no_traceback(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        err = Exception("test error")
        client.report_error(err)

    def test_report_error_with_traceback(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        try:
            raise ValueError("traceback test")
        except ValueError as e:
            client.report_error(e)

    def test_report_error_none(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        client.report_error(None)  # should return early

    def test_report_error_sampling_false(self):
        cfg = make_config()
        cfg.sampling.traces.percentage = 0.0
        cfg.sampling.traces.always_sample_errors = False
        client = OTelClient(cfg)
        client.init()
        err = Exception("not sampled")
        client.report_error(err)  # returns early after sampling check

    def test_report_error_with_http_context(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        try:
            raise RuntimeError("http error")
        except RuntimeError as e:
            client.report_error(e, http_context={
                "method": "GET",
                "url": "/api/test",
                "status_code": 500,
            })

    def test_report_error_http_context_with_log(self):
        cfg = make_config()
        cfg.sampling.traces.percentage = 1.0
        cfg.sampling.logs.min_http_status = 500
        client = OTelClient(cfg)
        client.init()
        try:
            raise RuntimeError("with log")
        except RuntimeError as e:
            client.report_error(e, http_context={"status_code": 500})

    def test_report_error_with_explicit_file_line(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        err = Exception("explicit")
        client.report_error(err, file="myfile.py", line=42)

    def test_report_error_partial_file_or_line(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        try:
            raise Exception("partial")
        except Exception as e:
            # Only provide file, not line
            client.report_error(e, file="some.py")

    def test_report_error_http_context_without_log(self):
        cfg = make_config()
        cfg.sampling.logs.min_http_status = 0
        cfg.sampling.logs.capture_on_trace_error = False
        cfg.sampling.logs.levels = []
        client = OTelClient(cfg)
        client.init()
        try:
            raise Exception("no log")
        except Exception as e:
            client.report_error(e, http_context={"method": "POST", "url": "/path", "status_code": 200})


class TestOTelClientSubmitApplicationError:
    def test_basic_submit(self):
        cfg = make_config()
        client = OTelClient(cfg)
        # Should not raise even with unreachable server
        client.submit_application_error("TypeError", "msg")

    def test_submit_with_all_options(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.submit_application_error(
            name="DBError",
            message="connection failed",
            file="handler.py",
            line=42,
            status_code=503,
            method="POST",
            url="/api/data",
            request_body=b"request body",
        )

    def test_submit_with_large_request_body(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.submit_application_error(
            "err", "msg", request_body=b"x" * 3000
        )

    def test_submit_with_string_request_body(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.submit_application_error("err", "msg", request_body="string body")

    def test_submit_endpoint_strips_v1_traces(self):
        cfg = new_config("http://localhost:19999/v1/traces", "s")
        client = OTelClient(cfg)
        client.submit_application_error("err", "msg")

    def test_submit_endpoint_strips_v1_logs(self):
        cfg = new_config("http://localhost:19999/v1/logs", "s")
        client = OTelClient(cfg)
        client.submit_application_error("err", "msg")

    def test_submit_with_token(self):
        cfg = make_config()
        cfg.token = "tok"
        client = OTelClient(cfg)
        client.submit_application_error("err", "msg")

    def test_submit_with_real_server(self):
        """Covers client.py:255 — the successful urlopen path."""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request)
        t.start()

        cfg = new_config(f"http://127.0.0.1:{port}", "svc", "tok")
        client = OTelClient(cfg)
        client.submit_application_error("err", "msg")
        t.join(timeout=5)


class TestOTelClientShutdown:
    def test_shutdown_uninitiated(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.shutdown()  # should not raise
        assert not client.initialized

    def test_shutdown_after_init(self):
        cfg = make_config()
        client = OTelClient(cfg)
        client.init()
        client.shutdown()
        assert not client.initialized


# ── init_global_client / get_global_client ──────────────────────────────────


class TestInitGlobalClient:
    def test_init_with_config(self):
        cfg = make_config()
        client = init_global_client(cfg)
        assert client is not None
        assert client.initialized

    def test_init_idempotent(self):
        cfg = make_config()
        c1 = init_global_client(cfg)
        c2 = init_global_client(cfg)
        assert c1 is c2

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://localhost:19999")
        client = init_global_client(None)
        assert client is not None

    def test_get_global_client_returns_none_when_not_set(self):
        assert get_global_client() is None

    def test_get_global_client_returns_client_after_init(self):
        cfg = make_config()
        init_global_client(cfg)
        assert get_global_client() is not None
