"""Demonstration endpoints' plumbing: TrustGate and SecureProxy egress.

Neither of these is something an agent framework gives you. They exist so a
customer evaluating AgentBox can trigger the two controls that previously had
no path at all from inside a running application -- package policy and egress
policy -- and see the platform's own verdict, in the platform's own words.

Both are deliberately thin: they run the real thing and report what came back.
Neither interprets policy, and neither has a "pretend" mode.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from base64 import b64encode
from pathlib import Path
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# TrustGate
# ---------------------------------------------------------------------------

#: `pip3` is deliberately absent: TrustGate shims `pip`, not `pip3`, so a
#: `pip3 install` would reach the real binary ungoverned.
_MANAGERS = ("pip", "npm")

#: A package name, an optional npm scope, and an optional version specifier.
#: The install runs WITHOUT a shell (no `sh -c`), so this is defence in depth
#: rather than the only thing between a name and a command -- but a name that
#: cannot be a package should be refused before TrustGate is asked about it.
#:
#: Both optional halves are real, not hypothetical: a first version of this
#: accepted neither, which refused `@scope/name` and `docopt==0.6.2` -- an
#: ordinary scoped npm package and an ordinary pinned pip install. A scope must
#: begin with an alphanumeric so `@../evil/x` cannot pass as one.
_PACKAGE_RE = re.compile(
    r"^(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,63}/)?"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
    r"(?:[@=<>!~^][A-Za-z0-9._*+!=<>~^-]{0,63})?$"
)

#: npm needs a project root it can write. /app is an image layer under a
#: read-only rootfs, and npm walks UP to the nearest package.json, so without
#: one here it targets /app and fails with a misleading ENOENT about a
#: platform-specific package. /app/scratch is declared in filesystem_writable.
_NPM_ROOT = Path(os.environ.get("DOCBRIEF_INSTALL_ROOT", "/app/scratch"))

#: Substrings meaning TrustGate FAILED rather than DECIDED. They matter
#: because they are invisible in the verdict: TrustGate wraps every exception
#: as "Blocked by KOBIL TrustGate: {exc}", so an infrastructure failure reads
#: word for word like a policy block. Check this before trusting a verdict.
_INFRA_FAILURES = (
    "execv",
    "staticx",
    "daemon unavailable",
    "daemon returned no final response",
    "resolution failed",
    "error loading shared library",
    "unsupported resolver tool",
    "connection refused",
)


def _verdict(output: str) -> str:
    """Which decision TrustGate rendered.

    Keys off the exact prefixes policy.py emits rather than loose keywords:
    "approval" appears both in a genuine HOLD and in "Blocked by ...: approval
    request was rejected", so keyword matching cannot tell the two apart.
    """
    lowered = output.lower()
    if "blocked by kobil trustgate" in lowered:
        return "block"
    if "held by kobil trustgate" in lowered:
        return "hold"
    if "allowed by kobil trustgate" in lowered:
        return "allow"
    return "none"


def _ensure_npm_root() -> None:
    _NPM_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = _NPM_ROOT / "package.json"
    if not manifest.is_file():
        manifest.write_text('{"name":"agent-scratch","private":true}', encoding="utf-8")


def install_package(package: str, manager: str) -> dict:
    """Run a real, TrustGate-governed install and report the verdict.

    Invoked as a bare argv (`["pip", "install", pkg]`), NOT through a shell and
    NOT as `python -m pip`. Both alternatives miss the point: `python -m pip`
    reaches pip's module entry point directly and never touches the PATH shim
    that IS the enforcement, and a login shell (`sh -lc`) re-initialises PATH
    from the image profile and drops the shim directory entirely, leaving a
    login shell unable to see npm at all. This process
    inherits the container's ENV PATH, which has the shim directory on it, so
    a bare name resolves to the wrapper exactly as an agent's own call would.
    """
    package = (package or "").strip()
    manager = (manager or "").strip().lower()
    if manager not in _MANAGERS:
        raise ValueError(f"manager must be one of {', '.join(_MANAGERS)}")
    if not _PACKAGE_RE.match(package):
        raise ValueError("package is not a valid package name")

    cwd = None
    if manager == "npm":
        _ensure_npm_root()
        cwd = str(_NPM_ROOT)

    try:
        completed = subprocess.run(  # noqa: S603 - bare argv, validated name, no shell
            [manager, "install", package],
            capture_output=True,
            text=True,
            # 300s, not 180: an ALLOWed package proceeds to a real download,
            # and a smaller ceiling gets hit by ordinary ones. A timeout
            # reported as a failure would misattribute a slow network to
            # TrustGate.
            timeout=300,
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{manager} is not installed in this image, so TrustGate's shim has "
            f"nothing to wrap: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{manager} install of {package!r} did not finish within 300s"
        ) from exc

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    lowered = output.lower()
    infra = [needle for needle in _INFRA_FAILURES if needle in lowered]
    return {
        "package": package,
        "manager": manager,
        "verdict": _verdict(output),
        "exit_code": completed.returncode,
        # Bounded: an ALLOWed install prints a full resolution log, and this
        # is rendered in an admin's browser.
        "output": output[-4000:],
        # A verdict reached by a broken TrustGate fails CLOSED, which is the
        # safe direction but also the deceptive one -- it looks exactly like a
        # policy block. Name it rather than let it read as enforcement.
        "infra_failure": infra or None,
    }


# ---------------------------------------------------------------------------
# SecureProxy egress
# ---------------------------------------------------------------------------

_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


def probe_egress(host: str, port: int = 443) -> dict:
    """Ask SecureProxy's forward proxy to open a tunnel, and report its answer.

    A RAW CONNECT, reading the proxy's own status line, rather than an ordinary
    HTTPS request. Two reasons, both learned the hard way:

    * A denied CONNECT reaches urllib as a generic OSError, not an HTTPError --
      the proxy's status is lost, so a probe built on urlopen reports the same
      opaque failure whether the destination was refused by policy or the proxy
      itself was broken. Reading the status line keeps "403 Forbidden" distinct
      from "cannot reach the proxy at all", which is the entire question here.
    * The credential has to be spelled `Proxy-Authorization` exactly. urllib's
      ProxyHandler adds it via `add_header`, which capitalises it to
      `Proxy-authorization`, and only the exact spelling is forwarded into the
      tunnel -- so every request comes back 407. That silently disabled
      TrustGate's own intel fetches until it was found.
    """
    host = (host or "").strip()
    if not _HOST_RE.match(host):
        raise ValueError("host must be a bare hostname, without scheme or path")
    port = int(port)
    if not (1 <= port <= 65535):
        raise ValueError("port must be between 1 and 65535")

    raw = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
    if not raw:
        return {
            "host": host,
            "port": port,
            "proxied": False,
            "status": 0,
            "reason": "no HTTPS_PROXY is set, so this app has no egress route at all",
        }

    proxy = urlsplit(raw)
    if not proxy.hostname or not proxy.port:
        return {
            "host": host,
            "port": port,
            "proxied": False,
            "status": 0,
            "reason": f"unparseable proxy url: {raw!r}",
        }

    lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
    if proxy.username is not None:
        token = b64encode(
            f"{proxy.username or ''}:{proxy.password or ''}".encode()
        ).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode()

    try:
        sock = socket.create_connection((proxy.hostname, proxy.port), timeout=25)
    except OSError as exc:
        return {
            "host": host,
            "port": port,
            "proxied": False,
            "status": 0,
            "reason": f"cannot reach the proxy at {proxy.hostname}:{proxy.port} -- {exc}",
        }

    try:
        sock.sendall(request)
        sock.settimeout(25)
        buffer = b""
        while b"\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
    except OSError as exc:
        return {
            "host": host,
            "port": port,
            "proxied": True,
            "status": 0,
            "reason": f"proxy connection failed mid-request -- {exc}",
        }
    finally:
        sock.close()

    if not buffer:
        return {
            "host": host,
            "port": port,
            "proxied": True,
            "status": 0,
            "reason": "the proxy closed the connection without a status line",
        }

    status_line = buffer.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    bits = status_line.split(None, 2)
    if len(bits) < 2 or not bits[1].isdigit():
        return {
            "host": host,
            "port": port,
            "proxied": True,
            "status": 0,
            "reason": f"unparseable proxy status line: {status_line!r}",
        }

    status = int(bits[1])
    reason = bits[2] if len(bits) > 2 else ""
    return {
        "host": host,
        "port": port,
        "proxied": True,
        "status": status,
        "reason": reason,
        # 200 means the tunnel opened: this destination is allowed by the
        # app's current egress policy. 403 is the policy refusing it. 407
        # means the credential did not survive, which is a wiring fault, not
        # a policy decision.
        "allowed": status == 200,
    }
