import { importShared } from './__federation_fn_import-JrT3xvdd.js';

/** 宿主负责鉴权，组件不读取或保存用户凭据。 */
function createClient(api, pluginId = "sitetoolbox") {
  const base = `plugin/${pluginId}`;
  const iconCache = new Map();
  function unwrap(value) {
    if (value && typeof value === "object" && "success" in value) return value;
    return { success: true, data: value, message: "" };
  }
  async function get(path, params) {
    return unwrap(
      await api.get(`${base}${path}`, { feedback: "silent", params }),
    );
  }
  return {
    get,
    async post(path, body = {}) {
      return unwrap(
        await api.post(`${base}${path}`, body, { feedback: "silent" }),
      );
    },
    async settings() {
      const response = unwrap(await api.get(base, { feedback: "silent" }));
      if (!response.success)
        throw new Error(response.message || "读取配置失败");
      return response.data || {};
    },
    async saveSettings(changes) {
      // 运行结果不属于设置表单；保存前重新读取，保留服务器上的最新结果和未知配置。
      const current = await this.settings();
      if (
        Object.values(current.latest_job_states || {}).some(
          (job) => job.status === "running",
        )
      )
        throw new Error("检测正在运行，请完成后再保存设置");
      return unwrap(
        await api.put(base, { ...current, ...changes }, { feedback: "silent" }),
      );
    },
    async icon(id) {
      if (!iconCache.has(id))
        iconCache.set(
          id,
          Promise.resolve(api.get(`site/icon/${id}`, { feedback: "silent" }))
            .then(unwrap)
            .then((r) => r.data?.icon || "")
            .catch(() => ""),
        );
      return iconCache.get(id);
    },
  };
}

