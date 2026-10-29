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
 *   Console work (PTM-01/02/06) is decided HERE, not in Python, because only
 *   the bridge sees the prompt after every line:
 *     get_state  prompt / mode / booting / hasIos / type / model
 *     plan       the exact lines a configure call would send (no sending)
 *     configure  plan + send, one line at a time, waiting for each prompt
 *     exec       the same machinery for one exec command ("show" is an alias)
 *   Mode rules: auto | raw | exec | config, plus keep_session / stop_on_error.
 *   See docs/cli-semantics.md.
 *
 * Protocol (must match server.py):
 *   request : {"id":"<id>","op":"<op>","args":{...}}
 *   response: {"id":"<id>","ok":true,"result":<any>}
 *             {"id":"<id>","ok":false,"error":"<msg>"}
 *
 * NOTE: the bridge always acts on whichever .pkt is currently ACTIVE in PT.
 * ========================================================================== */

/* The mailbox lives inside Packet Tracer's own user folder (``~/pt`` on Linux,
 * "Documents" on Windows), so the module works for any user without editing
 * this file. The server's DEFAULT_HOME mirrors the Linux value; set
 * PT_MCP_HOME on both sides on a platform where they differ. */
var PT_MCP_HOME = String(ipc.appWindow().getUserFolder()) + "/pt-mcp";
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

/* ==========================================================================
 * CLI state, modes and line-by-line console driving
 * --------------------------------------------------------------------------
 * Everything that decides *what* to type into a device lives here, because
 * only the bridge can see the prompt after every line:
 *
 *   get_state -- prompt, mode, booting, hasIos, type, model
 *   plan      -- the exact lines a configure call would send (no sending)
 *   configure -- plan + send, one line at a time, waiting for each prompt
 *   exec      -- the same machinery for a single exec command
 *
 * The Python side only splits input into non-blank lines; it has no mode
 * policy at all.
 * ========================================================================== */

/* Device types that speak IOS (and therefore have enable/config modes). */
var IOS_DEVICE_TYPES = { 0: true, 1: true, 16: true, 26: true, 39: true };

/* Accepted values of the "mode" argument. */
var CLI_MODES = { auto: true, raw: true, exec: true, config: true };

/* First word of a line that is an exec-mode command, not a config statement. */
var EXEC_VERBS = {
    show: true, ping: true, traceroute: true, telnet: true, ssh: true,
    clear: true, copy: true, write: true, reload: true, dir: true, more: true,
    debug: true, undebug: true, clock: true, exit: true, enable: true,
    disable: true, configure: true, terminal: true
};

