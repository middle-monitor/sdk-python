import os
from enum import Enum
from typing import List, Optional


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"
    PANIC = "PANIC"


class TracesSamplingConfig:
    def __init__(
        self,
        percentage: float = -1.0,
        always_sample_errors: bool = True,
        always_sample_routes: Optional[List[str]] = None,
        never_sample_routes: Optional[List[str]] = None,
    ) -> None:
        # -1 = auto (uses the default sampling rate); 0.0–1.0 for an explicit rate
        self.percentage = percentage
        self.always_sample_errors = always_sample_errors
        self.always_sample_routes: List[str] = always_sample_routes or []
        self.never_sample_routes: List[str] = never_sample_routes or [
            "/health", "/metrics", "/ready", "/healthz", "/readyz",
        ]


class LogsSamplingConfig:
    def __init__(
        self,
        levels: Optional[List[LogLevel]] = None,
        min_http_status: int = 500,
        capture_on_trace_error: bool = True,
        always_capture_routes: Optional[List[str]] = None,
        never_capture_routes: Optional[List[str]] = None,
    ) -> None:
        self.levels: List[LogLevel] = levels or [LogLevel.ERROR, LogLevel.FATAL, LogLevel.PANIC]
        self.min_http_status = min_http_status
        self.capture_on_trace_error = capture_on_trace_error
        self.always_capture_routes: List[str] = always_capture_routes or []
        self.never_capture_routes: List[str] = never_capture_routes or [
            "/health", "/metrics", "/ready", "/healthz", "/readyz",
        ]


class SamplingConfig:
    def __init__(
        self,
        traces: Optional[TracesSamplingConfig] = None,
        logs: Optional[LogsSamplingConfig] = None,
    ) -> None:
        self.traces = traces or TracesSamplingConfig()
        self.logs = logs or LogsSamplingConfig()


def default_sampling_config() -> SamplingConfig:
    percentage = 0.10
    return SamplingConfig(
        traces=TracesSamplingConfig(percentage=percentage),
        logs=LogsSamplingConfig(),
    )


class Config:
    def __init__(
        self,
        endpoint: str,
        service: str,
        token: Optional[str] = None,
        insecure: Optional[bool] = None,
        protocol: str = "http",
        sampling: Optional[SamplingConfig] = None,
        timeout: float = 5.0,
    ) -> None:
        self.endpoint = (endpoint or "https://api.middlemonitor.io").rstrip("/")
        self.insecure = insecure if insecure is not None else self.endpoint.startswith("http://")
        self.service = service
        self.token = token
        self.protocol = protocol
        self.sampling = sampling or default_sampling_config()
        self.timeout = timeout


def new_config(endpoint: str, service: str, token: Optional[str] = None) -> Config:
    """Create a Config with smart defaults (mirrors Go's NewConfig)."""
    if not endpoint:
        endpoint = "https://api.middlemonitor.io"
    return Config(
        endpoint=endpoint,
        service=service,
        token=token,
        sampling=default_sampling_config(),
    )


def config_from_env() -> Config:
    """Build a Config from environment variables (mirrors Go's ConfigFromEnv)."""
    endpoint = (
        os.getenv("MIDDLE_MONITOR_API_URL")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "https://api.middlemonitor.io"
    )

    service = (
        os.getenv("MIDDLE_MONITOR_SERVICE")
        or os.getenv("OTEL_SERVICE_NAME")
        or "unknown"
    )

    token = os.getenv("MIDDLE_MONITOR_TOKEN")
    if not token:
        headers_str = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
        if headers_str and "=" in headers_str:
            for part in headers_str.split(","):
                kv = part.strip().split("=", 1)
                if len(kv) == 2 and kv[0].lower() == "authorization":
                    token = kv[1].removeprefix("Bearer ")
                    break

    protocol = (
        os.getenv("MIDDLE_MONITOR_PROTOCOL")
        or os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http"
    )

    cfg = new_config(endpoint, service, token)
    cfg.protocol = protocol

    traces_pct = os.getenv("MIDDLE_MONITOR_TRACES_SAMPLING")
    if traces_pct:
        pct = float(traces_pct)
        if not -1 <= pct <= 1:
            raise ValueError(f"MIDDLE_MONITOR_TRACES_SAMPLING must be between -1 and 1, got {pct}")
        cfg.sampling.traces.percentage = pct

    logs_levels_raw = os.getenv("MIDDLE_MONITOR_LOGS_LEVELS")
    if logs_levels_raw:
        levels = []
        for lvl in logs_levels_raw.split(","):
            lvl = lvl.strip().upper()
            try:
                levels.append(LogLevel(lvl))
            except ValueError:
                raise ValueError(f"Invalid log level in MIDDLE_MONITOR_LOGS_LEVELS: {lvl!r}")
        if levels:
            cfg.sampling.logs.levels = levels

    min_status = os.getenv("MIDDLE_MONITOR_LOGS_MIN_HTTP_STATUS")
    if min_status:
        cfg.sampling.logs.min_http_status = int(min_status)

    return cfg


def should_sample_trace(cfg: "Config", route: str, has_error: bool) -> bool:
    """Mirrors Go's Config.ShouldSampleTrace."""
    traces = cfg.sampling.traces

    for pattern in traces.never_sample_routes:
        if _matches_route(route, pattern):
            if traces.always_sample_errors and has_error:
                return True
            return False

    for pattern in traces.always_sample_routes:
        if _matches_route(route, pattern):
            return True

    if traces.always_sample_errors and has_error:
        return True

    pct = traces.percentage
    if pct < 0:
        pct = default_sampling_config().traces.percentage

    if pct >= 1.0:
        return True
    if pct <= 0:
        return False
    import random
    return random.random() < pct


def should_sample_log(cfg: "Config", route: str, level: LogLevel, http_status: int, trace_has_error: bool) -> bool:
    """Mirrors Go's Config.ShouldSampleLog."""
    logs = cfg.sampling.logs

    for pattern in logs.never_capture_routes:
        if _matches_route(route, pattern):
            if logs.min_http_status > 0 and http_status >= logs.min_http_status:
                return True
            return False

    for pattern in logs.always_capture_routes:
        if _matches_route(route, pattern):
            return True

    if logs.min_http_status > 0 and http_status >= logs.min_http_status:
        return True

    if level in logs.levels:
        return True

    if logs.capture_on_trace_error and trace_has_error:
        return True

    return False


def _matches_route(route: str, pattern: str) -> bool:
    if route == pattern:
        return True
    if "*" in pattern:
        import re
        regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
        return bool(re.match(regex, route))
    return False
