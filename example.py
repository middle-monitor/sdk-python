#!/usr/bin/env python3
import middlemonitor
from middlemonitor import MiddleMonitorClient, LogLevel
from middlemonitor.config import new_config


def main():
    # Option 1: explicit client with a Config object
    cfg = new_config(
        endpoint="https://api.middlemonitor.io",
        service="example-service",
    )
    client = MiddleMonitorClient(cfg)

    try:
        raise ValueError("Something went wrong")
    except Exception as e:
        client.report_error(e)

    client.report_custom_error(
        name="DatabaseError",
        message="Failed to connect to database",
        file="/path/to/db.py",
        line=123,
    )

    @client.capture_exception
    def risky_function():
        raise RuntimeError("This will be automatically reported")

    try:
        risky_function()
    except RuntimeError:
        pass

    # Structured logs
    client.log(LogLevel.INFO, "Service started", {"version": "1.0.0"})
    client.log_sync(LogLevel.ERROR, "Critical failure", {"reason": "OOM"})

    # Option 2: global client from environment variables
    middlemonitor.init_simple()
    middlemonitor.report_error(ValueError("Global error"))
    middlemonitor.log(LogLevel.WARN, "Low disk space", {"disk": "/dev/sda1"})
    middlemonitor.flush_logs()


if __name__ == "__main__":
    main()
