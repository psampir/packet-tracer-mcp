"use strict";

/*
 * CLI semantics of bridge/bridge.js: prompt detection, the auto/raw/exec/config
 * mode rules (PTM-02), line-by-line sending with error reporting (PTM-06),
 * readiness state and dry-run planning (PTM-16).
 *
 * The harness models an IOS prompt state machine, so these tests assert both
 * the *decision* (what the bridge would type) and the *effect* (what the mock
 * console saw). Nothing here needs Packet Tracer.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { loadBridge, addMockDevice } = require("./harness.js");

/** Copy a vm-realm value into a plain this-realm value. */
const plain = (value) => JSON.parse(JSON.stringify(value));

/** Let real time pass (the bridge's per-line wait is real, not mocked). */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A device whose console prompt is exactly what IOS would show. */
function device(iso, options) {
  return addMockDevice(iso, options);
}

// --------------------------------------------------------------------------
// PTM-01: per-device state / readiness
// --------------------------------------------------------------------------
test("get_state describes an IOS device at the enable prompt", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", model: "2911", type: 0, prompt: "R1#" });

  assert.deepEqual(plain(iso.ctx.dispatch("get_state", { device: "R1" })), {
    name: "R1",
    prompt: "R1#",
    mode: "enable",
    booting: false,
    hasIos: true,
    type: 0,
    model: "2911",
  });
});

test("get_state classifies every prompt shape", () => {
  const iso = loadBridge();
  device(iso, { name: "PC1", type: 8, prompt: "C:\\>" });
  device(iso, { name: "SW1", type: 1, prompt: "Switch>" });
  device(iso, { name: "S1", type: 1, prompt: "S1(config)#" });
  device(iso, { name: "S2", type: 1, prompt: "S2(config-if)#" });
  device(iso, { name: "R9", type: 0, prompt: "--- System Configuration Dialog ---" });

  const state = (name) => plain(iso.ctx.dispatch("get_state", { device: name }));

  assert.equal(state("PC1").mode, "shell");
  assert.equal(state("PC1").hasIos, false);
  assert.equal(state("PC1").prompt, "C:\\>");
  assert.equal(state("SW1").mode, "user");
  assert.equal(state("S1").mode, "config");
  assert.equal(state("S2").mode, "config", "(config-if)# is still config");
  assert.equal(state("R9").mode, "unknown");
  assert.equal(state("R9").booting, true, "the config dialog is not a prompt");
});

test("hasIos is the IOS device-type allowlist, nothing else", () => {
  const iso = loadBridge();
  const ios = { eRouter: 0, eSwitch: 1, eMultiLayerSwitch: 16, eASA: 26, eWirelessLanController: 39 };
  const noIos = {
    eAccessPoint: 7,
    ePc: 8,
    eServer: 9,
    ePrinter: 10,
    eLaptop: 17,
    eIpPhone: 12,
    eThing: 36,
  };

  for (const [name, type] of Object.entries(ios)) {
    device(iso, { name, type, prompt: name + "#" });
    assert.equal(plain(iso.ctx.dispatch("get_state", { device: name })).hasIos, true, name);
  }
  for (const [name, type] of Object.entries(noIos)) {
    device(iso, { name, type, prompt: "C:\\>" });
    assert.equal(plain(iso.ctx.dispatch("get_state", { device: name })).hasIos, false, name);
  }
});

test("get_state reports a booting device and then a ready one", async () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1#", bootMs: 60 });

  const booting = plain(iso.ctx.dispatch("get_state", { device: "R1" }));
  assert.equal(booting.booting, true);
  assert.equal(booting.prompt, "");
  assert.equal(booting.mode, "unknown");

  await sleep(90);

  const ready = plain(iso.ctx.dispatch("get_state", { device: "R1" }));
  assert.equal(ready.booting, false);
  assert.equal(ready.prompt, "R1#");
  assert.equal(ready.mode, "enable");
});

test("isBooting() wins over a prompt that already looks ready", () => {
  const iso = loadBridge();
  device(iso, {
    name: "R1",
    type: 0,
    prompt: "R1#",
    isBooting: () => true,
  });

  const state = plain(iso.ctx.dispatch("get_state", { device: "R1" }));
  assert.equal(state.prompt, "R1#");
  assert.equal(state.booting, true, "device.isBooting() is authoritative when present");
});

