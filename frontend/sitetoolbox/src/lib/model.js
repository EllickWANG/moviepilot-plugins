export const MODULES = {
  rss: "RSS 检测",
  userdata: "用户数据",
  missing: "缺失种子扫描",
  mteam: "馒头登录检查",
};
export const STATES = {
  success: { label: "正常", icon: "mdi-check-circle", color: "success" },
  pending: { label: "待观察", icon: "mdi-eye-outline", color: "pending" },
  offline: { label: "连接失败", icon: "mdi-lan-disconnect", color: "offline" },
  error: { label: "已确认异常", icon: "mdi-alert-circle", color: "error" },
  warning: {
    label: "已确认异常",
    icon: "mdi-alert-circle-outline",
    color: "error",
  },
  unknown: {
    label: "未检测",
    icon: "mdi-help-circle-outline",
    color: "unknown",
  },
};
export const DEFAULTS = {
  enabled: true,
  monitor_enabled: true,
  monitor_modules: Object.keys(MODULES),
  monitor_start: "08:00",
  monitor_end: "23:00",
  monitor_mode: "interval",
  monitor_interval: 120,
  monitor_times: ["08:00"],
  monitor_failure_days: 3,
  site_ids: [],
  timeout: 20,
  auto_discover: true,
  save_discovered: false,
  patch_userdata: true,
  site_conf: "",
  cleanup_downloader_names: [],
  cleanup_delete_files: false,
  cleanup_auto_cleanup: false,
  error_retention_days: 3,
  mteam_site_ids: [],
  mteam_warning_days: 25,
  userdata_stale_days: 2,
};
export function stateInfo(state) {
  return STATES[state] || STATES.unknown;
}
export function matchesSite(site, query, filter) {
  const text = String(query || "")
    .trim()
    .toLowerCase();
  if (text && !`${site.name} ${site.domain}`.toLowerCase().includes(text))
    return false;
  return (
    filter === "all" ||
    (filter === "confirmed" ? Boolean(site.confirmed) : site.state === filter)
  );
}
export function sortedSites(sites, key = "attention", ascending = true) {
  const priorities = {
    error: 0,
    warning: 0,
    pending: 1,
    offline: 2,
    unknown: 3,
    success: 4,
  };
  return [...sites].sort((a, b) => {
    let compared;
    if (key === "attention")
      compared = (priorities[a.state] ?? 3) - (priorities[b.state] ?? 3);
    else if (key === "failure_days")
      compared = Number(a.failure_days || 0) - Number(b.failure_days || 0);
    else
      compared = String(a[key] || "").localeCompare(
        String(b[key] || ""),
        "zh-CN",
        { numeric: true },
      );
    // 同状态保留宿主站点优先级，复测和刷新时不打乱熟悉的顺序。
    return compared * (ascending ? 1 : -1);
  });
}
export function shortTime(value) {
  if (!value) return "尚未检测";
  const today = new Date().toLocaleDateString("sv-SE");
  return value.startsWith(today)
    ? `今天 ${value.slice(11, 16)}`
    : value.slice(0, 16);
}
export function sizeText(value) {
  const number = Number(value || 0);
  if (!number) return "0 B";
  const exponent = Math.min(4, Math.floor(Math.log(number) / Math.log(1024)));
  return `${(number / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${["B", "KB", "MB", "GB", "TB"][exponent]}`;
}
export function planSummary(plan = {}) {
  const interval = Number(plan.monitor_interval || 120);
  const cadence =
    plan.monitor_mode === "fixed"
      ? (plan.monitor_times || []).join("、")
      : `每 ${interval % 60 ? `${interval} 分钟` : `${interval / 60} 小时`}`;
  return {
    window: `${plan.monitor_start || "08:00"} – ${plan.monitor_end || "23:00"}`,
    cadence,
  };
}
export function validateSettings(config) {
  const errors = [];
  const clock = (value) => /^([01]\d|2[0-3]):[0-5]\d$/.test(String(value));
  if (!clock(config.monitor_start) || !clock(config.monitor_end))
    errors.push("请填写有效的执行时段");
  for (const [key, label, min, max] of [
    ["monitor_failure_days", "异常确认天数", 1, 365],
    ["monitor_interval", "检测间隔", 5, 1440],
    ["timeout", "请求超时", 5, 120],
    ["mteam_warning_days", "登录记录最长间隔", 1, 365],
    ["userdata_stale_days", "数据过期天数", 1, 365],
  ]) {
    const n = Number(config[key]);
    if (!Number.isInteger(n) || n < min || n > max)
      errors.push(`${label}需要是 ${min}–${max} 的整数`);
  }
  if (config.monitor_enabled && !config.monitor_modules?.length)
    errors.push("请至少选择一个检测模块");
  if (config.monitor_mode === "fixed") {
    const times = Array.isArray(config.monitor_times)
      ? config.monitor_times
      : String(config.monitor_times || "")
          .replaceAll("，", ",")
          .split(",")
          .map((v) => v.trim());
    if (!times.length || times.some((time) => !clock(time)))
      errors.push("固定时刻请使用 HH:MM，多个时刻以逗号分隔");
    else if (clock(config.monitor_start) && clock(config.monitor_end)) {
      const minutes = (time) =>
        Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
      const start = minutes(config.monitor_start),
        duration = (minutes(config.monitor_end) - start + 1440) % 1440 || 1440;
      if (
        times.some((time) => (minutes(time) - start + 1440) % 1440 > duration)
      )
        errors.push("固定时刻需要位于允许执行的时段内");
    }
  }
  const rules = String(config.site_conf || "").trim();
  if (rules) {
    try {
      if (rules.startsWith("{") || rules.startsWith("[")) JSON.parse(rules);
      else
        rules
          .split("\n")
          .filter((line) => line.trim())
          .forEach((line) => {
            const separator = line.indexOf("|");
            if (separator < 1) throw new Error("invalid rule");
            JSON.parse(atob(line.slice(separator + 1).trim()));
          });
    } catch {
      errors.push("适配规则不是有效的 JSON 或 domain|base64(JSON)");
    }
  }
  return errors;
}
