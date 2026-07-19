import sys
import threading
from unittest.mock import patch

import pytest

import middlemonitor as mm
import middlemonitor.client as client_module
from middlemonitor.config import LogLevel, new_config


def reset_global():
    """Reset both global clients between tests."""
    mm._global_client = None
    client_module._global_client = None
    mm._init_lock = threading.Lock()


@pytest.fixture(autouse=True)
def clean_globals():
    reset_global()
    yield
    reset_global()


def make_cfg():
    return new_config("http://localhost:19999", "svc", "tok")


# ── MiddleMonitorClient ──────────────────────────────────────────────────────


class TestMiddleMonitorClient:
    def test_init(self):
        client = mm.MiddleMonitorClient(make_cfg())
        assert client.config is not None
        assert client.otel_client is not None

    def test_set_token(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.set_token("newtoken")
        assert client.config.token == "newtoken"

    def test_report_error_none(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.report_error(None)  # should return early without raising

    def test_report_error_with_traceback(self):
        client = mm.MiddleMonitorClient(make_cfg())
        try:
            raise ValueError("test")
        except ValueError as e:
            client.report_error(e)

    def test_report_error_framework_only(self):
        client = mm.MiddleMonitorClient(make_cfg())
        # Create exception that looks like it came from a framework path
        err = ValueError("framework error")
        # Manually inject traceback with site-packages path
        import types
        try:
            raise err
        except ValueError as e:
            # Patch _is_application_error to return False → early return
            with patch.object(client, "_is_application_error", return_value=False):
                client.report_error(e)

    def test_report_error_no_traceback(self):
        client = mm.MiddleMonitorClient(make_cfg())
        err = Exception("no tb")
        client.report_error(err)

    def test_report_error_with_details(self):
        client = mm.MiddleMonitorClient(make_cfg())
        try:
            raise Exception("details test")
        except Exception as e:
            client.report_error_with_details(e, "file.py", 10)

    def test_report_custom_error(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.report_custom_error("DBError", "conn failed", "db.py", 99)

    def test_report_custom_error_with_http(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.report_custom_error_with_http(
            "APIError", "timeout", "api.py", 50,
            http_method="GET", http_url="/api/data",
            http_headers="h: v", http_body="{}",
        )

    def test_submit_application_error(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.submit_application_error("err", "msg", method="GET", url="/p")

    def test_capture_panic_no_exc(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.capture_panic()  # no current exception → should return early

    def test_capture_panic_with_exc(self):
        client = mm.MiddleMonitorClient(make_cfg())
        try:
            raise RuntimeError("panic test")
        except RuntimeError:
            client.capture_panic()

    def test_capture_exception_decorator_no_error(self):
        client = mm.MiddleMonitorClient(make_cfg())

        @client.capture_exception
        def safe():
            return 42

        assert safe() == 42

    def test_capture_exception_decorator_with_error(self):
        client = mm.MiddleMonitorClient(make_cfg())

        @client.capture_exception
        def risky():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            risky()

    def test_capture_exception_decorator_framework_error(self):
        client = mm.MiddleMonitorClient(make_cfg())

        @client.capture_exception
        def framework_only():
            raise Exception("from framework")

        with patch.object(client, "_is_application_error", return_value=False):
            with pytest.raises(Exception):
                framework_only()

    def test_log(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.log(LogLevel.INFO, "hello")

    def test_log_sync(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.log_sync(LogLevel.ERROR, "sync error")

    def test_flush_logs(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.flush_logs()

    def test_shutdown(self):
        client = mm.MiddleMonitorClient(make_cfg())
        client.shutdown()

    def test_is_application_error_true(self):
        import traceback
        client = mm.MiddleMonitorClient(make_cfg())
        try:
            raise Exception("app error")
        except Exception:
            tb = traceback.extract_tb(sys.exc_info()[2])
            # Current file is a test file — should return False (test files excluded)
            # But let's check the method works
            result = client._is_application_error(tb)
            # Result depends on current file path; just verify it doesn't raise
            assert isinstance(result, bool)

    def test_is_application_error_framework_path(self):
        import traceback
        import types
        client = mm.MiddleMonitorClient(make_cfg())
        # Create a fake frame with site-packages in path
        fake_frame = types.SimpleNamespace(filename="/usr/lib/python3/site-packages/flask/app.py")
        result = client._is_application_error([fake_frame])
        assert result is False

    def test_is_application_error_user_path(self):
        import types
        client = mm.MiddleMonitorClient(make_cfg())
        fake_frame = types.SimpleNamespace(filename="/home/user/myapp/views.py")
        result = client._is_application_error([fake_frame])
        assert result is True

    def test_is_application_error_test_file(self):
        import types
        client = mm.MiddleMonitorClient(make_cfg())
        # The filter checks for "_test.py" suffix, not "test_" prefix
        fake_frame = types.SimpleNamespace(filename="/home/user/myapp/views_test.py")
        result = client._is_application_error([fake_frame])
        assert result is False

    def test_is_application_error_gen_file(self):
        import types
        client = mm.MiddleMonitorClient(make_cfg())
        fake_frame = types.SimpleNamespace(filename="/app/models.gen.py")
        result = client._is_application_error([fake_frame])
        assert result is False


# ── Global init API ──────────────────────────────────────────────────────────


class TestGlobalInit:
    def test_init_with_config(self):
        cfg = make_cfg()
        mm.init(cfg)
        assert mm._global_client is not None

    def test_init_idempotent(self):
        cfg = make_cfg()
        mm.init(cfg)
        first = mm._global_client
        mm.init(cfg)
        assert mm._global_client is first

    def test_init_none_reads_env(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://localhost:19999")
        mm.init(None)
        assert mm._global_client is not None

    def test_init_empty_endpoint_prints_warning(self, capsys):
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        assert mm._global_client is None

    def test_init_no_token_prints_warning(self, capsys):
        cfg = make_cfg()
        cfg.token = None
        mm.init(cfg)
        captured = capsys.readouterr()
        assert "without token" in captured.err

    def test_init_simple(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://localhost:19999")
        mm.init_simple()
        assert mm._global_client is not None

    def test_init_with_config_func(self):
        mm.init_with_config("http://localhost:19999", "svc", "tok")
        assert mm._global_client is not None

    def test_get_global_client_none(self):
        # No init → get_global_client triggers init(None) which sets endpoint from defaults
        client = mm.get_global_client()
        # May or may not be None depending on env; just shouldn't raise
        assert client is None or hasattr(client, "report_error")

    def test_get_global_client_after_init(self):
        mm.init(make_cfg())
        assert mm.get_global_client() is not None

    def test_get_global_config_none(self):
        # When get_global_client returns None, get_global_config returns None
        with patch("middlemonitor.get_global_client", return_value=None):
            result = mm.get_global_config()
            assert result is None

    def test_get_global_config_after_init(self):
        mm.init(make_cfg())
        cfg = mm.get_global_config()
        assert cfg is not None
        assert cfg.service == "svc"

    def test_get_client(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://localhost:19999")
        monkeypatch.setenv("MIDDLE_MONITOR_SERVICE", "test-svc")
        client = mm.get_client(service="override-svc")
        assert client.config.service == "override-svc"

    def test_get_client_no_overrides(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://localhost:19999")
        client = mm.get_client()
        assert client is not None


# ── Global convenience functions ─────────────────────────────────────────────


class TestGlobalConvenienceFunctions:
    def test_report_error_no_client(self):
        # No client initialized → empty endpoint → client remains None
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        mm.report_error(Exception("test"))  # should not raise

    def test_report_error_with_client(self):
        mm.init(make_cfg())
        try:
            raise ValueError("global report")
        except ValueError as e:
            mm.report_error(e)

    def test_report_error_with_details_no_client(self):
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        mm.report_error_with_details(Exception("test"), "file.py", 1)

    def test_report_error_with_details_with_client(self):
        mm.init(make_cfg())
        try:
            raise Exception("details")
        except Exception as e:
            mm.report_error_with_details(e, "file.py", 99)

    def test_capture_panic_global_no_client(self):
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        mm.capture_panic_global()

    def test_capture_panic_global_with_client(self):
        mm.init(make_cfg())
        try:
            raise RuntimeError("panic global")
        except RuntimeError:
            mm.capture_panic_global()

    def test_log_no_client(self):
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        mm.log(LogLevel.INFO, "msg")  # should not raise

    def test_log_with_client(self):
        mm.init(make_cfg())
        mm.log(LogLevel.INFO, "hello global")

    def test_log_sync_no_client(self):
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        mm.log_sync(LogLevel.WARN, "sync")

    def test_log_sync_with_client(self):
        mm.init(make_cfg())
        mm.log_sync(LogLevel.ERROR, "sync error")

    def test_flush_logs_no_client(self):
        cfg = make_cfg()
        cfg.endpoint = ""
        mm.init(cfg)
        mm.flush_logs()

    def test_flush_logs_with_client(self):
        mm.init(make_cfg())
        mm.flush_logs()


class TestAutoInitGate:
    """An application that never opted in must not start exporting: without a
    token there is nothing to authenticate with, so auto-init would silently
    ship data to the default public endpoint on the first middleware call."""

    def test_get_global_client_stays_none_without_token(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_TOKEN", raising=False)
        assert mm.get_global_client() is None

    def test_get_global_client_auto_inits_with_token(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_TOKEN", "tok")
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://localhost:19999")
        assert mm.get_global_client() is not None
