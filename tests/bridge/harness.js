"use strict";

/*
 * Test harness for bridge/bridge.js — the ES5 script that runs inside Packet
 * Tracer's Script Module engine.
 *
 * bridge.js is a plain script, not a module: it declares globals and expects the
 * host to provide `ipc`. It is deliberately NOT modified for testing (it is
 * already installed inside Packet Tracer and copied in by hand), so this harness
 * loads the real file into a `node:vm` context and plays the part of PT:
 *
 *   - `ipc.appWindow()`, `ipc.network()`, `ipc.systemFileManager()`
 *   - an in-memory filesystem (`mem`) with the subset of SystemFileManager
 *     bridge.js actually calls
 *   - mock devices with a ConsoleLine (has getOutput) and a TerminalLine (does
 *     not — that difference is the point of getCommandLine()), plus a prompt
 *     state machine (`>` → `#` → `(config)#` → `(config-if)#`, `end`, `exit`) so
 *     the CLI rules in bridge.js have something IOS-shaped to act on
 *
 * The first evaluation of bridge.js does not call main() (PT_MCP_EVALS === 1),
 * so loading is side-effect-free and every test starts from a clean context.
 *
 * Cross-realm note: objects created by the vm context have that realm's
 * prototypes, so `assert.deepStrictEqual` would reject them. Compare with
 * `JSON.stringify` or the `plain()` helper in the test file instead.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const BRIDGE_PATH = path.resolve(__dirname, "..", "..", "bridge", "bridge.js");

/** Normalise a POSIX path the way the bridge's absolute paths are written. */
function normalize(p) {
  const s = String(p).replace(/\\/g, "/");
  const n = path.posix.normalize(s);
  return n.length > 1 && n.endsWith("/") ? n.slice(0, -1) : n;
}

/**
 * In-memory SystemFileManager.
 *
 * `dirs` is a Set of directory paths, `files` a Map of path -> text. Tests may
 * seed files directly with `mem.putFile()` (bypassing writePlainTextToFile, so
 * it still works while `failPlainWrite` is set).
 */
function createMemFS() {
  const dirs = new Set();
  const files = new Map();

  const mem = {
    dirs,
    files,
    /** Set to true to make writePlainTextToFile() report failure (fallback test). */
    failPlainWrite: false,
    /** Every successful writePlainTextToFile() call: {path, text}. */
    plainWrites: [],
    /** Every writeTextToFile() call: {path, text}. */
    textWrites: [],

    /** Seed a file without going through the manager (test setup helper). */
    putFile(p, text) {
      const key = normalize(p);
      files.set(key, String(text));
      return key;
    },

    hasFile(p) {
      return files.has(normalize(p));
    },

    directoryExists(p) {
      return dirs.has(normalize(p));
    },

    makeDirectory(p) {
      let cur = normalize(p);
      while (cur && cur !== "/" && !dirs.has(cur)) {
        dirs.add(cur);
        cur = path.posix.dirname(cur);
      }
      dirs.add("/");
      return true;
    },

    /** Full paths of the direct children (files first, then subdirectories). */
    getFilesInDirectory(dir) {
      const d = normalize(dir);
      const out = [];
      for (const f of files.keys()) {
        if (path.posix.dirname(f) === d) {
          out.push(f);
        }
      }
      for (const sub of dirs) {
        if (sub !== d && sub !== "/" && path.posix.dirname(sub) === d) {
          out.push(sub);
        }
      }
      out.sort();
      return out;
    },

    getFileContents(p) {
      const key = normalize(p);
      if (!files.has(key)) {
        throw new Error("no such file: " + key);
      }
      return files.get(key);
    },

    writePlainTextToFile(p, text) {
      if (mem.failPlainWrite) {
        return false;
      }
      const key = mem.putFile(p, text);
      mem.plainWrites.push({ path: key, text: String(text) });
      return true;
    },

    writeTextToFile(p, text) {
      const key = mem.putFile(p, text);
      mem.textWrites.push({ path: key, text: String(text) });
      return true;
    },

    removeFile(p) {
      return files.delete(normalize(p));
    },
  };

  return mem;
}

/**
 * DeviceType ids that speak IOS inside Packet Tracer. Everything else (PC,
 * server, printer, laptop, access point, phone, IoT, ...) has no enable/config
 * mode, which is exactly the distinction the bridge's `hasIos` exposes.
 */
const IOS_TYPES = new Set([0, 1, 16, 26, 39]);

