"use strict";

/*
 * Tests for bridge/bridge.js -- the ES5 script that runs inside Packet Tracer.
 *
 * bridge.js is loaded unmodified into a `node:vm` context by ./harness.js, which
 * fakes the `ipc.*` API and the SystemFileManager. Nothing here needs Packet
 * Tracer, a network or credentials.
 *
 * Note on realms: values returned from the vm context carry that realm's
 * prototypes, so compare them through `plain()` (a JSON round trip) instead of
 * assert.deepStrictEqual.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { loadBridge, addMockDevice } = require("./harness.js");

/** Copy a vm-realm value into a plain this-realm value. */
const plain = (value) => JSON.parse(JSON.stringify(value));

const jobPath = (iso, fileName) => iso.PT_MCP_JOBS + "/" + fileName;
const resultPath = (iso, id) => iso.PT_MCP_RESULTS + "/" + id + ".json";

/** Seed a job file directly (works even while writePlainTextToFile fails). */
function putJob(iso, fileName, body) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  iso.mem.putFile(jobPath(iso, fileName), text);
}

function readResult(iso, id) {
  return JSON.parse(iso.mem.getFileContents(resultPath(iso, id)));
}

// --------------------------------------------------------------------------
// loading
// --------------------------------------------------------------------------
test("loading bridge.js is side-effect-free", () => {
  const iso = loadBridge();

  assert.equal(typeof iso.ctx.dispatch, "function");
  assert.equal(typeof iso.ctx.processPending, "function");
  assert.equal(iso.ctx.PT_MCP_EVALS, 1, "first evaluation must not run main()");
  assert.deepEqual(iso.logs, [], "no PT log output before main()");
  assert.equal(iso.devices.size, 0);
  assert.equal(iso.ctx.PT_MCP_HOME, "/home/test/pt/pt-mcp");
  assert.equal(iso.PT_MCP_JOBS, iso.ctx.PT_MCP_HOME + "/jobs");
  assert.equal(iso.PT_MCP_RESULTS, iso.ctx.PT_MCP_HOME + "/results");
});

// --------------------------------------------------------------------------
// pure helpers
// --------------------------------------------------------------------------
test("resolveDeviceType accepts enum names, ints and case variants", () => {
  const { ctx } = loadBridge();

  assert.equal(ctx.resolveDeviceType("eRouter"), 0);
  assert.equal(ctx.resolveDeviceType("eSwitch"), 1);
  assert.equal(ctx.resolveDeviceType("ePc"), 8);
  assert.equal(ctx.resolveDeviceType(8), 8);
  assert.equal(ctx.resolveDeviceType("8"), 8);
  assert.equal(ctx.resolveDeviceType("erouter"), 0, "case-insensitive fallback");
  assert.equal(ctx.resolveDeviceType("eswitch"), 1);
});

test("resolveDeviceType throws on unknown and missing types", () => {
  const { ctx } = loadBridge();

  assert.throws(() => ctx.resolveDeviceType("eNope"), /unknown device type 'eNope'/);
  assert.throws(() => ctx.resolveDeviceType(null), /'type' is required/);
  assert.throws(() => ctx.resolveDeviceType(undefined), /'type' is required/);
});

test("resolveCable maps friendly names to CONNECT_TYPES", () => {
  const { ctx } = loadBridge();

  assert.equal(ctx.resolveCable("eCopperGigabitEthernet"), 8100);
  assert.equal(ctx.resolveCable("fiber"), 8103);
  assert.equal(ctx.resolveCable("serial"), 8106);
  assert.equal(ctx.resolveCable(8101), 8101);
  assert.equal(ctx.resolveCable("8106"), 8106);
  assert.equal(ctx.resolveCable(""), 8100, "empty means straight-through");
  assert.equal(ctx.resolveCable(undefined), 8100);
  assert.equal(ctx.resolveCable("ETHERNET_CROSS"), 8101, "canonical enum name");
});

test("resolveCable passes an unknown cable string through unchanged", () => {
  const iso = loadBridge();

  assert.equal(iso.ctx.resolveCable("banana"), "banana");
  assert.ok(iso.logs.some((line) => line.includes("unknown cable 'banana'")));
});

test("baseName and tail handle both separators and short input", () => {
  const { ctx } = loadBridge();

  assert.equal(ctx.baseName("/a/b/c.json"), "c.json");
  assert.equal(ctx.baseName("C:\\labs\\r1.pkt"), "r1.pkt");
  assert.equal(ctx.baseName("plain.json"), "plain.json");
  assert.equal(ctx.baseName(""), "");

  assert.equal(ctx.tail("abcdef", 3), "def");
  assert.equal(ctx.tail("ab", 3), "ab");
  assert.equal(ctx.tail(null, 3), "");
  assert.equal(ctx.tail(12345, 2), "45");
});

