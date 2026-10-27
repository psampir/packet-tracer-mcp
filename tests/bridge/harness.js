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
 *     not — that difference is the point of `enterLines()`)
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
 * A mock device: ConsoleLine (with getOutput) and TerminalLine (without).
 *
 * Options:
 *   name, model, type          identity
 *   consoleLine: false         getConsoleLine() returns null -> TerminalLine path
 *   commandLine: false         getCommandLine() returns null -> "no command line"
 *   throwOnEnter: true         ConsoleLine.enterCommand() throws -> device.enterCommand fallback
 *   prompt                     ConsoleLine prompt (default "R1#")
 */
function createDevice(devices, options) {
  const opts = options || {};
  const consoleLine = {
    commands: [],
    output: "",
    prompt: typeof opts.prompt === "undefined" ? "R1#" : String(opts.prompt),
    flushArgs: [],
    enterCommand(cmd) {
      if (opts.throwOnEnter) {
        throw new Error("console line is busy");
      }
      consoleLine.commands.push(String(cmd));
      consoleLine.output += String(cmd) + "\n";
    },
    getOutput() {
      return consoleLine.output;
    },
    getPrompt() {
      return consoleLine.prompt;
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
      return ">";
    },
    flush() {
      terminalLine.flushed = true;
    },
    /* deliberately no getOutput(): TerminalLine has none in the PT API */
  };

  const device = {
    name: String(opts.name === undefined ? "PC" : opts.name),
    model: String(opts.model === undefined ? "" : opts.model),
    type: typeof opts.type === "number" ? opts.type : 8,
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
      devices.set(device.name, device);
    },
    setPower(on) {
      device.power = !!on;
    },
    skipBoot() {
      device.skippedBoot = true;
    },
  };

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
      const device = createDevice(devices, { name, model: String(model), type });
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
