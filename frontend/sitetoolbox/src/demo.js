/** 本地交互预览只使用演示数据；所有写操作均在内存中模拟。 */
import { reactive } from "vue";
import { DEFAULTS, MODULES } from "./lib/model";
const day = "2026-10-23";
const nowText = () => new Date().toLocaleString("sv-SE").replace(",", "");
const normal = () => ({
  state: "success",
  raw_state: "success",
  message: "RSS正常",
  raw_message: "RSS正常",
  failure_days: 0,
  confirmed: false,
  tested_at: `${day} 11:02:46`,
});
const dataNormal = () => ({
  ...normal(),
  message: "用户数据完整",
  raw_message: "用户数据完整",
  upload: 5.2e12,
  download: 1.3e12,
  user_level: "Power User",
  updated_at: `${day} 08:00:00`,
});
const names = [
  [32, "PT时间", "pttime.org"],
  [11, "高清杜比", "hddolby.com"],
  [26, "红豆饭", "hdfans.org"],
  [23, "学校", "btschool.club"],
  [17, "憨憨", "hhanclub.net"],
  [1, "UBits", "ubits.club"],
  [2, "彩虹岛", "ptchdbits.co"],
  [3, "我堡", "ourbits.club"],
  [4, "朋友", "keepfrds.com"],
  [5, "猫站", "pterclub.net"],
  [6, "铂金家", "pthome.net"],
  [7, "LuckPT", "luckpt.de"],
  [8, "蝶粉", "discfan.net"],
  [9, "家园", "hdhome.org"],
  [10, "观众", "audiences.me"],
  [12, "高清时间", "hdtime.org"],
  [13, "高清视界", "hdarea.club"],
  [14, "GGPT", "gamegamept.com"],
  [15, "幼儿园", "u2.dmhy.org"],
  [16, "听听歌", "totheglory.im"],
  [18, "天空", "hdsky.me"],
  [19, "麒麟", "hdkylin.com"],
  [20, "蟹黄堡", "crabpt.vip"],
  [21, "天雪", "skyeysnow.com"],
  [22, "龟站", "kamept.com"],
  [24, "城市", "hdcity.city"],
  [25, "天枢", "dubhe.site"],
  [28, "PTS", "ptskit.org"],
  [27, "春天", "springsunday.net"],
  [29, "馒头", "m-team.cc"],
  [30, "AGSVPT", "agsvpt.com"],
  [31, "HDVIDEO", "hdvideo.top"],
  [33, "萝莉", "xloli.cc"],
  [34, "麒麟二站", "hdkyl.in"],
];
const config = reactive({
  ...JSON.parse(JSON.stringify(DEFAULTS)),
  timeout: 60,
  mteam_warning_days: 10,
  cleanup_downloader_names: ["主服务"],
  site_ids: names.map((row) => row[0]),
});
const state = reactive({
  plugin_version: "1.4.1",
  enabled: true,
  sites: names.map(([id, name, domain]) => ({
    id,
    name,
    domain,
    url: `https://${domain}/`,
    active: true,
    monitored: true,
    adapted: [17, 23, 32].includes(id),
    state: "success",
    confirmed: false,
    failure_days: 0,
    last_checked_at: `${day} 11:02:46`,
    checks: {
      rss: normal(),
      userdata: dataNormal(),
      ...(id === 29
        ? { mteam: { ...normal(), raw_message: "最近网页登录 2 天前" } }
        : {}),
    },
  })),
  jobs: {},
  runs: [],
  maintenance: {
    missing: {
      created_at: "",
      total_count: 0,
      confirmed_count: 0,
      items: [],
      errors: [],
    },
    cleanup: {},
    delete_files: false,
    auto_cleanup: false,
  },
  rules: [
    { domain: "hhanclub.net", indexer: true, userdata: true, asyncfix: true },
    { domain: "btschool.club", indexer: true, userdata: true, asyncfix: false },
    { domain: "pttime.org", indexer: false, userdata: true, asyncfix: false },
  ],
});
const pending = state.sites.find((row) => row.id === 32);
Object.assign(pending, { state: "pending", failure_days: 1 });
pending.checks.rss = {
  ...normal(),
  state: "pending",
  raw_state: "error",
  message: "连续异常 1/3 天，待观察：账号等级暂不支持 RSS",
  raw_message: "账号等级暂不支持 RSS",
  failure_days: 1,
  confirmed: false,
  first_failed_at: `${day} 11:02:46`,
};
const offline = state.sites.find((row) => row.id === 11);
Object.assign(offline, { state: "offline", failure_days: 1 });
offline.checks.rss = {
  ...normal(),
  state: "offline",
  raw_state: "offline",
  message: "站点连接失败，RSS 状态暂不判定",
  raw_message: "站点连接失败",
  failure_days: 1,
  confirmed: false,
  first_failed_at: `${day} 08:00:00`,
};
offline.checks.userdata = {
  state: "unknown",
  message: "尚未检测",
  failure_days: 0,
  confirmed: false,
};
function nextMockRun() {
  if (!config.monitor_enabled || !config.monitor_modules.length) return "";
  const now = new Date(),
    start = config.monitor_start.split(":").map(Number),
    end = config.monitor_end.split(":").map(Number);
  const from = start[0] * 60 + start[1],
    duration = (end[0] * 60 + end[1] - from + 1440) % 1440 || 1440;
  const slots = [];
  for (let d = -1; d < 3; d++) {
    const midnight = new Date(now);
    midnight.setDate(now.getDate() + d);
    midnight.setHours(0, 0, 0, 0);
    const offsets =
      config.monitor_mode === "fixed"
        ? config.monitor_times
            .map((t) => {
              const [h, m] = t.split(":").map(Number);
              return (h * 60 + m - from + 1440) % 1440;
            })
            .filter((n) => n <= duration)
        : Array.from(
            {
              length:
                Math.floor(
                  (duration - (duration === 1440 ? 1 : 0)) /
                    config.monitor_interval,
                ) + 1,
            },
            (_, i) => i * config.monitor_interval,
          );
    for (const offset of offsets) {
      const date = new Date(midnight.getTime() + (from + offset) * 60000);
      if (date > now) slots.push(date);
    }
  }
  return slots.sort((a, b) => a - b)[0]?.toLocaleString("sv-SE") || "";
}
function updateClassification() {
  for (const site of state.sites) {
    let confirmed = false;
    for (const check of Object.values(site.checks)) {
      if (!["error", "warning", "offline"].includes(check.raw_state)) continue;
      check.confirmed = check.failure_days >= config.monitor_failure_days;
      check.state =
        check.raw_state === "offline"
          ? "offline"
          : check.confirmed
            ? check.raw_state
            : "pending";
      check.message = `连续异常 ${check.failure_days}/${config.monitor_failure_days} 天：${check.raw_message}`;
      confirmed ||= check.confirmed;
    }
    site.confirmed = confirmed;
    site.state = confirmed ? "error" : site.checks.rss.state;
  }
  for (const item of state.maintenance.missing.items) {
    const check = item.monitor;
    check.raw_state = "error";
    check.confirmed = check.failure_days >= config.monitor_failure_days;
    check.state = check.confirmed ? "error" : "pending";
  }
  state.maintenance.missing.confirmed_count =
    state.maintenance.missing.items.filter(
      (item) => item.monitor.confirmed,
    ).length;
}
const clone = (value) => JSON.parse(JSON.stringify(value));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const errors = [
  {
    source: "站点请求",
    level: "ERROR",
    message: "站点暂时无法连接，请稍后复测。",
    count: 27,
    last_at: `${day} 11:00:00`,
  },
  {
    source: "下载器",
    level: "ERROR",
    message: "下载器请求超时，下一轮将再次检测。",
    count: 4,
    last_at: `${day} 10:40:00`,
  },
  {
    source: "用户数据",
    level: "ERROR",
    message: "用户数据解析缺少字段。",
    count: 2,
    last_at: `${day} 09:00:00`,
  },
];
export const demoApi = {
  async get(path, options = {}) {
    await sleep(100);
    if (path === "plugin/sitetoolbox")
      return { success: true, data: clone(config) };
    if (path.endsWith("/overview"))
      return {
        success: true,
        data: {
          ...clone(state),
          plan: {
            ...clone(config),
            timezone: "Asia/Tokyo",
            next_run_at: nextMockRun(),
          },
        },
      };
    if (path.endsWith("/options"))
      return {
        success: true,
        data: {
          sites: names.map(([value, name, domain]) => ({
            value,
            title: `${name} (${domain})`,
            mteam: value === 29,
          })),
          downloaders: [
            { title: "主服务 (qBittorrent)", value: "主服务" },
            { title: "保种下载器 (qBittorrent)", value: "保种下载器" },
          ],
          modules: MODULES,
        },
      };
    if (path.endsWith("/errors")) {
      const p = options.params || {};
      const items = errors.filter(
        (item) =>
          (p.level === "all" || !p.level || item.level === p.level) &&
          `${item.source} ${item.message}`.includes(p.query || ""),
      );
      return {
        success: true,
        data: { items, total: items.length, sampled: true },
      };
    }
    if (path.startsWith("site/icon/"))
      return {
        success: true,
        data: {
          icon: [8, 24].includes(Number(path.split("/").at(-1)))
            ? ""
            : `/review/site-icons/${path.split("/").at(-1)}.png`,
        },
      };
    return { success: false, message: "预览未提供此接口" };
  },
  async post(path, body = {}) {
    await sleep(120);
    if (path.includes("/repair/rss"))
      return {
        success: false,
        message: "演示站点账号等级暂不支持 RSS，无法自动修复。",
      };
    const key = path.endsWith("/monitor/run")
      ? "monitor"
      : path.includes("/cleanup/missing/preview")
        ? "missing_preview"
        : path.endsWith("/cleanup/missing")
          ? "missing_cleanup"
          : path.includes("userdata")
            ? "userdata_check"
            : "mteam_login_check";
    const started = nowText();
    state.jobs[key] = {
      key,
      name: key === "monitor" ? "统一检测" : "专项检查",
      status: "running",
      message: "1/4 · RSS 检测",
      started_at: started,
      finished_at: "",
    };
    setTimeout(() => {
      if (key === "missing_preview") {
        state.maintenance.missing = {
          created_at: nowText(),
          total_count: 2,
          confirmed_count: 1,
          errors: [],
          items: [
            {
              hash: "demo-confirmed",
              name: "演示剧集 S01E01",
              downloader: "主服务",
              save_path: "/downloads/series",
              size: 2.4e9,
              monitor: { state: "error", failure_days: 3, confirmed: true },
            },
            {
              hash: "demo-pending",
              name: "演示电影 1080p",
              downloader: "主服务",
              save_path: "/downloads/movies",
              size: 5.1e9,
              monitor: { state: "pending", failure_days: 1, confirmed: false },
            },
          ],
        };
      } else if (key === "missing_cleanup") {
        state.maintenance.missing.items =
          state.maintenance.missing.items.filter(
            (item) => !item.monitor.confirmed,
          );
        state.maintenance.missing.total_count =
          state.maintenance.missing.items.length;
        state.maintenance.missing.confirmed_count = 0;
        state.maintenance.cleanup = {
          cleaned_at: nowText(),
          deleted_count: 1,
          failed_count: 0,
          skipped_count: 0,
        };
      } else {
        state.sites
          .filter((site) => !body.site_ids || body.site_ids.includes(site.id))
          .forEach((site) => {
            site.last_checked_at = nowText();
            Object.values(site.checks).forEach((check) => {
              if (check.state !== "unknown") check.tested_at = nowText();
            });
          });
      }
      updateClassification();
      state.jobs[key] = {
        ...state.jobs[key],
        status: "success",
        finished_at: nowText(),
        message:
          key === "missing_cleanup"
            ? "演示清理完成：删除 1，保留待观察项 1"
            : "检测完成，状态已更新",
      };
      if (key === "monitor")
        state.runs.unshift({
          id: String(Date.now()),
          started_at: started,
          finished_at: nowText(),
          status: "success",
          confirmed: state.sites.filter((site) => site.confirmed).length,
          steps: (body.modules || config.monitor_modules).map((module) => ({
            module,
            state: "success",
            message: "检测完成",
          })),
        });
    }, 1400);
    return {
      success: true,
      message: "已开始检测，结果会自动更新",
      data: clone(state.jobs[key]),
    };
  },
  async put(path, body) {
    await sleep(200);
    Object.assign(config, body);
    state.enabled = config.enabled;
    state.maintenance.delete_files = config.cleanup_delete_files;
    state.maintenance.auto_cleanup = config.cleanup_auto_cleanup;
    updateClassification();
    return { success: true, message: "设置已保存" };
  },
};
