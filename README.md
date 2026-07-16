# Middle-Monitor Python SDK

Python SDK for capturing and reporting errors to Middle-Monitor.

## Installation

From GitHub:

```bash
pip install git+https://github.com/middle-monitor/sdk-python.git
```

Or from a local path:

```bash
pip install -e .
```

## Usage

### Basic setup

```python
from middlemonitor import MiddleMonitorClient

client = MiddleMonitorClient(
    api_url="https://api.middlemonitor.io",
    service="my-service"
)

try:
    raise ValueError("Something went wrong")
except Exception as e:
    client.report_error(e)
```

### Custom error

```python
client.report_custom_error(
    name="DatabaseError",
    message="Failed to connect to database",
    file="/path/to/db.py",
    line=123
)
```

### Exception decorator

```python
@client.capture_exception
def risky_function():
    raise ValueError("This will be automatically reported")
```

### Flask integration

One line to enable automatic capture: one trace per request, error status on 4xx/5xx, and 5xx responses reported to the Errors view.

```python
from middlemonitor import init_simple
from middlemonitor.flask_middleware import instrument_flask

init_simple()
instrument_flask(app)
```

To only report 5xx errors without tracing, use `app.after_request(capture_exception_errors)` instead (do not combine both).

### Environment variable setup

```python
from middlemonitor import get_client

# Reads MIDDLE_MONITOR_API_URL, MIDDLE_MONITOR_SERVICE
client = get_client()
```

### Environment variables

```bash
export MIDDLE_MONITOR_API_URL=https://api.middlemonitor.io
export MIDDLE_MONITOR_SERVICE=my-service
```