test("safeId sanitises everything outside [A-Za-z0-9_.-]", () => {
  const { ctx } = loadBridge();

  assert.equal(ctx.safeId("../x y"), ".._x_y");
  assert.equal(ctx.safeId("job-1_a.b"), "job-1_a.b");
  assert.equal(ctx.safeId("a/b\\c:d"), "a_b_c_d");
  assert.equal(ctx.safeId(null), "");
});

test("safeStringify copes with undefined and circular values", () => {
  const { ctx } = loadBridge();

  assert.equal(ctx.safeStringify({ a: 1 }), '{"a":1}');
  assert.equal(ctx.safeStringify(undefined), "undefined");

  const circular = { name: "loop" };
  circular.self = circular;
  assert.equal(ctx.safeStringify(circular), "[object Object]", "falls back to String()");
});

// --------------------------------------------------------------------------
// dispatch: observe
// --------------------------------------------------------------------------
test("ping reports the PT version and device count", () => {
  const iso = loadBridge();
  addMockDevice(iso, { name: "R1", model: "2911", type: 0 });

  assert.deepEqual(plain(iso.ctx.dispatch("ping", {})), {
    version: "9.0.1-test",
    deviceCount: 1,
  });
});

test("list_devices reports name/type/typeName/model and the link count", () => {
  const iso = loadBridge();
  iso.ctx.dispatch("add_device", { type: "eRouter", model: "2911" });
  iso.ctx.dispatch("add_device", { type: "ePc", model: "PC-PT" });
  iso.ctx.dispatch("link", {
    a: "R-auto",
    aPort: "GigabitEthernet0/0",
    b: "R-auto-2",
    bPort: "FastEthernet0",
    cable: "copper",
  });

  const result = plain(iso.ctx.dispatch("list_devices", {}));

  assert.equal(result.devices.length, 2);
  assert.deepEqual(result.devices[0], {
    name: "R-auto",
    type: 0,
    typeName: "eRouter",
    model: "2911",
  });
  assert.deepEqual(result.devices[1], {
    name: "R-auto-2",
    type: 8,
    typeName: "ePc",
    model: "PC-PT",
  });
  assert.equal(result.links, 1);
});

test("list_devices on an empty workspace returns empty devices and no links", () => {
  const { ctx } = loadBridge();
  assert.deepEqual(plain(ctx.dispatch("list_devices")), { devices: [], links: 0 });
});

// --------------------------------------------------------------------------
// dispatch: build
// --------------------------------------------------------------------------
test("add_device without a name keeps PT's auto name", () => {
  const iso = loadBridge();

  const result = plain(iso.ctx.dispatch("add_device", { type: "eSwitch", model: "2960" }));

  assert.deepEqual(result, { name: "R-auto", ready: true });
  assert.ok(iso.devices.has("R-auto"));
  assert.equal(iso.devices.get("R-auto").type, 1);
  assert.equal(iso.devices.get("R-auto").getName(), "R-auto");
});

test("add_device with a name calls setName and returns the new name", () => {
  const iso = loadBridge();

  const result = plain(
    iso.ctx.dispatch("add_device", { name: "R1", type: 0, model: "2911", x: 10, y: 20 }),
  );

  assert.deepEqual(result, { name: "R1", ready: true });
  assert.ok(iso.devices.has("R1"));
  assert.ok(!iso.devices.has("R-auto"), "the auto name is gone after setName");
  assert.equal(iso.devices.get("R1").x, 10);
  assert.equal(iso.devices.get("R1").y, 20);
});

test("add_device defaults coordinates when they are absent or not numeric", () => {
  const iso = loadBridge();

  iso.ctx.dispatch("add_device", { type: "eRouter", model: "2911" });
  const device = iso.devices.get("R-auto");

  assert.equal(device.x, 100);
  assert.equal(device.y, 100);
});

test("remove_device reports whether the device existed", () => {
  const iso = loadBridge();
  addMockDevice(iso, { name: "R1", model: "2911", type: 0 });

  assert.deepEqual(plain(iso.ctx.dispatch("remove_device", { name: "R1" })), { removed: true });
  assert.equal(iso.devices.size, 0);
  assert.deepEqual(plain(iso.ctx.dispatch("remove_device", { name: "R1" })), { removed: false });
});