test("get_state works for a device with no console line and still rejects ghosts", () => {
  const iso = loadBridge();
  device(iso, { name: "HUB", type: 4, consoleLine: false, commandLine: false });

  const state = plain(iso.ctx.dispatch("get_state", { device: "HUB" }));
  assert.equal(state.prompt, "");
  assert.equal(state.mode, "unknown");
  assert.equal(state.booting, true);

  assert.throws(() => iso.ctx.dispatch("get_state", { device: "ghost" }), /device not found: ghost/);
});

test("add_device reports readiness without waiting", () => {
  const iso = loadBridge();

  iso.addDeviceOptions.push({ bootMs: 80 });
  const booting = plain(iso.ctx.dispatch("add_device", { type: "eRouter", model: "2911" }));
  assert.deepEqual(booting, { name: "R-auto", ready: false });

  const ready = plain(iso.ctx.dispatch("add_device", { type: "ePc", model: "PC-PT" }));
  assert.deepEqual(ready, { name: "R-auto-2", ready: true });
});

// --------------------------------------------------------------------------
// PTM-02 / PTM-16: plan (dry run) and mode rules
// --------------------------------------------------------------------------
test("plan on a PC returns the lines untouched", () => {
  const iso = loadBridge();
  const pc = device(iso, { name: "PC-Users", type: 8, prompt: "C:\\>" });

  const plan = plain(iso.ctx.dispatch("plan", { device: "PC-Users", lines: ["ping 8.8.8.8"] }));

  assert.deepEqual(plan, {
    lines: ["ping 8.8.8.8"],
    mode: "auto",
    effective_mode: "raw",
    prompt: "C:\\>",
    booting: false,
  });
  assert.deepEqual(pc.commands, [], "plan must never touch the console");
});

test("plan on a booting device is provisional and says so", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1#", bootMs: 200 });

  const plan = plain(iso.ctx.dispatch("plan", { device: "R1", lines: ["hostname R1"] }));

  assert.equal(plan.booting, true);
  assert.equal(plan.prompt, "");
  assert.deepEqual(plan.lines, ["hostname R1"], "no prompt yet, so nothing is wrapped");
});

test("plan does not append a second end to a block that ends with one", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1#" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "R1", lines: ["hostname R1", "end"] }),
  );

  assert.deepEqual(plan.lines, ["configure terminal", "hostname R1", "end"]);
});

test("plan on a PC ignores a requested config mode (no IOS, no insertions)", () => {
  const iso = loadBridge();
  device(iso, { name: "PC-Users", type: 8, prompt: "C:\\>" });

  const plan = plain(
    iso.ctx.dispatch("plan", {
      device: "PC-Users",
      lines: ["ipconfig"],
      mode: "config",
    }),
  );

  assert.deepEqual(plan.lines, ["ipconfig"]);
  assert.equal(plan.effective_mode, "raw");
});

test("plan from user exec adds enable, configure terminal and end", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch>" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "Switch", lines: ["hostname S1"] }),
  );

  assert.deepEqual(plan.lines, ["enable", "configure terminal", "hostname S1", "end"]);
  assert.equal(plan.effective_mode, "config");
});

test("plan from enable mode configures without a redundant enable", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch#" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "Switch", lines: ["hostname S1"] }),
  );

  assert.deepEqual(plan.lines, ["configure terminal", "hostname S1", "end"]);
});

test("plan from config mode adds no prefix", () => {
  const iso = loadBridge();
  device(iso, { name: "S1", type: 1, prompt: "S1(config)#" });

  const plan = plain(iso.ctx.dispatch("plan", { device: "S1", lines: ["ip routing"] }));

  assert.deepEqual(plan.lines, ["ip routing", "end"]);
  assert.equal(plan.effective_mode, "config");
});

test("plan with keep_session never appends end", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch>" });

  const plan = plain(
    iso.ctx.dispatch("plan", {
      device: "Switch",
      lines: ["hostname S1"],
      keep_session: true,
    }),
  );

  assert.deepEqual(plan.lines, ["enable", "configure terminal", "hostname S1"]);
});

test("plan in auto mode keeps a show line in exec mode", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch#" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "Switch", lines: ["show vlan brief"] }),
  );

  assert.deepEqual(plan.lines, ["show vlan brief"], "no configure terminal, no end");
  assert.equal(plan.effective_mode, "exec");
});

test("plan in raw mode sends exactly what was given", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch>" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "Switch", lines: ["no ip domain-lookup"], mode: "raw" }),
  );

  assert.deepEqual(plan.lines, ["no ip domain-lookup"]);
  assert.equal(plan.mode, "raw");
  assert.equal(plan.effective_mode, "raw");
});

test("plan in exec mode enables but never configures", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch>" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "Switch", lines: ["show run"], mode: "exec" }),
  );

  assert.deepEqual(plan.lines, ["enable", "show run"]);
  assert.equal(plan.effective_mode, "exec");
});

