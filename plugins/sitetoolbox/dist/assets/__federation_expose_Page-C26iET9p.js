import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { s as stateInfo, c as createClient, p as planSummary, M as MODULES, a as sortedSites, m as matchesSite, b as shortTime, d as sizeText, _ as _sfc_main$3 } from './styles-B4AzQwru.js';

const {resolveComponent:_resolveComponent$2,createVNode:_createVNode$1,toDisplayString:_toDisplayString$1,createElementVNode:_createElementVNode$1,normalizeClass:_normalizeClass$2,openBlock:_openBlock$2,createElementBlock:_createElementBlock$2} = await importShared('vue');


const {computed: computed$1} = await importShared('vue');

const _sfc_main$2 = {
  __name: 'StateBadge',
  props: { state: String, compact: Boolean },
  setup(__props) {

const props = __props;
const info = computed$1(() => stateInfo(props.state));

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent$2("v-icon");

  return (_openBlock$2(), _createElementBlock$2("span", {
    class: _normalizeClass$2(["stb-badge", [`is-${info.value.color}`, { 'is-compact': __props.compact }]])
  }, [
    _createVNode$1(_component_v_icon, {
      icon: info.value.icon,
      size: __props.compact ? 17 : 18
    }, null, 8, ["icon", "size"]),
    _createElementVNode$1("span", null, _toDisplayString$1(info.value.label), 1)
  ], 2))
}
}

};

const {openBlock:_openBlock$1,createElementBlock:_createElementBlock$1,createCommentVNode:_createCommentVNode$1,resolveComponent:_resolveComponent$1,createBlock:_createBlock$1,normalizeClass:_normalizeClass$1} = await importShared('vue');


const _hoisted_1$1 = ["src"];

const {ref: ref$1,watch: watch$1} = await importShared('vue');


const _sfc_main$1 = {
  __name: 'SiteAvatar',
  props: { site: Object, client: Object, large: Boolean },
  setup(__props) {

const props = __props;
const icon = ref$1("");
const failed = ref$1(false);
watch$1(
  () => props.site?.id,
  async (id, _previous, onCleanup) => {
    let cancelled = false;
    onCleanup(() => {
      cancelled = true;
    });
    icon.value = "";
    failed.value = false;
    if (!id || !props.client?.icon) return;
    try {
      const value = await props.client.icon(id);
      if (cancelled) return;
      if (/^(https?:\/\/|data:image\/|\/(?!\/))/i.test(value || ""))
        icon.value = value;
      else if (/^[a-zA-Z0-9+/=]+$/.test(value || ""))
        icon.value = `data:image/png;base64,${value}`;
    } catch {
      /* 图标缺失时使用与宿主一致的站点图标。 */
    }
  },
  { immediate: true },
);

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent$1("v-icon");

  return (_openBlock$1(), _createElementBlock$1("span", {
    class: _normalizeClass$1(["stb-avatar", { 'is-large': __props.large }])
  }, [
    (icon.value && !failed.value)
      ? (_openBlock$1(), _createElementBlock$1("img", {
          key: 0,
          src: icon.value,
          alt: "",
          onError: _cache[0] || (_cache[0] = $event => (failed.value = true))
        }, null, 40, _hoisted_1$1))
      : (_openBlock$1(), _createBlock$1(_component_v_icon, {
          key: 1,
          icon: "mdi-domain",
          size: __props.large ? 34 : 24
        }, null, 8, ["size"]))
  ], 2))
}
}

};

const {resolveComponent:_resolveComponent,createVNode:_createVNode,createElementVNode:_createElementVNode,createTextVNode:_createTextVNode,withCtx:_withCtx,renderList:_renderList,Fragment:_Fragment,openBlock:_openBlock,createElementBlock:_createElementBlock,toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,createBlock:_createBlock,createCommentVNode:_createCommentVNode,unref:_unref,vModelText:_vModelText,withDirectives:_withDirectives,withModifiers:_withModifiers,withKeys:_withKeys} = await importShared('vue');


const _hoisted_1 = ["inert"];
const _hoisted_2 = { class: "stb-page-header" };
const _hoisted_3 = { class: "stb-header-actions" };
const _hoisted_4 = {
  class: "stb-nav",
  "aria-label": "工具箱导航"
};
const _hoisted_5 = ["aria-current", "onClick"];
const _hoisted_6 = {
  key: 2,
  class: "stb-running",
  role: "status",
  "aria-live": "polite"
};
const _hoisted_7 = {
  key: 3,
  class: "stb-initial-loading",
  role: "status"
};
const _hoisted_8 = {
  key: 0,
  class: "stb-sites-view"
};
const _hoisted_9 = { class: "stb-health-strip" };
const _hoisted_10 = ["aria-label", "onClick"];
const _hoisted_11 = { class: "stb-health-time" };
const _hoisted_12 = { class: "stb-table-toolbar" };
const _hoisted_13 = { class: "stb-search" };
const _hoisted_14 = {
  class: "stb-filters",
  "aria-label": "站点状态筛选"
};
const _hoisted_15 = ["aria-pressed", "onClick"];
const _hoisted_16 = { class: "stb-table-surface" };
const _hoisted_17 = { class: "stb-table-scroll" };
const _hoisted_18 = { class: "stb-site-table" };
const _hoisted_19 = ["aria-sort"];
const _hoisted_20 = ["aria-sort"];
const _hoisted_21 = ["aria-sort"];
const _hoisted_22 = ["onClick"];
const _hoisted_23 = { class: "stb-site-name" };
const _hoisted_24 = { class: "stb-desktop-rss" };
const _hoisted_25 = ["title"];
const _hoisted_26 = { class: "stb-mobile-overall" };
const _hoisted_27 = { class: "stb-row-reason" };
const _hoisted_28 = { class: "stb-nowrap" };
const _hoisted_29 = { class: "stb-muted" };
const _hoisted_30 = { class: "stb-nowrap stb-muted" };
const _hoisted_31 = { class: "stb-row-actions" };
const _hoisted_32 = { key: 0 };
const _hoisted_33 = {
  colspan: "6",
  class: "stb-empty"
};
const _hoisted_34 = { class: "stb-pagination" };
const _hoisted_35 = { key: 0 };
const _hoisted_36 = {
  key: 1,
  class: "stb-maintenance-view"
};
const _hoisted_37 = { class: "stb-subtabs" };
const _hoisted_38 = ["onClick"];
const _hoisted_39 = { key: 0 };
const _hoisted_40 = { class: "stb-section-heading" };
const _hoisted_41 = { class: "stb-row-actions" };
const _hoisted_42 = {
  key: 0,
  class: "stb-empty stb-surface"
};
const _hoisted_43 = {
  key: 1,
  class: "stb-table-surface stb-table-scroll"
};
const _hoisted_44 = { class: "stb-plain-table" };
const _hoisted_45 = { class: "stb-block stb-muted" };
const _hoisted_46 = { class: "stb-block stb-muted" };
const _hoisted_47 = { class: "stb-path" };
const _hoisted_48 = {
  key: 2,
  class: "stb-inline-note mt-4"
};
const _hoisted_49 = {
  key: 1,
  class: "stb-tool-list"
};
const _hoisted_50 = { key: 2 };
const _hoisted_51 = { class: "stb-section-heading" };
const _hoisted_52 = { class: "stb-table-surface" };
const _hoisted_53 = { class: "stb-plain-table" };
const _hoisted_54 = { key: 0 };
const _hoisted_55 = {
  key: 2,
  class: "stb-logs-view"
};
const _hoisted_56 = { class: "stb-task-list" };
const _hoisted_57 = {
  key: 1,
  class: "stb-empty stb-surface"
};
const _hoisted_58 = { class: "stb-muted" };
const _hoisted_59 = { class: "stb-section-heading" };
const _hoisted_60 = { class: "stb-error-filters" };
const _hoisted_61 = {
  key: 2,
  class: "stb-field-note mb-3"
};
const _hoisted_62 = { class: "stb-table-surface stb-table-scroll" };
const _hoisted_63 = { class: "stb-plain-table" };
const _hoisted_64 = { class: "stb-muted" };
const _hoisted_65 = { class: "stb-error-message" };
const _hoisted_66 = { class: "stb-nowrap" };
const _hoisted_67 = { key: 0 };
const _hoisted_68 = {
  colspan: "3",
  class: "stb-empty"
};
const _hoisted_69 = { class: "stb-version" };
const _hoisted_70 = ["role", "aria-modal", "onKeydown"];
const _hoisted_71 = { class: "stb-detail-header" };
const _hoisted_72 = ["href", "aria-label"];
const _hoisted_73 = { class: "stb-detail-tabs" };
const _hoisted_74 = {
  key: 0,
  class: "stb-detail-card"
};
const _hoisted_75 = { class: "stb-current-status" };
const _hoisted_76 = { class: "stb-streak" };
const _hoisted_77 = { class: "stb-detail-times" };
const _hoisted_78 = { class: "stb-diagnostic-note" };
const _hoisted_79 = { key: 0 };
const _hoisted_80 = { class: "stb-check-list" };
const _hoisted_81 = ["title"];
const _hoisted_82 = { class: "stb-global-scope-note" };
const _hoisted_83 = { class: "stb-detail-actions" };
const _hoisted_84 = {
  key: 1,
  class: "stb-detail-card"
};
const _hoisted_85 = { class: "stb-info-list" };
const _hoisted_86 = {
  key: 1,
  class: "stb-detail-placeholder"
};
const _hoisted_87 = {
  key: 2,
  class: "stb-plan-card"
};
const _hoisted_88 = { class: "stb-plan-timezone" };

const {computed,inject,nextTick,onBeforeUnmount,onMounted,ref,watch} = await importShared('vue');

const {useDisplay,useTheme} = await importShared('vuetify');

