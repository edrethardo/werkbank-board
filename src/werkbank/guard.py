"""Request guards for the board (WB-35 security review, findings F1/F2).

The board has no login by design — it is meant for one person on one machine.
That is only safe if requests really come from that person's board page:

* F1 (CSRF): any web page the user visits could POST to 127.0.0.1 with a
  CORS-safelisted content type (text/plain, form encodings) and never trigger
  a preflight. Since a ticket is an executable prompt, that would be remote
  code execution. Writes therefore require an `application/json` content type
  AND, when the browser sends one, a same-origin `Origin` header.
* F2 (DNS rebinding): an attacker domain resolving to 127.0.0.1 would be
  same-origin and defeat the Origin check, so the `Host` header must name
  localhost as well.

Non-browser clients (curl, the skills) send no Origin — that is allowed; they
cannot be driven by a hostile web page.
"""


def _local_hosts(port) -> set:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def check_read(headers, port):
    """(ok, reason) for GET requests — Host must be local (F2)."""
    host = (headers.get("Host") or "").strip()
    if host and host not in _local_hosts(port):
        return False, ("Zugriff nur über http://127.0.0.1 möglich "
                       f"(angefragter Host: {host}).")
    return True, None


def check_write(headers, port):
    """(ok, reason) for POST/DELETE — Host local, JSON body, same-origin."""
    ok, reason = check_read(headers, port)
    if not ok:
        return ok, reason
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return False, ("Änderungen brauchen den Inhaltstyp application/json "
                       "(Schutz vor fremden Webseiten).")
    origin = (headers.get("Origin") or "").strip()
    if origin:
        allowed = {f"http://{h}" for h in _local_hosts(port)}
        if origin not in allowed:
            return False, f"Fremde Herkunft abgelehnt: {origin}"
    return True, None
