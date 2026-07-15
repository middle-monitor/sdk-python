import json
import sys
import traceback
import urllib.request
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Union

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode

from opentelemetry._logs import set_logger_provider
from opentelemetry._logs._internal import LogRecord
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

from .config import Config, LogLevel, should_sample_trace, should_sample_log


def get_message_from_exception_body(body: Union[bytes, str], status_code: int = 500) -> str:
    """Extract error message from a server error response body (e.g. JSON with "error" field).
    Used so the Errors view shows the real cause instead of "HTTP 500"."""
    if not body:
        return f"HTTP {status_code}"
    try:
        raw = body.decode("utf-8") if isinstance(body, bytes) else body
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return f"HTTP {status_code}"


class OTelClient:
    def __init__(self, config: Config):
        self.config = config
        self.tracer_provider: Optional[TracerProvider] = None
        self.logger_provider: Optional[LoggerProvider] = None
        self.tracer = None
        self.logger = None
        self.initialized = False

    def init(self) -> None:
        if self.initialized:
            return

        resource = Resource.create({
            SERVICE_NAME: self.config.service,
        })

        endpoint = self.config.endpoint.rstrip("/")
        if endpoint.endswith("/v1/traces"):
            endpoint = endpoint[: -len("/v1/traces")]
        elif endpoint.endswith("/v1/logs"):
            endpoint = endpoint[: -len("/v1/logs")]

        headers = {}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        trace_exporter = OTLPSpanExporter(
            endpoint=f"{endpoint}/v1/traces",
            headers=headers,
            timeout=int(self.config.timeout),
        )

        # AlwaysOn: sampling decisions are made in should_sample_trace() before span creation.
        self.tracer_provider = TracerProvider(
            resource=resource,
            sampler=ALWAYS_ON,
        )
        self.tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(self.tracer_provider)
        self.tracer = trace.get_tracer("middle-monitor-sdk")

        log_exporter = OTLPLogExporter(
            endpoint=f"{endpoint}/v1/logs",
            headers=headers,
            timeout=int(self.config.timeout),
        )
        self.logger_provider = LoggerProvider(resource=resource)
        self.logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(self.logger_provider)
        self.logger = self.logger_provider.get_logger("middle-monitor-sdk")

        self.initialized = True

    def _build_log_record(
        self,
        level: LogLevel,
        message: str,
        attrs: Optional[Dict[str, str]] = None,
    ) -> LogRecord:
        from opentelemetry._logs.severity import SeverityNumber

        severity_map = {
            LogLevel.DEBUG: SeverityNumber.DEBUG,
            LogLevel.INFO: SeverityNumber.INFO,
            LogLevel.WARN: SeverityNumber.WARN,
            LogLevel.ERROR: SeverityNumber.ERROR,
            LogLevel.FATAL: SeverityNumber.FATAL,
            LogLevel.PANIC: SeverityNumber.FATAL,
        }

        record_attrs: Dict[str, Any] = {
            "service.name": self.config.service,
        }
        if attrs:
            record_attrs.update(attrs)

        return LogRecord(
            severity_number=severity_map.get(level, SeverityNumber.INFO),
            severity_text=level.value,
            body=message,
            attributes=record_attrs,
        )

    def log(self, level: LogLevel, message: str, attrs: Optional[Dict[str, str]] = None) -> None:
        """Buffer a log record (flushed by BatchLogRecordProcessor)."""
        if not self.initialized:
            self.init()
        self.logger.emit(self._build_log_record(level, message, attrs))

    def log_sync(self, level: LogLevel, message: str, attrs: Optional[Dict[str, str]] = None) -> None:
        """Emit a log record and immediately force-flush."""
        self.log(level, message, attrs)
        self.flush_logs()

    def flush_logs(self, timeout: float = 5.0) -> None:
        """Force-flush all buffered log records."""
        if self.logger_provider:
            self.logger_provider.force_flush(timeout_millis=int(timeout * 1000))

    def report_error(
        self,
        error: Exception,
        file: Optional[str] = None,
        line: Optional[int] = None,
        http_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.initialized:
            self.init()
        if not error:
            return

        error_file = file
        error_line = line
        if not error_file or not error_line:
            tb = traceback.extract_tb(error.__traceback__)
            if tb:
                error_file = error_file or tb[-1].filename
                error_line = error_line or tb[-1].lineno

        route = (http_context.get("url") or "") if http_context else ""
        http_status = (http_context.get("status_code") or 0) if http_context else 0
        has_error = True  # we only call report_error for actual errors

        if not should_sample_trace(self.config, route, has_error):
            return

        span = self.tracer.start_span(
            "error.report",
            attributes={
                "error.message": str(error),
                "error.type": type(error).__name__,
                "error.file": error_file or "unknown",
                "error.line": error_line or 0,
                "service.name": self.config.service,
            },
        )

        if http_context:
            if http_context.get("method"):
                span.set_attribute("http.method", http_context["method"])
            if http_context.get("url"):
                span.set_attribute("http.url", http_context["url"])
            if http_context.get("status_code"):
                span.set_attribute("http.status_code", http_context["status_code"])

        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.record_exception(error)

        if should_sample_log(self.config, route, LogLevel.ERROR, http_status, has_error):
            log_attrs: Dict[str, str] = {
                "error.name": type(error).__name__,
                "error.message": str(error),
                "error.file": error_file or "unknown",
                "error.line": str(error_line or 0),
            }
            if http_context:
                if http_context.get("method"):
                    log_attrs["http.method"] = str(http_context["method"])
                if http_context.get("url"):
                    log_attrs["http.url"] = str(http_context["url"])
                if http_context.get("status_code"):
                    log_attrs["http.status_code"] = str(http_context["status_code"])
            self.logger.emit(self._build_log_record(LogLevel.ERROR, str(error), log_attrs))

        span.end()

    def submit_application_error(
        self,
        name: str,
        message: str,
        file: str = "handler",
        line: int = 0,
        status_code: int = 500,
        method: Optional[str] = None,
        url: Optional[str] = None,
        request_body: Optional[Union[bytes, str]] = None,
    ) -> None:
        """Submit an error to the Middle-Monitor Errors API (POST /api/v1/errors).
        Use get_message_from_exception_body() to extract the message from a server error response."""
        base = (self.config.endpoint or "").rstrip("/")
        if base.endswith("/v1/traces") or base.endswith("/v1/logs"):
            base = base.rsplit("/", 2)[0]
        api_url = f"{base}/api/v1/errors"
        payload: Dict[str, Any] = {
            "name": name,
            "message": message,
            "file": file,
            "line": line,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "service": self.config.service,
        }
        if method:
            payload["http_method"] = method
        if url:
            payload["http_url"] = url
        if request_body is not None:
            body_str = request_body.decode("utf-8", errors="replace") if isinstance(request_body, bytes) else str(request_body)
            if len(body_str) > 2000:
                body_str = body_str[:2000] + "..."
            payload["http_body"] = body_str
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            api_url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.config.token:
            req.add_header("Authorization", f"Bearer {self.config.token}")
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass

    def shutdown(self) -> None:
        if self.tracer_provider:
            self.tracer_provider.shutdown()
        if self.logger_provider:
            self.logger_provider.shutdown()
        self.initialized = False


_global_client: Optional[OTelClient] = None


def init_global_client(cfg: Optional[Config] = None) -> OTelClient:
    """Initialize and return the global OTelClient. Idempotent."""
    global _global_client
    if _global_client:
        return _global_client
    if cfg is None:
        from .config import config_from_env
        cfg = config_from_env()
    _global_client = OTelClient(cfg)
    _global_client.init()
    return _global_client


def get_global_client() -> Optional[OTelClient]:
    return _global_client
