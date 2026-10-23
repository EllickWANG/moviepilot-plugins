import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULTS,
  matchesSite,
  sortedSites,
  validateSettings,
  planSummary,
} from "../src/lib/model.js";
import { createClient } from "../src/lib/client.js";

const config = (changes) => ({ ...structuredClone(DEFAULTS), ...changes });
test("cross-midnight fixed times are accepted only inside the window", () => {
  assert.deepEqual(
    validateSettings(
      config({
        monitor_start: "22:00",
        monitor_end: "06:00",
        monitor_mode: "fixed",
        monitor_times: ["23:00", "01:00", "06:00"],
      }),
    ),
    [],
  );
  assert.match(
    validateSettings(
      config({
        monitor_start: "22:00",
        monitor_end: "06:00",
        monitor_mode: "fixed",
        monitor_times: ["12:00"],
      }),
    ).join(),
    /时段内/,
  );
});
test("one confirmation threshold is validated for all modules", () => {
  assert.match(
    validateSettings(config({ monitor_failure_days: 0 })).join(),
    /异常确认天数/,
  );
  assert.match(
    validateSettings(config({ monitor_failure_days: 1.5 })).join(),
    /异常确认天数/,
  );
  assert.deepEqual(validateSettings(config({ monitor_failure_days: 7 })), []);
  assert.match(
    validateSettings(config({ monitor_modules: [] })).join(),
    /至少选择/,
  );
});
test("filtering combines trimmed search, state and confirmed status", () => {
  const site = {
    name: "PT时间",
    domain: "pttime.org",
    state: "pending",
    confirmed: false,
  };
  assert.ok(matchesSite(site, " PTTIME ", "pending"));
  assert.equal(matchesSite(site, "PT", "confirmed"), false);
  assert.equal(matchesSite(site, "other", "all"), false);
  assert.ok(
    matchesSite({ ...site, state: "error", confirmed: true }, "", "confirmed"),
  );
});
test("attention ordering is stable within the host site priority", () => {
  const sites = [
    { id: 26, state: "success" },
    { id: 23, state: "success" },
    { id: 11, state: "offline" },
    { id: 32, state: "pending" },
  ];
  assert.deepEqual(
    sortedSites(sites).map((s) => s.id),
    [32, 11, 26, 23],
  );
  assert.deepEqual(
    sites.map((s) => s.id),
    [26, 23, 11, 32],
  );
});
test("malformed rule edits are rejected before replacing current rules", () => {
  assert.match(
    validateSettings(config({ site_conf: "{invalid" })).join(),
    /适配规则/,
  );
  assert.deepEqual(
    validateSettings(config({ site_conf: '{"domain":"example.test"}' })),
    [],
  );
});
test("summary uses the same fixed times and interval as the saved plan", () => {
  assert.equal(
    planSummary(config({ monitor_interval: 90 })).cadence,
    "每 90 分钟",
  );
  assert.equal(
    planSummary(
      config({ monitor_mode: "fixed", monitor_times: ["08:00", "20:00"] }),
    ).cadence,
    "08:00、20:00",
  );
});
test("settings save re-reads current results and preserves unknown host fields", async () => {
  let body;
  const api = {
    get: async () => ({
      enabled: true,
      latest_results: [{ site_id: 1, tested_at: "new" }],
      future_option: 7,
    }),
    put: async (path, value) => {
      body = value;
      return { success: true };
    },
  };
  const client = createClient(api);
  await client.saveSettings({ monitor_failure_days: 5 });
  assert.deepEqual(body.latest_results, [{ site_id: 1, tested_at: "new" }]);
  assert.equal(body.future_option, 7);
  assert.equal(body.monitor_failure_days, 5);
});
test("failed host settings reads cannot overwrite the configuration", async () => {
  let writes = 0;
  const client = createClient({
    get: async () => ({ success: false, message: "denied" }),
    put: async () => writes++,
  });
  await assert.rejects(
    client.saveSettings({ monitor_failure_days: 5 }),
    /denied/,
  );
  assert.equal(writes, 0);
});

test("a new scheduled job prevents a stale settings form from interrupting it", async () => {
  let writes = 0;
  const client = createClient({
    get: async () => ({
      latest_job_states: { monitor: { status: "running" } },
    }),
    put: async () => writes++,
  });
  await assert.rejects(
    client.saveSettings({ monitor_failure_days: 5 }),
    /检测正在运行/,
  );
  assert.equal(writes, 0);
});
