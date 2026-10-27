/* ============================================================================
 * pt-mcp -- Packet Tracer <-> MCP file-drop bridge (Script Module engine)
 * ----------------------------------------------------------------------------
 * Paste this ENTIRE file into Packet Tracer's Script Module editor.
 *   Extensions -> Scripting -> Configure PT Script Modules... -> your module
 *   Script Engine tab -> add a new file (name it bridge.js) -> paste -> Apply
 *
 * It is written in ES5 ONLY (QtScript): var / function only. No let/const,
 * no arrow functions, no template literals, no classes, no #include.
 *
 * How it works:
 *   The Python MCP server writes a request file into  <HOME>/jobs/<id>.json
 *   This bridge picks it up, runs the op against the PT IPC API, writes
 *   <HOME>/results/<id>.json, then deletes the job file. No network at all.
 *
 * Protocol (must match server.py):
 *   request : {"id":"<id>","op":"<op>","args":{...}}
 *   response: {"id":"<id>","ok":true,"result":<any>}
 *             {"id":"<id>","ok":false,"error":"<msg>"}
 *
 * NOTE: the bridge always acts on whichever .pkt is currently ACTIVE in PT.
 * ========================================================================== */

var PT_MCP_HOME = "/home/pawel/.local/share/pt-mcp";
var PT_MCP_JOBS = PT_MCP_HOME + "/jobs";
var PT_MCP_RESULTS = PT_MCP_HOME + "/results";

/* --------------------------------------------------------------------------
 * DeviceType enum (integer values, PT 9.0.1)
 * ------------------------------------------------------------------------ */
var DEVICE_TYPES = {
    eRouter: 0,
    eSwitch: 1,
    eCloud: 2,
    eBridge: 3,
    eHub: 4,
    eRepeater: 5,
    eCoAxialSplitter: 6,
    eAccessPoint: 7,
    ePc: 8,
    eServer: 9,
    ePrinter: 10,
    eWirelessRouter: 11,
    eIpPhone: 12,
    eDslModem: 13,
    eCableModem: 14,
    eRemoteNetwork: 15,
    eMultiLayerSwitch: 16,
    eLaptop: 17,
    eTabletPC: 18,
    ePda: 19,
    eWirelessEndDevice: 20,
    eWiredEndDevice: 21,
    eTV: 22,
    eHomeVoip: 23,
    eAnalogPhone: 24,
    eMultiUser: 25,
    eASA: 26,
    eIoE: 27,
    eHomeGateway: 28,
    eCellTower: 29,
    eCentralOfficeServer: 30,
    eCiscoAccessPoint: 31,
    eEmbeddedCiscoAccessPoint: 32,
    eSniffer: 33,
    eMCU: 34,
    eSBC: 35,
    eThing: 36,
    eMCUComponent: 37,
    eEmbeddedServer: 38,
    eWirelessLanController: 39,
    eCluster: 40
};

/* Reverse map for list_devices (int -> name). */
var DEVICE_TYPES_BY_ID = {};
(function () {
    var k;
    for (k in DEVICE_TYPES) {
        if (DEVICE_TYPES.hasOwnProperty(k)) {
            DEVICE_TYPES_BY_ID[DEVICE_TYPES[k]] = k;
        }
    }
}());

/* --------------------------------------------------------------------------
 * CONNECT_TYPES enum for createLink().
 *
 * VERIFIED against the local PT 9.0.1 Doxygen docs (class_link.html):
 *   FIRST_TYPE = 8100, ETHERNET_STRAIGHT = FIRST_TYPE, ETHERNET_CROSS,
 *   ETHERNET_ROLL, FIBER, PHONE, CABLE, SERIAL, AUTO, CONSOLE, WIRELESS,
 *   COAXIAL, OCTAL, CELLULAR, USB, CUSTOM_IO, BLUETOOTH_PAIRED,
 *   BLUETOOTH_BROADCAST, LAST_TYPE = BLUETOOTH_BROADCAST
 *
 * IMPORTANT: the names eCopperEthernet / eCopperGigabitEthernet /
 * eFiberGigabitEthernet / eSerial etc. that appear in the PT API docs are
 * actually the **PortType** enum (used by Port.getType()), NOT CONNECT_TYPES.
 * createLink's 5th argument needs a CONNECT_TYPES value, so we map friendly
 * names to the numeric enum above and only pass a raw string through as a
 * last-resort fallback (some PT builds may also accept the enum name string).
 * ------------------------------------------------------------------------ */