test("link maps the cable name to a CONNECT_TYPES integer", () => {
  const iso = loadBridge();

  const result = plain(
    iso.ctx.dispatch("link", {
      a: "R1",
      aPort: "GigabitEthernet0/0",
      b: "R2",
      bPort: "GigabitEthernet0/1",
      cable: "fiber",
    }),
  );

  assert.deepEqual(result, { linked: true });
  assert.deepEqual(plain(iso.links[0]), {
    a: "R1",
    aPort: "GigabitEthernet0/0",
    b: "R2",
    bPort: "GigabitEthernet0/1",
    cable: 8103,
  });
});

test("link defaults to straight-through copper when no cable is given", () => {
  const iso = loadBridge();
  iso.ctx.dispatch("link", { a: "R1", aPort: "Gi0/0", b: "SW1", bPort: "Gi0/1" });

  assert.equal(iso.links[0].cable, 8100);
});

test("autolink lets PT pick the ports", () => {
  const iso = loadBridge();

  assert.deepEqual(plain(iso.ctx.dispatch("autolink", { a: "R1", b: "SW1" })), { ok: true });
  assert.deepEqual(plain(iso.autoConnects), [{ a: "R1", b: "SW1" }]);
});

// --------------------------------------------------------------------------
// dispatch: CLI
// --------------------------------------------------------------------------
test("configure prefers ConsoleLine and returns per-line results and the final prompt", () => {
  const iso = loadBridge();
  const device = addMockDevice(iso, { name: "R1", model: "2911", type: 0, prompt: "R1#" });
  const lines = ["hostname R1", "interface GigabitEthernet0/0", "no shutdown"];
  const sent = ["configure terminal"].concat(lines, ["end"]);

  const result = plain(iso.ctx.dispatch("configure", { device: "R1", lines }));

  assert.deepEqual(device.commands, sent, "mode wrappers added, every line entered in order");
  assert.equal(device.consoleLineCalls, 1);
  assert.equal(device.commandLineCalls, 0, "TerminalLine has no getOutput()");
  assert.equal(result.ok, true);
  assert.equal(result.prompt, "R1#");
  assert.equal(result.mode, "auto");
  assert.equal(result.effective_mode, "config");
  assert.equal(result.stopped_on, null);
  assert.deepEqual(
    result.lines.map((entry) => entry.line),
    sent,
  );
  assert.deepEqual(
    result.lines.map((entry) => entry.output),
    sent.map((line) => line + "\n"),
    "each line reports its own delta",
  );
  assert.equal(result.output, sent.map((l) => l + "\n").join(""), "deltas joined");
  assert.equal(device.consoleLine.flushArgs[0], 10, "flush(10) while waiting per line");
});

test("configure falls back to TerminalLine (which has no getOutput)", () => {
  const iso = loadBridge();
  const device = addMockDevice(iso, {
    name: "SW1",
    model: "2960",
    type: 1,
    consoleLine: false,
    terminalPrompt: "SW1#",
  });

  const result = plain(iso.ctx.dispatch("configure", { device: "SW1", lines: ["show run"] }));

  assert.equal(device.consoleLineCalls, 1);
  assert.equal(device.commandLineCalls, 1);
  assert.deepEqual(device.terminalLine.commands, ["show run"]);
  assert.equal(result.output, "", "no getOutput() means no delta");
  assert.equal(result.prompt, "SW1#");
  assert.equal(result.effective_mode, "exec", "a show line at enable stays exec");
});

test("configure reports a device with no command line at all", () => {
  const iso = loadBridge();
  addMockDevice(iso, { name: "HUB", type: 4, consoleLine: false, commandLine: false });

  assert.throws(
    () => iso.ctx.dispatch("configure", { device: "HUB", lines: ["enable"] }),
    /no command line available on device/,
  );
});

test("configure falls back to device.enterCommand when the line throws", () => {
  const iso = loadBridge();
  const device = addMockDevice(iso, { name: "R1", type: 0, throwOnEnter: true });

  const result = plain(iso.ctx.dispatch("configure", { device: "R1", lines: ["enable", "end"] }));

  assert.deepEqual(plain(device.deviceCommands), [
    { cmd: "enable", mode: "" },
    { cmd: "end", mode: "" },
  ]);
  assert.equal(result.output, "", "output stays empty");
  assert.equal(result.prompt, "R1#", "prompt is still read");
  assert.equal(result.ok, true);
});

test("configure rejects an empty command list", () => {
  const iso = loadBridge();
  addMockDevice(iso, { name: "R1", type: 0 });

  assert.throws(() => iso.ctx.dispatch("configure", { device: "R1", lines: [] }), /no lines supplied/);
});