test("plan in exec mode leaves config before the command", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1(config)#" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "R1", lines: ["show ip interface brief"], mode: "exec" }),
  );

  assert.deepEqual(plan.lines, ["end", "show ip interface brief"]);
  assert.equal(plan.effective_mode, "exec");
});

test("exec from config ends config then runs the command", () => {
  const iso = loadBridge();
  const r1 = device(iso, { name: "R1", type: 0, prompt: "R1(config)#" });

  const result = plain(
    iso.ctx.dispatch("exec", { device: "R1", command: "show ip interface brief" }),
  );

  assert.deepEqual(r1.commands, ["end", "show ip interface brief"]);
  assert.equal(result.prompt, "R1#");
  assert.equal(result.ok, true);
});

test("plan in config mode from user exec inserts both wrappers", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1>" });

  const plan = plain(
    iso.ctx.dispatch("plan", { device: "R1", lines: ["hostname R1"], mode: "config" }),
  );

  assert.deepEqual(plan.lines, ["enable", "configure terminal", "hostname R1", "end"]);
});

test("plan rejects an unknown mode with the valid list", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1#" });

  assert.throws(
    () => iso.ctx.dispatch("plan", { device: "R1", lines: ["hostname R1"], mode: "turbo" }),
    /unknown mode 'turbo'; valid: auto, raw, exec, config/,
  );
});

test("plan rejects an empty line list and a missing device", () => {
  const iso = loadBridge();
  device(iso, { name: "R1", type: 0, prompt: "R1#" });

  assert.throws(() => iso.ctx.dispatch("plan", { device: "R1", lines: [] }), /no lines supplied/);
  assert.throws(
    () => iso.ctx.dispatch("plan", { device: "ghost", lines: ["hostname R1"] }),
    /device not found: ghost/,
  );
});

// --------------------------------------------------------------------------
// PTM-02: configure actually sends the planned sequence
// --------------------------------------------------------------------------
test("configure on a PC sends one line: no enable, no configure, no end", () => {
  const iso = loadBridge();
  const pc = device(iso, { name: "PC-Users", type: 8, prompt: "C:\\>" });

  const result = plain(
    iso.ctx.dispatch("configure", { device: "PC-Users", lines: ["ping 192.168.20.10"] }),
  );

  assert.deepEqual(pc.commands, ["ping 192.168.20.10"], "exactly the line that was asked for");
  assert.equal(result.effective_mode, "raw");
  assert.doesNotMatch(result.output, /Invalid Command/);
  assert.doesNotMatch(result.output, /configure terminal/);
});

test("configure at Switch# keeps a show command in exec mode", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "Switch", type: 1, prompt: "Switch#" });

  const result = plain(
    iso.ctx.dispatch("configure", { device: "Switch", lines: ["show vlan brief"] }),
  );

  assert.deepEqual(sw.commands, ["show vlan brief"]);
  assert.equal(result.prompt, "Switch#");
  assert.equal(result.effective_mode, "exec");
});

test("configure enters config, runs the body and ends", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "Switch", type: 1, prompt: "Switch#" });

  const result = plain(
    iso.ctx.dispatch("configure", { device: "Switch", lines: ["hostname S1", "vlan 10"] }),
  );

  assert.deepEqual(sw.commands, ["configure terminal", "hostname S1", "vlan 10", "end"]);
  assert.equal(result.prompt, "S1#", "hostname took effect and end left config mode");
  assert.equal(result.effective_mode, "config");
});

test("configure with keep_session leaves the device in config mode", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "Switch", type: 1, prompt: "Switch#", host: "Switch" });

  const result = plain(
    iso.ctx.dispatch("configure", {
      device: "Switch",
      lines: ["vlan 10"],
      keep_session: true,
    }),
  );

  assert.deepEqual(sw.commands, ["configure terminal", "vlan 10"]);
  assert.equal(result.prompt, "Switch(config)#");
  assert.equal(sw.cliMode, "config");
});

test("configure in raw mode sends nothing extra, even from config", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "S1", type: 1, prompt: "S1(config)#" });

  const result = plain(
    iso.ctx.dispatch("configure", { device: "S1", lines: ["ip routing"], mode: "raw" }),
  );

  assert.deepEqual(sw.commands, ["ip routing"]);
  assert.equal(result.prompt, "S1(config)#", "still in config, and no end was appended");
});