var CONNECT_TYPES = {
    /* canonical enum names (numeric) */
    ETHERNET_STRAIGHT: 8100,
    ETHERNET_CROSS: 8101,
    ETHERNET_ROLL: 8102,
    FIBER: 8103,
    PHONE: 8104,
    CABLE: 8105,
    SERIAL: 8106,
    AUTO: 8107,
    CONSOLE: 8108,
    WIRELESS: 8109,
    COAXIAL: 8110,
    OCTAL: 8111,
    CELLULAR: 8112,
    USB: 8113,
    CUSTOM_IO: 8114,
    BLUETOOTH_PAIRED: 8115,
    BLUETOOTH_BROADCAST: 8116,

    /* friendly / alias names */
    copper: 8100,
    ethernet: 8100,
    straight: 8100,
    "straight-through": 8100,
    crossover: 8101,
    cross: 8101,
    rollover: 8102,
    fiber: 8103,
    phone: 8104,
    cable: 8105,
    serial: 8106,
    auto: 8107,
    console: 8108,
    wireless: 8109,
    coax: 8110,
    coaxial: 8110,
    octal: 8111,
    cellular: 8112,
    usb: 8113,
    bluetooth: 8115,

    /* PortType-style names the prompt mentioned -- aliased to closest cable */
    eCopperEthernet: 8100,
    eCopperFastEthernet: 8100,
    eCopperGigabitEthernet: 8100,
    eFiberFastEthernet: 8103,
    eFiberGigabitEthernet: 8103,
    eCopperCoaxial: 8110,
    eSerial: 8106
};

/* --------------------------------------------------------------------------
 * Internal state
 * ------------------------------------------------------------------------ */
var PT_MCP_BUSY = false;       /* re-entrancy guard for the job scanner     */
var PT_MCP_AGAIN = false;      /* a rescan was requested while busy         */
var PT_MCP_INITED = false;     /* watcher registered?                       */
var PT_MCP_SEEN = {};          /* ids already processed this session        */

/* Bumped on every evaluation of this file (Script Module start = 1). Used so
 * that "Run File" (which re-evaluates without calling main()) triggers an
 * automatic rescan. */
if (typeof PT_MCP_EVALS === "undefined") { PT_MCP_EVALS = 0; }
PT_MCP_EVALS = PT_MCP_EVALS + 1;

/* --------------------------------------------------------------------------
 * Logging / small helpers
 * ------------------------------------------------------------------------ */
function ptLog(msg) {
    try {
        ipc.appWindow().writeToPT("[pt-mcp] " + msg + "\n");
    } catch (e) {
        /* never let logging break a job */
    }
}

function baseName(p) {
    var s = String(p);
    var a = s.lastIndexOf("/");
    var b = s.lastIndexOf("\\");
    var i = a > b ? a : b;
    return i >= 0 ? s.substring(i + 1) : s;
}

function tail(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.substring(s.length - n) : s;
}

function safeId(id) {
    return String(id == null ? "" : id).replace(/[^A-Za-z0-9_.-]/g, "_");
}

function safeStringify(v) {
    try {
        var s = JSON.stringify(v);
        return typeof s === "undefined" ? String(v) : s;
    } catch (e) {
        try { return String(v); } catch (e2) { return "<unstringifiable>"; }
    }
}

function errorMessage(e) {
    if (!e) { return "unknown error"; }
    if (e.message) { return String(e.message); }
    return String(e);
}

/* --------------------------------------------------------------------------
 * Directories
 * ------------------------------------------------------------------------ */
function ensureDir(path) {
    try {
        var fm = ipc.systemFileManager();
        if (!fm.directoryExists(path)) {
            fm.makeDirectory(path);
        }
    } catch (e) {
        ptLog("ensureDir(" + path + ") failed: " + errorMessage(e));
    }
}

function ensureDirs() {
    /* create parent first in case makeDirectory() is not recursive */
    ensureDir(PT_MCP_HOME);
    ensureDir(PT_MCP_JOBS);
    ensureDir(PT_MCP_RESULTS);
}