/* Console output that means the line was not accepted. */
var CLI_ERROR_RE = /(% Invalid input|% Incomplete|% Ambiguous|% Unknown|% Please answer|Translating ")/;

/* Per-line wait: poll, stable-for, hard cap (milliseconds). */
var SEND_POLL_MS = 15;
var SEND_STABLE_MS = 120;
var SEND_TIMEOUT_MS = 3000;

function nowMs() {
    /* ES5 has Date.now(); the fallback keeps older QtScript builds happy. */
    if (typeof Date.now === "function") { return Date.now(); }
    return new Date().getTime();
}

/* Bounded busy-wait: the Script Engine has no usable timers, and each wait is
 * only a few milliseconds. Readiness (seconds) is handled by the server. */
function sleepMs(ms) {
    var until = nowMs() + ms;
    while (nowMs() < until) { /* spin */ }
}

function deviceTypeId(device) {
    var t = null;
    try { t = device.getType(); } catch (e) { t = null; }
    return t;
}

function deviceHasIos(type) {
    if (type === null || typeof type === "undefined") { return false; }
    return IOS_DEVICE_TYPES.hasOwnProperty(type) === true;
}

/* Classify a prompt: shell (PC C:\>), config, enable, user, unknown. */
function promptMode(prompt) {
    var p = String(prompt === null || typeof prompt === "undefined" ? "" : prompt);
    p = p.replace(/\s+$/, "");
    if (!p) { return "unknown"; }
    var last = p.charAt(p.length - 1);
    if (last === "#") {
        return p.indexOf("(config") >= 0 ? "config" : "enable";
    }
    if (last === ">") {
        /* A drive-letter prompt is a host shell, not IOS user exec. */
        if (/^[A-Za-z]:[\\\/]/.test(p)) { return "shell"; }
        return "user";
    }
    return "unknown";
}

function isReadyMode(mode) {
    return mode === "user" || mode === "enable" ||
        mode === "config" || mode === "shell";
}

function deviceBooting(device, prompt) {
    try {
        if (device && typeof device.isBooting === "function") {
            return !!device.isBooting();
        }
    } catch (e) { /* fall back to the prompt heuristic */ }
    var p = String(prompt === null || typeof prompt === "undefined" ? "" : prompt);
    if (!p) { return true; }
    if (p.indexOf("System Configuration Dialog") >= 0) { return true; }
    if (p.indexOf("Press RETURN") >= 0) { return true; }
    return false;
}

/* The console line to drive: ConsoleLine first (it has getOutput()), then
 * TerminalLine (which has no getOutput()). */
function getCommandLine(device) {
    var line = null;
    var lineErr = null;
    try { line = device.getConsoleLine(); } catch (e) { lineErr = e; }
    if (!line) {
        try { line = device.getCommandLine(); } catch (e) { lineErr = e; }
    }
    if (!line) {
        throw new Error("no command line available on device" +
            (lineErr ? ": " + errorMessage(lineErr) : ""));
    }
    return line;
}

function readPrompt(line) {
    var prompt = "";
    try {
        if (typeof line.getPrompt === "function") { prompt = line.getPrompt(); }
    } catch (e) { prompt = ""; }
    return String(prompt === null || typeof prompt === "undefined" ? "" : prompt);
}

function readOutput(line) {
    var output = "";
    try {
        if (typeof line.getOutput === "function") { output = line.getOutput(); }
    } catch (e) { output = ""; }
    return String(output === null || typeof output === "undefined" ? "" : output);
}

function readDeviceState(device, line) {
    var ln = line || null;
    if (!ln) {
        try { ln = getCommandLine(device); } catch (e) { ln = null; }
    }
    var prompt = ln ? readPrompt(ln) : "";
    var type = deviceTypeId(device);
    var model = "";
    try { model = String(device.getModel()); } catch (e) { model = ""; }
    return {
        name: String(device.getName()),
        prompt: prompt,
        mode: promptMode(prompt),
        booting: deviceBooting(device, prompt),
        hasIos: deviceHasIos(type),
        type: type,
        model: model
    };
}

function normalizeCliMode(mode) {
    var m = String(mode === null || typeof mode === "undefined" || mode === "" ? "auto" : mode);
    m = m.toLowerCase();
    if (!CLI_MODES.hasOwnProperty(m)) {
        throw new Error("unknown mode '" + String(mode) +
            "'; valid: auto, raw, exec, config");
    }
    return m;
}

function firstMeaningfulLine(lines) {
    var i, s;
    for (i = 0; i < lines.length; i++) {
        s = String(lines[i] === null || typeof lines[i] === "undefined" ? "" : lines[i]);
        s = s.replace(/^\s+/, "");
        if (s) { return s; }
    }
    return null;
}

function lastMeaningfulLine(lines) {
    var i, s;
    for (i = lines.length - 1; i >= 0; i--) {
        s = String(lines[i] === null || typeof lines[i] === "undefined" ? "" : lines[i]);
        s = s.replace(/\s+$/, "");
        if (s) { return s; }
    }
    return null;
}

function isExecVerb(line) {
    var m = /^[A-Za-z][A-Za-z-]*/.exec(String(line));
    if (!m) { return false; }
    return EXEC_VERBS.hasOwnProperty(m[0].toLowerCase());
}

/* config: enable (only from user exec) -> configure terminal -> body -> end. */
function wrapConfig(currentMode, lines, keepSession) {
    var out = [];
    if (currentMode === "user") { out.push("enable"); }
    if (currentMode !== "config") { out.push("configure terminal"); }
    out = out.concat(lines);
    /* No second end if the block already finishes with one. */
    var last = lastMeaningfulLine(lines);
    var alreadyEnded = last !== null && last.toLowerCase() === "end";
    if (!keepSession && !alreadyEnded) { out.push("end"); }
    return out;
}

/* exec: leave config first (end), enable from user exec; never enter config. */
function wrapExec(currentMode, lines) {
    var out = [];
    if (currentMode === "config") {
        /* A failed/keep_session configure can leave the console in config;
         * `show` is rejected there, so step back to enable first. */
        out.push("end");
    } else if (currentMode === "user") {
        out.push("enable");
    }
    return out.concat(lines);
}

/* With the prompt at enable (or user, after enable), decide exec vs config. */
function planAtEnable(lines, keepSession) {
    var first = firstMeaningfulLine(lines);
    if (first !== null && isExecVerb(first)) {
        return { effective: "exec", lines: lines.slice() };
    }
    return {
        effective: "config",
        lines: wrapConfig("enable", lines, keepSession)
    };
}

/* Resolve the requested mode against the observed prompt. Never sends. */
function planLines(state, lines, requestedMode, keepSession) {
    var requested = normalizeCliMode(requestedMode);
    var input = lines.slice();
    var atEnable;

    /* No IOS (PC, server, printer, AP, ...) means zero insertions. */
    if (!state.hasIos) {
        return { requested: requested, effective: "raw", lines: input };
    }
    if (requested === "raw") {
        return { requested: requested, effective: "raw", lines: input };
    }
    if (requested === "exec") {
        return {
            requested: requested,
            effective: "exec",
            lines: wrapExec(state.mode, input)
        };
    }
    if (requested === "config") {
        return {
            requested: requested,
            effective: "config",
            lines: wrapConfig(state.mode, input, keepSession)
        };
    }

    /* auto */
    if (state.mode === "config") {
        return {
            requested: requested,
            effective: "config",
            lines: wrapConfig("config", input, keepSession)
        };
    }
    if (state.mode === "user") {
        atEnable = planAtEnable(input, keepSession);
        return {
            requested: requested,
            effective: atEnable.effective,
            lines: ["enable"].concat(atEnable.lines)
        };
    }
    if (state.mode === "enable") {
        atEnable = planAtEnable(input, keepSession);
        return {
            requested: requested,
            effective: atEnable.effective,
            lines: atEnable.lines
        };
    }
    /* shell / unknown: never inject anything. */
    return { requested: requested, effective: "raw", lines: input };
}

/* Send one line and wait for it to be absorbed: the prompt changed, or the
 * output buffer stayed the same length for SEND_STABLE_MS. */
function sendOneLine(device, line, cmd, timeoutMs) {
    var promptBefore = readPrompt(line);
    var lenBefore = readOutput(line).length;
    var lastLen = lenBefore;
    var started = nowMs();
    var lastChange = started;
    var timedOut = false;
    var promptAfter = promptBefore;

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

    for (;;) {
        /* TerminalLine has flush(int); ConsoleLine does not in some builds. */
        try {
            if (typeof line.flush === "function") { line.flush(10); }
        } catch (e3) { /* ignore: flush is a hint, not a requirement */ }

        var out = readOutput(line);
        var now = nowMs();
        if (out.length !== lastLen) {
            lastLen = out.length;
            lastChange = now;
        }
        promptAfter = readPrompt(line);
        if (promptAfter !== promptBefore) { break; }
        if (now - lastChange >= SEND_STABLE_MS) { break; }
        if (now - started >= timeoutMs) {
            timedOut = true;
            break;
        }
        sleepMs(SEND_POLL_MS);
    }

    var delta = readOutput(line).substring(lenBefore);
    return {
        prompt: promptAfter,
        delta: delta,
        timedOut: timedOut,
        ok: !timedOut && !CLI_ERROR_RE.test(delta)
    };
}

/* Run a resolved plan line by line and build the configure/exec result. */
function runLines(device, plan, options, line) {
    var opts = options || {};
    var stopOnError = opts.stopOnError !== false;
    var timeoutMs = typeof opts.timeoutMs === "number"
        ? opts.timeoutMs
        : parseInt(opts.timeoutMs, 10);
    if (isNaN(timeoutMs) || timeoutMs <= 0) { timeoutMs = SEND_TIMEOUT_MS; }

    var console = line || getCommandLine(device);
    var records = [];
    var chunks = [];
    var failed = [];
    var stoppedOn = null;
    var i, cmd, sent, failure;

    for (i = 0; i < plan.lines.length; i++) {
        cmd = String(plan.lines[i]);
        sent = sendOneLine(device, console, cmd, timeoutMs);
        records.push({
            line: cmd,
            prompt: sent.prompt,
            output: sent.delta,
            ok: sent.ok
        });
        if (sent.delta) { chunks.push(sent.delta); }
        if (!sent.ok) {
            failure = {
                index: i + 1,
                line: cmd,
                prompt: sent.prompt,
                reason: sent.timedOut ? "timeout" : "rejected",
                output: sent.delta
            };
            if (stoppedOn === null) { stoppedOn = failure; }
            failed.push(i + 1);
            if (stopOnError) { break; }
        }
    }

    var result = {
        ok: stoppedOn === null,
        prompt: readPrompt(console),
        lines: records,
        output: chunks.join(""),
        stopped_on: stoppedOn,
        mode: plan.requested,
        effective_mode: plan.effective
    };
    if (stoppedOn !== null) {
        result.error = "line " + stoppedOn.index + " '" + stoppedOn.line + "' " +
            (stoppedOn.reason === "timeout" ? "timed out" : "was rejected") +
            " at prompt '" + stoppedOn.prompt + "'";
        result.failed = failed;
    }
    return result;
}

/* Configure op: resolve the plan, then send it. */
function configureDevice(device, args) {
    var lines = args.lines || [];
    if (!lines.length) { throw new Error("configure: no lines supplied"); }
    var console = getCommandLine(device);
    var state = readDeviceState(device, console);
    var plan = planLines(state, lines, args.mode, !!args.keep_session);
    return runLines(device, plan, {
        stopOnError: args.stop_on_error !== false,
        timeoutMs: args.timeout_ms
    }, console);
}

/* Plan op: the same resolution, without touching the console. */
function planDevice(device, args) {
    var lines = args.lines || [];
    if (!lines.length) { throw new Error("plan: no lines supplied"); }
    var state = readDeviceState(device);
    var plan = planLines(state, lines, args.mode, !!args.keep_session);
    return {
        lines: plan.lines,
        mode: plan.requested,
        effective_mode: plan.effective,
        /* A plan taken while the device boots is provisional: the prompt is
         * not known yet, so `auto` cannot wrap anything. */
        prompt: state.prompt,
        booting: state.booting
    };
}

/* Exec op (and the older "show" alias): one command, exec mode, no end. */
function execDevice(device, command, timeoutMs, opName) {
    var cmd = String(command === null || typeof command === "undefined" ? "" : command);
    if (!cmd.replace(/\s+$/, "")) {
        throw new Error((opName || "exec") + ": no command supplied");
    }
    var console = getCommandLine(device);
    var state = readDeviceState(device, console);
    var plan = planLines(state, [cmd], "exec", true);
    return runLines(device, plan, {
        stopOnError: true,
        timeoutMs: timeoutMs
    }, console);
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
        /* Non-blocking readiness flag: a freshly added IOS device is usually
         * still booting. The server's wait_ready() polls get_state. */
        var ready = false;
        try {
            var newState = readDeviceState(getDeviceOrThrow(newName));
            ready = !newState.booting && isReadyMode(newState.mode);
        } catch (eAdd) { ready = false; }
        return { name: newName, ready: ready };
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

    if (op === "get_state") {
        return readDeviceState(getDeviceOrThrow(args.device));
    }

    if (op === "plan") {
        return planDevice(getDeviceOrThrow(args.device), args);
    }

    if (op === "configure") {
        return configureDevice(getDeviceOrThrow(args.device), args);
    }

    if (op === "exec") {
        return execDevice(
            getDeviceOrThrow(args.device),
            args.command,
            args.timeout_ms,
            "exec"
        );
    }

    if (op === "show") {
        /* Backwards-compatible alias for exec (one command, no config). */
        return execDevice(
            getDeviceOrThrow(args.device),
            args.command,
            args.timeout_ms,
            "show"
        );
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
