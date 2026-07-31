"""Caller address resolution for the HTTP middlewares."""
import ipaddress
from typing import Any, Mapping, Optional

from .config import ClientIpMode, Config

# Read in order, before falling back to the socket address. Behind a proxy
# (Caddy, nginx, Cloudflare) the socket only ever shows the proxy.
_FORWARDED_HEADERS = ("CF-Connecting-IP", "True-Client-IP", "X-Forwarded-For", "X-Real-IP")


def resolve_client_ip(cfg: Config, headers: Mapping[str, Any], remote_addr: Optional[str] = None) -> str:
    """Returns the caller address to record for this request, already reduced to
    what cfg.client_ip allows. Empty means nothing is recorded — an unparseable
    address is dropped rather than stored raw."""
    if cfg.client_ip == ClientIpMode.OFF:
        return ""

    raw = _forwarded_client_ip(headers) or (remote_addr or "").strip()
    raw = raw.strip("[]")

    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return ""

    # A socket may hand an IPv4 address over in its IPv6-mapped form; without
    # unmapping it the anonymised value would keep the whole address.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if cfg.client_ip == ClientIpMode.FULL:
        return str(ip)
    return _anonymize(ip)


def _forwarded_client_ip(headers: Mapping[str, Any]) -> str:
    """Returns the first address a proxy put in the request, or ''."""
    for name in _FORWARDED_HEADERS:
        raw = (headers.get(name) or "").strip()
        if not raw:
            continue
        # X-Forwarded-For is a chain: the client is the leftmost entry.
        first = raw.split(",")[0].strip()
        if first:
            return first
    return ""


def _anonymize(ip: Any) -> str:
    """Zeroes the host part: the last octet of an IPv4 address, the last 80 bits of
    an IPv6 one (the /48 a subscriber is assigned)."""
    prefix = 24 if isinstance(ip, ipaddress.IPv4Address) else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False).network_address)
