"""Request guards for the board (WB-35 security review, findings F1/F2;
extended for LAN mode in WB-44).

The board has no login by default — it is meant for one person on one machine.
That is only safe if requests really come from that person's board page:

* F1 (CSRF): any web page the user visits could POST to the board with a
  CORS-safelisted content type (text/plain, form encodings) and never trigger
  a preflight. Since a ticket is an executable prompt, that would be remote
  code execution. Writes therefore require an `application/json` content type
  AND, when the browser sends one, an `Origin` matching the board itself.
* F2 (DNS rebinding): an attacker domain resolving to the board's address would
  be same-origin and defeat the Origin check, so the `Host` header must name
  the board too — localhost only, or (LAN mode) a private address.

Non-browser clients (curl, the skills) send no Origin — that is allowed; they
cannot be driven by a hostile web page. LAN mode additionally requires a
password (see auth.py): the host rules below only decide WHO MAY ASK, never
who is authenticated.
"""

import ipaddress


def _split_host(host: str):
    host = (host or "").strip()
    if host.startswith("["):                      # [::1]:8765
        name, _, port = host.partition("]")
        return name[1:], port.lstrip(":")
    name, _, port = host.rpartition(":")
    return (name or host), port


def _is_private(name: str) -> bool:
    """A LAN address the board may legitimately be reached at. Names are
    refused on purpose: DNS rebinding always arrives with a hostname."""
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _allowed_hosts(port) -> set:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def check_read(headers, port, lan: bool = False):
    """(ok, reason) for GET requests — the Host must name the board (F2)."""
    host = (headers.get("Host") or "").strip()
    if not host:
        return True, None
    if host in _allowed_hosts(port):
        return True, None
    if lan:
        name, host_port = _split_host(host)
        if _is_private(name) and (not host_port or host_port == str(port)):
            return True, None
        return False, ("Zugriff nur über die lokale Netzwerk-Adresse möglich "
                       f"(angefragter Host: {host}).")
    return False, ("Zugriff nur über http://127.0.0.1 möglich "
                   f"(angefragter Host: {host}).")


def check_write(headers, port, lan: bool = False):
    """(ok, reason) for POST/DELETE — Host allowed, JSON body, same origin."""
    ok, reason = check_read(headers, port, lan)
    if not ok:
        return ok, reason
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return False, ("Änderungen brauchen den Inhaltstyp application/json "
                       "(Schutz vor fremden Webseiten).")
    origin = (headers.get("Origin") or "").strip()
    if origin:
        host = (headers.get("Host") or "").strip()
        allowed = {f"http://{h}" for h in _allowed_hosts(port)}
        if host:
            allowed |= {f"http://{host}", f"https://{host}"}
        if origin not in allowed:
            return False, f"Fremde Herkunft abgelehnt: {origin}"
    return True, None