const _sfc_main = {
  __name: 'Page',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: "sitetoolbox" },
  sourcePluginId: String,
},
  emits: ["close", "switch", "layout", "action"],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;
const client = createClient(props.api, props.pluginId || "sitetoolbox");
const toast = inject("moviepilot:toast", null);
const display = useDisplay();
const hostTheme = useTheme();
const isLight = computed(() => !hostTheme.current.value.dark);
const mobile = computed(() => display.width.value < 1100);
const overview = ref(null),
  error = ref(""),
  loading = ref(true),
  submitting = ref(false);
const activeTab = ref("sites"),
  maintenanceTab = ref("missing"),
  detailTab = ref("diagnosis");
const query = ref(""),
  filter = ref("all"),
  sortKey = ref("attention"),
  ascending = ref(true),
  page = ref(1),
  pageSize = ref(8);
const selectedId = ref(null),
  detailOpen = ref(false);
const detailPane = ref(null);
let detailTrigger;
const settingsOpen = ref(false),
  settings = ref({}),
  options = ref({}),
  saving = ref(false);
const snackbar = ref({ show: false, text: "", color: "success" });
const confirmation = ref({
  open: false,
  title: "",
  text: "",
  destructive: false,
  action: null,
});
const errorGroups = ref({ items: [], total: 0 }),
  errorQuery = ref(""),
  errorLevel = ref("all"),
  errorPage = ref(1),
  errorsLoading = ref(false),
  errorsError = ref("");
const trackedJobs = new Set();
let timer,
  errorTimer,
  refreshTimer,
  lastIdleRefresh = 0,
  loadSequence = 0,
  errorsSequence = 0;