/* --------------------------------------------------------------------------
 * Job discovery + result writing
 * ------------------------------------------------------------------------ */
function listJobFiles() {
    var out = [];
    try {
        var fm = ipc.systemFileManager();
        var files = fm.getFilesInDirectory(PT_MCP_JOBS);
        if (!files) { return out; }
        var i, n, name;
        /* QList<QString> normally converts to a JS array; be defensive. */
        n = files.length;
        if (typeof n === "undefined") { return out; }
        for (i = 0; i < n; i++) {
            name = baseName(files[i]);
            if (/\.json$/i.test(name) && !/\.tmp$/i.test(name)) {
                out.push(name);
            }
        }
    } catch (e) {
        ptLog("listJobFiles failed: " + errorMessage(e));
    }
    return out;
}

function writeResult(id, ok, payload) {
    var sid = safeId(id);
    var obj = { id: String(id), ok: !!ok };
    if (ok) { obj.result = payload; }
    else { obj.error = String(payload); }
    var text = safeStringify(obj);
    var path = PT_MCP_RESULTS + "/" + sid + ".json";
    try {
        var fm = ipc.systemFileManager();
        /* SystemFileManager has no rename(); write directly. The server
         * tolerates a partial read by retrying JSON parse. */
        var wrote = fm.writePlainTextToFile(path, text);
        if (!wrote) {
            fm.writeTextToFile(path, text);
        }
    } catch (e) {
        ptLog("writeResult(" + id + ") failed: " + errorMessage(e));
    }
}

function removeJob(fileName) {
    try {
        ipc.systemFileManager().removeFile(PT_MCP_JOBS + "/" + fileName);
    } catch (e) {
        ptLog("removeJob(" + fileName + ") failed: " + errorMessage(e));
    }
}

/* --------------------------------------------------------------------------
 * Type / cable resolution
 * ------------------------------------------------------------------------ */
function resolveDeviceType(type) {
    if (typeof type === "number") { return type; }
    if (type === null || typeof type === "undefined") {
        throw new Error("add_device: 'type' is required");
    }
    var s = String(type);
    if (/^-?[0-9]+$/.test(s)) { return parseInt(s, 10); }
    if (DEVICE_TYPES.hasOwnProperty(s)) { return DEVICE_TYPES[s]; }
    var low = s.toLowerCase();
    var k;
    for (k in DEVICE_TYPES) {
        if (DEVICE_TYPES.hasOwnProperty(k) && k.toLowerCase() === low) {
            return DEVICE_TYPES[k];
        }
    }
    throw new Error("add_device: unknown device type '" + s + "'");
}

function resolveCable(cable) {
    if (typeof cable === "number") { return cable; }
    if (cable === null || typeof cable === "undefined" || cable === "") {
        return CONNECT_TYPES.ETHERNET_STRAIGHT;
    }
    var s = String(cable);
    if (/^-?[0-9]+$/.test(s)) { return parseInt(s, 10); }
    if (CONNECT_TYPES.hasOwnProperty(s)) { return CONNECT_TYPES[s]; }
    var low = s.toLowerCase();
    var k;
    for (k in CONNECT_TYPES) {
        if (CONNECT_TYPES.hasOwnProperty(k) && k.toLowerCase() === low) {
            return CONNECT_TYPES[k];
        }
    }
    ptLog("resolveCable: unknown cable '" + s + "' -- passing through as-is");
    return s; /* last-resort: let PT try to interpret the name string */
}

/* --------------------------------------------------------------------------
 * PT object helpers
 * ------------------------------------------------------------------------ */
function getDeviceOrThrow(name) {
    var dev = null;
    try { dev = ipc.network().getDevice(String(name)); } catch (e) { dev = null; }
    if (!dev) { throw new Error("device not found: " + name); }
    return dev;
}

function getLogicalWorkspace() {
    return ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
}

