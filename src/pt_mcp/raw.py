"""``raw``: arbitrary JS in the PT Script Engine, hardened (PTM-15).

A thrown error comes back as data (with the JS line number when the engine
reports one) instead of a raw C++ exception, and a huge result is cut at
``max_chars``. ``getProcess("IP")`` crashes PT with an opaque ``substr`` error, so
a too-short process name is rejected before the call (PTM-11).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .eval import _api_error, _eval
from .protocol import PTBridgeError

#: ``getProcess("IP")`` crashes inside PT with ``basic_string::substr: __pos
#: ...`` instead of naming the typo; a too-short name is always a mistake (PTM-11).
_PROCESS_HINT = (
    "DnsServer, HttpServer, DhcpClient, HttpClient, DhcpServer, AaaProcess, "
    "IcmpProcess, ArpProcess, TcpProcess, UdpProcess, EthernetProcess, "
    "StpProcess, VtpProcess, IosProcess"
)
_PROCESS_CALL_RE = re.compile(r"""getProcess\(\s*(['"])([^'"]+)\1\s*\)""")
_MIN_PROCESS_NAME = 5


def _validate_process_calls(js: str) -> None:
    """Turn PT's opaque ``substr`` crash into a named error (PTM-11)."""
    for match in _PROCESS_CALL_RE.finditer(js):
        name = match.group(2)
        if len(name) < _MIN_PROCESS_NAME:
            raise PTBridgeError(
                f"unknown process '{name}'; known processes include: {_PROCESS_HINT}"
            )


_RAW_PREFIX = (
    "(function(){try{var __v=eval("
)
_RAW_SUFFIX = (
    ");return JSON.stringify({ok:true,value:__v});}"
    "catch(e){return JSON.stringify({ok:false,error:String(e),"
    "line:(e&&e.lineNumber)||null});}})()"
)


def _raw(js: str, max_chars: int) -> dict[str, Any]:
    """Evaluate ``js`` and return its value as a real JSON value (PTM-15).

    The snippet is wrapped so a thrown error comes back as data (with the JS
    line number when the engine reports one) instead of a raw C++ exception, and
    a huge result is cut at ``max_chars`` with ``"truncated": true``.
    """
    _validate_process_calls(js)
    # The script is embedded as a *string literal* so eval() receives its source
    # text. Concatenating it raw would eval the expression's value instead, which
    # turns a string result into code (`"ok"` -> eval(ok) -> ReferenceError).
    payload = _eval(_RAW_PREFIX + json.dumps(js) + _RAW_SUFFIX, None, "raw")

    if isinstance(payload, dict) and payload.get("ok") is False:
        line = payload.get("line")
        message = str(payload.get("error") or "unknown error")
        if line:
            message += f" at line {line}"
        raise PTBridgeError(_api_error(message, None, "raw"))

    if isinstance(payload, dict) and payload.get("ok") is True:
        value = payload.get("value")
    else:
        value = payload

    try:
        rendered = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(value)

    if max_chars > 0 and len(rendered) > max_chars:
        return {"value": rendered[:max_chars], "truncated": True, "chars": len(rendered)}
    return {"value": value}