const sites = computed(() => overview.value?.sites || []);
const plan = computed(() => overview.value?.plan || {});
const summary = computed(() => planSummary(plan.value));
const threshold = computed(() => plan.value.monitor_failure_days || 3);
const jobs = computed(() => Object.values(overview.value?.jobs || {}));
const running = computed(() =>
  jobs.value.filter((job) => job.status === "running"),
);
const busy = computed(() => submitting.value || running.value.length > 0);
const selected = computed(() =>
  sites.value.find((site) => site.id === selectedId.value),
);
const checks = computed(() =>
  Object.entries(selected.value?.checks || {}).map(([key, value]) => ({
    key,
    label: MODULES[key],
    ...value,
  })),
);
const selectedMeta = computed(() => stateInfo(selected.value?.state));
const firstFailedAt = computed(
  () =>
    checks.value
      .map((check) => check.first_failed_at)
      .filter(Boolean)
      .sort()[0] || "尚无记录",
);
const primaryReason = computed(
  () =>
    checks.value.find((check) => !["success", "unknown"].includes(check.state))
      ?.raw_message ||
    checks.value.find((check) => check.state === "unknown")?.message ||
    "已检测项目均正常。",
);
const lastChecked = computed(
  () =>
    sites.value
      .map((site) => site.last_checked_at || "")
      .sort()
      .at(-1) || "",
);
const counts = computed(() => ({
  all: sites.value.length,
  success: sites.value.filter((site) => site.state === "success").length,
  pending: sites.value.filter((site) => site.state === "pending").length,
  offline: sites.value.filter((site) => site.state === "offline").length,
  confirmed: sites.value.filter((site) => site.confirmed).length,
  unknown: sites.value.filter((site) => site.state === "unknown").length,
}));
const filters = computed(() => [
  { value: "all", label: "全部" },
  { value: "success", label: "正常", color: "success" },
  { value: "pending", label: "待观察", color: "pending" },
  { value: "offline", label: "连接失败", color: "offline" },
  { value: "confirmed", label: "已确认异常", color: "error" },
  ...(counts.value.unknown
    ? [{ value: "unknown", label: "未检测", color: "unknown" }]
    : []),
]);
const filtered = computed(() =>
  sortedSites(
    sites.value.filter((site) => matchesSite(site, query.value, filter.value)),
    sortKey.value,
    ascending.value,
  ),
);
const pageCount = computed(() =>
  Math.max(1, Math.ceil(filtered.value.length / pageSize.value)),
);
const visibleSites = computed(() =>
  filtered.value.slice(
    (page.value - 1) * pageSize.value,
    page.value * pageSize.value,
  ),
);
const missing = computed(() => overview.value?.maintenance?.missing || {});
const missingItems = computed(() => missing.value.items || []);
const runs = computed(() => overview.value?.runs || []);
watch([query, filter, pageSize], () => {
  page.value = 1;
});
watch(pageCount, (count) => {
  page.value = Math.min(page.value, count);
});
watch([errorQuery, errorLevel], () => {
  errorPage.value = 1;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(loadErrors, 300);
});
watch(errorPage, loadErrors);
watch(activeTab, (tab) => {
  if (tab === "logs") loadErrors();
});
function notify(text, color = "success") {
  if (toast?.[color]) toast[color](text);
  else snackbar.value = { show: true, text, color };
}
async function load(silent = false) {
  const sequence = ++loadSequence;
  if (!silent) loading.value = true;
  try {
    const response = await client.get("/overview");
    if (!response.success)
      throw new Error(response.message || "读取工作台失败");
    if (sequence !== loadSequence) return;
    const initial = !overview.value;
    overview.value = response.data;
    error.value = "";
    if (initial && sites.value.length && !mobile.value) {
      selectedId.value = sortedSites(sites.value)[0].id;
      detailOpen.value = true;
    }
    for (const key of [...trackedJobs]) {
      const job = overview.value.jobs?.[key];
      if (job && job.status !== "running") {
        notify(
          job.message || "检测完成",
          job.status === "error" ? "error" : "success",
        );
        trackedJobs.delete(key);
      }
    }
  } catch (e) {
    if (sequence === loadSequence)
      error.value = e?.message || "读取失败，请稍后重试";
  } finally {
    if (sequence === loadSequence) loading.value = false;
  }
}
async function perform(path, body = {}) {
  if (busy.value) return;
  submitting.value = true;
  try {
    const response = await client.post(path, body);
    if (!response.success) throw new Error(response.message || "操作未完成");
    if (response.data?.key) trackedJobs.add(response.data.key);
    notify(response.message || "任务已提交", "info");
    await load(true);
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => load(true), 1000);
  } catch (e) {
    notify(e?.message || "请求失败，请重试", "error");
  } finally {
    submitting.value = false;
  }
}
function recheck(site) {
  const modules = (plan.value.monitor_modules || []).filter(
    (key) => key !== "missing" && (key !== "mteam" || site.checks.mteam),
  );
  if (!modules.length)
    return notify("当前计划没有启用适用于该站点的检测模块", "warning");
  perform("/monitor/run", { site_ids: [site.id], modules });
}
function choose(site, event) {
  detailTrigger =
    event?.currentTarget?.closest("button") ||
    event?.currentTarget?.querySelector('button[aria-label^="查看"]');
  selectedId.value = site.id;
  detailTab.value = "diagnosis";
  detailOpen.value = true;
  if (mobile.value)
    nextTick(() =>
      detailPane.value
        ?.querySelector('button[aria-label="关闭站点详情"]')
        ?.focus(),
    );
}
function closeDetails() {
  detailOpen.value = false;
  if (mobile.value) nextTick(() => detailTrigger?.focus());
}
function trapDetailFocus(event) {
  if (!mobile.value || event.key !== "Tab") return;
  const controls = [
    ...detailPane.value.querySelectorAll(
      "a[href], button:not([disabled]), input:not([disabled])",
    ),
  ].filter((el) => el.getClientRects().length);
  const first = controls[0],
    last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
function sort(key) {
  if (sortKey.value === key) ascending.value = !ascending.value;
  else {
    sortKey.value = key;
    ascending.value = true;
  }
  page.value = 1;
}
function sortAria(key) {
  return sortKey.value !== key
    ? "none"
    : ascending.value
      ? "ascending"
      : "descending";
}
function reason(site) {
  return !["success", "unknown"].includes(site.checks.rss.state)
    ? site.checks.rss.raw_message || ""
    : "";
}
async function openSettings() {
  try {
    const [config, response] = await Promise.all([
      client.settings(),
      client.get("/options"),
    ]);
    if (!response.success) throw new Error(response.message);
    settings.value = config;
    options.value = response.data;
    settingsOpen.value = true;
  } catch (e) {
    notify(e?.message || "无法打开设置，请重试", "error");
  }
}
async function saveSettings(changes) {
  saving.value = true;
  try {
    const response = await client.saveSettings(changes);
    if (!response.success) throw new Error(response.message || "保存失败");
    settingsOpen.value = false;
    notify("设置已保存，所有检测模块使用新的统一计划");
    await load(true);
  } catch (e) {
    notify(e?.message || "保存失败，修改仍保留在表单中", "error");
  } finally {
    saving.value = false;
  }
}
function confirm(title, text, action, destructive = false) {
  confirmation.value = { open: true, title, text, action, destructive };
}
function acceptConfirmation() {
  const action = confirmation.value.action;
  confirmation.value.open = false;
  action?.();
}
function repair(site = null) {
  confirm(
    "尝试修复 RSS？",
    `${site ? `将重新获取 ${site.name} 的 RSS 地址` : "将重新获取配置范围内站点的 RSS 地址"}，只有验证成功才写回站点配置。`,
    () => perform(site ? `/repair/rss/${site.id}` : "/repair/rss"),
  );
}
function cleanup() {
  const count = missing.value.confirmed_count || 0;
  const deleteFiles = Boolean(overview.value?.maintenance?.delete_files);
  confirm(
    "清理已确认的缺失种子？",
    `将处理 ${count} 个已达到连续异常天数阈值的种子。${deleteFiles ? "同时删除数据文件已开启，相关数据文件也会删除。" : "只删除下载器任务，保留数据文件。"}清理前会再次核对任务状态。`,
    () => perform("/cleanup/missing", { delete_files: deleteFiles }),
    true,
  );
}
async function copyDiagnostic() {
  try {
    await navigator.clipboard.writeText(
      JSON.stringify(
        {
          site: selected.value.name,
          domain: selected.value.domain,
          checks: selected.value.checks,
        },
        null,
        2,
      ),
    );
    notify("诊断信息已复制");
  } catch {
    notify("浏览器不允许复制，请直接查看诊断内容", "warning");
  }
}
async function loadErrors() {
  const sequence = ++errorsSequence;
  errorsLoading.value = true;
  try {
    const response = await client.get("/errors", {
      query: errorQuery.value,
      level: errorLevel.value,
      page: errorPage.value,
      limit: 10,
    });
    if (!response.success) throw new Error(response.message);
    if (sequence === errorsSequence) {
      errorGroups.value = response.data;
      errorsError.value = "";
    }
  } catch (e) {
    if (sequence === errorsSequence)
      errorsError.value = e?.message || "读取日志失败";
  } finally {
    if (sequence === errorsSequence) errorsLoading.value = false;
  }
}
onMounted(async () => {
  emit("layout", { maxWidth: "96rem" });
  await load();
  timer = setInterval(() => {
    if (document.visibilityState === "hidden") return;
    if (
      running.value.length ||
      trackedJobs.size ||
      Date.now() - lastIdleRefresh > 30000
    ) {
      lastIdleRefresh = Date.now();
      load(true);
    }
  }, 2000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  clearTimeout(errorTimer);
  clearTimeout(refreshTimer);
  loadSequence++;
  errorsSequence++;
});

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_progress_circular = _resolveComponent("v-progress-circular");
  const _component_v_pagination = _resolveComponent("v-pagination");
  const _component_v_select = _resolveComponent("v-select");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_dialog = _resolveComponent("v-dialog");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_card_actions = _resolveComponent("v-card-actions");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_snackbar = _resolveComponent("v-snackbar");

  return (_openBlock(), _createElementBlock("div", {
    class: _normalizeClass(["stb-root stb-layout", {
      'stb-detail-open': detailOpen.value && selected.value && activeTab.value === 'sites',
      'stb-is-light': isLight.value,
    }])
  }, [
    _createElementVNode("main", {
      class: "stb-main",
      inert: mobile.value && detailOpen.value && selected.value && activeTab.value === 'sites'
    }, [
      _createElementVNode("header", _hoisted_2, [
        _createVNode(_component_v_icon, {
          icon: "mdi-toolbox",
          class: "stb-toolbox-icon",
          size: "64"
        }),
        _cache[28] || (_cache[28] = _createElementVNode("div", { class: "stb-heading" }, [
          _createElementVNode("h1", null, "站点工具箱"),
          _createElementVNode("p", null, "统一监控站点的 RSS、用户数据、缺失种子和登录状态。")
        ], -1)),
        _createElementVNode("div", _hoisted_3, [
          _createVNode(_component_v_btn, {
            color: "primary",
            variant: "flat",
            "prepend-icon": "mdi-play",
            loading: submitting.value,
            disabled: busy.value || loading.value,
            onClick: _cache[0] || (_cache[0] = $event => (perform('/monitor/run')))
          }, {
            default: _withCtx(() => [...(_cache[26] || (_cache[26] = [
              _createTextVNode("立即检测", -1)
            ]))]),
            _: 1
          }, 8, ["loading", "disabled"]),
          _createVNode(_component_v_btn, {
            variant: "outlined",
            "prepend-icon": "mdi-cog-outline",
            onClick: openSettings
          }, {
            default: _withCtx(() => [...(_cache[27] || (_cache[27] = [
              _createTextVNode("设置", -1)
            ]))]),
            _: 1
          })
        ])
      ]),
      _createElementVNode("nav", _hoisted_4, [
        (_openBlock(), _createElementBlock(_Fragment, null, _renderList([
            { key: 'sites', label: '站点诊断' },
            { key: 'maintenance', label: '维护工具' },
            { key: 'logs', label: '运行记录' },
          ], (tab) => {
          return _createElementVNode("button", {
            key: tab.key,
            type: "button",
            class: _normalizeClass({ active: activeTab.value === tab.key }),
            "aria-current": activeTab.value === tab.key ? 'page' : undefined,
            onClick: $event => (activeTab.value = tab.key)
          }, _toDisplayString(tab.label), 11, _hoisted_5)
        }), 64))
      ]),
      (error.value)
        ? (_openBlock(), _createBlock(_component_v_alert, {
            key: 0,
            type: "error",
            variant: "tonal",
            class: "my-4",
            role: "alert"
          }, {
            append: _withCtx(() => [
              _createVNode(_component_v_btn, {
                variant: "text",
                onClick: _cache[1] || (_cache[1] = $event => (load()))
              }, {
                default: _withCtx(() => [...(_cache[29] || (_cache[29] = [
                  _createTextVNode("重试", -1)
                ]))]),
                _: 1
              })
            ]),
            default: _withCtx(() => [
              _createTextVNode(_toDisplayString(error.value), 1)
            ]),
            _: 1
          }))
        : _createCommentVNode("", true),
      (overview.value && !overview.value.enabled)
        ? (_openBlock(), _createBlock(_component_v_alert, {
            key: 1,
            type: "info",
            variant: "tonal",
            class: "my-4"
          }, {
            default: _withCtx(() => [
              _cache[31] || (_cache[31] = _createTextVNode("插件已停用，定时监控和站点适配未运行。", -1)),
              _createVNode(_component_v_btn, {
                variant: "text",
                size: "small",
                onClick: openSettings
              }, {
                default: _withCtx(() => [...(_cache[30] || (_cache[30] = [
                  _createTextVNode("打开设置", -1)
                ]))]),
                _: 1
              })
            ]),
            _: 1
          }))
        : _createCommentVNode("", true),
      (running.value.length)
        ? (_openBlock(), _createElementBlock("div", _hoisted_6, [
            _createVNode(_component_v_progress_circular, {
              indeterminate: "",
              size: "19",
              width: "2",
              color: "primary"
            }),
            _createElementVNode("div", null, [
              _createElementVNode("strong", null, _toDisplayString(running.value[0].name), 1),
              _createElementVNode("span", null, _toDisplayString(running.value[0].message || "正在检测，请稍候…"), 1)
            ]),
            _cache[32] || (_cache[32] = _createElementVNode("span", null, "结果会自动更新", -1))
          ]))
        : _createCommentVNode("", true),
      (loading.value && !overview.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_7, [
            _createVNode(_component_v_progress_circular, {
              indeterminate: "",
              color: "primary"
            }),
            _cache[33] || (_cache[33] = _createElementVNode("span", null, "正在读取站点状态…", -1))
          ]))
        : (overview.value)
          ? (_openBlock(), _createElementBlock(_Fragment, { key: 4 }, [
              (activeTab.value === 'sites')
                ? (_openBlock(), _createElementBlock("section", _hoisted_8, [
                    _createElementVNode("div", _hoisted_9, [
                      (_openBlock(), _createElementBlock(_Fragment, null, _renderList([
                { state: 'success', key: 'success', label: '正常' },
                { state: 'pending', key: 'pending', label: '待观察' },
                { state: 'offline', key: 'offline', label: '连接失败' },
                { state: 'error', key: 'confirmed', label: '已确认异常' },
              ], (metric) => {
                        return _createElementVNode("button", {
                          key: metric.key,
                          type: "button",
                          class: "stb-health-metric",
                          "aria-label": `筛选${metric.label}站点`,
                          onClick: $event => (filter.value = metric.key)
                        }, [
                          _createElementVNode("span", {
                            class: _normalizeClass(["stb-metric-icon", `is-${metric.key === 'confirmed' && !counts.value.confirmed ? 'unknown' : _unref(stateInfo)(metric.state).color}`])
                          }, [
                            _createVNode(_component_v_icon, {
                              icon: _unref(stateInfo)(metric.state).icon,
                              size: "26"
                            }, null, 8, ["icon"])
                          ], 2),
                          _createElementVNode("span", null, [
                            _createElementVNode("small", null, _toDisplayString(metric.label), 1),
                            _createElementVNode("strong", null, [
                              _createTextVNode(_toDisplayString(counts.value[metric.key]), 1),
                              _createElementVNode("em", null, "/ " + _toDisplayString(counts.value.all), 1)
                            ])
                          ])
                        ], 8, _hoisted_10)
                      }), 64)),
                      _createElementVNode("div", _hoisted_11, [
                        _cache[35] || (_cache[35] = _createElementVNode("small", null, "最后检测", -1)),
                        _createElementVNode("span", null, _toDisplayString(_unref(shortTime)(lastChecked.value)), 1),
                        _createElementVNode("small", null, [
                          _cache[34] || (_cache[34] = _createTextVNode("下次运行 ", -1)),
                          _createElementVNode("b", null, _toDisplayString(plan.value.next_run_at ? _unref(shortTime)(plan.value.next_run_at) : "未启用"), 1)
                        ])
                      ])
                    ]),
                    _createElementVNode("div", _hoisted_12, [
                      _createElementVNode("label", _hoisted_13, [
                        _createVNode(_component_v_icon, {
                          icon: "mdi-magnify",
                          size: "21"
                        }),
                        _withDirectives(_createElementVNode("input", {
                          "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((query).value = $event)),
                          type: "search",
                          "aria-label": "搜索站点名称或域名",
                          placeholder: "搜索站点名称或域名…"
                        }, null, 512), [
                          [_vModelText, query.value]
                        ])
                      ]),
                      _createElementVNode("div", _hoisted_14, [
                        (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(filters.value, (item) => {
                          return (_openBlock(), _createElementBlock("button", {
                            key: item.value,
                            type: "button",
                            class: _normalizeClass({ active: filter.value === item.value }),
                            "aria-pressed": filter.value === item.value,
                            onClick: $event => (filter.value = item.value)
                          }, [
                            (item.color)
                              ? (_openBlock(), _createElementBlock("span", {
                                  key: 0,
                                  class: _normalizeClass(["stb-status-dot", `is-${item.color}`])
                                }, null, 2))
                              : _createCommentVNode("", true),
                            _createTextVNode(_toDisplayString(item.label) + " ", 1),
                            _createElementVNode("span", null, "(" + _toDisplayString(counts.value[item.value]) + ")", 1)
                          ], 10, _hoisted_15))
                        }), 128))
                      ])
                    ]),
                    _createElementVNode("div", _hoisted_16, [
                      _createElementVNode("div", _hoisted_17, [
                        _createElementVNode("table", _hoisted_18, [
                          _createElementVNode("thead", null, [
                            _createElementVNode("tr", null, [
                              _createElementVNode("th", {
                                "aria-sort": sortAria('name')
                              }, [
                                _createElementVNode("button", {
                                  type: "button",
                                  onClick: _cache[3] || (_cache[3] = $event => (sort('name')))
                                }, [
                                  _cache[36] || (_cache[36] = _createTextVNode(" 站点", -1)),
                                  _createVNode(_component_v_icon, {
                                    icon: "mdi-unfold-more-horizontal",
                                    size: "16"
                                  })
                                ])
                              ], 8, _hoisted_19),
                              _cache[39] || (_cache[39] = _createElementVNode("th", null, "RSS", -1)),
                              _cache[40] || (_cache[40] = _createElementVNode("th", null, "用户数据", -1)),
                              _createElementVNode("th", {
                                "aria-sort": sortAria('failure_days')
                              }, [
                                _createElementVNode("button", {
                                  type: "button",
                                  onClick: _cache[4] || (_cache[4] = $event => (sort('failure_days')))
                                }, [
                                  _cache[37] || (_cache[37] = _createTextVNode(" 连续异常", -1)),
                                  _createVNode(_component_v_icon, {
                                    icon: "mdi-unfold-more-horizontal",
                                    size: "16"
                                  })
                                ])
                              ], 8, _hoisted_20),
                              _createElementVNode("th", {
                                "aria-sort": sortAria('last_checked_at')
                              }, [
                                _createElementVNode("button", {
                                  type: "button",
                                  onClick: _cache[5] || (_cache[5] = $event => (sort('last_checked_at')))
                                }, [
                                  _cache[38] || (_cache[38] = _createTextVNode(" 最近检测", -1)),
                                  _createVNode(_component_v_icon, {
                                    icon: "mdi-unfold-more-horizontal",
                                    size: "16"
                                  })
                                ])
                              ], 8, _hoisted_21),
                              _cache[41] || (_cache[41] = _createElementVNode("th", null, "操作", -1))
                            ])
                          ]),
                          _createElementVNode("tbody", null, [
                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(visibleSites.value, (site) => {
                              return (_openBlock(), _createElementBlock("tr", {
                                key: site.id,
                                class: _normalizeClass({ selected: selectedId.value === site.id && detailOpen.value }),
                                onClick: $event => (choose(site, $event))
                              }, [
                                _createElementVNode("td", null, [
                                  _createElementVNode("div", _hoisted_23, [
                                    _createVNode(_sfc_main$1, {
                                      site: site,
                                      client: _unref(client)
                                    }, null, 8, ["site", "client"]),
                                    _createElementVNode("div", null, [
                                      _createElementVNode("strong", null, _toDisplayString(site.name), 1),
                                      _createElementVNode("small", null, _toDisplayString(site.domain), 1)
                                    ])
                                  ])
                                ]),
                                _createElementVNode("td", null, [
                                  _createElementVNode("div", _hoisted_24, [
                                    _createVNode(_sfc_main$2, {
                                      state: site.checks.rss.state,
                                      compact: ""
                                    }, null, 8, ["state"]),
                                    (reason(site))
                                      ? (_openBlock(), _createElementBlock("small", {
                                          key: 0,
                                          class: "stb-row-reason",
                                          title: reason(site)
                                        }, _toDisplayString(reason(site)), 9, _hoisted_25))
                                      : _createCommentVNode("", true)
                                  ]),
                                  _createElementVNode("div", _hoisted_26, [
                                    _createVNode(_sfc_main$2, {
                                      state: site.state,
                                      compact: ""
                                    }, null, 8, ["state"]),
                                    _createElementVNode("small", _hoisted_27, _toDisplayString(Object.values(site.checks).find(
                              (check) =>
                                !["success", "unknown"].includes(check.state),
                            )?.raw_message || ""), 1)
                                  ])
                                ]),
                                _createElementVNode("td", null, [
                                  _createVNode(_sfc_main$2, {
                                    state: site.checks.userdata.state,
                                    compact: ""
                                  }, null, 8, ["state"])
                                ]),
                                _createElementVNode("td", _hoisted_28, [
                                  _createElementVNode("span", {
                                    class: _normalizeClass({ 'stb-attention-text': site.failure_days > 0 })
                                  }, _toDisplayString(site.failure_days || 0), 3),
                                  _createElementVNode("span", _hoisted_29, _toDisplayString(site.failure_days ? ` / ${threshold.value} 天` : " 天"), 1)
                                ]),
                                _createElementVNode("td", _hoisted_30, _toDisplayString(_unref(shortTime)(site.last_checked_at)), 1),
                                _createElementVNode("td", null, [
                                  _createElementVNode("div", _hoisted_31, [
                                    _createVNode(_component_v_btn, {
                                      size: "small",
                                      variant: "flat",
                                      color: "primary",
                                      "prepend-icon": "mdi-refresh",
                                      "aria-label": `复测${site.name}`,
                                      disabled: busy.value,
                                      onClick: _withModifiers($event => (recheck(site)), ["stop"])
                                    }, {
                                      default: _withCtx(() => [...(_cache[42] || (_cache[42] = [
                                        _createTextVNode("复测", -1)
                                      ]))]),
                                      _: 1
                                    }, 8, ["aria-label", "disabled", "onClick"]),
                                    _createVNode(_component_v_btn, {
                                      size: "small",
                                      variant: "outlined",
                                      "prepend-icon": "mdi-text-box-search-outline",
                                      "aria-label": `查看${site.name}详情`,
                                      onClick: _withModifiers($event => (choose(site, $event)), ["stop"])
                                    }, {
                                      default: _withCtx(() => [...(_cache[43] || (_cache[43] = [
                                        _createTextVNode("详情", -1)
                                      ]))]),
                                      _: 1
                                    }, 8, ["aria-label", "onClick"])
                                  ])
                                ])
                              ], 10, _hoisted_22))
                            }), 128)),
                            (!visibleSites.value.length)
                              ? (_openBlock(), _createElementBlock("tr", _hoisted_32, [
                                  _createElementVNode("td", _hoisted_33, [
                                    _createVNode(_component_v_icon, {
                                      icon: "mdi-magnify-close",
                                      size: "32"
                                    }),
                                    _createElementVNode("h3", null, _toDisplayString(sites.value.length
                            ? "没有符合条件的站点"
                            : "还没有可展示的站点"), 1),
                                    _createElementVNode("p", null, _toDisplayString(sites.value.length
                            ? "试试其他名称、域名或状态筛选。"
                            : "请先在 MoviePilot 站点管理中添加站点。"), 1),
                                    (query.value || filter.value !== 'all')
                                      ? (_openBlock(), _createBlock(_component_v_btn, {
                                          key: 0,
                                          variant: "text",
                                          color: "primary",
                                          onClick: _cache[6] || (_cache[6] = $event => {
                          query.value = '';
                          filter.value = 'all';
                        })
                                        }, {
                                          default: _withCtx(() => [...(_cache[44] || (_cache[44] = [
                                            _createTextVNode("清除筛选", -1)
                                          ]))]),
                                          _: 1
                                        }))
                                      : _createCommentVNode("", true)
                                  ])
                                ]))
                              : _createCommentVNode("", true)
                          ])
                        ])
                      ]),
                      _createElementVNode("footer", _hoisted_34, [
                        _createElementVNode("span", null, [
                          _createTextVNode("共 " + _toDisplayString(filtered.value.length) + " 个站点", 1),
                          (filtered.value.length)
                            ? (_openBlock(), _createElementBlock("span", _hoisted_35, "　|　" + _toDisplayString((page.value - 1) * pageSize.value + 1) + "–" + _toDisplayString(Math.min(page.value * pageSize.value, filtered.value.length)) + " / " + _toDisplayString(filtered.value.length), 1))
                            : _createCommentVNode("", true)
                        ]),
                        _createVNode(_component_v_pagination, {
                          modelValue: page.value,
                          "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((page).value = $event)),
                          length: pageCount.value,
                          "total-visible": mobile.value ? 3 : 5,
                          density: "compact",
                          rounded: "lg",
                          "show-first-last-page": false
                        }, null, 8, ["modelValue", "length", "total-visible"]),
                        _createVNode(_component_v_select, {
                          modelValue: pageSize.value,
                          "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((pageSize).value = $event)),
                          items: [
                  { title: '8 条/页', value: 8 },
                  { title: '15 条/页', value: 15 },
                  { title: '30 条/页', value: 30 },
                ],
                          "aria-label": "每页站点数",
                          variant: "outlined",
                          density: "compact",
                          "hide-details": ""
                        }, null, 8, ["modelValue"])
                      ])
                    ])
                  ]))
                : (activeTab.value === 'maintenance')
                  ? (_openBlock(), _createElementBlock("section", _hoisted_36, [
                      _cache[59] || (_cache[59] = _createElementVNode("div", { class: "stb-section-heading" }, [
                        _createElementVNode("div", null, [
                          _createElementVNode("h2", null, "维护工具"),
                          _createElementVNode("p", null, "先查看影响范围，再执行会修改数据的操作。")
                        ])
                      ], -1)),
                      _createElementVNode("div", _hoisted_37, [
                        (_openBlock(), _createElementBlock(_Fragment, null, _renderList([
                { key: 'missing', label: '缺失种子' },
                { key: 'rss', label: 'RSS 与登录' },
                { key: 'rules', label: '适配规则' },
              ], (item) => {
                          return _createElementVNode("button", {
                            key: item.key,
                            class: _normalizeClass({ active: maintenanceTab.value === item.key }),
                            onClick: $event => (maintenanceTab.value = item.key)
                          }, _toDisplayString(item.label), 11, _hoisted_38)
                        }), 64))
                      ]),
                      (maintenanceTab.value === 'missing')
                        ? (_openBlock(), _createElementBlock("div", _hoisted_39, [
                            _createElementVNode("div", _hoisted_40, [
                              _createElementVNode("div", null, [
                                _cache[45] || (_cache[45] = _createElementVNode("h3", null, "缺失文件种子", -1)),
                                _createElementVNode("p", null, " 发现 " + _toDisplayString(missing.value.total_count || 0) + " 个，已确认 " + _toDisplayString(missing.value.confirmed_count || 0) + " 个 · 最近扫描 " + _toDisplayString(_unref(shortTime)(missing.value.created_at)), 1)
                              ]),
                              _createElementVNode("div", _hoisted_41, [
                                _createVNode(_component_v_btn, {
                                  variant: "outlined",
                                  "prepend-icon": "mdi-magnify-scan",
                                  disabled: busy.value,
                                  onClick: _cache[9] || (_cache[9] = $event => (perform('/cleanup/missing/preview')))
                                }, {
                                  default: _withCtx(() => [...(_cache[46] || (_cache[46] = [
                                    _createTextVNode("扫描", -1)
                                  ]))]),
                                  _: 1
                                }, 8, ["disabled"]),
                                _createVNode(_component_v_btn, {
                                  color: "error",
                                  variant: "tonal",
                                  "prepend-icon": "mdi-delete-outline",
                                  disabled: busy.value || !missing.value.confirmed_count,
                                  onClick: cleanup
                                }, {
                                  default: _withCtx(() => [...(_cache[47] || (_cache[47] = [
                                    _createTextVNode("清理已确认项", -1)
                                  ]))]),
                                  _: 1
                                }, 8, ["disabled"])
                              ])
                            ]),
                            _createVNode(_component_v_alert, {
                              type: overview.value.maintenance.delete_files ? 'warning' : 'info',
                              variant: "tonal",
                              density: "compact",
                              class: "my-4"
                            }, {
                              default: _withCtx(() => [
                                _createTextVNode(_toDisplayString(overview.value.maintenance.delete_files
                  ? "当前设置会同时删除数据文件。清理前需要再次确认。"
                  : "当前清理仅删除下载器任务，保留数据文件。") + " 只有连续异常达到 " + _toDisplayString(threshold.value) + " 天的种子才可清理。", 1)
                              ]),
                              _: 1
                            }, 8, ["type"]),
                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(missing.value.errors || [], (item) => {
                              return (_openBlock(), _createBlock(_component_v_alert, {
                                key: item.downloader,
                                type: "warning",
                                variant: "tonal",
                                density: "compact",
                                class: "mb-2"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(item.downloader) + "：" + _toDisplayString((missing.value.downloader_status || []).find(
                  (status) =>
                    status.object_id === `downloader:${item.downloader}`,
                )?.message || item.message), 1)
                                ]),
                                _: 2
                              }, 1024))
                            }), 128)),
                            (!missingItems.value.length)
                              ? (_openBlock(), _createElementBlock("div", _hoisted_42, [
                                  _createVNode(_component_v_icon, {
                                    icon:
                  missing.value.errors?.length
                    ? 'mdi-alert-circle-outline'
                    : 'mdi-check-circle-outline'
                ,
                                    size: "40",
                                    color: missing.value.errors?.length ? 'warning' : 'success'
                                  }, null, 8, ["icon", "color"]),
                                  _createElementVNode("h3", null, _toDisplayString(missing.value.errors?.length
                    ? "部分下载器扫描失败"
                    : missing.value.created_at
                      ? "最近扫描未发现缺失文件种子"
                      : "尚未扫描缺失文件种子"), 1),
                                  _createElementVNode("p", null, _toDisplayString(missing.value.created_at
                    ? "下载器连接失败时，相关任务的状态仍待确认。"
                    : "选择下载器后运行扫描，结果会保留在这里。"), 1)
                                ]))
                              : (_openBlock(), _createElementBlock("div", _hoisted_43, [
                                  _createElementVNode("table", _hoisted_44, [
                                    _cache[48] || (_cache[48] = _createElementVNode("thead", null, [
                                      _createElementVNode("tr", null, [
                                        _createElementVNode("th", null, "种子 / 下载器"),
                                        _createElementVNode("th", null, "大小"),
                                        _createElementVNode("th", null, "确认状态"),
                                        _createElementVNode("th", null, "保存路径")
                                      ])
                                    ], -1)),
                                    _createElementVNode("tbody", null, [
                                      (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(missingItems.value, (item) => {
                                        return (_openBlock(), _createElementBlock("tr", {
                                          key: `${item.downloader}:${item.hash}`
                                        }, [
                                          _createElementVNode("td", null, [
                                            _createElementVNode("strong", null, _toDisplayString(item.name), 1),
                                            _createElementVNode("small", _hoisted_45, _toDisplayString(item.downloader), 1)
                                          ]),
                                          _createElementVNode("td", null, _toDisplayString(_unref(sizeText)(item.size)), 1),
                                          _createElementVNode("td", null, [
                                            _createVNode(_sfc_main$2, {
                                              state: item.monitor?.state
                                            }, null, 8, ["state"]),
                                            _createElementVNode("small", _hoisted_46, _toDisplayString(item.monitor?.failure_days || 1) + " / " + _toDisplayString(threshold.value) + " 天", 1)
                                          ]),
                                          _createElementVNode("td", _hoisted_47, _toDisplayString(item.save_path), 1)
                                        ]))
                                      }), 128))
                                    ])
                                  ])
                                ])),
                            (overview.value.maintenance.cleanup.cleaned_at)
                              ? (_openBlock(), _createElementBlock("div", _hoisted_48, [
                                  _createVNode(_component_v_icon, {
                                    icon: "mdi-history",
                                    size: "19"
                                  }),
                                  _createTextVNode("最近清理 " + _toDisplayString(_unref(shortTime)(overview.value.maintenance.cleanup.cleaned_at)) + "：删除 " + _toDisplayString(overview.value.maintenance.cleanup.deleted_count || 0) + "，跳过 " + _toDisplayString(overview.value.maintenance.cleanup.skipped_count || 0) + "，失败 " + _toDisplayString(overview.value.maintenance.cleanup.failed_count || 0) + "。 ", 1)
                                ]))
                              : _createCommentVNode("", true)
                          ]))
                        : (maintenanceTab.value === 'rss')
                          ? (_openBlock(), _createElementBlock("div", _hoisted_49, [
                              _createElementVNode("article", null, [
                                _cache[50] || (_cache[50] = _createElementVNode("div", null, [
                                  _createElementVNode("h3", null, "RSS 地址修复"),
                                  _createElementVNode("p", null, " 重新获取或规范化配置范围内站点的 RSS，验证成功后才写回地址。 ")
                                ], -1)),
                                _createVNode(_component_v_btn, {
                                  variant: "outlined",
                                  "prepend-icon": "mdi-wrench-outline",
                                  disabled: busy.value,
                                  onClick: _cache[10] || (_cache[10] = $event => (repair()))
                                }, {
                                  default: _withCtx(() => [...(_cache[49] || (_cache[49] = [
                                    _createTextVNode("尝试修复", -1)
                                  ]))]),
                                  _: 1
                                }, 8, ["disabled"])
                              ]),
                              _createElementVNode("article", null, [
                                _createElementVNode("div", null, [
                                  _cache[51] || (_cache[51] = _createElementVNode("h3", null, "馒头登录历史", -1)),
                                  _createElementVNode("p", null, " 检查真实网页登录记录，使用统一的 " + _toDisplayString(threshold.value) + " 天异常确认阈值。 ", 1)
                                ]),
                                _createVNode(_component_v_btn, {
                                  variant: "outlined",
                                  "prepend-icon": "mdi-login-variant",
                                  disabled: busy.value,
                                  onClick: _cache[11] || (_cache[11] = $event => (perform('/mteam/login/check')))
                                }, {
                                  default: _withCtx(() => [...(_cache[52] || (_cache[52] = [
                                    _createTextVNode("检查登录", -1)
                                  ]))]),
                                  _: 1
                                }, 8, ["disabled"])
                              ]),
                              _createElementVNode("article", null, [
                                _cache[54] || (_cache[54] = _createElementVNode("div", null, [
                                  _createElementVNode("h3", null, "用户数据完整性"),
                                  _createElementVNode("p", null, "检查最新账号数据中的缺失字段、连接状态与过期情况。")
                                ], -1)),
                                _createVNode(_component_v_btn, {
                                  variant: "outlined",
                                  "prepend-icon": "mdi-account-check-outline",
                                  disabled: busy.value,
                                  onClick: _cache[12] || (_cache[12] = $event => (perform('/check/userdata')))
                                }, {
                                  default: _withCtx(() => [...(_cache[53] || (_cache[53] = [
                                    _createTextVNode("检查数据", -1)
                                  ]))]),
                                  _: 1
                                }, 8, ["disabled"])
                              ])
                            ]))
                          : (_openBlock(), _createElementBlock("div", _hoisted_50, [
                              _createElementVNode("div", _hoisted_51, [
                                _createElementVNode("div", null, [
                                  _cache[55] || (_cache[55] = _createElementVNode("h3", null, "已加载的站点适配规则", -1)),
                                  _createElementVNode("p", null, " 共 " + _toDisplayString(overview.value.rules.length) + " 个域名，兼容现有索引与用户数据规则。 ", 1)
                                ]),
                                _createVNode(_component_v_btn, {
                                  variant: "outlined",
                                  "prepend-icon": "mdi-tune-variant",
                                  onClick: openSettings
                                }, {
                                  default: _withCtx(() => [...(_cache[56] || (_cache[56] = [
                                    _createTextVNode("编辑规则", -1)
                                  ]))]),
                                  _: 1
                                })
                              ]),
                              _createElementVNode("div", _hoisted_52, [
                                _createElementVNode("table", _hoisted_53, [
                                  _cache[58] || (_cache[58] = _createElementVNode("thead", null, [
                                    _createElementVNode("tr", null, [
                                      _createElementVNode("th", null, "域名"),
                                      _createElementVNode("th", null, "索引适配"),
                                      _createElementVNode("th", null, "用户数据"),
                                      _createElementVNode("th", null, "异步请求适配")
                                    ])
                                  ], -1)),
                                  _createElementVNode("tbody", null, [
                                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(overview.value.rules, (rule) => {
                                      return (_openBlock(), _createElementBlock("tr", {
                                        key: rule.domain
                                      }, [
                                        _createElementVNode("td", null, _toDisplayString(rule.domain), 1),
                                        _createElementVNode("td", null, _toDisplayString(rule.indexer ? "已启用" : "—"), 1),
                                        _createElementVNode("td", null, _toDisplayString(rule.userdata ? "已启用" : "—"), 1),
                                        _createElementVNode("td", null, _toDisplayString(rule.asyncfix ? "已启用" : "—"), 1)
                                      ]))
                                    }), 128)),
                                    (!overview.value.rules.length)
                                      ? (_openBlock(), _createElementBlock("tr", _hoisted_54, [...(_cache[57] || (_cache[57] = [
                                          _createElementVNode("td", {
                                            colspan: "4",
                                            class: "stb-empty"
                                          }, "暂无自定义适配规则", -1)
                                        ]))]))
                                      : _createCommentVNode("", true)
                                  ])
                                ])
                              ])
                            ]))
                    ]))
                  : (_openBlock(), _createElementBlock("section", _hoisted_55, [
                      (jobs.value.length)
                        ? (_openBlock(), _createElementBlock(_Fragment, { key: 0 }, [
                            _cache[60] || (_cache[60] = _createElementVNode("div", { class: "stb-section-heading" }, [
                              _createElementVNode("div", null, [
                                _createElementVNode("h2", null, "最近后台任务"),
                                _createElementVNode("p", null, "显示每类任务的最近一次执行状态。")
                              ])
                            ], -1)),
                            _createElementVNode("div", _hoisted_56, [
                              (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(jobs.value, (job) => {
                                return (_openBlock(), _createElementBlock("article", {
                                  key: job.key
                                }, [
                                  _createVNode(_component_v_icon, {
                                    icon:
                    job.status === 'success'
                      ? 'mdi-check-circle-outline'
                      : job.status === 'running'
                        ? 'mdi-progress-clock'
                        : 'mdi-alert-circle-outline'
                  ,
                                    color:
                    job.status === 'success'
                      ? 'success'
                      : job.status === 'running'
                        ? 'primary'
                        : 'warning'
                  ,
                                    size: "22"
                                  }, null, 8, ["icon", "color"]),
                                  _createElementVNode("div", null, [
                                    _createElementVNode("strong", null, _toDisplayString(job.name) + " · " + _toDisplayString({
                        success: "已完成",
                        error: "执行失败",
                        running: "运行中",
                        interrupted: "已中断",
                      }[job.status] || job.status), 1),
                                    _createElementVNode("p", null, _toDisplayString(job.message), 1)
                                  ]),
                                  _createElementVNode("small", null, _toDisplayString(_unref(shortTime)(job.finished_at || job.started_at)), 1)
                                ]))
                              }), 128))
                            ])
                          ], 64))
                        : _createCommentVNode("", true),
                      _cache[66] || (_cache[66] = _createElementVNode("div", { class: "stb-section-heading" }, [
                        _createElementVNode("div", null, [
                          _createElementVNode("h2", null, "检测运行记录"),
                          _createElementVNode("p", null, "查看最近 50 轮统一检测的结果与各模块执行情况。")
                        ])
                      ], -1)),
                      (!runs.value.length)
                        ? (_openBlock(), _createElementBlock("div", _hoisted_57, [
                            _createVNode(_component_v_icon, {
                              icon: "mdi-history",
                              size: "36"
                            }),
                            _cache[61] || (_cache[61] = _createElementVNode("h3", null, "还没有统一检测记录", -1)),
                            _cache[62] || (_cache[62] = _createElementVNode("p", null, "运行一次“立即检测”后，完整结果会保存在这里。", -1))
                          ]))
                        : _createCommentVNode("", true),
                      (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(runs.value, (run) => {
                        return (_openBlock(), _createElementBlock("details", {
                          key: run.id,
                          class: "stb-run-record"
                        }, [
                          _createElementVNode("summary", null, [
                            _createVNode(_component_v_icon, {
                              icon:
                  run.status === 'success'
                    ? 'mdi-check-circle'
                    : 'mdi-alert-circle'
                ,
                              color: run.status === 'success' ? 'success' : 'error',
                              size: "20"
                            }, null, 8, ["icon", "color"]),
                            _createElementVNode("strong", null, _toDisplayString(_unref(shortTime)(run.started_at)), 1),
                            _createElementVNode("span", null, _toDisplayString(run.status === "success" ? "已完成" : "部分检测失败"), 1),
                            _createElementVNode("span", _hoisted_58, "确认异常 " + _toDisplayString(run.confirmed || 0) + " 项", 1),
                            _createVNode(_component_v_icon, {
                              icon: "mdi-chevron-down",
                              size: "20"
                            })
                          ]),
                          (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(run.steps, (step) => {
                            return (_openBlock(), _createElementBlock("div", {
                              key: step.module,
                              class: "stb-run-step"
                            }, [
                              _createElementVNode("span", null, _toDisplayString(_unref(MODULES)[step.module]), 1),
                              _createElementVNode("span", null, _toDisplayString(step.message), 1)
                            ]))
                          }), 128))
                        ]))
                      }), 128)),
                      _createVNode(_component_v_divider, { class: "my-7" }),
                      _createElementVNode("div", _hoisted_59, [
                        _cache[64] || (_cache[64] = _createElementVNode("div", null, [
                          _createElementVNode("h2", null, "系统错误"),
                          _createElementVNode("p", null, "按相同来源和错误信息聚合，重复报错不会淹没诊断结果。")
                        ], -1)),
                        _createVNode(_component_v_btn, {
                          variant: "text",
                          "prepend-icon": "mdi-refresh",
                          loading: errorsLoading.value,
                          onClick: loadErrors
                        }, {
                          default: _withCtx(() => [...(_cache[63] || (_cache[63] = [
                            _createTextVNode("刷新", -1)
                          ]))]),
                          _: 1
                        }, 8, ["loading"])
                      ]),
                      _createElementVNode("div", _hoisted_60, [
                        _createVNode(_component_v_text_field, {
                          modelValue: errorQuery.value,
                          "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((errorQuery).value = $event)),
                          label: "搜索错误或来源",
                          "aria-label": "搜索错误或来源",
                          "prepend-inner-icon": "mdi-magnify",
                          variant: "outlined",
                          density: "compact",
                          "hide-details": ""
                        }, null, 8, ["modelValue"]),
                        _createVNode(_component_v_select, {
                          modelValue: errorLevel.value,
                          "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((errorLevel).value = $event)),
                          items: [
                { title: '全部级别', value: 'all' },
                { title: 'ERROR', value: 'ERROR' },
                { title: 'CRITICAL', value: 'CRITICAL' },
              ],
                          label: "级别",
                          variant: "outlined",
                          density: "compact",
                          "hide-details": ""
                        }, null, 8, ["modelValue"])
                      ]),
                      (errorGroups.value.sampled)
                        ? (_openBlock(), _createElementBlock("p", _hoisted_61, " 为保持响应速度，仅聚合最近约 2 MB 日志；次数来自这个样本。 "))
                        : _createCommentVNode("", true),
                      (errorsError.value)
                        ? (_openBlock(), _createBlock(_component_v_alert, {
                            key: 3,
                            type: "error",
                            variant: "tonal",
                            class: "mb-3"
                          }, {
                            default: _withCtx(() => [
                              _createTextVNode(_toDisplayString(errorsError.value), 1)
                            ]),
                            _: 1
                          }))
                        : _createCommentVNode("", true),
                      _createElementVNode("div", _hoisted_62, [
                        _createElementVNode("table", _hoisted_63, [
                          _cache[65] || (_cache[65] = _createElementVNode("thead", null, [
                            _createElementVNode("tr", null, [
                              _createElementVNode("th", null, "错误 / 来源"),
                              _createElementVNode("th", null, "次数"),
                              _createElementVNode("th", null, "最近发生")
                            ])
                          ], -1)),
                          _createElementVNode("tbody", null, [
                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(errorGroups.value.items, (item, index) => {
                              return (_openBlock(), _createElementBlock("tr", { key: index }, [
                                _createElementVNode("td", null, [
                                  _createElementVNode("strong", null, [
                                    _createTextVNode(_toDisplayString(item.source) + " ", 1),
                                    _createElementVNode("span", _hoisted_64, _toDisplayString(item.level), 1)
                                  ]),
                                  _createElementVNode("p", _hoisted_65, _toDisplayString(item.message), 1)
                                ]),
                                _createElementVNode("td", null, _toDisplayString(item.count), 1),
                                _createElementVNode("td", _hoisted_66, _toDisplayString(_unref(shortTime)(item.last_at)), 1)
                              ]))
                            }), 128)),
                            (!errorGroups.value.items.length)
                              ? (_openBlock(), _createElementBlock("tr", _hoisted_67, [
                                  _createElementVNode("td", _hoisted_68, _toDisplayString(errorsLoading.value ? "正在读取日志…" : "没有符合条件的错误记录"), 1)
                                ]))
                              : _createCommentVNode("", true)
                          ])
                        ])
                      ]),
                      (errorGroups.value.total > 10)
                        ? (_openBlock(), _createBlock(_component_v_pagination, {
                            key: 4,
                            modelValue: errorPage.value,
                            "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((errorPage).value = $event)),
                            length: Math.ceil(errorGroups.value.total / 10),
                            "total-visible": 5,
                            density: "compact",
                            class: "mt-3"
                          }, null, 8, ["modelValue", "length"]))
                        : _createCommentVNode("", true)
                    ]))
            ], 64))
          : _createCommentVNode("", true),
      _createElementVNode("footer", _hoisted_69, [
        _createTextVNode(" 站点工具箱 v" + _toDisplayString(overview.value?.plugin_version || "1.4.0"), 1),
        _cache[67] || (_cache[67] = _createElementVNode("span", null, "所有检测模块共用统一监控计划", -1))
      ])
    ], 8, _hoisted_1),
    (mobile.value && detailOpen.value && selected.value && activeTab.value === 'sites')
      ? (_openBlock(), _createElementBlock("button", {
          key: 0,
          class: "stb-drawer-backdrop",
          "aria-label": "关闭站点详情",
          onClick: closeDetails
        }))
      : _createCommentVNode("", true),
    _createElementVNode("aside", {
      ref_key: "detailPane",
      ref: detailPane,
      class: "stb-aside",
      role: mobile.value ? 'dialog' : undefined,
      "aria-modal": mobile.value ? 'true' : undefined,
      "aria-label": "诊断详情与统一计划",
      onKeydown: [
        _withKeys(_withModifiers(closeDetails, ["stop","prevent"]), ["esc"]),
        trapDetailFocus
      ]
    }, [
      (selected.value && detailOpen.value && activeTab.value === 'sites')
        ? (_openBlock(), _createElementBlock(_Fragment, { key: 0 }, [
            _createElementVNode("header", _hoisted_71, [
              _createVNode(_sfc_main$1, {
                site: selected.value,
                client: _unref(client),
                large: ""
              }, null, 8, ["site", "client"]),
              _createElementVNode("div", null, [
                _createElementVNode("h2", null, [
                  _createTextVNode(_toDisplayString(selected.value.name), 1),
                  _createElementVNode("a", {
                    href: selected.value.url,
                    target: "_blank",
                    rel: "noopener noreferrer",
                    "aria-label": `打开${selected.value.name}网站`
                  }, [
                    _createVNode(_component_v_icon, {
                      icon: "mdi-open-in-new",
                      size: "18"
                    })
                  ], 8, _hoisted_72)
                ]),
                _createElementVNode("p", null, _toDisplayString(selected.value.domain), 1)
              ]),
              _createVNode(_component_v_btn, {
                icon: "mdi-close",
                variant: "text",
                size: "small",
                "aria-label": "关闭站点详情",
                onClick: closeDetails
              })
            ]),
            _createElementVNode("div", _hoisted_73, [
              _createElementVNode("button", {
                class: _normalizeClass({ active: detailTab.value === 'diagnosis' }),
                onClick: _cache[16] || (_cache[16] = $event => (detailTab.value = 'diagnosis'))
              }, " 诊断结果", 2),
              _createElementVNode("button", {
                class: _normalizeClass({ active: detailTab.value === 'info' }),
                onClick: _cache[17] || (_cache[17] = $event => (detailTab.value = 'info'))
              }, " 站点信息 ", 2)
            ]),
            (detailTab.value === 'diagnosis')
              ? (_openBlock(), _createElementBlock("section", _hoisted_74, [
                  _createElementVNode("div", _hoisted_75, [
                    _createElementVNode("span", {
                      class: _normalizeClass(["stb-large-status-icon", `is-${selectedMeta.value.color}`])
                    }, [
                      _createVNode(_component_v_icon, {
                        icon: selectedMeta.value.icon,
                        size: "34"
                      }, null, 8, ["icon"])
                    ], 2),
                    _createElementVNode("div", null, [
                      _cache[68] || (_cache[68] = _createElementVNode("small", null, "当前状态", -1)),
                      _createElementVNode("h3", {
                        class: _normalizeClass(`is-${selectedMeta.value.color}`)
                      }, _toDisplayString(selectedMeta.value.label), 3)
                    ]),
                    _createElementVNode("div", _hoisted_76, [
                      _cache[69] || (_cache[69] = _createElementVNode("small", null, "连续异常", -1)),
                      _createElementVNode("strong", null, [
                        _createTextVNode(_toDisplayString(selected.value.failure_days || 0) + " ", 1),
                        _createElementVNode("span", null, "/ " + _toDisplayString(threshold.value) + " 天", 1)
                      ])
                    ])
                  ]),
                  _createElementVNode("div", _hoisted_77, [
                    _createElementVNode("div", null, [
                      _cache[70] || (_cache[70] = _createElementVNode("small", null, "首次异常", -1)),
                      _createElementVNode("span", null, _toDisplayString(firstFailedAt.value), 1)
                    ]),
                    _createElementVNode("div", null, [
                      _cache[71] || (_cache[71] = _createElementVNode("small", null, "最近检测", -1)),
                      _createElementVNode("span", null, _toDisplayString(_unref(shortTime)(selected.value.last_checked_at)), 1)
                    ])
                  ]),
                  _createElementVNode("div", _hoisted_78, [
                    _createVNode(_component_v_icon, {
                      icon: "mdi-information-outline",
                      size: "18"
                    }),
                    _createElementVNode("p", null, [
                      _createTextVNode(_toDisplayString(primaryReason.value), 1),
                      (selected.value.failure_days && !selected.value.confirmed)
                        ? (_openBlock(), _createElementBlock("span", _hoisted_79, "达到连续 " + _toDisplayString(threshold.value) + " 天后才会确认异常并提醒。", 1))
                        : _createCommentVNode("", true)
                    ])
                  ]),
                  _createElementVNode("div", _hoisted_80, [
                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(checks.value, (check) => {
                      return (_openBlock(), _createElementBlock("article", {
                        key: check.key
                      }, [
                        _createElementVNode("div", null, [
                          _createElementVNode("span", null, [
                            _createVNode(_component_v_icon, {
                              icon:
                      {
                        rss: 'mdi-rss',
                        userdata: 'mdi-account-outline',
                        mteam: 'mdi-login-variant',
                      }[check.key]
                    ,
                              size: "17"
                            }, null, 8, ["icon"]),
                            _createTextVNode(_toDisplayString(check.label), 1)
                          ]),
                          _createVNode(_sfc_main$2, {
                            state: check.state,
                            compact: ""
                          }, null, 8, ["state"]),
                          _createElementVNode("small", null, _toDisplayString(check.tested_at ? _unref(shortTime)(check.tested_at) : "未检测"), 1)
                        ]),
                        (check.state !== 'success')
                          ? (_openBlock(), _createElementBlock("p", {
                              key: 0,
                              title: check.message
                            }, _toDisplayString(check.raw_message || check.message), 9, _hoisted_81))
                          : _createCommentVNode("", true)
                      ]))
                    }), 128)),
                    _createElementVNode("article", _hoisted_82, [
                      _createVNode(_component_v_icon, {
                        icon: "mdi-download-network-outline",
                        size: "17"
                      }),
                      _cache[72] || (_cache[72] = _createElementVNode("span", null, "缺失种子按下载器监控", -1)),
                      _createElementVNode("button", {
                        onClick: _cache[18] || (_cache[18] = $event => {
                  activeTab.value = 'maintenance';
                  maintenanceTab.value = 'missing';
                  if (mobile.value) detailOpen.value = false;
                })
                      }, " 查看 ")
                    ])
                  ]),
                  _createElementVNode("div", _hoisted_83, [
                    _createVNode(_component_v_btn, {
                      size: "small",
                      variant: "text",
                      "prepend-icon": "mdi-content-copy",
                      onClick: copyDiagnostic
                    }, {
                      default: _withCtx(() => [...(_cache[73] || (_cache[73] = [
                        _createTextVNode("复制诊断", -1)
                      ]))]),
                      _: 1
                    }),
                    (selected.value.checks.rss.raw_state !== 'success')
                      ? (_openBlock(), _createBlock(_component_v_btn, {
                          key: 0,
                          size: "small",
                          variant: "text",
                          color: "primary",
                          "prepend-icon": "mdi-wrench-outline",
                          disabled: busy.value,
                          onClick: _cache[19] || (_cache[19] = $event => (repair(selected.value)))
                        }, {
                          default: _withCtx(() => [...(_cache[74] || (_cache[74] = [
                            _createTextVNode("修复 RSS", -1)
                          ]))]),
                          _: 1
                        }, 8, ["disabled"]))
                      : _createCommentVNode("", true)
                  ])
                ]))
              : (_openBlock(), _createElementBlock("section", _hoisted_84, [
                  _createElementVNode("dl", _hoisted_85, [
                    _cache[77] || (_cache[77] = _createElementVNode("dt", null, "站点名称", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.name), 1),
                    _cache[78] || (_cache[78] = _createElementVNode("dt", null, "域名", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.domain), 1),
                    _cache[79] || (_cache[79] = _createElementVNode("dt", null, "站点状态", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.active ? "已启用" : "已停用"), 1),
                    _cache[80] || (_cache[80] = _createElementVNode("dt", null, "监控范围", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.monitored ? "已包含" : "未包含"), 1),
                    _cache[81] || (_cache[81] = _createElementVNode("dt", null, "自定义适配", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.adapted ? "已加载" : "无自定义规则"), 1),
                    _cache[82] || (_cache[82] = _createElementVNode("dt", null, "最近检测", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.last_checked_at || "尚未检测"), 1),
                    _cache[83] || (_cache[83] = _createElementVNode("dt", null, "RSS 条目 / 耗时", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.checks.rss.count ?? "—") + " / " + _toDisplayString(selected.value.checks.rss.seconds ?? "—") + " 秒 ", 1),
                    _cache[84] || (_cache[84] = _createElementVNode("dt", null, "RSS 来源", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.checks.rss.source || "尚无记录"), 1),
                    _cache[85] || (_cache[85] = _createElementVNode("dt", null, "用户等级", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.checks.userdata.user_level || "尚无记录"), 1),
                    _cache[86] || (_cache[86] = _createElementVNode("dt", null, "上传 / 下载", -1)),
                    _createElementVNode("dd", null, _toDisplayString(_unref(sizeText)(selected.value.checks.userdata.upload)) + " / " + _toDisplayString(_unref(sizeText)(selected.value.checks.userdata.download)), 1),
                    _cache[87] || (_cache[87] = _createElementVNode("dt", null, "分享率 / 魔力", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.checks.userdata.ratio ?? "—") + " / " + _toDisplayString(selected.value.checks.userdata.bonus ?? "—"), 1),
                    _cache[88] || (_cache[88] = _createElementVNode("dt", null, "做种 / 下载中", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.checks.userdata.seeding ?? "—") + " / " + _toDisplayString(selected.value.checks.userdata.leeching ?? "—"), 1),
                    _cache[89] || (_cache[89] = _createElementVNode("dt", null, "做种体积", -1)),
                    _createElementVNode("dd", null, _toDisplayString(_unref(sizeText)(selected.value.checks.userdata.seeding_size)), 1),
                    _cache[90] || (_cache[90] = _createElementVNode("dt", null, "用户数据更新", -1)),
                    _createElementVNode("dd", null, _toDisplayString(selected.value.checks.userdata.updated_at || "尚无记录"), 1),
                    (selected.value.checks.mteam)
                      ? (_openBlock(), _createElementBlock(_Fragment, { key: 0 }, [
                          _cache[75] || (_cache[75] = _createElementVNode("dt", null, "最近网页登录", -1)),
                          _createElementVNode("dd", null, _toDisplayString(selected.value.checks.mteam.last_login_at || "尚无记录"), 1),
                          _cache[76] || (_cache[76] = _createElementVNode("dt", null, "最近访问", -1)),
                          _createElementVNode("dd", null, _toDisplayString(selected.value.checks.mteam.last_browse_at || "尚无记录"), 1)
                        ], 64))
                      : _createCommentVNode("", true)
                  ])
                ]))
          ], 64))
        : (!mobile.value && activeTab.value === 'sites')
          ? (_openBlock(), _createElementBlock("div", _hoisted_86, [
              _createVNode(_component_v_icon, {
                icon: "mdi-text-box-search-outline",
                size: "32"
              }),
              _cache[91] || (_cache[91] = _createElementVNode("p", null, "选择一个站点，查看诊断与处理建议。", -1))
            ]))
          : _createCommentVNode("", true),
      (overview.value)
        ? (_openBlock(), _createElementBlock("section", _hoisted_87, [
            _createElementVNode("header", null, [
              _createElementVNode("h3", null, [
                _createVNode(_component_v_icon, {
                  icon: "mdi-clock-outline",
                  size: "22"
                }),
                _cache[92] || (_cache[92] = _createTextVNode("统一监控计划", -1))
              ]),
              _createVNode(_component_v_btn, {
                size: "small",
                variant: "tonal",
                color: "primary",
                "prepend-icon": "mdi-pencil-outline",
                onClick: openSettings
              }, {
                default: _withCtx(() => [...(_cache[93] || (_cache[93] = [
                  _createTextVNode("编辑计划", -1)
                ]))]),
                _: 1
              })
            ]),
            _createElementVNode("dl", null, [
              _createElementVNode("div", null, [
                _createElementVNode("dt", null, [
                  _createVNode(_component_v_icon, {
                    icon: "mdi-clock-start",
                    size: "17"
                  }),
                  _cache[94] || (_cache[94] = _createTextVNode("执行时间", -1))
                ]),
                _createElementVNode("dd", null, "每天 " + _toDisplayString(summary.value.window), 1)
              ]),
              _createElementVNode("div", null, [
                _createElementVNode("dt", null, [
                  _createVNode(_component_v_icon, {
                    icon: "mdi-timer-sync-outline",
                    size: "17"
                  }),
                  _cache[95] || (_cache[95] = _createTextVNode("执行频率", -1))
                ]),
                _createElementVNode("dd", null, _toDisplayString(summary.value.cadence), 1)
              ]),
              _createElementVNode("div", null, [
                _createElementVNode("dt", null, [
                  _createVNode(_component_v_icon, {
                    icon: "mdi-clock-check-outline",
                    size: "17"
                  }),
                  _cache[96] || (_cache[96] = _createTextVNode("下次运行", -1))
                ]),
                _createElementVNode("dd", null, _toDisplayString(plan.value.next_run_at
                  ? _unref(shortTime)(plan.value.next_run_at)
                  : "未启用定时监控"), 1)
              ]),
              _createElementVNode("div", null, [
                _createElementVNode("dt", null, [
                  _createVNode(_component_v_icon, {
                    icon: "mdi-shield-check-outline",
                    size: "17"
                  }),
                  _cache[97] || (_cache[97] = _createTextVNode("异常确认天数 ", -1))
                ]),
                _createElementVNode("dd", null, [
                  _createTextVNode(_toDisplayString(threshold.value) + " 天 ", 1),
                  _cache[98] || (_cache[98] = _createElementVNode("small", null, "（所有模块共用）", -1))
                ])
              ])
            ]),
            _createElementVNode("p", null, " 监控范围：" + _toDisplayString((plan.value.monitor_modules || [])
              .map(
                (key) =>
                  ({
                    rss: "RSS",
                    userdata: "用户数据",
                    missing: "缺失种子",
                    mteam: "馒头登录",
                  })[key],
              )
              .join("、") || "未选择检测模块") + "。 ", 1),
            _cache[99] || (_cache[99] = _createElementVNode("p", null, "成功即清零，同日多次只计一天；清理仅处理已确认项。", -1)),
            _createElementVNode("small", _hoisted_88, "按服务器时间执行" + _toDisplayString(plan.value.timezone ? ` · ${plan.value.timezone}` : ""), 1)
          ]))
        : _createCommentVNode("", true)
    ], 40, _hoisted_70),
    _createVNode(_component_v_dialog, {
      modelValue: settingsOpen.value,
      "onUpdate:modelValue": _cache[21] || (_cache[21] = $event => ((settingsOpen).value = $event)),
      "max-width": "960",
      fullscreen: mobile.value,
      persistent: "",
      scrollable: ""
    }, {
      default: _withCtx(() => [
        (settingsOpen.value)
          ? (_openBlock(), _createBlock(_sfc_main$3, {
              key: 0,
              initial: settings.value,
              options: options.value,
              saving: saving.value,
              busy: busy.value,
              onSave: saveSettings,
              onClose: _cache[20] || (_cache[20] = $event => (settingsOpen.value = false))
            }, null, 8, ["initial", "options", "saving", "busy"]))
          : _createCommentVNode("", true)
      ]),
      _: 1
    }, 8, ["modelValue", "fullscreen"]),
    _createVNode(_component_v_dialog, {
      modelValue: confirmation.value.open,
      "onUpdate:modelValue": _cache[23] || (_cache[23] = $event => ((confirmation.value.open) = $event)),
      "max-width": "500"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, {
          title: confirmation.value.title,
          text: confirmation.value.text
        }, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_actions, null, {
              default: _withCtx(() => [
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  variant: "text",
                  onClick: _cache[22] || (_cache[22] = $event => (confirmation.value.open = false))
                }, {
                  default: _withCtx(() => [...(_cache[100] || (_cache[100] = [
                    _createTextVNode("取消", -1)
                  ]))]),
                  _: 1
                }),
                _createVNode(_component_v_btn, {
                  color: confirmation.value.destructive ? 'error' : 'primary',
                  variant: "flat",
                  onClick: acceptConfirmation
                }, {
                  default: _withCtx(() => [
                    _createTextVNode(_toDisplayString(confirmation.value.destructive ? "确认清理" : "确认修复"), 1)
                  ]),
                  _: 1
                }, 8, ["color"])
              ]),
              _: 1
            })
          ]),
          _: 1
        }, 8, ["title", "text"])
      ]),
      _: 1
    }, 8, ["modelValue"]),
    _createVNode(_component_v_snackbar, {
      modelValue: snackbar.value.show,
      "onUpdate:modelValue": _cache[25] || (_cache[25] = $event => ((snackbar.value.show) = $event)),
      color: snackbar.value.color,
      location: "bottom",
      timeout: 4500
    }, {
      actions: _withCtx(() => [
        _createVNode(_component_v_btn, {
          "aria-label": "关闭提示",
          variant: "text",
          icon: "mdi-close",
          size: "small",
          onClick: _cache[24] || (_cache[24] = $event => (snackbar.value.show = false))
        })
      ]),
      default: _withCtx(() => [
        _createTextVNode(_toDisplayString(snackbar.value.text), 1)
      ]),
      _: 1
    }, 8, ["modelValue", "color"])
  ], 2))
}
}

};

export { _sfc_main as default };