/* Enter a list of CLI lines on a device and return the resulting output. */
function enterLines(device, lines) {
    var line = null;
    var lineErr = null;

    /* Prefer ConsoleLine: it exposes getOutput() (TerminalLine does not).
     * Verified in the PT 9.0.1 API docs -- ConsoleLine::enterCommand(string),
     * getOutput(), getPrompt(), getMode(); TerminalLine has no getOutput(). */
    try { line = device.getConsoleLine(); } catch (e) { lineErr = e; }
    if (!line) {
        try { line = device.getCommandLine(); } catch (e) { lineErr = e; }
    }
    if (!line) {
        throw new Error("no command line available on device" +
            (lineErr ? ": " + errorMessage(lineErr) : ""));
    }

    var i, cmd;
    for (i = 0; i < lines.length; i++) {
        cmd = String(lines[i]);
        try {
            line.enterCommand(cmd);
        } catch (e) {
            /* Fallback to CiscoDevice.enterCommand(cmd, mode) */
            try {
                device.enterCommand(cmd, "");
            } catch (e2) {
                throw new Error("enterCommand('" + cmd + "') failed: " +
                    errorMessage(e2));
            }
        }
    }

    /* Give the engine a chance to render output. flush(int) per docs. */
    try { if (typeof line.flush === "function") { line.flush(500); } }
    catch (e) { try { line.flush(); } catch (e2) { /* ignore */ } }

    var output = "";
    try {
        if (typeof line.getOutput === "function") {
            output = line.getOutput();
        }
    } catch (e) { output = ""; }

    var prompt = "";
    try {
        if (typeof line.getPrompt === "function") {
            prompt = line.getPrompt();
        }
    } catch (e) { prompt = ""; }

    return { output: tail(output, 8000), prompt: String(prompt) };
}

/* --------------------------------------------------------------------------
 * Op dispatch
 * ------------------------------------------------------------------------ */
function dispatch(op, args) {
    args = args || {};

    if (op === "ping") {
        return {
            version: String(ipc.appWindow().getVersion()),
            deviceCount: ipc.network().getDeviceCount()
        };
    }

    if (op === "list_devices") {
        var net = ipc.network();
        var count = net.getDeviceCount();
        var devices = [];
        var i;
        for (i = 0; i < count; i++) {
            var d = net.getDeviceAt(i);
            if (!d) { continue; }
            var t = null;
            try { t = d.getType(); } catch (e) { t = null; }
            var entry = {
                name: String(d.getName()),
                type: t,
                typeName: (t !== null && DEVICE_TYPES_BY_ID.hasOwnProperty(t))
                    ? DEVICE_TYPES_BY_ID[t] : null,
                model: String(d.getModel())
            };
            devices.push(entry);
        }
        var links = 0;
        try { links = net.getLinkCount(); } catch (e) { links = 0; }
        return { devices: devices, links: links };
    }

    if (op === "add_device") {
        var lw = getLogicalWorkspace();
        var typeInt = resolveDeviceType(args.type);
        var model = (typeof args.model === "undefined" || args.model === null)
            ? "" : String(args.model);
        var x = (typeof args.x === "number") ? args.x : parseFloat(args.x);
        var y = (typeof args.y === "number") ? args.y : parseFloat(args.y);
        if (isNaN(x)) { x = 100.0; }
        if (isNaN(y)) { y = 100.0; }
        var newName = lw.addDevice(typeInt, model, x, y);
        newName = String(newName);
        if (args.name) {
            var dev = getDeviceOrThrow(newName);
            dev.setName(String(args.name));
            newName = String(args.name);
        }
        return { name: newName };
    }

    if (op === "remove_device") {
        var removed = getLogicalWorkspace().removeDevice(String(args.name));
        return { removed: !!removed };
    }

    if (op === "link") {
        var cable = resolveCable(args.cable);
        var linked = getLogicalWorkspace().createLink(
            String(args.a), String(args.aPort),
            String(args.b), String(args.bPort),
            cable
        );
        return { linked: !!linked };
    }

    if (op === "autolink") {
        getLogicalWorkspace().autoConnectDevices(String(args.a), String(args.b));
        return { ok: true };
    }

    if (op === "configure") {
        var cdev = getDeviceOrThrow(args.device);
        var clines = args.lines || [];
        if (!clines.length) { throw new Error("configure: no lines supplied"); }
        return enterLines(cdev, clines);
    }

    if (op === "show") {
        var sdev = getDeviceOrThrow(args.device);
        return enterLines(sdev, [String(args.command)]);
    }

    if (op === "power") {
        var pdev = getDeviceOrThrow(args.device);
        pdev.setPower(!!args.on);
        return { ok: true };
    }

    if (op === "skip_boot") {
        var bdev = getDeviceOrThrow(args.device);
        bdev.skipBoot();
        return { ok: true };
    }

    if (op === "save") {
        var path = String(args.path);
        ipc.appWindow().fileSaveAsNoPrompt(path, false);
        return { saved: true, path: path };
    }

    if (op === "eval") {
        var result = eval(String(args.js)); /* escape hatch -- documented */
        return { value: safeStringify(result) };
    }

    throw new Error("unknown op: " + op);
}

