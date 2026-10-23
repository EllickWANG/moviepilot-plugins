<script setup>
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useDisplay, useTheme } from "vuetify";
import { createClient } from "../lib/client";
import {
  MODULES,
  matchesSite,
  planSummary,
  shortTime,
  sizeText,
  sortedSites,
  stateInfo,
} from "../lib/model";
import StateBadge from "./StateBadge.vue";
import SiteAvatar from "./SiteAvatar.vue";
import SettingsPanel from "./SettingsPanel.vue";
import "../styles.css";
const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: "sitetoolbox" },
  sourcePluginId: String,
});
const emit = defineEmits(["close", "switch", "layout", "action"]);
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
</script>

<template>
  <div
    class="stb-root stb-layout"
    :class="{
      'stb-detail-open': detailOpen && selected && activeTab === 'sites',
      'stb-is-light': isLight,
    }"
  >
    <main
      class="stb-main"
      :inert="mobile && detailOpen && selected && activeTab === 'sites'"
    >
      <header class="stb-page-header">
        <v-icon icon="mdi-toolbox" class="stb-toolbox-icon" size="64" />
        <div class="stb-heading">
          <h1>站点工具箱</h1>
          <p>统一监控站点的 RSS、用户数据、缺失种子和登录状态。</p>
        </div>
        <div class="stb-header-actions">
          <v-btn
            color="primary"
            variant="flat"
            prepend-icon="mdi-play"
            :loading="submitting"
            :disabled="busy || loading"
            @click="perform('/monitor/run')"
            >立即检测</v-btn
          ><v-btn
            variant="outlined"
            prepend-icon="mdi-cog-outline"
            @click="openSettings"
            >设置</v-btn
          >
        </div>
      </header>
      <nav class="stb-nav" aria-label="工具箱导航">
        <button
          v-for="tab in [
            { key: 'sites', label: '站点诊断' },
            { key: 'maintenance', label: '维护工具' },
            { key: 'logs', label: '运行记录' },
          ]"
          :key="tab.key"
          type="button"
          :class="{ active: activeTab === tab.key }"
          :aria-current="activeTab === tab.key ? 'page' : undefined"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </nav>
      <v-alert
        v-if="error"
        type="error"
        variant="tonal"
        class="my-4"
        role="alert"
        >{{ error
        }}<template #append
          ><v-btn variant="text" @click="load()">重试</v-btn></template
        ></v-alert
      >
      <v-alert
        v-if="overview && !overview.enabled"
        type="info"
        variant="tonal"
        class="my-4"
        >插件已停用，定时监控和站点适配未运行。<v-btn
          variant="text"
          size="small"
          @click="openSettings"
          >打开设置</v-btn
        ></v-alert
      >
      <div
        v-if="running.length"
        class="stb-running"
        role="status"
        aria-live="polite"
      >
        <v-progress-circular
          indeterminate
          size="19"
          width="2"
          color="primary"
        />
        <div>
          <strong>{{ running[0].name }}</strong
          ><span>{{ running[0].message || "正在检测，请稍候…" }}</span>
        </div>
        <span>结果会自动更新</span>
      </div>
      <div
        v-if="loading && !overview"
        class="stb-initial-loading"
        role="status"
      >
        <v-progress-circular indeterminate color="primary" /><span
          >正在读取站点状态…</span
        >
      </div>
      <template v-else-if="overview">
        <section v-if="activeTab === 'sites'" class="stb-sites-view">
          <div class="stb-health-strip">
            <button
              v-for="metric in [
                { state: 'success', key: 'success', label: '正常' },
                { state: 'pending', key: 'pending', label: '待观察' },
                { state: 'offline', key: 'offline', label: '连接失败' },
                { state: 'error', key: 'confirmed', label: '已确认异常' },
              ]"
              :key="metric.key"
              type="button"
              class="stb-health-metric"
              :aria-label="`筛选${metric.label}站点`"
              @click="filter = metric.key"
            >
              <span
                class="stb-metric-icon"
                :class="`is-${metric.key === 'confirmed' && !counts.confirmed ? 'unknown' : stateInfo(metric.state).color}`"
                ><v-icon :icon="stateInfo(metric.state).icon" size="26" /></span
              ><span
                ><small>{{ metric.label }}</small
                ><strong
                  >{{ counts[metric.key] }}<em>/ {{ counts.all }}</em></strong
                ></span
              >
            </button>
            <div class="stb-health-time">
              <small>最后检测</small><span>{{ shortTime(lastChecked) }}</span
              ><small
                >下次运行
                <b>{{
                  plan.next_run_at ? shortTime(plan.next_run_at) : "未启用"
                }}</b></small
              >
            </div>
          </div>
          <div class="stb-table-toolbar">
            <label class="stb-search"
              ><v-icon icon="mdi-magnify" size="21" /><input
                v-model="query"
                type="search"
                aria-label="搜索站点名称或域名"
                placeholder="搜索站点名称或域名…"
            /></label>
            <div class="stb-filters" aria-label="站点状态筛选">
              <button
                v-for="item in filters"
                :key="item.value"
                type="button"
                :class="{ active: filter === item.value }"
                :aria-pressed="filter === item.value"
                @click="filter = item.value"
              >
                <span
                  v-if="item.color"
                  class="stb-status-dot"
                  :class="`is-${item.color}`"
                />{{ item.label }} <span>({{ counts[item.value] }})</span>
              </button>
            </div>
          </div>
          <div class="stb-table-surface">
            <div class="stb-table-scroll">
              <table class="stb-site-table">
                <thead>
                  <tr>
                    <th :aria-sort="sortAria('name')">
                      <button type="button" @click="sort('name')">
                        站点<v-icon
                          icon="mdi-unfold-more-horizontal"
                          size="16"
                        />
                      </button>
                    </th>
                    <th>RSS</th>
                    <th>用户数据</th>
                    <th :aria-sort="sortAria('failure_days')">
                      <button type="button" @click="sort('failure_days')">
                        连续异常<v-icon
                          icon="mdi-unfold-more-horizontal"
                          size="16"
                        />
                      </button>
                    </th>
                    <th :aria-sort="sortAria('last_checked_at')">
                      <button type="button" @click="sort('last_checked_at')">
                        最近检测<v-icon
                          icon="mdi-unfold-more-horizontal"
                          size="16"
                        />
                      </button>
                    </th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="site in visibleSites"
                    :key="site.id"
                    :class="{ selected: selectedId === site.id && detailOpen }"
                    @click="choose(site, $event)"
                  >
                    <td>
                      <div class="stb-site-name">
                        <SiteAvatar :site="site" :client="client" />
                        <div>
                          <strong>{{ site.name }}</strong
                          ><small>{{ site.domain }}</small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div class="stb-desktop-rss">
                        <StateBadge
                          :state="site.checks.rss.state"
                          compact
                        /><small
                          v-if="reason(site)"
                          class="stb-row-reason"
                          :title="reason(site)"
                          >{{ reason(site) }}</small
                        >
                      </div>
                      <div class="stb-mobile-overall">
                        <StateBadge :state="site.state" compact /><small
                          class="stb-row-reason"
                          >{{
                            Object.values(site.checks).find(
                              (check) =>
                                !["success", "unknown"].includes(check.state),
                            )?.raw_message || ""
                          }}</small
                        >
                      </div>
                    </td>
                    <td>
                      <StateBadge :state="site.checks.userdata.state" compact />
                    </td>
                    <td class="stb-nowrap">
                      <span
                        :class="{ 'stb-attention-text': site.failure_days > 0 }"
                        >{{ site.failure_days || 0 }}</span
                      ><span class="stb-muted">{{
                        site.failure_days ? ` / ${threshold} 天` : " 天"
                      }}</span>
                    </td>
                    <td class="stb-nowrap stb-muted">
                      {{ shortTime(site.last_checked_at) }}
                    </td>
                    <td>
                      <div class="stb-row-actions">
                        <v-btn
                          size="small"
                          variant="flat"
                          color="primary"
                          prepend-icon="mdi-refresh"
                          :aria-label="`复测${site.name}`"
                          :disabled="busy"
                          @click.stop="recheck(site)"
                          >复测</v-btn
                        ><v-btn
                          size="small"
                          variant="outlined"
                          prepend-icon="mdi-text-box-search-outline"
                          :aria-label="`查看${site.name}详情`"
                          @click.stop="choose(site, $event)"
                          >详情</v-btn
                        >
                      </div>
                    </td>
                  </tr>
                  <tr v-if="!visibleSites.length">
                    <td colspan="6" class="stb-empty">
                      <v-icon icon="mdi-magnify-close" size="32" />
                      <h3>
                        {{
                          sites.length
                            ? "没有符合条件的站点"
                            : "还没有可展示的站点"
                        }}
                      </h3>
                      <p>
                        {{
                          sites.length
                            ? "试试其他名称、域名或状态筛选。"
                            : "请先在 MoviePilot 站点管理中添加站点。"
                        }}
                      </p>
                      <v-btn
                        v-if="query || filter !== 'all'"
                        variant="text"
                        color="primary"
                        @click="
                          query = '';
                          filter = 'all';
                        "
                        >清除筛选</v-btn
                      >
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <footer class="stb-pagination">
              <span
                >共 {{ filtered.length }} 个站点<span v-if="filtered.length"
                  >　|　{{ (page - 1) * pageSize + 1 }}–{{
                    Math.min(page * pageSize, filtered.length)
                  }}
                  / {{ filtered.length }}</span
                ></span
              ><v-pagination
                v-model="page"
                :length="pageCount"
                :total-visible="mobile ? 3 : 5"
                density="compact"
                rounded="lg"
                :show-first-last-page="false"
              /><v-select
                v-model="pageSize"
                :items="[
                  { title: '8 条/页', value: 8 },
                  { title: '15 条/页', value: 15 },
                  { title: '30 条/页', value: 30 },
                ]"
                aria-label="每页站点数"
                variant="outlined"
                density="compact"
                hide-details
              />
            </footer>
          </div>
        </section>

        <section
          v-else-if="activeTab === 'maintenance'"
          class="stb-maintenance-view"
        >
          <div class="stb-section-heading">
            <div>
              <h2>维护工具</h2>
              <p>先查看影响范围，再执行会修改数据的操作。</p>
            </div>
          </div>
          <div class="stb-subtabs">
            <button
              v-for="item in [
                { key: 'missing', label: '缺失种子' },
                { key: 'rss', label: 'RSS 与登录' },
                { key: 'rules', label: '适配规则' },
              ]"
              :key="item.key"
              :class="{ active: maintenanceTab === item.key }"
              @click="maintenanceTab = item.key"
            >
              {{ item.label }}
            </button>
          </div>
          <div v-if="maintenanceTab === 'missing'">
            <div class="stb-section-heading">
              <div>
                <h3>缺失文件种子</h3>
                <p>
                  发现 {{ missing.total_count || 0 }} 个，已确认
                  {{ missing.confirmed_count || 0 }} 个 · 最近扫描
                  {{ shortTime(missing.created_at) }}
                </p>
              </div>
              <div class="stb-row-actions">
                <v-btn
                  variant="outlined"
                  prepend-icon="mdi-magnify-scan"
                  :disabled="busy"
                  @click="perform('/cleanup/missing/preview')"
                  >扫描</v-btn
                ><v-btn
                  color="error"
                  variant="tonal"
                  prepend-icon="mdi-delete-outline"
                  :disabled="busy || !missing.confirmed_count"
                  @click="cleanup"
                  >清理已确认项</v-btn
                >
              </div>
            </div>
            <v-alert
              :type="overview.maintenance.delete_files ? 'warning' : 'info'"
              variant="tonal"
              density="compact"
              class="my-4"
              >{{
                overview.maintenance.delete_files
                  ? "当前设置会同时删除数据文件。清理前需要再次确认。"
                  : "当前清理仅删除下载器任务，保留数据文件。"
              }}
              只有连续异常达到 {{ threshold }} 天的种子才可清理。</v-alert
            >
            <v-alert
              v-for="item in missing.errors || []"
              :key="item.downloader"
              type="warning"
              variant="tonal"
              density="compact"
              class="mb-2"
              >{{ item.downloader }}：{{
                (missing.downloader_status || []).find(
                  (status) =>
                    status.object_id === `downloader:${item.downloader}`,
                )?.message || item.message
              }}</v-alert
            >
            <div v-if="!missingItems.length" class="stb-empty stb-surface">
              <v-icon
                :icon="
                  missing.errors?.length
                    ? 'mdi-alert-circle-outline'
                    : 'mdi-check-circle-outline'
                "
                size="40"
                :color="missing.errors?.length ? 'warning' : 'success'"
              />
              <h3>
                {{
                  missing.errors?.length
                    ? "部分下载器扫描失败"
                    : missing.created_at
                      ? "最近扫描未发现缺失文件种子"
                      : "尚未扫描缺失文件种子"
                }}
              </h3>
              <p>
                {{
                  missing.created_at
                    ? "下载器连接失败时，相关任务的状态仍待确认。"
                    : "选择下载器后运行扫描，结果会保留在这里。"
                }}
              </p>
            </div>
            <div v-else class="stb-table-surface stb-table-scroll">
              <table class="stb-plain-table">
                <thead>
                  <tr>
                    <th>种子 / 下载器</th>
                    <th>大小</th>
                    <th>确认状态</th>
                    <th>保存路径</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="item in missingItems"
                    :key="`${item.downloader}:${item.hash}`"
                  >
                    <td>
                      <strong>{{ item.name }}</strong
                      ><small class="stb-block stb-muted">{{
                        item.downloader
                      }}</small>
                    </td>
                    <td>{{ sizeText(item.size) }}</td>
                    <td>
                      <StateBadge :state="item.monitor?.state" /><small
                        class="stb-block stb-muted"
                        >{{ item.monitor?.failure_days || 1 }} /
                        {{ threshold }} 天</small
                      >
                    </td>
                    <td class="stb-path">{{ item.save_path }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div
              v-if="overview.maintenance.cleanup.cleaned_at"
              class="stb-inline-note mt-4"
            >
              <v-icon icon="mdi-history" size="19" />最近清理
              {{ shortTime(overview.maintenance.cleanup.cleaned_at) }}：删除
              {{ overview.maintenance.cleanup.deleted_count || 0 }}，跳过
              {{ overview.maintenance.cleanup.skipped_count || 0 }}，失败
              {{ overview.maintenance.cleanup.failed_count || 0 }}。
            </div>
          </div>
          <div v-else-if="maintenanceTab === 'rss'" class="stb-tool-list">
            <article>
              <div>
                <h3>RSS 地址修复</h3>
                <p>
                  重新获取或规范化配置范围内站点的 RSS，验证成功后才写回地址。
                </p>
              </div>
              <v-btn
                variant="outlined"
                prepend-icon="mdi-wrench-outline"
                :disabled="busy"
                @click="repair()"
                >尝试修复</v-btn
              >
            </article>
            <article>
              <div>
                <h3>馒头登录历史</h3>
                <p>
                  检查真实网页登录记录，使用统一的
                  {{ threshold }} 天异常确认阈值。
                </p>
              </div>
              <v-btn
                variant="outlined"
                prepend-icon="mdi-login-variant"
                :disabled="busy"
                @click="perform('/mteam/login/check')"
                >检查登录</v-btn
              >
            </article>
            <article>
              <div>
                <h3>用户数据完整性</h3>
                <p>检查最新账号数据中的缺失字段、连接状态与过期情况。</p>
              </div>
              <v-btn
                variant="outlined"
                prepend-icon="mdi-account-check-outline"
                :disabled="busy"
                @click="perform('/check/userdata')"
                >检查数据</v-btn
              >
            </article>
          </div>
          <div v-else>
            <div class="stb-section-heading">
              <div>
                <h3>已加载的站点适配规则</h3>
                <p>
                  共
                  {{ overview.rules.length }}
                  个域名，兼容现有索引与用户数据规则。
                </p>
              </div>
              <v-btn
                variant="outlined"
                prepend-icon="mdi-tune-variant"
                @click="openSettings"
                >编辑规则</v-btn
              >
            </div>
            <div class="stb-table-surface">
              <table class="stb-plain-table">
                <thead>
                  <tr>
                    <th>域名</th>
                    <th>索引适配</th>
                    <th>用户数据</th>
                    <th>异步请求适配</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="rule in overview.rules" :key="rule.domain">
                    <td>{{ rule.domain }}</td>
                    <td>{{ rule.indexer ? "已启用" : "—" }}</td>
                    <td>{{ rule.userdata ? "已启用" : "—" }}</td>
                    <td>{{ rule.asyncfix ? "已启用" : "—" }}</td>
                  </tr>
                  <tr v-if="!overview.rules.length">
                    <td colspan="4" class="stb-empty">暂无自定义适配规则</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section v-else class="stb-logs-view">
          <template v-if="jobs.length"
            ><div class="stb-section-heading">
              <div>
                <h2>最近后台任务</h2>
                <p>显示每类任务的最近一次执行状态。</p>
              </div>
            </div>
            <div class="stb-task-list">
              <article v-for="job in jobs" :key="job.key">
                <v-icon
                  :icon="
                    job.status === 'success'
                      ? 'mdi-check-circle-outline'
                      : job.status === 'running'
                        ? 'mdi-progress-clock'
                        : 'mdi-alert-circle-outline'
                  "
                  :color="
                    job.status === 'success'
                      ? 'success'
                      : job.status === 'running'
                        ? 'primary'
                        : 'warning'
                  "
                  size="22"
                />
                <div>
                  <strong
                    >{{ job.name }} ·
                    {{
                      {
                        success: "已完成",
                        error: "执行失败",
                        running: "运行中",
                        interrupted: "已中断",
                      }[job.status] || job.status
                    }}</strong
                  >
                  <p>{{ job.message }}</p>
                </div>
                <small>{{
                  shortTime(job.finished_at || job.started_at)
                }}</small>
              </article>
            </div></template
          >
          <div class="stb-section-heading">
            <div>
              <h2>检测运行记录</h2>
              <p>查看最近 50 轮统一检测的结果与各模块执行情况。</p>
            </div>
          </div>
          <div v-if="!runs.length" class="stb-empty stb-surface">
            <v-icon icon="mdi-history" size="36" />
            <h3>还没有统一检测记录</h3>
            <p>运行一次“立即检测”后，完整结果会保存在这里。</p>
          </div>
          <details v-for="run in runs" :key="run.id" class="stb-run-record">
            <summary>
              <v-icon
                :icon="
                  run.status === 'success'
                    ? 'mdi-check-circle'
                    : 'mdi-alert-circle'
                "
                :color="run.status === 'success' ? 'success' : 'error'"
                size="20"
              /><strong>{{ shortTime(run.started_at) }}</strong
              ><span>{{
                run.status === "success" ? "已完成" : "部分检测失败"
              }}</span
              ><span class="stb-muted"
                >确认异常 {{ run.confirmed || 0 }} 项</span
              ><v-icon icon="mdi-chevron-down" size="20" />
            </summary>
            <div
              v-for="step in run.steps"
              :key="step.module"
              class="stb-run-step"
            >
              <span>{{ MODULES[step.module] }}</span
              ><span>{{ step.message }}</span>
            </div>
          </details>
          <v-divider class="my-7" />
          <div class="stb-section-heading">
            <div>
              <h2>系统错误</h2>
              <p>按相同来源和错误信息聚合，重复报错不会淹没诊断结果。</p>
            </div>
            <v-btn
              variant="text"
              prepend-icon="mdi-refresh"
              :loading="errorsLoading"
              @click="loadErrors"
              >刷新</v-btn
            >
          </div>
          <div class="stb-error-filters">
            <v-text-field
              v-model="errorQuery"
              label="搜索错误或来源"
              aria-label="搜索错误或来源"
              prepend-inner-icon="mdi-magnify"
              variant="outlined"
              density="compact"
              hide-details
            /><v-select
              v-model="errorLevel"
              :items="[
                { title: '全部级别', value: 'all' },
                { title: 'ERROR', value: 'ERROR' },
                { title: 'CRITICAL', value: 'CRITICAL' },
              ]"
              label="级别"
              variant="outlined"
              density="compact"
              hide-details
            />
          </div>
          <p v-if="errorGroups.sampled" class="stb-field-note mb-3">
            为保持响应速度，仅聚合最近约 2 MB 日志；次数来自这个样本。
          </p>
          <v-alert
            v-if="errorsError"
            type="error"
            variant="tonal"
            class="mb-3"
            >{{ errorsError }}</v-alert
          >
          <div class="stb-table-surface stb-table-scroll">
            <table class="stb-plain-table">
              <thead>
                <tr>
                  <th>错误 / 来源</th>
                  <th>次数</th>
                  <th>最近发生</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in errorGroups.items" :key="index">
                  <td>
                    <strong
                      >{{ item.source }}
                      <span class="stb-muted">{{ item.level }}</span></strong
                    >
                    <p class="stb-error-message">{{ item.message }}</p>
                  </td>
                  <td>{{ item.count }}</td>
                  <td class="stb-nowrap">{{ shortTime(item.last_at) }}</td>
                </tr>
                <tr v-if="!errorGroups.items.length">
                  <td colspan="3" class="stb-empty">
                    {{
                      errorsLoading ? "正在读取日志…" : "没有符合条件的错误记录"
                    }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <v-pagination
            v-if="errorGroups.total > 10"
            v-model="errorPage"
            :length="Math.ceil(errorGroups.total / 10)"
            :total-visible="5"
            density="compact"
            class="mt-3"
          />
        </section>
      </template>
      <footer class="stb-version">
        站点工具箱 v{{ overview?.plugin_version || "1.4.0"
        }}<span>所有检测模块共用统一监控计划</span>
      </footer>
    </main>

    <button
      v-if="mobile && detailOpen && selected && activeTab === 'sites'"
      class="stb-drawer-backdrop"
      aria-label="关闭站点详情"
      @click="closeDetails"
    />
    <aside
      ref="detailPane"
      class="stb-aside"
      :role="mobile ? 'dialog' : undefined"
      :aria-modal="mobile ? 'true' : undefined"
      aria-label="诊断详情与统一计划"
      @keydown.esc.stop.prevent="closeDetails"
      @keydown="trapDetailFocus"
    >
      <template v-if="selected && detailOpen && activeTab === 'sites'">
        <header class="stb-detail-header">
          <SiteAvatar :site="selected" :client="client" large />
          <div>
            <h2>
              {{ selected.name
              }}<a
                :href="selected.url"
                target="_blank"
                rel="noopener noreferrer"
                :aria-label="`打开${selected.name}网站`"
                ><v-icon icon="mdi-open-in-new" size="18"
              /></a>
            </h2>
            <p>{{ selected.domain }}</p>
          </div>
          <v-btn
            icon="mdi-close"
            variant="text"
            size="small"
            aria-label="关闭站点详情"
            @click="closeDetails"
          />
        </header>
        <div class="stb-detail-tabs">
          <button
            :class="{ active: detailTab === 'diagnosis' }"
            @click="detailTab = 'diagnosis'"
          >
            诊断结果</button
          ><button
            :class="{ active: detailTab === 'info' }"
            @click="detailTab = 'info'"
          >
            站点信息
          </button>
        </div>
        <section v-if="detailTab === 'diagnosis'" class="stb-detail-card">
          <div class="stb-current-status">
            <span
              class="stb-large-status-icon"
              :class="`is-${selectedMeta.color}`"
              ><v-icon :icon="selectedMeta.icon" size="34"
            /></span>
            <div>
              <small>当前状态</small>
              <h3 :class="`is-${selectedMeta.color}`">
                {{ selectedMeta.label }}
              </h3>
            </div>
            <div class="stb-streak">
              <small>连续异常</small
              ><strong
                >{{ selected.failure_days || 0 }}
                <span>/ {{ threshold }} 天</span></strong
              >
            </div>
          </div>
          <div class="stb-detail-times">
            <div>
              <small>首次异常</small><span>{{ firstFailedAt }}</span>
            </div>
            <div>
              <small>最近检测</small
              ><span>{{ shortTime(selected.last_checked_at) }}</span>
            </div>
          </div>
          <div class="stb-diagnostic-note">
            <v-icon icon="mdi-information-outline" size="18" />
            <p>
              {{ primaryReason
              }}<span v-if="selected.failure_days && !selected.confirmed"
                >达到连续 {{ threshold }} 天后才会确认异常并提醒。</span
              >
            </p>
          </div>
          <div class="stb-check-list">
            <article v-for="check in checks" :key="check.key">
              <div>
                <span
                  ><v-icon
                    :icon="
                      {
                        rss: 'mdi-rss',
                        userdata: 'mdi-account-outline',
                        mteam: 'mdi-login-variant',
                      }[check.key]
                    "
                    size="17"
                  />{{ check.label }}</span
                ><StateBadge :state="check.state" compact /><small>{{
                  check.tested_at ? shortTime(check.tested_at) : "未检测"
                }}</small>
              </div>
              <p v-if="check.state !== 'success'" :title="check.message">
                {{ check.raw_message || check.message }}
              </p>
            </article>
            <article class="stb-global-scope-note">
              <v-icon icon="mdi-download-network-outline" size="17" /><span
                >缺失种子按下载器监控</span
              ><button
                @click="
                  activeTab = 'maintenance';
                  maintenanceTab = 'missing';
                  if (mobile) detailOpen = false;
                "
              >
                查看
              </button>
            </article>
          </div>
          <div class="stb-detail-actions">
            <v-btn
              size="small"
              variant="text"
              prepend-icon="mdi-content-copy"
              @click="copyDiagnostic"
              >复制诊断</v-btn
            ><v-btn
              v-if="selected.checks.rss.raw_state !== 'success'"
              size="small"
              variant="text"
              color="primary"
              prepend-icon="mdi-wrench-outline"
              :disabled="busy"
              @click="repair(selected)"
              >修复 RSS</v-btn
            >
          </div>
        </section>
        <section v-else class="stb-detail-card">
          <dl class="stb-info-list">
            <dt>站点名称</dt>
            <dd>{{ selected.name }}</dd>
            <dt>域名</dt>
            <dd>{{ selected.domain }}</dd>
            <dt>站点状态</dt>
            <dd>{{ selected.active ? "已启用" : "已停用" }}</dd>
            <dt>监控范围</dt>
            <dd>{{ selected.monitored ? "已包含" : "未包含" }}</dd>
            <dt>自定义适配</dt>
            <dd>{{ selected.adapted ? "已加载" : "无自定义规则" }}</dd>
            <dt>最近检测</dt>
            <dd>{{ selected.last_checked_at || "尚未检测" }}</dd>
            <dt>RSS 条目 / 耗时</dt>
            <dd>
              {{ selected.checks.rss.count ?? "—" }} /
              {{ selected.checks.rss.seconds ?? "—" }} 秒
            </dd>
            <dt>RSS 来源</dt>
            <dd>{{ selected.checks.rss.source || "尚无记录" }}</dd>
            <dt>用户等级</dt>
            <dd>{{ selected.checks.userdata.user_level || "尚无记录" }}</dd>
            <dt>上传 / 下载</dt>
            <dd>
              {{ sizeText(selected.checks.userdata.upload) }} /
              {{ sizeText(selected.checks.userdata.download) }}
            </dd>
            <dt>分享率 / 魔力</dt>
            <dd>
              {{ selected.checks.userdata.ratio ?? "—" }} /
              {{ selected.checks.userdata.bonus ?? "—" }}
            </dd>
            <dt>做种 / 下载中</dt>
            <dd>
              {{ selected.checks.userdata.seeding ?? "—" }} /
              {{ selected.checks.userdata.leeching ?? "—" }}
            </dd>
            <dt>做种体积</dt>
            <dd>{{ sizeText(selected.checks.userdata.seeding_size) }}</dd>
            <dt>用户数据更新</dt>
            <dd>{{ selected.checks.userdata.updated_at || "尚无记录" }}</dd>
            <template v-if="selected.checks.mteam"
              ><dt>最近网页登录</dt>
              <dd>{{ selected.checks.mteam.last_login_at || "尚无记录" }}</dd>
              <dt>最近访问</dt>
              <dd>
                {{ selected.checks.mteam.last_browse_at || "尚无记录" }}
              </dd></template
            >
          </dl>
        </section>
      </template>
      <div
        v-else-if="!mobile && activeTab === 'sites'"
        class="stb-detail-placeholder"
      >
        <v-icon icon="mdi-text-box-search-outline" size="32" />
        <p>选择一个站点，查看诊断与处理建议。</p>
      </div>
      <section v-if="overview" class="stb-plan-card">
        <header>
          <h3><v-icon icon="mdi-clock-outline" size="22" />统一监控计划</h3>
          <v-btn
            size="small"
            variant="tonal"
            color="primary"
            prepend-icon="mdi-pencil-outline"
            @click="openSettings"
            >编辑计划</v-btn
          >
        </header>
        <dl>
          <div>
            <dt><v-icon icon="mdi-clock-start" size="17" />执行时间</dt>
            <dd>每天 {{ summary.window }}</dd>
          </div>
          <div>
            <dt><v-icon icon="mdi-timer-sync-outline" size="17" />执行频率</dt>
            <dd>{{ summary.cadence }}</dd>
          </div>
          <div>
            <dt><v-icon icon="mdi-clock-check-outline" size="17" />下次运行</dt>
            <dd>
              {{
                plan.next_run_at
                  ? shortTime(plan.next_run_at)
                  : "未启用定时监控"
              }}
            </dd>
          </div>
          <div>
            <dt>
              <v-icon icon="mdi-shield-check-outline" size="17" />异常确认天数
            </dt>
            <dd>{{ threshold }} 天 <small>（所有模块共用）</small></dd>
          </div>
        </dl>
        <p>
          监控范围：{{
            (plan.monitor_modules || [])
              .map(
                (key) =>
                  ({
                    rss: "RSS",
                    userdata: "用户数据",
                    missing: "缺失种子",
                    mteam: "馒头登录",
                  })[key],
              )
              .join("、") || "未选择检测模块"
          }}。
        </p>
        <p>成功即清零，同日多次只计一天；清理仅处理已确认项。</p>
        <small class="stb-plan-timezone"
          >按服务器时间执行{{
            plan.timezone ? ` · ${plan.timezone}` : ""
          }}</small
        >
      </section>
    </aside>
    <v-dialog
      v-model="settingsOpen"
      max-width="960"
      :fullscreen="mobile"
      persistent
      scrollable
      ><SettingsPanel
        v-if="settingsOpen"
        :initial="settings"
        :options="options"
        :saving="saving"
        :busy="busy"
        @save="saveSettings"
        @close="settingsOpen = false"
    /></v-dialog>
    <v-dialog v-model="confirmation.open" max-width="500"
      ><v-card :title="confirmation.title" :text="confirmation.text"
        ><v-card-actions
          ><v-spacer /><v-btn variant="text" @click="confirmation.open = false"
            >取消</v-btn
          ><v-btn
            :color="confirmation.destructive ? 'error' : 'primary'"
            variant="flat"
            @click="acceptConfirmation"
            >{{ confirmation.destructive ? "确认清理" : "确认修复" }}</v-btn
          ></v-card-actions
        ></v-card
      ></v-dialog
    >
    <v-snackbar
      v-model="snackbar.show"
      :color="snackbar.color"
      location="bottom"
      :timeout="4500"
      >{{ snackbar.text
      }}<template #actions
        ><v-btn
          aria-label="关闭提示"
          variant="text"
          icon="mdi-close"
          size="small"
          @click="snackbar.show = false" /></template
    ></v-snackbar>
  </div>
</template>
