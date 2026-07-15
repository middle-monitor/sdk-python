"""
Optional Flask middleware to capture server error (5xx) responses and submit the error message
to the Middle-Monitor Errors API (so "Contexte / Corrélation" shows the real cause,
e.g. external API failure, instead of a generic "HTTP 500").

Usage:
    from middlemonitor import get_global_client, get_message_from_exception_body
    from middlemonitor.flask_middleware import capture_exception_errors

    app.after_request(capture_exception_errors)
"""
from typing import Any

from . import get_global_client, get_message_from_exception_body


def capture_exception_errors(response: Any) -> Any:
    """
    Flask after_request hook: on 5xx, capture response body, extract "error" from JSON,
    and submit to Middle-Monitor Errors API so the UI shows the detailed message.
    """
    if response.status_code < 500:
        return response
    try:
        from flask import request
    except ImportError:
        return response
    client = get_global_client()
    if not client:
        return response
    body = response.get_data()
    message = get_message_from_exception_body(body, response.status_code)
    request_body = None
    if request.get_data():
        data = request.get_data()
        if len(data) <= 2000:
            request_body = data
        else:
            request_body = data[:2000]
    try:
        client.submit_application_error(
            name="http",
            message=message,
            file="handler",
            line=0,
            status_code=response.status_code,
            method=request.method,
            url=request.url,
            request_body=request_body,
        )
    except Exception:
        pass
    return response
