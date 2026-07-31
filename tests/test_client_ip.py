import pytest

from middlemonitor.client_ip import resolve_client_ip
from middlemonitor.config import ClientIpMode, new_config


def cfg_with(mode):
    cfg = new_config("http://localhost:19999", "svc", "tok")
    cfg.client_ip = mode
    return cfg


@pytest.mark.parametrize("headers,want", [
    ({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.4"}, "203.0.113.7"),
    ({"X-Forwarded-For": "203.0.113.7, 198.51.100.4, 127.0.0.1"}, "203.0.113.7"),
    ({"X-Real-IP": "203.0.113.7"}, "203.0.113.7"),
    ({}, "192.0.2.1"),
])
def test_prefers_forwarded_over_socket(headers, want):
    """Behind a proxy the socket only shows the proxy, so reading it alone labels
    every request with the reverse proxy and the scanner signal is lost."""
    assert resolve_client_ip(cfg_with(ClientIpMode.FULL), headers, "192.0.2.1") == want


def test_anonymized_by_default():
    """The default must not store an address that identifies a person: only the
    network it came from, which still makes two hits the same recognisable scan."""
    cfg = new_config("http://localhost:19999", "svc", "tok")
    assert cfg.client_ip == ClientIpMode.ANONYMIZED
    assert resolve_client_ip(cfg, {"X-Forwarded-For": "203.0.113.42"}) == "203.0.113.0"
    assert resolve_client_ip(cfg, {"X-Forwarded-For": "2001:db8:1234:5678::1"}) == "2001:db8:1234::"


def test_unmaps_ipv4_mapped_ipv6():
    """A dual-stack socket hands IPv4 over in its mapped form; without unmapping
    it the anonymised value would keep the whole address in the last 32 bits."""
    assert resolve_client_ip(cfg_with(ClientIpMode.ANONYMIZED), {}, "::ffff:203.0.113.42") == "203.0.113.0"


def test_off_records_nothing():
    """A deployment that turns collection off must not leak the address through
    the socket fallback."""
    assert resolve_client_ip(cfg_with(ClientIpMode.OFF), {"X-Forwarded-For": "203.0.113.42"}, "192.0.2.1") == ""


def test_unparseable_is_dropped():
    """A forwarded header is caller-controlled: anything that is not an address is
    dropped, so the attribute never carries injected text."""
    assert resolve_client_ip(cfg_with(ClientIpMode.FULL), {"X-Forwarded-For": "not-an-ip"}) == ""
