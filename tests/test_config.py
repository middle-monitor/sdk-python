import os
import pytest

from middlemonitor.config import (
    Config,
    LogLevel,
    LogsSamplingConfig,
    SamplingConfig,
    TracesSamplingConfig,
    _matches_route,
    config_from_env,
    default_sampling_config,
    new_config,
    should_sample_log,
    should_sample_trace,
)


class TestLogLevel:
    def test_values(self):
        assert LogLevel.DEBUG == "DEBUG"
        assert LogLevel.INFO == "INFO"
        assert LogLevel.WARN == "WARN"
        assert LogLevel.ERROR == "ERROR"
        assert LogLevel.FATAL == "FATAL"
        assert LogLevel.PANIC == "PANIC"


class TestTracesSamplingConfig:
    def test_defaults(self):
        cfg = TracesSamplingConfig()
        assert cfg.percentage == -1.0
        assert cfg.always_sample_errors is True
        assert "/health" in cfg.never_sample_routes
        assert cfg.always_sample_routes == []

    def test_explicit_values(self):
        cfg = TracesSamplingConfig(
            percentage=0.5,
            always_sample_errors=False,
            always_sample_routes=["/admin"],
            never_sample_routes=["/metrics"],
        )
        assert cfg.percentage == 0.5
        assert cfg.always_sample_errors is False
        assert cfg.always_sample_routes == ["/admin"]
        assert cfg.never_sample_routes == ["/metrics"]


class TestLogsSamplingConfig:
    def test_defaults(self):
        cfg = LogsSamplingConfig()
        assert LogLevel.ERROR in cfg.levels
        assert cfg.min_http_status == 500
        assert cfg.capture_on_trace_error is True
        assert "/health" in cfg.never_capture_routes

    def test_explicit_values(self):
        cfg = LogsSamplingConfig(
            levels=[LogLevel.WARN],
            min_http_status=400,
            capture_on_trace_error=False,
            always_capture_routes=["/api"],
            never_capture_routes=["/ping"],
        )
        assert cfg.levels == [LogLevel.WARN]
        assert cfg.min_http_status == 400


class TestSamplingConfig:
    def test_defaults(self):
        cfg = SamplingConfig()
        assert isinstance(cfg.traces, TracesSamplingConfig)
        assert isinstance(cfg.logs, LogsSamplingConfig)

    def test_explicit(self):
        traces = TracesSamplingConfig(percentage=0.1)
        logs = LogsSamplingConfig(min_http_status=400)
        cfg = SamplingConfig(traces=traces, logs=logs)
        assert cfg.traces.percentage == 0.1
        assert cfg.logs.min_http_status == 400


class TestDefaultSamplingConfig:
    def test_default_percentage(self):
        cfg = default_sampling_config()
        assert cfg.traces.percentage == 0.10

    def test_default_routes(self):
        cfg = default_sampling_config()
        assert "/health" in cfg.traces.never_sample_routes
        assert "/metrics" in cfg.logs.never_capture_routes


class TestConfig:
    def test_defaults(self):
        cfg = Config(endpoint="http://localhost:8080", service="svc")
        assert cfg.endpoint == "http://localhost:8080"
        assert cfg.insecure is True  # starts with http://
        assert cfg.service == "svc"
        assert cfg.token is None
        assert cfg.protocol == "http"
        assert cfg.timeout == 5.0

    def test_trailing_slash_stripped(self):
        cfg = Config(endpoint="http://host:8080/", service="s")
        assert cfg.endpoint == "http://host:8080"

    def test_empty_endpoint_defaults(self):
        cfg = Config(endpoint="", service="s")
        assert cfg.endpoint == "https://api.middlemonitor.io"

    def test_https_not_insecure(self):
        cfg = Config(endpoint="https://host:4318", service="s")
        assert cfg.insecure is False

    def test_explicit_insecure(self):
        cfg = Config(endpoint="https://host", service="s", insecure=True)
        assert cfg.insecure is True

    def test_explicit_sampling(self):
        sampling = SamplingConfig(traces=TracesSamplingConfig(percentage=0.5))
        cfg = Config(endpoint="http://h", service="s", sampling=sampling)
        assert cfg.sampling.traces.percentage == 0.5