/** Classify a mock prompt: user, enable, config, config-if, shell, unknown. */
function promptModeOf(prompt) {
  const p = String(prompt === undefined || prompt === null ? "" : prompt).replace(/\s+$/, "");
  if (!p) {
    return "unknown";
  }
  const last = p.slice(-1);
  if (last === "#") {
    return p.includes("(config-if") ? "config-if" : p.includes("(config") ? "config" : "enable";
  }
  if (last === ">") {
    return /^[A-Za-z]:[\\/]/.test(p) ? "shell" : "user";
  }
  return "unknown";
}

/** Host part of a prompt: "R1(config)#" -> "R1", "C:\>" -> "C:\>". */
function hostOfPrompt(prompt) {
  const m = /^(.+?)(\(config[^)]*\))?[#>]$/.exec(String(prompt).trim());
  return m ? m[1] : String(prompt).trim();
}

/**
 * A mock device: ConsoleLine (with getOutput) and TerminalLine (without).
 *
 * The ConsoleLine models the CLI, not just an echo: it has a prompt state
 * machine (> -> # -> (config)# -> (config-if)# ...), so the bridge's mode and
 * readiness logic is exercised against something that behaves like IOS.
 *
 * Options:
 *   name, model, type          identity (type decides IOS-ness)
 *   hasIos                     override the type-derived IOS allowlist
 *   prompt                     starting prompt (default "R1#"/"Name#", PC "C:\>")
 *   host                       hostname in the prompt (default: from `prompt`)
 *   consoleLine: false         getConsoleLine() returns null -> TerminalLine path
 *   commandLine: false         getCommandLine() returns null -> "no command line"
 *   throwOnEnter: true         ConsoleLine.enterCommand() throws -> device.enterCommand fallback
 *   rejectLine                 string/array/function: emit "% Invalid input ..." for that line
 *   bootMs                     stay in boot (empty prompt) for this many milliseconds
 *   bootPrompt                 prompt while booting (default "")
 *   isBooting                  explicit isBooting() implementation
 */
