"""The ES5 snippets the host, topology and raw tools ship through ``eval``.

Generated ES5 runs inside the PT Script Engine and has no access to Python: every
template is a :class:`string.Template` whose placeholders are already-encoded
values (:func:`pt_mcp.eval._lit`) or code fragments. They are filled in
``pt_mcp.hosts`` and ``pt_mcp.topology``; keep the ``$marker`` line first — it is
what identifies a snippet in ``raw`` and in the logs.
"""

from __future__ import annotations

import string

_GET_CONFIG_JS = string.Template(
    """$marker(function () {
    $lookup
    function v6text(entry) {
        if (entry === null || typeof entry === "undefined") { return ""; }
        if (typeof entry === "string") { return entry; }
        var addr = entry.address;
        if (addr !== null && typeof addr !== "undefined") {
            var prefix = entry.prefix;
            if (prefix !== null && typeof prefix !== "undefined") {
                return String(addr) + "/" + String(prefix);
            }
            return String(addr);
        }
        return String(entry);
    }
    function readPort(port, index) {
        var name = "Port" + index;
        var ip = "";
        var mask = "";
        var dhcp = false;
        var up = false;
        var v6 = [];
        try { name = String(port.getName()); } catch (e1) { name = "Port" + index; }
        try { ip = String(port.getIpAddress()); } catch (e2) { ip = ""; }
        try { mask = String(port.getSubnetMask()); } catch (e3) { mask = ""; }
        try { dhcp = !!port.isDhcpClientOn(); } catch (e4) { dhcp = false; }
        try { up = !!port.isPortUp(); } catch (e5) { up = false; }
        try {
            var rawV6 = port.getIpv6Addresses();
            if (rawV6) {
                if (typeof rawV6.length === "number") {
                    for (var j = 0; j < rawV6.length; j += 1) { v6.push(v6text(rawV6[j])); }
                } else {
                    v6.push(v6text(rawV6));
                }
            }
        } catch (e6) { v6 = []; }
        return {name: name, ip: ip, mask: mask, dhcp: dhcp, ipv6: v6, up: up};
    }
    var ports = [];
$collect
    return ports;
}())"""
)

_SET_IP_JS = string.Template(
    """$marker(function () {
    $lookup
    var port = null;
    try { port = dev.getPort($port); } catch (e1) { port = null; }
    if (!port) { throw new Error("port not found: " + $port); }
$action
    var result = {port: $port, ip: "", mask: "", dhcp: false};
    try { result.ip = String(port.getIpAddress()); } catch (e2) { result.ip = ""; }
    try { result.mask = String(port.getSubnetMask()); } catch (e3) { result.mask = ""; }
    try { result.dhcp = !!port.isDhcpClientOn(); } catch (e4) { result.dhcp = false; }
    return result;
}())"""
)

_SET_IPV6_JS = string.Template(
    """$marker(function () {
    $lookup
    var port = null;
    try { port = dev.getPort($port); } catch (e1) { port = null; }
    if (!port) { throw new Error("port not found: " + $port); }
    port.setIpv6Enabled(true);
    port.addIpv6Address($address, $prefix, 0, false);
$gateway
    function v6text(entry) {
        if (entry === null || typeof entry === "undefined") { return ""; }
        if (typeof entry === "string") { return entry; }
        var addr = entry.address;
        if (addr !== null && typeof addr !== "undefined") {
            var prefix = entry.prefix;
            if (prefix !== null && typeof prefix !== "undefined") {
                return String(addr) + "/" + String(prefix);
            }
            return String(addr);
        }
        return String(entry);
    }
    var v6 = [];
    try {
        var rawV6 = port.getIpv6Addresses();
        if (rawV6) {
            if (typeof rawV6.length === "number") {
                for (var j = 0; j < rawV6.length; j += 1) { v6.push(v6text(rawV6[j])); }
            } else {
                v6.push(v6text(rawV6));
            }
        }
    } catch (e2) { v6 = []; }
    return {port: $port, address: $address, prefix: $prefix, gateway: $gateway_value, ipv6: v6};
}())"""
)

_DNS_RECORD_JS = string.Template(
    """$marker(function () {
    $lookup
    var dns = null;
    try { dns = dev.getProcess("DnsServer"); } catch (e1) { dns = null; }
    if (!dns) { throw new Error("no DnsServer process on " + $want); }
    var result = {
        name: $name,
        ip: $ip,
        zone: $zone,
        ns: $ns,
        mailbox: $mailbox,
        enabled: null,
        soa: $soa,
        ns_record: $ns_record,
        ip_entry: null,
        a_record: null,
        entries: null
    };
$action
    try { result.enabled = !!dns.isEnabled(); } catch (e8) { result.enabled = $enabled; }
    try { result.entries = dns.getEntryCount(); } catch (e9) { result.entries = null; }
    return result;
}())"""
)

