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
    api_url="http://localhost:8080",
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

### Environment variable setup

```python
from middlemonitor import get_client

# Reads MIDDLE_MONITOR_API_URL, MIDDLE_MONITOR_SERVICE
client = get_client()
```

### Environment variables

```bash
export MIDDLE_MONITOR_API_URL=http://monitor.example.com
export MIDDLE_MONITOR_SERVICE=my-service
```
