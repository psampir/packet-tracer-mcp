"""Waiting for a usable device prompt (PTM-01).

The bridge reads the prompt before and after every line; this module adds the one
thing it cannot do — *real waiting*: :func:`wait_ready` polls ``get_state`` with
:func:`time.sleep` until a freshly added or freshly powered device has a usable
prompt, answering the IOS initial-configuration dialog on the way. See
``docs/cli-semantics.md``.
"""

from __future__ import annotations

import contextlib
import re
import time
from typing import Any

from .protocol import PTBridgeError, _err, call_pt

#: How often :func:`wait_ready` asks the bridge for the device state, in seconds.
READY_POLL_INTERVAL = 0.25

#: Prompt modes that mean "the console is usable" (see docs/cli-semantics.md).
_READY_MODES = frozenset({"user", "enable", "config", "shell"})

#: Default readiness budget, in seconds.
READY_TIMEOUT = 45.0

#: A fresh IOS device may park at the initial-configuration dialog instead of a
#: prompt. ``no`` drops it to the normal console; the regex identifies the wait.
_SETUP_DIALOG_RE = re.compile(
    r"(initial configuration dialog|would you like to enter|\[yes/no\])",
    re.IGNORECASE,
)


def wait_ready(device: str, timeout: float = READY_TIMEOUT) -> dict[str, Any]:
    """Block until ``device`` shows a usable prompt, or raise :class:`PTBridgeError`.

    Polls ``get_state`` every :data:`READY_POLL_INTERVAL` seconds (real
    ``time.sleep``; the bridge must not busy-wait for a boot) until the device
    reports ``booting: false`` and a prompt mode of ``user``, ``enable``,
    ``config`` or ``shell``. A freshly added router sits in
    ``--- System Configuration Dialog ---`` for tens of seconds, and commands
    sent before that are eaten as wizard answers (PTM-01).
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    state: dict[str, Any] = {}
    answered_setup = False
    pressed_return = False

    while True:
        state = call_pt("get_state", {"device": device})
        prompt = str(state.get("prompt") or "")

        if _SETUP_DIALOG_RE.search(prompt):
            # A freshly added IOS device can sit at the initial-configuration
            # dialog; answer "no" once to reach the normal prompt. If it does
            # not take, the deadline below reports the dialog specifically.
            if not answered_setup:
                # Keep polling even if the answer fails: the deadline is
                # authoritative and its error names the dialog.
                with contextlib.suppress(PTBridgeError):
                    call_pt(
                        "exec",
                        {"device": device, "command": "no", "timeout_ms": 5000},
                    )
                answered_setup = True
                if time.monotonic() < deadline:
                    time.sleep(READY_POLL_INTERVAL)
                continue
            if time.monotonic() >= deadline:
                raise PTBridgeError(
                    f"device '{device}' is stuck at the IOS initial-configuration "
                    f"dialog after answering 'no' (prompt={state.get('prompt')!r}).\n"
                    "Click 'Skip boot' in the device's config dialog, or "
                    "power-cycle it with power(on=True, ready=True)."
                )
            time.sleep(READY_POLL_INTERVAL)
            continue

        # After "no" IOS prints "Press RETURN to get started!" and shows no
        # prompt; an empty line is the answer. Send it exactly once.
        if answered_setup and not pressed_return and not prompt:
            with contextlib.suppress(PTBridgeError):
                call_pt(
                    "configure",
                    {
                        "device": device,
                        "lines": [""],
                        "mode": "raw",
                        "stop_on_error": False,
                    },
                )
            pressed_return = True
            if time.monotonic() < deadline:
                time.sleep(READY_POLL_INTERVAL)
            continue

        if not state.get("booting") and state.get("mode") in _READY_MODES:
            return state
        if time.monotonic() >= deadline:
            raise PTBridgeError(
                f"device '{device}' was not ready within {timeout:g}s "
                f"(prompt={state.get('prompt')!r}, mode={state.get('mode')!r}, "
                f"booting={state.get('booting')}).\n"
                "The device is probably still booting: retry with a longer "
                "ready_timeout, power-cycle it with power(on=True, ready=True), "
                "or click 'Skip boot' in its config dialog."
            )
        time.sleep(READY_POLL_INTERVAL)


def _wait_or_error(device: str, timeout: float) -> str | None:
    """Run :func:`wait_ready`, returning the JSON error string on failure."""
    try:
        wait_ready(device, timeout)
    except PTBridgeError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything to the client
        return _err(f"Unexpected error waiting for device '{device}': {exc}")
    return None
