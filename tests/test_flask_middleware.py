import threading
from unittest.mock import MagicMock, patch

import pytest

import middlemonitor as mm
import middlemonitor.client as client_module
from middlemonitor.config import new_config
from middlemonitor.flask_middleware import capture_exception_errors


def reset_global():
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


class TestCaptureExceptionErrors:
    def _make_response(self, status_code: int, body: bytes = b""):
        resp = MagicMock()
        resp.status_code = status_code
        resp.get_data.return_value = body
        return resp

    def test_2xx_returns_unchanged(self):
        response = self._make_response(200)
        result = capture_exception_errors(response)
        assert result is response
        response.get_data.assert_not_called()

    def test_4xx_returns_unchanged(self):
        response = self._make_response(404)
        result = capture_exception_errors(response)
        assert result is response

    def test_5xx_no_flask_module(self):
        response = self._make_response(500)
        mm.init(make_cfg())
        with patch.dict("sys.modules", {"flask": None}):
            result = capture_exception_errors(response)
        assert result is response

    def test_5xx_no_global_client(self):
        response = self._make_response(500)
        # Patch the reference used inside flask_middleware (imported from middlemonitor)
        with patch("middlemonitor.flask_middleware.get_global_client", return_value=None):
            from flask import Flask
            app = Flask(__name__)
            with app.test_request_context("/"):
                result = capture_exception_errors(response)
        assert result is response

    def test_5xx_with_client_json_body(self):
        import json
        mm.init(make_cfg())
        body = json.dumps({"error": "database error"}).encode()
        response = self._make_response(500, body)

        from flask import Flask
        app = Flask(__name__)
        with app.test_request_context("/api/test", method="POST", data=b"req body"):
            result = capture_exception_errors(response)
        assert result is response

    def test_5xx_with_client_plain_body(self):
        mm.init(make_cfg())
        response = self._make_response(503, b"service unavailable")

        from flask import Flask
        app = Flask(__name__)
        with app.test_request_context("/health"):
            result = capture_exception_errors(response)
        assert result is response

    def test_5xx_large_request_body(self):
        mm.init(make_cfg())
        response = self._make_response(500, b"error")

        from flask import Flask
        app = Flask(__name__)
        large_body = b"x" * 3000
        with app.test_request_context("/upload", method="POST", data=large_body):
            result = capture_exception_errors(response)
        assert result is response

    def test_5xx_small_request_body(self):
        mm.init(make_cfg())
        response = self._make_response(500, b"error")

        from flask import Flask
        app = Flask(__name__)
        with app.test_request_context("/api", method="POST", data=b"small"):
            result = capture_exception_errors(response)
        assert result is response

    def test_5xx_no_request_body(self):
        mm.init(make_cfg())
        response = self._make_response(500, b"")

        from flask import Flask
        app = Flask(__name__)
        with app.test_request_context("/api"):
            result = capture_exception_errors(response)
        assert result is response

    def test_5xx_submit_raises_exception(self):
        mm.init(make_cfg())
        response = self._make_response(500, b"error")

        from flask import Flask
        app = Flask(__name__)
        with app.test_request_context("/"):
            with patch.object(mm._global_client, "submit_application_error", side_effect=Exception("submit fail")):
                result = capture_exception_errors(response)
        assert result is response