const MODULES = {
  rss: "RSS 检测",
  userdata: "用户数据",
  missing: "缺失种子扫描",
  mteam: "馒头登录检查",
};
const STATES = {
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
const DEFAULTS = {
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
function stateInfo(state) {
  return STATES[state] || STATES.unknown;
}
function matchesSite(site, query, filter) {
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
function sortedSites(sites, key = "attention", ascending = true) {
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
function shortTime(value) {
  if (!value) return "尚未检测";
  const today = new Date().toLocaleDateString("sv-SE");
  return value.startsWith(today)
    ? `今天 ${value.slice(11, 16)}`
    : value.slice(0, 16);
}
function sizeText(value) {
  const number = Number(value || 0);
  if (!number) return "0 B";
  const exponent = Math.min(4, Math.floor(Math.log(number) / Math.log(1024)));
  return `${(number / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${["B", "KB", "MB", "GB", "TB"][exponent]}`;
}
function planSummary(plan = {}) {
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
function validateSettings(config) {
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

const {createElementVNode:_createElementVNode,resolveComponent:_resolveComponent,createVNode:_createVNode,createTextVNode:_createTextVNode,withCtx:_withCtx,renderList:_renderList,Fragment:_Fragment,openBlock:_openBlock,createElementBlock:_createElementBlock,toDisplayString:_toDisplayString,createBlock:_createBlock,createCommentVNode:_createCommentVNode,unref:_unref,vShow:_vShow,withDirectives:_withDirectives,withModifiers:_withModifiers,normalizeClass:_normalizeClass} = await importShared('vue');


const _hoisted_1 = { class: "stb-settings-header" };
const _hoisted_2 = { class: "stb-settings-body" };
const _hoisted_3 = { class: "stb-settings-section-title" };
const _hoisted_4 = { class: "stb-form-pair" };
const _hoisted_5 = { class: "stb-module-options" };
const _hoisted_6 = { class: "stb-threshold-setting" };
const _hoisted_7 = { key: 0 };
const _hoisted_8 = { key: 0 };
const _hoisted_9 = { key: 0 };
const _hoisted_10 = { class: "stb-form-pair" };
const _hoisted_11 = { class: "stb-settings-footer" };

const {computed,reactive,ref,watch} = await importShared('vue');

const {useTheme} = await importShared('vuetify');

const _sfc_main = {
  __name: 'SettingsPanel',
  props: {
  initial: { type: Object, default: () => ({}) },
  options: { type: Object, default: () => ({}) },
  saving: Boolean,
  busy: Boolean,
},
  emits: ["save", "close"],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;
const tab = ref("monitor");
const hostTheme = useTheme();
const isLight = computed(() => !hostTheme.current.value.dark);
const draft = reactive({ ...DEFAULTS });
const fixedTimes = ref("08:00");
const errors = ref([]);
const original = ref("");
const discard = ref(false);
watch(
  () => props.initial,
  (value) => {
    const next = JSON.parse(
      JSON.stringify({
        ...DEFAULTS,
        ...Object.fromEntries(
          Object.keys(DEFAULTS)
            .filter((key) => value[key] !== undefined)
            .map((key) => [key, value[key]]),
        ),
      }),
    );
    if (
      value.monitor_failure_days === undefined &&
      value.rss_failure_days !== undefined
    )
      next.monitor_failure_days = value.rss_failure_days;
    Object.assign(draft, next);
    fixedTimes.value = (
      Array.isArray(next.monitor_times)
        ? next.monitor_times
        : [next.monitor_times]
    ).join(", ");
    original.value = JSON.stringify({
      ...draft,
      monitor_times: fixedTimes.value,
    });
    errors.value = [];
  },
  { immediate: true },
);
const dirty = computed(
  () =>
    JSON.stringify({ ...draft, monitor_times: fixedTimes.value }) !==
    original.value,
);
function close() {
  if (dirty.value) discard.value = true;
  else emit("close");
}
function submit() {
  const body = {
    ...draft,
    monitor_times: fixedTimes.value
      .replaceAll("，", ",")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
  };
  errors.value = validateSettings(body);
  if (errors.value.length) return;
  for (const key of [
    "monitor_failure_days",
    "monitor_interval",
    "timeout",
    "mteam_warning_days",
    "userdata_stale_days",
    "error_retention_days",
  ])
    body[key] = Number(body[key]);
  emit("save", body);
}

return (_ctx, _cache) => {
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_tab = _resolveComponent("v-tab");
  const _component_v_tabs = _resolveComponent("v-tabs");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_switch = _resolveComponent("v-switch");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_btn_toggle = _resolveComponent("v-btn-toggle");
  const _component_v_select = _resolveComponent("v-select");
  const _component_v_checkbox = _resolveComponent("v-checkbox");
  const _component_v_autocomplete = _resolveComponent("v-autocomplete");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_textarea = _resolveComponent("v-textarea");
  const _component_v_form = _resolveComponent("v-form");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_card_actions = _resolveComponent("v-card-actions");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_dialog = _resolveComponent("v-dialog");

  return (_openBlock(), _createElementBlock("section", {
    class: _normalizeClass(["stb-settings", { 'stb-is-light': isLight.value }])
  }, [
    _createElementVNode("header", _hoisted_1, [
      _cache[26] || (_cache[26] = _createElementVNode("div", null, [
        _createElementVNode("h2", null, "统一监控与设置"),
        _createElementVNode("p", null, "一次配置，所有检测模块共用。")
      ], -1)),
      _createVNode(_component_v_btn, {
        "aria-label": "关闭设置",
        icon: "mdi-close",
        variant: "text",
        onClick: close
      })
    ]),
    _createVNode(_component_v_tabs, {
      modelValue: tab.value,
      "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((tab).value = $event)),
      color: "primary",
      class: "stb-settings-tabs"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_tab, { value: "monitor" }, {
          default: _withCtx(() => [...(_cache[27] || (_cache[27] = [
            _createTextVNode("监控计划", -1)
          ]))]),
          _: 1
        }),
        _createVNode(_component_v_tab, { value: "scope" }, {
          default: _withCtx(() => [...(_cache[28] || (_cache[28] = [
            _createTextVNode("范围与策略", -1)
          ]))]),
          _: 1
        }),
        _createVNode(_component_v_tab, { value: "advanced" }, {
          default: _withCtx(() => [...(_cache[29] || (_cache[29] = [
            _createTextVNode("高级适配", -1)
          ]))]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"]),
    _createVNode(_component_v_form, {
      onSubmit: _withModifiers(submit, ["prevent"])
    }, {
      default: _withCtx(() => [
        _createElementVNode("div", _hoisted_2, [
          (errors.value.length)
            ? (_openBlock(), _createBlock(_component_v_alert, {
                key: 0,
                type: "error",
                variant: "tonal",
                class: "mb-5",
                role: "alert"
              }, {
                default: _withCtx(() => [
                  (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(errors.value, (error) => {
                    return (_openBlock(), _createElementBlock("div", { key: error }, _toDisplayString(error), 1))
                  }), 128))
                ]),
                _: 1
              }))
            : _createCommentVNode("", true),
          (__props.busy)
            ? (_openBlock(), _createBlock(_component_v_alert, {
                key: 1,
                type: "info",
                variant: "tonal",
                density: "compact",
                class: "mb-5"
              }, {
                default: _withCtx(() => [...(_cache[30] || (_cache[30] = [
                  _createTextVNode("检测正在运行，可以先编辑，完成后再保存设置。", -1)
                ]))]),
                _: 1
              }))
            : _createCommentVNode("", true),
          _withDirectives(_createElementVNode("div", null, [
            _createElementVNode("div", _hoisted_3, [
              _cache[31] || (_cache[31] = _createElementVNode("div", null, [
                _createElementVNode("h3", null, "统一监控计划"),
                _createElementVNode("p", null, "按服务器时间执行；手动检测不受时段限制。")
              ], -1)),
              _createVNode(_component_v_switch, {
                modelValue: draft.monitor_enabled,
                "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((draft.monitor_enabled) = $event)),
                color: "primary",
                label: "启用定时监控",
                inset: "",
                "hide-details": ""
              }, null, 8, ["modelValue"])
            ]),
            _createElementVNode("div", _hoisted_4, [
              _createVNode(_component_v_text_field, {
                modelValue: draft.monitor_start,
                "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((draft.monitor_start) = $event)),
                type: "time",
                label: "开始时间",
                "aria-label": "开始时间",
                variant: "outlined",
                density: "comfortable"
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.monitor_end,
                "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((draft.monitor_end) = $event)),
                type: "time",
                label: "结束时间",
                "aria-label": "结束时间",
                variant: "outlined",
                density: "comfortable"
              }, null, 8, ["modelValue"])
            ]),
            _cache[36] || (_cache[36] = _createElementVNode("p", { class: "stb-field-note" }, " 支持跨午夜，例如 22:00–06:00；开始与结束相同表示全天。 ", -1)),
            _cache[37] || (_cache[37] = _createElementVNode("h3", { class: "mt-5 mb-3" }, "执行方式", -1)),
            _createVNode(_component_v_btn_toggle, {
              modelValue: draft.monitor_mode,
              "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((draft.monitor_mode) = $event)),
              mandatory: "",
              color: "primary",
              variant: "outlined",
              divided: "",
              class: "mb-4"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_v_btn, {
                  value: "interval",
                  "prepend-icon": "mdi-timer-outline"
                }, {
                  default: _withCtx(() => [...(_cache[32] || (_cache[32] = [
                    _createTextVNode("周期执行", -1)
                  ]))]),
                  _: 1
                }),
                _createVNode(_component_v_btn, {
                  value: "fixed",
                  "prepend-icon": "mdi-clock-outline"
                }, {
                  default: _withCtx(() => [...(_cache[33] || (_cache[33] = [
                    _createTextVNode("固定时刻", -1)
                  ]))]),
                  _: 1
                })
              ]),
              _: 1
            }, 8, ["modelValue"]),
            (draft.monitor_mode === 'interval')
              ? (_openBlock(), _createBlock(_component_v_select, {
                  key: 0,
                  modelValue: draft.monitor_interval,
                  "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((draft.monitor_interval) = $event)),
                  items: [
              { title: '每 15 分钟', value: 15 },
              { title: '每 30 分钟', value: 30 },
              { title: '每 1 小时', value: 60 },
              { title: '每 2 小时', value: 120 },
              { title: '每 4 小时', value: 240 },
              { title: '每 6 小时', value: 360 },
              { title: '每天一次', value: 1440 },
            ],
                  label: "执行周期",
                  "aria-label": "执行周期",
                  variant: "outlined",
                  hint: "周期从当天执行时段的开始时间起计算",
                  "persistent-hint": ""
                }, null, 8, ["modelValue"]))
              : (_openBlock(), _createBlock(_component_v_text_field, {
                  key: 1,
                  modelValue: fixedTimes.value,
                  "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((fixedTimes).value = $event)),
                  label: "固定执行时刻",
                  "aria-label": "固定执行时刻",
                  placeholder: "08:00, 20:00",
                  variant: "outlined",
                  hint: "多个时刻用逗号分隔，需位于允许执行的时段内",
                  "persistent-hint": ""
                }, null, 8, ["modelValue"])),
            _cache[38] || (_cache[38] = _createElementVNode("h3", { class: "mt-6 mb-1" }, "检测模块", -1)),
            _cache[39] || (_cache[39] = _createElementVNode("p", { class: "stb-field-note" }, " 以下模块共用同一套执行计划和异常确认规则。 ", -1)),
            _createElementVNode("div", _hoisted_5, [
              (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(_unref(MODULES), (label, key) => {
                return (_openBlock(), _createBlock(_component_v_checkbox, {
                  key: key,
                  modelValue: draft.monitor_modules,
                  "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((draft.monitor_modules) = $event)),
                  value: key,
                  label: label,
                  color: "primary",
                  "hide-details": "",
                  density: "comfortable"
                }, null, 8, ["modelValue", "value", "label"]))
              }), 128))
            ]),
            _createElementVNode("div", _hoisted_6, [
              _cache[34] || (_cache[34] = _createElementVNode("div", null, [
                _createElementVNode("h3", null, "异常确认天数"),
                _createElementVNode("p", null, "连续异常达到设定天数，才确认问题并提醒。")
              ], -1)),
              _createVNode(_component_v_text_field, {
                modelValue: draft.monitor_failure_days,
                "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((draft.monitor_failure_days) = $event)),
                type: "number",
                min: "1",
                max: "365",
                label: "所有模块共用",
                "aria-label": "异常确认天数",
                suffix: "天",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"])
            ]),
            _createVNode(_component_v_alert, {
              type: "info",
              variant: "tonal",
              density: "compact",
              class: "mt-4"
            }, {
              default: _withCtx(() => [...(_cache[35] || (_cache[35] = [
                _createTextVNode("同一天重复异常只计一天；任何一次成功都会清零，漏测一天重新累计。记录会在重启后保留。", -1)
              ]))]),
              _: 1
            })
          ], 512), [
            [_vShow, tab.value === 'monitor']
          ]),
          _withDirectives(_createElementVNode("div", null, [
            _cache[41] || (_cache[41] = _createElementVNode("h3", { class: "mb-2" }, "站点范围", -1)),
            _cache[42] || (_cache[42] = _createElementVNode("p", { class: "stb-field-note mb-4" }, " 不指定站点时，检测所有已启用站点。登录检查只对 M-Team 站点运行。 ", -1)),
            _createVNode(_component_v_autocomplete, {
              modelValue: draft.site_ids,
              "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((draft.site_ids) = $event)),
              items: __props.options.sites || [],
              multiple: "",
              clearable: "",
              label: "监控站点",
              variant: "outlined",
              "menu-props": { maxHeight: 320 }
            }, {
              selection: _withCtx(({ index }) => [
                (index === 0)
                  ? (_openBlock(), _createElementBlock("span", _hoisted_7, "已选择 " + _toDisplayString(draft.site_ids.length) + " 个站点", 1))
                  : _createCommentVNode("", true)
              ]),
              _: 1
            }, 8, ["modelValue", "items"]),
            _createVNode(_component_v_text_field, {
              modelValue: draft.timeout,
              "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((draft.timeout) = $event)),
              label: "RSS 请求超时",
              suffix: "秒",
              type: "number",
              min: "5",
              max: "120",
              variant: "outlined"
            }, null, 8, ["modelValue"]),
            _createVNode(_component_v_switch, {
              modelValue: draft.auto_discover,
              "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((draft.auto_discover) = $event)),
              label: "未配置 RSS 时自动获取",
              color: "primary",
              "hide-details": ""
            }, null, 8, ["modelValue"]),
            _createVNode(_component_v_switch, {
              modelValue: draft.save_discovered,
              "onUpdate:modelValue": _cache[12] || (_cache[12] = $event => ((draft.save_discovered) = $event)),
              label: "将自动获取的 RSS 保存到站点",
              color: "primary",
              "hide-details": ""
            }, null, 8, ["modelValue"]),
            _createVNode(_component_v_divider, { class: "my-6" }),
            _cache[43] || (_cache[43] = _createElementVNode("h3", { class: "mb-2" }, "缺失种子", -1)),
            _createVNode(_component_v_select, {
              modelValue: draft.cleanup_downloader_names,
              "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((draft.cleanup_downloader_names) = $event)),
              items: __props.options.downloaders || [],
              multiple: "",
              clearable: "",
              label: "扫描下载器",
              variant: "outlined",
              class: "mt-4"
            }, {
              selection: _withCtx(({ index }) => [
                (index === 0)
                  ? (_openBlock(), _createElementBlock("span", _hoisted_8, "已选择 " + _toDisplayString(draft.cleanup_downloader_names.length) + " 个下载器", 1))
                  : _createCommentVNode("", true)
              ]),
              _: 1
            }, 8, ["modelValue", "items"]),
            _createVNode(_component_v_switch, {
              modelValue: draft.cleanup_auto_cleanup,
              "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((draft.cleanup_auto_cleanup) = $event)),
              label: "自动清理已确认异常的缺失种子",
              color: "primary",
              "hide-details": ""
            }, null, 8, ["modelValue"]),
            _cache[44] || (_cache[44] = _createElementVNode("p", { class: "stb-field-note" }, " 只处理达到全局异常天数阈值的种子，删除前再次核对 missingFiles 状态。 ", -1)),
            _createVNode(_component_v_switch, {
              modelValue: draft.cleanup_delete_files,
              "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((draft.cleanup_delete_files) = $event)),
              label: "清理时同时删除数据文件",
              color: "warning",
              "hide-details": ""
            }, null, 8, ["modelValue"]),
            (draft.cleanup_delete_files)
              ? (_openBlock(), _createBlock(_component_v_alert, {
                  key: 0,
                  type: "warning",
                  variant: "tonal",
                  density: "compact",
                  class: "mt-3"
                }, {
                  default: _withCtx(() => [...(_cache[40] || (_cache[40] = [
                    _createTextVNode("已开启删除数据文件。手动清理前会再次说明影响范围。", -1)
                  ]))]),
                  _: 1
                }))
              : _createCommentVNode("", true),
            _createVNode(_component_v_divider, { class: "my-6" }),
            _cache[45] || (_cache[45] = _createElementVNode("h3", { class: "mb-2" }, "检测条件", -1)),
            _cache[46] || (_cache[46] = _createElementVNode("p", { class: "stb-field-note mb-4" }, " 这些条件决定单次检测是否异常；连续几天才确认，统一使用“异常确认天数”。 ", -1)),
            _createVNode(_component_v_autocomplete, {
              modelValue: draft.mteam_site_ids,
              "onUpdate:modelValue": _cache[16] || (_cache[16] = $event => ((draft.mteam_site_ids) = $event)),
              items: (__props.options.sites || []).filter((site) => site.mteam),
              multiple: "",
              clearable: "",
              label: "馒头检查站点",
              "aria-label": "馒头检查站点",
              variant: "outlined",
              hint: "留空沿用上方监控站点范围",
              "persistent-hint": "",
              class: "mb-4"
            }, {
              selection: _withCtx(({ index }) => [
                (index === 0)
                  ? (_openBlock(), _createElementBlock("span", _hoisted_9, "已选择 " + _toDisplayString(draft.mteam_site_ids.length) + " 个馒头站点", 1))
                  : _createCommentVNode("", true)
              ]),
              _: 1
            }, 8, ["modelValue", "items"]),
            _createElementVNode("div", _hoisted_10, [
              _createVNode(_component_v_text_field, {
                modelValue: draft.userdata_stale_days,
                "onUpdate:modelValue": _cache[17] || (_cache[17] = $event => ((draft.userdata_stale_days) = $event)),
                type: "number",
                min: "1",
                max: "365",
                label: "用户数据过期时间",
                suffix: "天",
                variant: "outlined"
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.mteam_warning_days,
                "onUpdate:modelValue": _cache[18] || (_cache[18] = $event => ((draft.mteam_warning_days) = $event)),
                type: "number",
                min: "1",
                max: "365",
                label: "网页登录记录最长间隔",
                suffix: "天",
                variant: "outlined"
              }, null, 8, ["modelValue"])
            ])
          ], 512), [
            [_vShow, tab.value === 'scope']
          ]),
          _withDirectives(_createElementVNode("div", null, [
            _createVNode(_component_v_switch, {
              modelValue: draft.enabled,
              "onUpdate:modelValue": _cache[19] || (_cache[19] = $event => ((draft.enabled) = $event)),
              color: "primary",
              label: "启用站点工具箱",
              "hide-details": ""
            }, null, 8, ["modelValue"]),
            _createVNode(_component_v_switch, {
              modelValue: draft.patch_userdata,
              "onUpdate:modelValue": _cache[20] || (_cache[20] = $event => ((draft.patch_userdata) = $event)),
              color: "primary",
              label: "启用用户数据解析规则",
              "hide-details": ""
            }, null, 8, ["modelValue"]),
            _createVNode(_component_v_select, {
              modelValue: draft.error_retention_days,
              "onUpdate:modelValue": _cache[21] || (_cache[21] = $event => ((draft.error_retention_days) = $event)),
              items: [
              { title: '1 天', value: 1 },
              { title: '3 天', value: 3 },
              { title: '7 天', value: 7 },
            ],
              label: "系统错误保留时间",
              "aria-label": "系统错误保留时间",
              variant: "outlined",
              class: "mt-5"
            }, null, 8, ["modelValue"]),
            _cache[47] || (_cache[47] = _createElementVNode("h3", { class: "mb-2" }, "站点适配规则", -1)),
            _cache[48] || (_cache[48] = _createElementVNode("p", { class: "stb-field-note mb-4" }, " 兼容 JSON、JSON 数组和 domain|base64(JSON)。请勿将账号、Cookie 或密钥写入规则。 ", -1)),
            _createVNode(_component_v_textarea, {
              modelValue: draft.site_conf,
              "onUpdate:modelValue": _cache[22] || (_cache[22] = $event => ((draft.site_conf) = $event)),
              label: "规则内容",
              "aria-label": "规则内容",
              variant: "outlined",
              rows: "12",
              "no-resize": "",
              spellcheck: "false",
              class: "stb-code-input"
            }, null, 8, ["modelValue"])
          ], 512), [
            [_vShow, tab.value === 'advanced']
          ])
        ]),
        _createElementVNode("footer", _hoisted_11, [
          _createElementVNode("span", null, _toDisplayString(dirty.value ? "有未保存的修改" : "设置已同步"), 1),
          _createVNode(_component_v_btn, {
            variant: "text",
            onClick: close
          }, {
            default: _withCtx(() => [...(_cache[49] || (_cache[49] = [
              _createTextVNode("取消", -1)
            ]))]),
            _: 1
          }),
          _createVNode(_component_v_btn, {
            type: "submit",
            color: "primary",
            variant: "flat",
            "prepend-icon": "mdi-content-save-outline",
            loading: __props.saving,
            disabled: __props.busy || __props.saving
          }, {
            default: _withCtx(() => [...(_cache[50] || (_cache[50] = [
              _createTextVNode("保存设置", -1)
            ]))]),
            _: 1
          }, 8, ["loading", "disabled"])
        ])
      ]),
      _: 1
    }),
    _createVNode(_component_v_dialog, {
      modelValue: discard.value,
      "onUpdate:modelValue": _cache[25] || (_cache[25] = $event => ((discard).value = $event)),
      "max-width": "420"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, {
          title: "放弃未保存的修改？",
          text: "这次修改尚未保存，关闭后将恢复原设置。"
        }, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_actions, null, {
              default: _withCtx(() => [
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  onClick: _cache[23] || (_cache[23] = $event => (discard.value = false))
                }, {
                  default: _withCtx(() => [...(_cache[51] || (_cache[51] = [
                    _createTextVNode("继续编辑", -1)
                  ]))]),
                  _: 1
                }),
                _createVNode(_component_v_btn, {
                  color: "primary",
                  onClick: _cache[24] || (_cache[24] = $event => {
              discard.value = false;
              emit('close');
            })
                }, {
                  default: _withCtx(() => [...(_cache[52] || (_cache[52] = [
                    _createTextVNode("放弃修改", -1)
                  ]))]),
                  _: 1
                })
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"])
  ], 2))
}
}

};

export { DEFAULTS as D, MODULES as M, _sfc_main as _, sortedSites as a, shortTime as b, createClient as c, sizeText as d, matchesSite as m, planSummary as p, stateInfo as s };