test("show runs one command through the exec path", () => {
  const iso = loadBridge();
  const device = addMockDevice(iso, { name: "R1", type: 0, prompt: "R1#" });

  const result = plain(iso.ctx.dispatch("show", { device: "R1", command: "show ip interface brief" }));

  assert.deepEqual(device.commands, ["show ip interface brief"]);
  assert.equal(result.effective_mode, "exec");
  assert.match(result.output, /show ip interface brief/);
});

// --------------------------------------------------------------------------
// dispatch: power / boot / save / eval
// --------------------------------------------------------------------------
test("power toggles the device power state", () => {
  const iso = loadBridge();
  const device = addMockDevice(iso, { name: "R1", type: 0 });

  assert.deepEqual(plain(iso.ctx.dispatch("power", { device: "R1", on: true })), { ok: true });
  assert.equal(device.power, true);

  iso.ctx.dispatch("power", { device: "R1", on: false });
  assert.equal(device.power, false, "a falsy 'on' powers the device off");
});

test("skip_boot skips the boot sequence", () => {
  const iso = loadBridge();
  const device = addMockDevice(iso, { name: "R1", type: 0 });

  assert.deepEqual(plain(iso.ctx.dispatch("skip_boot", { device: "R1" })), { ok: true });
  assert.equal(device.skippedBoot, true);
});

test("save calls fileSaveAsNoPrompt with the requested path", () => {
  const iso = loadBridge();

  const result = plain(iso.ctx.dispatch("save", { path: "/tmp/lab.pkt" }));

  assert.deepEqual(result, { saved: true, path: "/tmp/lab.pkt" });
  assert.deepEqual(plain(iso.saves), [{ path: "/tmp/lab.pkt", flag: false }]);
});

test("eval returns the stringified value of arbitrary JS", () => {
  const iso = loadBridge();

  assert.deepEqual(plain(iso.ctx.dispatch("eval", { js: "1 + 1" })), { value: "2" });
  assert.deepEqual(plain(iso.ctx.dispatch("eval", { js: "({a:1})" })), { value: '{"a":1}' });
});

test("an unknown op throws", () => {
  const { ctx } = loadBridge();
  assert.throws(() => ctx.dispatch("teleport", {}), /unknown op: teleport/);
});

test("ops on a missing device throw 'device not found'", () => {
  const { ctx } = loadBridge();

  assert.throws(() => ctx.dispatch("power", { device: "ghost", on: true }), /device not found: ghost/);
  assert.throws(() => ctx.dispatch("show", { device: "ghost", command: "show run" }), /device not found/);
});

// --------------------------------------------------------------------------
// job processing (the real transport logic)
// --------------------------------------------------------------------------
test("processPending turns a job file into a result file", () => {
  const iso = loadBridge();
  putJob(iso, "j1.json", { id: "j1", op: "ping", args: {} });

  iso.ctx.processPending();

  assert.ok(iso.mem.hasFile(resultPath(iso, "j1")), "results/j1.json written");
  assert.ok(!iso.mem.hasFile(jobPath(iso, "j1.json")), "job consumed");

  const result = readResult(iso, "j1");
  assert.equal(result.id, "j1");
  assert.equal(result.ok, true);
  assert.equal(result.result.version, "9.0.1-test");
  assert.equal(result.result.deviceCount, 0);
});

test("processPending processes several jobs in one scan", () => {
  const iso = loadBridge();
  putJob(iso, "a.json", { id: "a", op: "ping", args: {} });
  putJob(iso, "b.json", { id: "b", op: "list_devices", args: {} });
  putJob(iso, "c.json", { id: "c", op: "eval", args: { js: "21 * 2" } });

  iso.ctx.processPending();

  assert.deepEqual(readResult(iso, "a").result, { version: "9.0.1-test", deviceCount: 0 });
  assert.deepEqual(readResult(iso, "b").result, { devices: [], links: 0 });
  assert.deepEqual(readResult(iso, "c").result, { value: "42" });
  assert.equal(iso.mem.getFilesInDirectory(iso.PT_MCP_JOBS).length, 0);
});