class TestNewConfig:
    def test_basic(self):
        cfg = new_config("http://host:8080", "svc", "tok")
        assert cfg.endpoint == "http://host:8080"
        assert cfg.service == "svc"
        assert cfg.token == "tok"

    def test_empty_endpoint_defaults(self):
        cfg = new_config("", "svc")
        assert cfg.endpoint == "https://api.middlemonitor.io"

    def test_no_token(self):
        cfg = new_config("http://h", "s")
        assert cfg.token is None


class TestConfigFromEnv:
    def test_defaults_no_env(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_API_URL", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("MIDDLE_MONITOR_SERVICE", raising=False)
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        monkeypatch.delenv("MIDDLE_MONITOR_TOKEN", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
        monkeypatch.delenv("MIDDLE_MONITOR_PROTOCOL", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
        monkeypatch.delenv("MIDDLE_MONITOR_TRACES_SAMPLING", raising=False)
        monkeypatch.delenv("MIDDLE_MONITOR_LOGS_LEVELS", raising=False)
        monkeypatch.delenv("MIDDLE_MONITOR_LOGS_MIN_HTTP_STATUS", raising=False)
        cfg = config_from_env()
        assert cfg.endpoint == "https://api.middlemonitor.io"
        assert cfg.service == "unknown"

    def test_custom_endpoint(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_API_URL", "http://custom:9090")
        cfg = config_from_env()
        assert cfg.endpoint == "http://custom:9090"

    def test_otel_endpoint_fallback(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_API_URL", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318")
        cfg = config_from_env()
        assert cfg.endpoint == "http://otel:4318"

    def test_service_from_otel(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_SERVICE", raising=False)
        monkeypatch.setenv("OTEL_SERVICE_NAME", "my-svc")
        cfg = config_from_env()
        assert cfg.service == "my-svc"

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_TOKEN", "mytoken")
        cfg = config_from_env()
        assert cfg.token == "mytoken"

    def test_token_from_otel_headers(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_TOKEN", raising=False)
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer secret123,x-other=val"
        )
        cfg = config_from_env()
        assert cfg.token == "secret123"

    def test_token_from_otel_headers_no_match(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_TOKEN", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-other=val")
        cfg = config_from_env()
        assert cfg.token is None

    def test_protocol_from_otel(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_PROTOCOL", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
        cfg = config_from_env()
        assert cfg.protocol == "grpc"

    def test_traces_sampling_override(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_TRACES_SAMPLING", "0.5")
        cfg = config_from_env()
        assert cfg.sampling.traces.percentage == 0.5

    def test_traces_sampling_invalid(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_TRACES_SAMPLING", "2.0")
        with pytest.raises(ValueError):
            config_from_env()

    def test_logs_levels_override(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_LOGS_LEVELS", "DEBUG,WARN")
        cfg = config_from_env()
        assert LogLevel.DEBUG in cfg.sampling.logs.levels
        assert LogLevel.WARN in cfg.sampling.logs.levels

    def test_logs_levels_invalid(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_LOGS_LEVELS", "INVALID")
        with pytest.raises(ValueError):
            config_from_env()

    def test_logs_min_http_status(self, monkeypatch):
        monkeypatch.setenv("MIDDLE_MONITOR_LOGS_MIN_HTTP_STATUS", "400")
        cfg = config_from_env()
        assert cfg.sampling.logs.min_http_status == 400

    def test_otel_headers_no_equals(self, monkeypatch):
        monkeypatch.delenv("MIDDLE_MONITOR_TOKEN", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "noequals")
        cfg = config_from_env()
        assert cfg.token is None


class TestMatchesRoute:
    def test_exact_match(self):
        assert _matches_route("/api/v1", "/api/v1") is True

    def test_no_match(self):
        assert _matches_route("/api/v2", "/api/v1") is False

    def test_wildcard_match(self):
        assert _matches_route("/api/users/123", "/api/users/*") is True

    def test_wildcard_no_match(self):
        assert _matches_route("/api/orders/1", "/api/users/*") is False

    def test_no_wildcard_no_match(self):
        assert _matches_route("/other", "/api") is False


class TestShouldSampleTrace:
    def _cfg(self, **kwargs):
        cfg = new_config("http://h", "svc")
        for k, v in kwargs.items():
            setattr(cfg.sampling.traces, k, v)
        return cfg

    def test_never_sample_route_no_error(self):
        cfg = self._cfg(never_sample_routes=["/health"])
        assert should_sample_trace(cfg, "/health", False) is False

    def test_never_sample_route_with_error_always_sample(self):
        cfg = self._cfg(never_sample_routes=["/health"], always_sample_errors=True)
        assert should_sample_trace(cfg, "/health", True) is True

    def test_never_sample_route_with_error_no_always(self):
        cfg = self._cfg(never_sample_routes=["/health"], always_sample_errors=False)
        assert should_sample_trace(cfg, "/health", True) is False

    def test_always_sample_route(self):
        cfg = self._cfg(always_sample_routes=["/admin"], percentage=0.0)
        assert should_sample_trace(cfg, "/admin", False) is True

    def test_always_sample_errors(self):
        cfg = self._cfg(percentage=0.0, always_sample_errors=True)
        assert should_sample_trace(cfg, "/api", True) is True

    def test_percentage_100(self):
        cfg = self._cfg(percentage=1.0)
        assert should_sample_trace(cfg, "/api", False) is True

    def test_percentage_0(self):
        cfg = self._cfg(percentage=0.0, always_sample_errors=False)
        assert should_sample_trace(cfg, "/api", False) is False

    def test_auto_percentage_resolves_to_default(self, monkeypatch):
        import random
        # -1 (auto) resolves to the fixed default rate (10%), independent of any environment
        cfg = self._cfg(percentage=-1, always_sample_errors=False)
        monkeypatch.setattr(random, "random", lambda: 0.05)
        assert should_sample_trace(cfg, "/api", False) is True
        monkeypatch.setattr(random, "random", lambda: 0.5)
        assert should_sample_trace(cfg, "/api", False) is False

    def test_random_sampling(self, monkeypatch):
        import random
        monkeypatch.setattr(random, "random", lambda: 0.1)
        cfg = self._cfg(percentage=0.5, always_sample_errors=False)
        assert should_sample_trace(cfg, "/api", False) is True

    def test_random_no_sample(self, monkeypatch):
        import random
        monkeypatch.setattr(random, "random", lambda: 0.9)
        cfg = self._cfg(percentage=0.5, always_sample_errors=False)
        assert should_sample_trace(cfg, "/api", False) is False


class TestShouldSampleLog:
    def _cfg(self, **kwargs):
        cfg = new_config("http://h", "svc")
        for k, v in kwargs.items():
            setattr(cfg.sampling.logs, k, v)
        return cfg

    def test_never_capture_route_below_status(self):
        cfg = self._cfg(never_capture_routes=["/health"], min_http_status=500)
        assert should_sample_log(cfg, "/health", LogLevel.INFO, 200, False) is False

    def test_never_capture_route_above_status(self):
        cfg = self._cfg(never_capture_routes=["/health"], min_http_status=500)
        assert should_sample_log(cfg, "/health", LogLevel.INFO, 500, False) is True

    def test_never_capture_route_min_status_zero(self):
        cfg = self._cfg(never_capture_routes=["/health"], min_http_status=0)
        assert should_sample_log(cfg, "/health", LogLevel.ERROR, 500, False) is False

    def test_always_capture_route(self):
        cfg = self._cfg(always_capture_routes=["/api"], min_http_status=500)
        assert should_sample_log(cfg, "/api", LogLevel.DEBUG, 200, False) is True

    def test_min_http_status_hit(self):
        cfg = self._cfg(min_http_status=500)
        assert should_sample_log(cfg, "/api", LogLevel.DEBUG, 500, False) is True

    def test_level_match(self):
        cfg = self._cfg(levels=[LogLevel.ERROR], min_http_status=500)
        assert should_sample_log(cfg, "/api", LogLevel.ERROR, 200, False) is True

    def test_capture_on_trace_error(self):
        cfg = self._cfg(capture_on_trace_error=True, min_http_status=500)
        assert should_sample_log(cfg, "/api", LogLevel.DEBUG, 200, True) is True

    def test_no_match_returns_false(self):
        cfg = self._cfg(
            levels=[LogLevel.ERROR],
            min_http_status=500,
            capture_on_trace_error=False,
        )
        assert should_sample_log(cfg, "/api", LogLevel.INFO, 200, False) is False

    def test_min_status_zero_skips_status_check(self):
        cfg = self._cfg(min_http_status=0, levels=[LogLevel.ERROR], capture_on_trace_error=False)
        assert should_sample_log(cfg, "/api", LogLevel.DEBUG, 500, False) is False