function createDevice(devices, options) {
  const opts = options || {};
  const type = typeof opts.type === "number" ? opts.type : 8;
  const ios = typeof opts.hasIos === "boolean" ? opts.hasIos : IOS_TYPES.has(type);
  const name = String(opts.name === undefined ? "PC" : opts.name);
  const givenPrompt = typeof opts.prompt === "string" ? opts.prompt : null;
  const shellPrompt =
    givenPrompt && /^[A-Za-z]:[\\/]/.test(givenPrompt) ? givenPrompt : "C:\\>";
  const bootPrompt = typeof opts.bootPrompt === "string" ? opts.bootPrompt : "";
  const bootUntil = opts.bootMs ? Date.now() + Number(opts.bootMs) : 0;
  const startMode = ios ? promptModeOf(givenPrompt === null ? "Name#" : givenPrompt) : "shell";

  const consoleLine = {
    commands: [],
    output: "",
    flushArgs: [],
    enterCommand(cmd) {
      if (opts.throwOnEnter) {
        throw new Error("console line is busy");
      }
      const text = String(cmd);
      consoleLine.commands.push(text);
      /* freezeAfter: N commands behave; later ones are swallowed by the mock
       * (no echo, no prompt change) to exercise the per-line timeout. */
      if (typeof opts.freezeAfter === "number" && consoleLine.commands.length > opts.freezeAfter) {
        return;
      }
      consoleLine.output += text + "\n";
      if (Date.now() < bootUntil) {
        consoleLine.output += "--- System Configuration Dialog ---\n";
        return;
      }
      if (isRejected(text, consoleLine.commands.length)) {
        consoleLine.output += "% Invalid input detected at '^' marker.\n";
        return;
      }
      if (!ios) {
        if (/^ping\b/.test(text)) {
          consoleLine.output += "Reply from 10.0.0.1: bytes=32 time=1ms TTL=128\n";
        }
        return;
      }
      applyIosCommand(text);
    },
    getOutput() {
      return consoleLine.output;
    },
    getPrompt() {
      return currentPrompt();
    },
    flush(arg) {
      consoleLine.flushArgs.push(arg);
    },
  };

  const terminalLine = {
    commands: [],
    enterCommand(cmd) {
      terminalLine.commands.push(String(cmd));
    },
    getPrompt() {
      return typeof opts.terminalPrompt === "string" ? opts.terminalPrompt : ">";
    },
    flush() {
      terminalLine.flushed = true;
    },
    /* deliberately no getOutput(): TerminalLine has none in the PT API */
  };

  const device = {
    name,
    model: String(opts.model === undefined ? "" : opts.model),
    type,
    hasIos: ios,
    host: String(opts.host === undefined ? hostOfPrompt(givenPrompt === null ? name + "#" : givenPrompt) : opts.host),
    cliMode: startMode,
    consoleLine,
    terminalLine,
    consoleLineCalls: 0,
    commandLineCalls: 0,
    deviceCommands: [],
    power: null,
    skippedBoot: false,

    getName() {
      return device.name;
    },
    getModel() {
      return device.model;
    },
    getType() {
      return device.type;
    },
    getPortCount() {
      return 2;
    },
    getPortAt(i) {
      return { name: "Port" + i };
    },
    getPort(portName) {
      return { name: String(portName) };
    },
    getConsoleLine() {
      device.consoleLineCalls++;
      return opts.consoleLine === false ? null : consoleLine;
    },
    getCommandLine() {
      device.commandLineCalls++;
      return opts.commandLine === false ? null : terminalLine;
    },
    enterCommand(cmd, mode) {
      device.deviceCommands.push({ cmd: String(cmd), mode: String(mode) });
      return { first: 0, second: "" };
    },
    setName(newName) {
      devices.delete(device.name);
      device.name = String(newName);
      if (!opts.host) {
        device.host = String(newName);
      }
      devices.set(device.name, device);
    },
    setPower(on) {
      device.power = !!on;
    },
    skipBoot() {
      device.skippedBoot = true;
    },
  };

  /* Optional isBooting(): PT exposes it on CiscoDevice, but the bridge also
   * has a prompt-based fallback, so it is only defined when asked for. */
  if (typeof opts.isBooting === "function") {
    device.isBooting = opts.isBooting;
  } else if (bootUntil) {
    device.isBooting = () => Date.now() < bootUntil;
  }

  /** Prompt the device shows right now (boot state wins while it lasts). */
  function currentPrompt() {
    if (Date.now() < bootUntil) {
      return bootPrompt;
    }
    if (device.cliMode === "unknown") {
      return givenPrompt === null ? "" : givenPrompt;
    }
    if (!ios) {
      return shellPrompt;
    }
    if (device.cliMode === "user") {
      return device.host + ">";
    }
    if (device.cliMode === "config") {
      return device.host + "(config)#";
    }
    if (device.cliMode === "config-if") {
      return device.host + "(config-if)#";
    }
    return device.host + "#";
  }

  /** A line can be made unacceptable: string, list of strings, or predicate. */
  function isRejected(text, index) {
    const reject = opts.rejectLine;
    if (typeof reject === "function") {
      return !!reject(text, index);
    }
    if (Array.isArray(reject)) {
      return reject.includes(text);
    }
    return typeof reject === "string" && reject === text;
  }

  /** The smallest IOS-like mode machine the bridge's rules need. */
  function applyIosCommand(text) {
    const lower = text.toLowerCase().replace(/\s+$/, "");
    if (lower === "enable" || lower === "en") {
      device.cliMode = "enable";
      return;
    }
    if (lower === "disable") {
      device.cliMode = "user";
      return;
    }
    if (/^(configure terminal|conf t|config t|configure t)$/.test(lower)) {
      device.cliMode = "config";
      return;
    }
    if (lower === "end") {
      device.cliMode = "enable";
      return;
    }
    if (lower === "exit") {
      if (device.cliMode === "config-if") {
        device.cliMode = "config";
      } else if (device.cliMode === "config") {
        device.cliMode = "enable";
      } else if (device.cliMode === "enable") {
        device.cliMode = "user";
      }
      return;
    }
    if (lower.startsWith("interface ")) {
      device.cliMode = device.cliMode === "config" ? "config-if" : device.cliMode;
      return;
    }
    if (lower.startsWith("hostname ") && device.cliMode === "config") {
      device.host = text.split(/\s+/)[1] || device.host;
    }
  }

  /* The ConsoleLine records into device.commands, per the bridge's contract. */
  device.commands = consoleLine.commands;

  return device;
}