_DNS_LIST_JS = string.Template(
    """$marker(function () {
    $lookup
    var dns = null;
    try { dns = dev.getProcess("DnsServer"); } catch (e1) { dns = null; }
    if (!dns) { throw new Error("no DnsServer process on " + $want); }
$body
}())"""
)

_HTTP_PAGE_JS = string.Template(
    """$marker(function () {
    $lookup
    var http = null;
    try { http = dev.getProcess("HttpServer"); } catch (e1) { http = null; }
    if (!http) { throw new Error("no HttpServer process on " + $want); }
    http.setEnable($enabled);
$content
    var result = {path: $path, enabled: $enabled, page: ""};
    if ($enabled) {
        try { result.enabled = !!http.isEnabled(); } catch (e2) { result.enabled = true; }
        try { result.page = String(http.getPage($path)); } catch (e3) { result.page = ""; }
    } else {
        /* A disabled HttpServer must not be queried: isEnabled()/getPage()
         * after setEnable(false) takes Packet Tracer down. */
        result.enabled = false;
        result.page = "";
    }
    return result;
}())"""
)

_PC_OUTPUT_JS = string.Template(
    """$marker(function () {
    $lookup
    var line = null;
    try { line = dev.getCommandLine(); } catch (e1) { line = null; }
    if (!line) { throw new Error("no command line on " + $want); }
    if (typeof line.getOutput !== "function") {
        throw new Error("device " + $want + " has no console output buffer");
    }
$what
}())"""
)

_LIST_LINKS_JS = string.Template(
    """$marker(function () {
    var net = ipc.network();
    var out = [];
    var seen = {};
    for (var d = 0; d < net.getDeviceCount(); d += 1) {
        var dev = net.getDeviceAt(d);
        if (!dev) { continue; }
        var portCount = 0;
        try { portCount = dev.getPortCount(); } catch (e1) { portCount = 0; }
        for (var p = 0; p < portCount; p += 1) {
            var port = null;
            try { port = dev.getPortAt(p); } catch (e2) { port = null; }
            if (!port) { continue; }
            var link = null;
            try { link = port.getLink(); } catch (e3) { link = null; }
            if (!link) { continue; }
            var p1 = null;
            var p2 = null;
            try { p1 = link.getPort1(); } catch (e4) { p1 = null; }
            try { p2 = link.getPort2(); } catch (e5) { p2 = null; }
            if (!p1 || !p2) { continue; }
            var a = "";
            var ap = "";
            var b = "";
            var bp = "";
            try { a = String(p1.getOwnerDevice().getName()); } catch (e6) { a = ""; }
            try { ap = String(p1.getName()); } catch (e7) { ap = ""; }
            try { b = String(p2.getOwnerDevice().getName()); } catch (e8) { b = ""; }
            try { bp = String(p2.getName()); } catch (e9) { bp = ""; }
            var key = a + "|" + ap + "|" + b + "|" + bp;
            var rev = b + "|" + bp + "|" + a + "|" + ap;
            if (seen[key] || seen[rev]) { continue; }
            seen[key] = true;
            var up = false;
            try { up = !!p1.isPortUp() && !!p2.isPortUp(); } catch (e10) { up = false; }
            var cable = "";
            try { cable = String(link.getConnectionType()); } catch (e11) { cable = ""; }
            out.push({a: a, a_port: ap, b: b, b_port: bp, cable: cable, up: up});
        }
    }
    return out;
}())"""
)

_LIST_PORTS_JS = string.Template(
    """$marker(function () {
    $lookup
    var ports = [];
    var count = 0;
    try { count = dev.getPortCount(); } catch (e1) { count = 0; }
    for (var i = 0; i < count; i += 1) {
        var port = null;
        try { port = dev.getPortAt(i); } catch (e2) { port = null; }
        if (!port) { continue; }
        var name = "Port" + i;
        var up = false;
        var proto = false;
        var ip = "";
        var mask = "";
        var link = "";
        try { name = String(port.getName()); } catch (e3) { name = "Port" + i; }
        try { up = !!port.isPortUp(); } catch (e4) { up = false; }
        try { proto = !!port.isProtocolUp(); } catch (e5) { proto = false; }
        try { ip = String(port.getIpAddress()); } catch (e6) { ip = ""; }
        try { mask = String(port.getSubnetMask()); } catch (e7) { mask = ""; }
        try {
            var cable = port.getLink();
            if (cable) {
                var p1 = cable.getPort1();
                var p2 = cable.getPort2();
                var other = p2;
                if (p1 && String(p1.getName()) === name &&
                        String(p1.getOwnerDevice().getName()) === String(dev.getName())) {
                    other = p2;
                } else {
                    other = p1;
                }
                if (other) {
                    link = String(other.getOwnerDevice().getName()) + " " +
                        String(other.getName());
                }
            }
        } catch (e8) { link = ""; }
        ports.push({name: name, up: up, protocol_up: proto, ip: ip, mask: mask, link: link});
    }
    return ports;
}())"""
)
