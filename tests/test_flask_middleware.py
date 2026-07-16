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


class TestInstrumentFlask:
    def _make_app(self):
        from flask import Flask, jsonify

        app = Flask(__name__)

        @app.route("/ok")
        def ok():
            return "ok", 200

        @app.route("/fail")
        def fail():
            return jsonify({"error": "db down"}), 500

        @app.route("/health")
        def health():
            return "ok", 200

        @app.route("/health-fail")
        def health_fail():
            return jsonify({"error": "db"}), 500

        @app.route("/boom")
        def boom():
            raise ValueError("boom")

        return app

    def _init_with_fake_tracer(self, cfg):
        mm.init(cfg)
        fake_tracer = MagicMock()
        fake_span = MagicMock()
        fake_tracer.start_span.return_value = fake_span
        mm.get_global_client().otel_client.tracer = fake_tracer
        return fake_tracer, fake_span

    def test_2xx_creates_span_with_ok_status(self):
        cfg = make_cfg()
        cfg.sampling.traces.percentage = 1.0
        fake_tracer, fake_span = self._init_with_fake_tracer(cfg)

        app = self._make_app()
        from middlemonitor.flask_middleware import instrument_flask
        instrument_flask(app)

        resp = app.test_client().get("/ok")
        assert resp.status_code == 200
        fake_tracer.start_span.assert_called_once()
        assert fake_tracer.start_span.call_args[0][0] == "GET /ok"
        fake_span.set_attribute.assert_any_call("http.status_code", 200)
        fake_span.set_attribute.assert_any_call("error", False)
        fake_span.end.assert_called_once()

    def test_5xx_marks_error_and_submits(self):
        cfg = make_cfg()
        cfg.sampling.traces.percentage = 1.0
        fake_tracer, fake_span = self._init_with_fake_tracer(cfg)

        app = self._make_app()
        from middlemonitor.flask_middleware import instrument_flask
        instrument_flask(app)

        with patch.object(mm._global_client, "submit_application_error") as submit:
            resp = app.test_client().get("/fail")
        assert resp.status_code == 500
        fake_span.set_attribute.assert_any_call("http.status_code", 500)
        fake_span.set_attribute.assert_any_call("error", True)
        fake_span.end.assert_called_once()
        # The 5xx submission of capture_exception_errors must still run
        submit.assert_called_once()
        assert submit.call_args.kwargs["message"] == "db down"

    def test_never_sampled_route_2xx_no_span(self):
        cfg = make_cfg()
        cfg.sampling.traces.never_sample_routes = ["/health"]
        fake_tracer, _ = self._init_with_fake_tracer(cfg)

        app = self._make_app()
        from middlemonitor.flask_middleware import instrument_flask
        instrument_flask(app)

        resp = app.test_client().get("/health")
        assert resp.status_code == 200
        fake_tracer.start_span.assert_not_called()

    def test_never_sampled_route_5xx_creates_error_span(self):
        cfg = make_cfg()
        cfg.sampling.traces.never_sample_routes = ["/health-fail"]
        cfg.sampling.traces.always_sample_errors = True
        fake_tracer, fake_span = self._init_with_fake_tracer(cfg)

        app = self._make_app()
        from middlemonitor.flask_middleware import instrument_flask
        instrument_flask(app)

        with patch.object(mm._global_client, "submit_application_error"):
            resp = app.test_client().get("/health-fail")
        assert resp.status_code == 500
        # No span at request start, one error span created at finish
        fake_tracer.start_span.assert_called_once()
        attrs = fake_tracer.start_span.call_args.kwargs["attributes"]
        assert attrs["http.status_code"] == 500
        assert attrs["error"] is True
        fake_span.end.assert_called_once()

    def test_unhandled_exception_recorded_on_span(self):
        cfg = make_cfg()
        cfg.sampling.traces.percentage = 1.0
        fake_tracer, fake_span = self._init_with_fake_tracer(cfg)

        app = self._make_app()
        from middlemonitor.flask_middleware import instrument_flask
        instrument_flask(app)

        with patch.object(mm._global_client, "submit_application_error"):
            resp = app.test_client().get("/boom")
        assert resp.status_code == 500
        fake_span.record_exception.assert_called_once()
        fake_span.end.assert_called_once()

    def test_no_client_passthrough(self):
        app = self._make_app()
        from middlemonitor.flask_middleware import instrument_flask
        instrument_flask(app)

        with patch("middlemonitor.flask_middleware.get_global_client", return_value=None):
            resp = app.test_client().get("/ok")
        assert resp.status_code == 200
