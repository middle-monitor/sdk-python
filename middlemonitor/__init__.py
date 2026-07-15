import sys
import traceback
import threading
from typing import Optional, Dict

from .config import Config, LogLevel, new_config, config_from_env
from .client import (
    OTelClient,
    init_global_client,
    get_global_client as _get_otel_client,
    get_message_from_exception_body,
)
from .errors import NotInitializedError, ConfigError

_global_client: Optional["MiddleMonitorClient"] = None
_init_lock = threading.Lock()


class MiddleMonitorClient:
    """Middle-Monitor client — thin wrapper around OTelClient."""

    def __init__(self, cfg: Config) -> None:
        self.config = cfg
        self.otel_client = OTelClient(cfg)
        self.otel_client.init()

    def set_token(self, token: str) -> None:
        """Update the stored token. Does not affect the active OTel exporter."""
        self.config.token = token

    def _is_application_error(self, tb: traceback.StackSummary) -> bool:
        framework_paths = [
            "site-packages", "dist-packages", "lib/python",
            "venv/", ".venv/", "env/", ".env/",
            "node_modules", "vendor/", "__pycache__", ".pyc",
        ]
        for frame in tb:
            filename = frame.filename or ""
            if not any(p in filename for p in framework_paths):
                if "_test.py" not in filename and ".gen.py" not in filename:
                    return True
        return False

    def report_error(self, error: Exception, file: Optional[str] = None, line: Optional[int] = None) -> None:
        """Report an exception. File and line are auto-detected from the traceback."""
        if error is None:
            return
        tb = traceback.extract_tb(error.__traceback__)
        if tb and not self._is_application_error(tb):
            return
        self.otel_client.report_error(error, file, line)

    def report_error_with_details(self, error: Exception, file: str, line: int) -> None:
        """Report an exception with explicit file and line."""
        self.otel_client.report_error(error, file, line)

    def report_custom_error(self, name: str, message: str, file: str, line: int) -> None:
        """Report a named error with an explicit file and line."""
        error_class = type(name, (Exception,), {})
        error = error_class(message)
        self.otel_client.report_error(error, file, line)

    def report_custom_error_with_http(
        self,
        name: str,
        message: str,
        file: str,
        line: int,
        http_method: Optional[str] = None,
        http_url: Optional[str] = None,
        http_headers: Optional[str] = None,
        http_body: Optional[str] = None,
    ) -> None:
        """Report a custom error with HTTP context."""
        error_class = type(name, (Exception,), {})
        error = error_class(message)
        self.otel_client.report_error(
            error, file, line,
            {"method": http_method, "url": http_url, "headers": http_headers, "body": http_body},
        )

    def submit_application_error(
        self,
        name: str,
        message: str,
        file: str = "handler",
        line: int = 0,
        status_code: int = 500,
        method: Optional[str] = None,
        url: Optional[str] = None,
        request_body=None,
    ) -> None:
        """Submit an error to /api/v1/errors. Use get_message_from_exception_body() as message
        when the upstream returned a JSON body with an "error" field."""
        self.otel_client.submit_application_error(
            name=name, message=message, file=file, line=line,
            status_code=status_code, method=method, url=url, request_body=request_body,
        )

    def capture_panic(self) -> None:
        """Capture the current exception and report it. Call from an except block or sys.excepthook."""
        _, exc_value, _ = sys.exc_info()
        if exc_value is None:
            return
        self.report_error(exc_value)

    def capture_exception(self, func):
        """Decorator: report and re-raise any exception from the wrapped function."""
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                tb = traceback.extract_tb(e.__traceback__)
                if tb and self._is_application_error(tb):
                    self.report_error(e)
                raise
        return wrapper

    def log(self, level: LogLevel, message: str, attrs: Optional[Dict[str, str]] = None) -> None:
        """Buffered log (BatchLogRecordProcessor handles delivery)."""
        self.otel_client.log(level, message, attrs)

    def log_sync(self, level: LogLevel, message: str, attrs: Optional[Dict[str, str]] = None) -> None:
        """Log and immediately flush."""
        self.otel_client.log_sync(level, message, attrs)

    def flush_logs(self, timeout: float = 5.0) -> None:
        """Force-flush buffered log records."""
        self.otel_client.flush_logs(timeout)

    def shutdown(self) -> None:
        self.flush_logs()
        self.otel_client.shutdown()


# ---------------------------------------------------------------------------
# Global init API (mirrors Go: Init, InitWithConfig, InitSimple, GetGlobalClient, GetGlobalConfig)
# ---------------------------------------------------------------------------

def init(cfg: Optional[Config] = None) -> None:
    """Initialize the global client. If cfg is None, reads config from environment variables."""
    global _global_client
    with _init_lock:
        if _global_client is not None:
            return
        if cfg is None:
            cfg = config_from_env()
        if not cfg.endpoint:
            print("[Middle-Monitor] endpoint not configured, error reporting disabled", file=sys.stderr)
            return
        _global_client = MiddleMonitorClient(cfg=cfg)
        if not cfg.token:
            print(
                f"[Middle-Monitor] initialized without token: service={cfg.service}",
                file=sys.stderr,
            )


def init_simple() -> None:
    """Initialize the global client from environment variables (mirrors Go's InitSimple)."""
    init(None)


def init_with_config(api_url: str, service: str, token: Optional[str] = None) -> None:
    """Initialize the global client with explicit parameters (mirrors Go's InitWithConfig)."""
    init(new_config(api_url, service, token))


def get_global_client() -> Optional[MiddleMonitorClient]:
    """Return the global client, auto-initializing from env vars if needed."""
    if _global_client is None:
        init()
    return _global_client


def get_global_config() -> Optional[Config]:
    """Return the global configuration, auto-initializing from env vars if needed."""
    client = get_global_client()
    return client.config if client else None


def get_client(service: str = "") -> MiddleMonitorClient:
    """Create a new (non-global) client configured from environment variables."""
    cfg = config_from_env()
    if service:
        cfg.service = service
    return MiddleMonitorClient(cfg=cfg)


# ---------------------------------------------------------------------------
# Global convenience functions (mirrors Go package-level funcs)
# ---------------------------------------------------------------------------

def report_error(error: Exception) -> None:
    """Report an exception using the global client."""
    client = get_global_client()
    if client:
        client.report_error(error)


def report_error_with_details(error: Exception, file: str, line: int) -> None:
    """Report an exception with explicit file and line using the global client."""
    client = get_global_client()
    if client:
        client.report_error_with_details(error, file, line)


def capture_panic_global() -> None:
    """Capture the current exception using the global client. Call from an except block or sys.excepthook."""
    client = get_global_client()
    if client:
        client.capture_panic()


def log(level: LogLevel, message: str, attrs: Optional[Dict[str, str]] = None) -> None:
    """Buffered log via the global client."""
    client = get_global_client()
    if client:
        client.log(level, message, attrs)


def log_sync(level: LogLevel, message: str, attrs: Optional[Dict[str, str]] = None) -> None:
    """Log and immediately flush via the global client."""
    client = get_global_client()
    if client:
        client.log_sync(level, message, attrs)


def flush_logs(timeout: float = 5.0) -> None:
    """Force-flush buffered log records via the global client."""
    client = get_global_client()
    if client:
        client.flush_logs(timeout)
