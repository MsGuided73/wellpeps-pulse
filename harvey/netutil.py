"""The real client IP behind a trusted reverse proxy (Coolify's Traefik).

``X-Forwarded-For`` is only believed when the direct peer is a trusted proxy.
Each proxy appends the address it saw, so the chain is read from the right:
trusted hops are skipped and the first untrusted address is the client.
Anything the client itself put on the left is never reached. On any doubt
(untrusted peer, garbage in the chain, every hop trusted) the direct peer is
returned, which is the conservative choice for rate limiting.
"""

import ipaddress
from functools import lru_cache
from typing import Iterable

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_networks(value: str | Iterable[str] | None) -> tuple[IPNetwork, ...]:
    """IPs/CIDRs from a comma-separated string or a list. Raises ValueError on garbage.

    A bare IP is a /32 (or /128). Host bits must be zero (``10.0.0.1/8`` is rejected
    as a likely typo).
    """
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    stripped = [str(item).strip() for item in items]
    if isinstance(value, str) and not any(stripped):
        return ()
    networks = []
    for item in stripped:
        if not item:
            raise ValueError("empty entry in the trusted proxy list")
        try:
            networks.append(ipaddress.ip_network(item, strict=True))
        except ValueError as exc:
            raise ValueError(f"'{item}' is not an IP address or CIDR network") from exc
    return tuple(networks)


@lru_cache(maxsize=32)
def _cached_networks(entries: tuple[str, ...]) -> tuple[IPNetwork, ...]:
    return parse_networks(list(entries))


def networks_from_config(entries: Iterable[str]) -> tuple[IPNetwork, ...]:
    """Parsed (and cached) networks for a validated config list."""
    return _cached_networks(tuple(entries))


def _parse_ip(text: str) -> IPAddress | None:
    text = (text or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _is_trusted(ip: IPAddress, trusted: Iterable[IPNetwork]) -> bool:
    return any(ip.version == net.version and ip in net for net in trusted)


def client_ip(peer: str | None, x_forwarded_for: str | None,
              trusted: Iterable[IPNetwork]) -> str:
    """The address to treat as the client (see the module docstring)."""
    peer = (peer or "").strip()
    if not peer:
        return "unknown"
    trusted = tuple(trusted)
    peer_ip = _parse_ip(peer)
    if peer_ip is None:
        return peer
    if not trusted or not _is_trusted(peer_ip, trusted):
        return str(peer_ip)
    for hop in reversed((x_forwarded_for or "").split(",")):
        ip = _parse_ip(hop)
        if ip is None:
            return str(peer_ip)  # a proxy wrote garbage: believe nothing further left
        if not _is_trusted(ip, trusted):
            return str(ip)
    return str(peer_ip)
