"""Device-type and cable tables, and the "did you mean …?" validation.

Both are mirrors of the enumerations in ``bridge/bridge.js`` (PT 9.0.1). A name is
validated here, *before* a call goes to Packet Tracer, so a typo comes back as an
error with the valid list instead of a rejected IPC call (PTM-09/10).
"""

from __future__ import annotations

import difflib
from typing import Any

#: ``DeviceType`` values, identical to ``DEVICE_TYPES`` in ``bridge/bridge.js``
#: (PT 9.0.1). Used to validate ``add_device`` *before* the call (PTM-09).
DEVICE_TYPES: dict[str, int] = {
    "eRouter": 0,
    "eSwitch": 1,
    "eCloud": 2,
    "eBridge": 3,
    "eHub": 4,
    "eRepeater": 5,
    "eCoAxialSplitter": 6,
    "eAccessPoint": 7,
    "ePc": 8,
    "eServer": 9,
    "ePrinter": 10,
    "eWirelessRouter": 11,
    "eIpPhone": 12,
    "eDslModem": 13,
    "eCableModem": 14,
    "eRemoteNetwork": 15,
    "eMultiLayerSwitch": 16,
    "eLaptop": 17,
    "eTabletPC": 18,
    "ePda": 19,
    "eWirelessEndDevice": 20,
    "eWiredEndDevice": 21,
    "eTV": 22,
    "eHomeVoip": 23,
    "eAnalogPhone": 24,
    "eMultiUser": 25,
    "eASA": 26,
    "eIoE": 27,
    "eHomeGateway": 28,
    "eCellTower": 29,
    "eCentralOfficeServer": 30,
    "eCiscoAccessPoint": 31,
    "eEmbeddedCiscoAccessPoint": 32,
    "eSniffer": 33,
    "eMCU": 34,
    "eSBC": 35,
    "eThing": 36,
    "eMCUComponent": 37,
    "eEmbeddedServer": 38,
    "eWirelessLanController": 39,
    "eCluster": 40,
}

#: Cable names accepted by ``link``, mapped to the bridge's ``CONNECT_TYPES``
#: integers (PTM-10). The ``e…`` names are the PortType spellings people (and
#: the PT docs) use; ``straight``/``crossover``/… are the friendly ones.
CABLES: dict[str, int] = {
    "eCopperEthernet": 8100,
    "eCopperFastEthernet": 8100,
    "eCopperGigabitEthernet": 8100,
    "eFiberFastEthernet": 8103,
    "eFiberGigabitEthernet": 8103,
    "eSerial": 8106,
    "eCopperCoaxial": 8110,
    "straight": 8100,
    "crossover": 8101,
    "fiber": 8103,
    "serial": 8106,
    "console": 8108,
    "wireless": 8109,
    "coax": 8110,
    "auto": 8107,
}


def _aliases(names: dict[str, Any]) -> dict[str, Any]:
    """Lower-case lookup for ``names``, also without the leading ``e``.

    ``"eRouter"`` can be written ``EROUTER``, ``erouter``, ``Router`` or
    ``router``; everything lands on the canonical key.
    """
    out: dict[str, Any] = {}
    for name, value in names.items():
        out[name.lower()] = value
        if name[:1] == "e" and name[1:]:
            out.setdefault(name[1:].lower(), value)
    return out


_DEVICE_ALIASES = _aliases(DEVICE_TYPES)
_CABLE_ALIASES = _aliases(CABLES)


def _unknown(kind: str, value: object, names: dict[str, Any], aliases: dict[str, Any]) -> str:
    """A "what you wrote, what is allowed, what you meant" error message."""
    text = str(value)
    canonical_names = list(names)
    message = f"unknown {kind} '{text}'; valid: {', '.join(canonical_names)}"
    close = difflib.get_close_matches(text.lower(), list(aliases), n=1, cutoff=0.6)
    if close:
        target = aliases[close[0]]
        suggestion = next(
            (name for name in canonical_names if names[name] == target), None
        )
        if suggestion:
            message += f"\nDid you mean {suggestion}?"
    return message


def resolve_device_type(value: int | str) -> int:
    """Validate a ``DeviceType``: name (with or without ``e``), int or digits."""
    if isinstance(value, bool):  # bool is an int subclass; not a device type
        raise ValueError(_unknown("device type", value, DEVICE_TYPES, _DEVICE_ALIASES))
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    if text.lower() in _DEVICE_ALIASES:
        return _DEVICE_ALIASES[text.lower()]
    raise ValueError(_unknown("device type", value, DEVICE_TYPES, _DEVICE_ALIASES))


def resolve_cable(value: int | str) -> int:
    """Validate a cable name (friendly or ``e…``) or a raw ``CONNECT_TYPES`` int."""
    if isinstance(value, bool):
        raise ValueError(_unknown("cable", value, CABLES, _CABLE_ALIASES))
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    if text.lower() in _CABLE_ALIASES:
        return _CABLE_ALIASES[text.lower()]
    raise ValueError(_unknown("cable", value, CABLES, _CABLE_ALIASES))