/* --------------------------------------------------------------------------
 * Job processing
 * ------------------------------------------------------------------------ */
function processJob(fileName) {
    var id = fileName.replace(/\.json$/i, "");
    if (PT_MCP_SEEN.hasOwnProperty(id)) { return; }
    PT_MCP_SEEN[id] = true;

    var path = PT_MCP_JOBS + "/" + fileName;
    var text = "";
    try {
        text = ipc.systemFileManager().getFileContents(path);
    } catch (e) {
        writeResult(id, false, "cannot read job: " + errorMessage(e));
        removeJob(fileName);
        return;
    }

    var req = null;
    try {
        req = JSON.parse(text);
    } catch (e) {
        writeResult(id, false, "invalid job JSON: " + errorMessage(e));
        removeJob(fileName);
        return;
    }

    var op = req && req.op ? String(req.op) : "";
    var args = (req && req.args) ? req.args : {};
    var jobId = (req && req.id) ? req.id : id;

    try {
        var result = dispatch(op, args);
        writeResult(jobId, true, result);
        ptLog("ok: " + op + " (" + jobId + ")");
    } catch (e) {
        writeResult(jobId, false, errorMessage(e));
        ptLog("error: " + op + " (" + jobId + "): " + errorMessage(e));
    } finally {
        removeJob(fileName);
    }
}

function processPending() {
    if (PT_MCP_BUSY) { PT_MCP_AGAIN = true; return; }
    PT_MCP_BUSY = true;
    try {
        do {
            PT_MCP_AGAIN = false;
            var files = listJobFiles();
            var i;
            for (i = 0; i < files.length; i++) {
                processJob(files[i]);
            }
        } while (PT_MCP_AGAIN);
    } catch (e) {
        ptLog("processPending failed: " + errorMessage(e));
    } finally {
        PT_MCP_BUSY = false;
    }
}

/* Exposed so it can be invoked manually (e.g. via the eval op). */
var ptmcp_rescan = processPending;

/* --------------------------------------------------------------------------
 * File watcher
 * ------------------------------------------------------------------------ */
function onFsEvent(src, args) {
    processPending();
}

function setupWatcher() {
    try {
        var watcher = ipc.systemFileManager().getFileWatcher();
        watcher.registerEvent("fileChanged", null, onFsEvent);
        watcher.registerEvent("directoryChanged", null, onFsEvent);
        watcher.addPath(PT_MCP_JOBS);
        ptLog("watching " + PT_MCP_JOBS);
    } catch (e) {
        ptLog("watcher setup failed (use 'Run File' to force a rescan): " +
            errorMessage(e));
    }
}

/* --------------------------------------------------------------------------
 * Entry points
 * ------------------------------------------------------------------------ */
function main() {
    ensureDirs();
    if (!PT_MCP_INITED) {
        setupWatcher();
        PT_MCP_INITED = true;
    }
    ptLog("bridge ready (home=" + PT_MCP_HOME + ")");
    processPending();
}

function cleanUp() {
    try {
        ipc.systemFileManager().getFileWatcher().removePath(PT_MCP_JOBS);
    } catch (e) { /* ignore */ }
}

/* PT calls main() on Script Module start. On subsequent evaluations (the
 * "Run File" button re-evaluates this file without calling main()), auto-run
 * main() so clicking Run File forces a rescan. */
if (PT_MCP_EVALS > 1) {
    try { main(); } catch (e) { ptLog("rescan-on-run failed: " + errorMessage(e)); }
}
