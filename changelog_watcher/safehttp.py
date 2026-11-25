"""HTTP client for user-configured URLs (sources, Prometheus, webhooks).

Every request, redirects included, is refused when the host resolves to loopback,
link-local (cloud metadata at 169.254.169.254: node credentials), unspecified or
multicast addresses. Private ranges stay allowed: Prometheus is usually internal.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx


class BlockedDestination(httpx.RequestError):
    pass


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = getattr(ip, "ipv4_mapped", None) or ip  # ::ffff:169.254.169.254
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast


async def _guard(request: httpx.Request) -> None:
    host = request.url.host
    try:  # a literal IP comes back as-is, without a DNS query
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return  # unresolvable: the request itself fails with a clear error
    # ponytail: checked at resolve time, a DNS rebind between this lookup and the connect is
    # not caught; pin the resolved IP in a custom transport if that threat matters.
    ips = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
    if any(_blocked(ip) for ip in ips):
        raise BlockedDestination(f"destino não permitido: {host}", request=request)


def client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(event_hooks={"request": [_guard]}, **kwargs)
