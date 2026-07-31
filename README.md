# Middle-Monitor Python SDK

Python SDK for capturing and reporting errors to Middle-Monitor.

**Documentation:** [middlemonitor.io/docs#sdk](https://middlemonitor.io/docs#sdk)

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

### Request logs

`instrument_flask` also writes one log line per failed request, so the Logs view carries traffic without the application calling `log` itself:

```text
GET /api/orders 500: pq: duplicate key
```

Carried as attributes: `http.method`, `http.route`, `http.status_code`, `duration_ms`. What gets through is decided by the log sampling rules — the defaults keep 2xx traffic out (that volume is what traces are for) and health probes out of the baseline:

| Response | Level | Logged by default |
|---|---|---|
| 2xx / 3xx | INFO | No |
| 4xx | WARN | Yes |
| 5xx | ERROR | Yes |
| `/health`, `/metrics`, `/ready` | — | No |

```python
cfg = new_config(api_url, service, token)
cfg.sampling.logs.levels = [LogLevel.INFO]                  # every request
cfg.sampling.logs.always_capture_routes = ["/api/pay/*"]    # every hit on a route
init(cfg)
```

### Caller address

The request log also carries a `client.ip` attribute, which is what tells a wall of 404s on `/wp-login.php` apart from a real user hitting a broken page. It is read from `CF-Connecting-IP`, `True-Client-IP`, `X-Forwarded-For` or `X-Real-IP` before falling back to `request.remote_addr`, so a service behind Caddy, nginx or Cloudflare records the caller and not the proxy.

An IP address is personal data, so the default keeps the network and drops the host part — `203.0.113.42` is stored as `203.0.113.0`, an IPv6 address is cut to its /48. That is enough to recognise a scan, not enough to single out a person.

```python
cfg = new_config(api_url, service, token)
cfg.client_ip = ClientIpMode.FULL   # whole address: needs its own legal basis
cfg.client_ip = ClientIpMode.OFF    # record nothing
init(cfg)
```

Recording full addresses is a decision about your users' data: give it a legal basis and say so in your privacy policy. An address that does not parse is dropped rather than stored, so a forged header never lands in the attribute.

### Correlating with host metrics

Every export is labelled with `host.name`, which is what lets Middle-Monitor line up a CPU or memory spike on a host with the traffic of the services running on it. Inside a container the OS hostname is the container ID and matches no host, so set the real one:

```yaml
environment:
  MIDDLE_MONITOR_HOSTNAME: host4   # as the host is named in Middle-Monitor
```

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
# Optional: caller address on request logs — anonymized (default), full or off
export MIDDLE_MONITOR_CLIENT_IP=off
```