// --------------------------------------------------------------------------
// PTM-06: per-line send, synchronisation and error reporting
// --------------------------------------------------------------------------
test("a 30-line block is sent completely and in order", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "S1", type: 1, prompt: "S1#" });
  const body = [];
  for (let i = 0; i < 15; i++) {
    body.push("interface FastEthernet0/" + i, "exit");
  }

  const result = plain(iso.ctx.dispatch("configure", { device: "S1", lines: body }));

  assert.equal(result.ok, true);
  assert.equal(result.lines.length, body.length + 2, "configure terminal + 30 + end");
  assert.deepEqual(
    result.lines.map((entry) => entry.line),
    ["configure terminal"].concat(body, ["end"]),
  );
  assert.deepEqual(sw.commands, result.lines.map((entry) => entry.line));
});

test("a rejected line stops the block and is reported with its 1-based index", () => {
  const iso = loadBridge();
  device(iso, { name: "S1", type: 1, prompt: "S1#", rejectLine: "bad-line" });

  const result = plain(
    iso.ctx.dispatch("configure", {
      device: "S1",
      lines: ["vlan 10", "bad-line", "vlan 20"],
    }),
  );

  assert.equal(result.ok, false);
  assert.equal(result.lines.length, 3, "stops on the failing line");
  assert.deepEqual(result.stopped_on, {
    index: 3,
    line: "bad-line",
    prompt: "S1(config)#",
    reason: "rejected",
    output: result.stopped_on.output,
  });
  assert.match(result.stopped_on.output, /% Invalid input/);
  assert.match(result.error, /line 3 'bad-line' was rejected at prompt 'S1\(config\)#'/);
  assert.deepEqual(result.failed, [3]);
});

test("stop_on_error=false keeps going and reports every failure", () => {
  const iso = loadBridge();
  const sw = device(iso, {
    name: "S1",
    type: 1,
    prompt: "S1#",
    rejectLine: ["bad-one", "bad-two"],
  });

  const result = plain(
    iso.ctx.dispatch("configure", {
      device: "S1",
      lines: ["vlan 10", "bad-one", "vlan 20", "bad-two"],
      stop_on_error: false,
    }),
  );

  assert.equal(result.ok, false);
  assert.deepEqual(
    result.lines.map((entry) => entry.line),
    ["configure terminal", "vlan 10", "bad-one", "vlan 20", "bad-two", "end"],
    "the whole block still ran",
  );
  assert.deepEqual(result.failed, [3, 5]);
  assert.equal(result.stopped_on.index, 3, "first failure is the one reported");
  assert.deepEqual(sw.commands.slice(-1), ["end"]);
});

test("a line that never settles is reported as a timeout, not ignored", () => {
  const iso = loadBridge();
  const sw = device(iso, {
    name: "S1",
    type: 1,
    prompt: "S1#",
    /* The first line is absorbed; everything after it is swallowed by the
     * console (no echo, no prompt change) to exercise the per-line timeout. */
    freezeAfter: 1,
  });

  const result = plain(
    iso.ctx.dispatch("configure", {
      device: "S1",
      lines: ["hostname S1"],
      timeout_ms: 60,
    }),
  );

  assert.equal(result.ok, false);
  assert.equal(result.stopped_on.reason, "timeout");
  assert.equal(result.stopped_on.index, 2, "configure terminal was line 1");
  assert.equal(result.stopped_on.line, "hostname S1");
  assert.match(result.error, /timed out at prompt/);
  assert.equal(sw.commands.length, 2, "nothing after the stuck line was sent");
});

test("exec runs one command in exec mode", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "Switch", type: 1, prompt: "Switch#" });

  const result = plain(
    iso.ctx.dispatch("exec", { device: "Switch", command: "show ip interface brief" }),
  );

  assert.deepEqual(sw.commands, ["show ip interface brief"]);
  assert.equal(result.mode, "exec");
  assert.equal(result.effective_mode, "exec");
  assert.equal(result.lines.length, 1);
  assert.equal(result.prompt, "Switch#");
});

test("exec from user exec prepends enable", () => {
  const iso = loadBridge();
  const sw = device(iso, { name: "Switch", type: 1, prompt: "Switch>" });

  const result = plain(iso.ctx.dispatch("exec", { device: "Switch", command: "show run" }));

  assert.deepEqual(sw.commands, ["enable", "show run"]);
  assert.equal(result.lines[0].line, "enable");
});

test("exec rejects an empty command", () => {
  const iso = loadBridge();
  device(iso, { name: "Switch", type: 1, prompt: "Switch#" });

  assert.throws(() => iso.ctx.dispatch("exec", { device: "Switch", command: "  " }), /no command supplied/);
});