test("a job id is never processed twice (PT_MCP_SEEN dedupe)", () => {
  const iso = loadBridge();
  putJob(iso, "j1.json", { id: "j1", op: "ping", args: {} });
  iso.ctx.processPending();
  assert.ok(iso.mem.hasFile(resultPath(iso, "j1")));

  // Same id again: the result is cleaned up (as the server would) and the job
  // re-appears, but the bridge must not run it a second time.
  iso.mem.removeFile(resultPath(iso, "j1"));
  putJob(iso, "j1.json", { id: "j1", op: "ping", args: {} });

  iso.ctx.processPending();

  assert.ok(!iso.mem.hasFile(resultPath(iso, "j1")), "no result recreated");
  assert.equal(iso.ctx.PT_MCP_SEEN.j1, true, "the id is remembered for the session");
});

test("a job with invalid JSON answers ok:false and is removed", () => {
  const iso = loadBridge();
  putJob(iso, "bad.json", "{ this is not json");

  iso.ctx.processPending();

  const result = readResult(iso, "bad");
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid job JSON/);
  assert.ok(!iso.mem.hasFile(jobPath(iso, "bad.json")));
});

test("a job that throws answers ok:false with the message", () => {
  const iso = loadBridge();
  putJob(iso, "cfg.json", {
    id: "cfg",
    op: "configure",
    args: { device: "ghost", lines: ["enable", "end"] },
  });

  iso.ctx.processPending();

  const result = readResult(iso, "cfg");
  assert.equal(result.ok, false);
  assert.match(result.error, /device not found: ghost/);
  assert.ok(!iso.mem.hasFile(jobPath(iso, "cfg.json")), "failing jobs are not retried forever");
  assert.ok(iso.logs.some((line) => line.includes("error: configure (cfg)")));
});

test("result files are named from the safeId of the job id", () => {
  const iso = loadBridge();
  putJob(iso, "weird.json", { id: "../escape", op: "ping", args: {} });

  iso.ctx.processPending();

  assert.equal(readResult(iso, ".._escape").ok, true);
});

test("writeResult falls back to writeTextToFile when plain text write fails", () => {
  const iso = loadBridge();
  putJob(iso, "j1.json", { id: "j1", op: "ping", args: {} });
  iso.mem.failPlainWrite = true;

  iso.ctx.processPending();

  assert.ok(iso.mem.hasFile(resultPath(iso, "j1")), "the result still lands");
  assert.equal(readResult(iso, "j1").ok, true);
  assert.ok(
    iso.mem.textWrites.some((write) => write.path === resultPath(iso, "j1")),
    "written through writeTextToFile",
  );
  assert.ok(
    iso.mem.plainWrites.every((write) => write.path !== resultPath(iso, "j1")),
    "writePlainTextToFile reported failure, so it is not in the success log",
  );
});

test("a job header without op or args does not crash the scan", () => {
  const iso = loadBridge();
  putJob(iso, "bare.json", { id: "bare" });

  iso.ctx.processPending();

  const result = readResult(iso, "bare");
  assert.equal(result.ok, false);
  assert.match(result.error, /unknown op/);
});

// --------------------------------------------------------------------------
// watcher / main
// --------------------------------------------------------------------------
test("main creates the directories, registers the watcher and drains jobs", () => {
  const iso = loadBridge();
  putJob(iso, "j1.json", { id: "j1", op: "ping", args: {} });
  assert.ok(!iso.mem.directoryExists(iso.PT_MCP_JOBS), "nothing exists before main()");

  iso.ctx.main();

  assert.ok(iso.mem.directoryExists(iso.PT_MCP_HOME));
  assert.ok(iso.mem.directoryExists(iso.PT_MCP_JOBS));
  assert.ok(iso.mem.directoryExists(iso.PT_MCP_RESULTS));

  assert.deepEqual(
    iso.watcher.events.map((event) => event.name),
    ["fileChanged", "directoryChanged"],
  );
  assert.deepEqual(iso.watcher.paths, [iso.PT_MCP_JOBS]);
  assert.ok(iso.logs.some((line) => line.includes("bridge ready")));

  assert.ok(iso.mem.hasFile(resultPath(iso, "j1")));
  assert.ok(!iso.mem.hasFile(jobPath(iso, "j1.json")));
  assert.equal(iso.ctx.PT_MCP_INITED, true);
});

test("main is idempotent: a second call does not re-register the watcher", () => {
  const iso = loadBridge();

  iso.ctx.main();
  iso.ctx.main();

  assert.equal(iso.watcher.events.length, 2);
  assert.deepEqual(iso.watcher.paths, [iso.PT_MCP_JOBS]);
});

test("a watcher event triggers a scan", () => {
  const iso = loadBridge();
  iso.ctx.main();

  putJob(iso, "later.json", { id: "later", op: "ping", args: {} });
  iso.watcher.events[0].callback();

  assert.equal(readResult(iso, "later").ok, true);
});
