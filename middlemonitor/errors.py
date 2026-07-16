class MiddleMonitorError(Exception):
    """Base class for all Middle-Monitor SDK errors."""


class NotInitializedError(MiddleMonitorError):
    """Raised when an operation is attempted before the client is initialized."""

    def __init__(self) -> None:
        super().__init__("client not initialized")


class ConfigError(MiddleMonitorError):
    """Raised when required configuration (endpoint, token) is missing."""

    def __init__(self, detail: str = "endpoint and token required") -> None:
        super().__init__(detail)


class InvalidConfigValueError(MiddleMonitorError, ValueError):
    """Raised when an invalid configuration value is encountered."""

