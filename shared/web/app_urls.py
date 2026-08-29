"""URL helpers for optional APP_ROOT_PATH (e.g. /talktomyportfolio)."""

from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from shared.config import APP_ROOT_PATH

_WILDCARD_HOSTS = {"", "0.0.0.0", "::"}
_LOCAL_ONLY_HOSTS = _WILDCARD_HOSTS | {"127.0.0.1", "localhost", "::1"}


def app_path(path: str = "/") -> str:
    """Browser path with APP_ROOT_PATH prefix (no scheme/host)."""
    if not path or path == "/":
        return APP_ROOT_PATH or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{APP_ROOT_PATH}{path}" if APP_ROOT_PATH else path


def _routed_ipv4() -> str:
    """Return the IPv4 address selected by the OS for a LAN-style route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        # UDP connect selects a route without sending traffic. TEST-NET-1 is
        # deliberately non-routable and avoids depending on an internet service.
        probe.connect(("192.0.2.1", 80))
        return str(probe.getsockname()[0])


def _hostname_ipv4() -> str:
    return socket.gethostbyname(socket.gethostname())


def _ipv4_from_ifconfig(output: str) -> str:
    """Prefer an active physical interface over VPN/virtual interfaces."""
    active: list[str] = []
    other: list[str] = []
    for block in re.split(r"(?m)(?=^[^\s])", output):
        match = re.search(r"(?m)^\s*inet\s+(\d{1,3}(?:\.\d{1,3}){3})\b", block)
        if not match or not _usable_lan_ipv4(match.group(1)):
            continue
        target = active if re.search(r"(?m)^\s*status:\s*active\s*$", block) else other
        target.append(match.group(1))
    return (active or other or [""])[0]


def _interface_ipv4() -> str:
    executable = shutil.which("ifconfig")
    if not executable:
        return ""
    result = subprocess.run(
        [executable],
        capture_output=True,
        check=False,
        text=True,
        timeout=1,
    )
    return _ipv4_from_ifconfig(result.stdout)


def _usable_lan_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return bool(address.version == 4 and not address.is_loopback and not address.is_unspecified)


def discover_lan_ipv4(
    probes: Iterable[Callable[[], str]] | None = None,
) -> str:
    """Discover a reachable IPv4 address, with a safe localhost fallback."""
    for probe in probes or (_routed_ipv4, _interface_ipv4, _hostname_ipv4):
        try:
            candidate = probe()
        except (OSError, socket.gaierror):
            continue
        if _usable_lan_ipv4(candidate):
            return candidate.strip()
    return "127.0.0.1"


def portfolio_display_url(
    base_url: str,
    *,
    bind_host: str | None = None,
    display_host: str | None = None,
    lan_ip_resolver: Callable[[], str] = discover_lan_ipv4,
) -> str:
    """Build the exact portfolio URL a browser on another device can open."""
    parsed = urlsplit(base_url)
    configured_host = (parsed.hostname or "").strip()
    lan_bound = (bind_host or "").strip() in _WILDCARD_HOSTS - {""}
    if configured_host in _WILDCARD_HOSTS or (lan_bound and configured_host in _LOCAL_ONLY_HOSTS):
        advertised_host = (display_host or "").strip() or lan_ip_resolver()
    else:
        advertised_host = configured_host

    rendered_host = (
        f"[{advertised_host}]" if ":" in advertised_host and not advertised_host.startswith("[") else advertised_host
    )
    netloc = f"{rendered_host}:{parsed.port}" if parsed.port else rendered_host
    return urlunsplit(
        (
            parsed.scheme or "http",
            netloc,
            app_path("/portfolio"),
            "",
            "",
        )
    )