/**
 * Load bridge/bridge.js into a fresh vm context with a mock Packet Tracer.
 *
 * @returns {{
 *   ctx: object, mem: object, devices: Map<string, object>, logs: string[],
 *   saves: Array<{path: string, flag: boolean}>, links: object[],
 *   autoConnects: object[], watcher: object,
 *   PT_MCP_HOME: string, PT_MCP_JOBS: string, PT_MCP_RESULTS: string
 * }}
 */
function loadBridge() {
  const code = fs.readFileSync(BRIDGE_PATH, "utf8");

  const mem = createMemFS();
  const devices = new Map();
  const logs = [];
  const saves = [];
  const links = [];
  const autoConnects = [];
  /** Per-add options, consumed by logicalWorkspace.addDevice (tests push here). */
  const addDeviceOptions = [];

  const watcher = {
    events: [],
    paths: [],
    registerEvent(name, filter, callback) {
      watcher.events.push({ name, filter, callback });
      return true;
    },
    addPath(p) {
      watcher.paths.push(normalize(p));
      return true;
    },
    removePath(p) {
      const i = watcher.paths.indexOf(normalize(p));
      if (i >= 0) {
        watcher.paths.splice(i, 1);
      }
      return true;
    },
  };

  let autoIndex = 0;

  const logicalWorkspace = {
    addDevice(type, model, x, y) {
      const name = autoIndex === 0 ? "R-auto" : "R-auto-" + (autoIndex + 1);
      autoIndex++;
      const extra = addDeviceOptions.shift() || {};
      const device = createDevice(
        devices,
        Object.assign({}, extra, { name, model: String(model), type }),
      );
      device.x = x;
      device.y = y;
      devices.set(name, device);
      return name;
    },
    removeDevice(name) {
      return devices.delete(String(name));
    },
    createLink(a, aPort, b, bPort, cable) {
      links.push({ a, aPort, b, bPort, cable });
      return true;
    },
    autoConnectDevices(a, b) {
      autoConnects.push({ a, b });
      return true;
    },
  };

  const appWindow = {
    getVersion() {
      return "9.0.1-test";
    },
    writeToPT(text) {
      logs.push(String(text));
      return true;
    },
    getActiveWorkspace() {
      return { getLogicalWorkspace: () => logicalWorkspace };
    },
    fileSaveAsNoPrompt(p, flag) {
      saves.push({ path: String(p), flag: !!flag });
      return true;
    },
  };

  const network = {
    getDevice(name) {
      return devices.get(String(name)) || null;
    },
    getDeviceAt(i) {
      return Array.from(devices.values())[i] || null;
    },
    getDeviceCount() {
      return devices.size;
    },
    getLinkCount() {
      return links.length;
    },
  };

  const systemFileManager = {
    directoryExists: (p) => mem.directoryExists(p),
    makeDirectory: (p) => mem.makeDirectory(p),
    getFilesInDirectory: (p) => mem.getFilesInDirectory(p),
    getFileContents: (p) => mem.getFileContents(p),
    writePlainTextToFile: (p, text) => mem.writePlainTextToFile(p, text),
    writeTextToFile: (p, text) => mem.writeTextToFile(p, text),
    removeFile: (p) => mem.removeFile(p),
    getFileWatcher: () => watcher,
  };

  const ipc = {
    appWindow: () => appWindow,
    network: () => network,
    systemFileManager: () => systemFileManager,
  };

  const sandboxConsole = {
    log: (msg) => logs.push(String(msg)),
    warn: (msg) => logs.push(String(msg)),
    error: (msg) => logs.push(String(msg)),
  };

  const sandbox = { ipc, console: sandboxConsole };
  vm.runInNewContext(code, sandbox, { filename: BRIDGE_PATH });
  const ctx = sandbox;

  return {
    ctx,
    mem,
    devices,
    logs,
    saves,
    links,
    autoConnects,
    addDeviceOptions,
    watcher,
    PT_MCP_HOME: ctx.PT_MCP_HOME,
    PT_MCP_JOBS: ctx.PT_MCP_JOBS,
    PT_MCP_RESULTS: ctx.PT_MCP_RESULTS,
  };
}

/**
 * Register one mock device in a loaded bridge context, with options (see
 * createDevice): useful for the TerminalLine-only, throwing-line and
 * no-command-line cases that `add_device` cannot produce.
 *
 * @returns {object} the device mock (already in `iso.devices`)
 */
function addMockDevice(iso, options) {
  const device = createDevice(iso.devices, options || {});
  iso.devices.set(device.name, device);
  return device;
}

module.exports = { loadBridge, addMockDevice, BRIDGE_PATH };
