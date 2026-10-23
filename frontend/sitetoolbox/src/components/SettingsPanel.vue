<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useTheme } from "vuetify";
import { DEFAULTS, MODULES, validateSettings } from "../lib/model";
const props = defineProps({
  initial: { type: Object, default: () => ({}) },
  options: { type: Object, default: () => ({}) },
  saving: Boolean,
  busy: Boolean,
});
const emit = defineEmits(["save", "close"]);
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
</script>
<template>
  <section class="stb-settings" :class="{ 'stb-is-light': isLight }">
    <header class="stb-settings-header">
      <div>
        <h2>统一监控与设置</h2>
        <p>一次配置，所有检测模块共用。</p>
      </div>
      <v-btn
        aria-label="关闭设置"
        icon="mdi-close"
        variant="text"
        @click="close"
      />
    </header>
    <v-tabs v-model="tab" color="primary" class="stb-settings-tabs"
      ><v-tab value="monitor">监控计划</v-tab
      ><v-tab value="scope">范围与策略</v-tab
      ><v-tab value="advanced">高级适配</v-tab></v-tabs
    >
    <v-form @submit.prevent="submit">
      <div class="stb-settings-body">
        <v-alert
          v-if="errors.length"
          type="error"
          variant="tonal"
          class="mb-5"
          role="alert"
          ><div v-for="error in errors" :key="error">{{ error }}</div></v-alert
        >
        <v-alert
          v-if="busy"
          type="info"
          variant="tonal"
          density="compact"
          class="mb-5"
          >检测正在运行，可以先编辑，完成后再保存设置。</v-alert
        >
        <div v-show="tab === 'monitor'">
          <div class="stb-settings-section-title">
            <div>
              <h3>统一监控计划</h3>
              <p>按服务器时间执行；手动检测不受时段限制。</p>
            </div>
            <v-switch
              v-model="draft.monitor_enabled"
              color="primary"
              label="启用定时监控"
              inset
              hide-details
            />
          </div>
          <div class="stb-form-pair">
            <v-text-field
              v-model="draft.monitor_start"
              type="time"
              label="开始时间"
              aria-label="开始时间"
              variant="outlined"
              density="comfortable"
            /><v-text-field
              v-model="draft.monitor_end"
              type="time"
              label="结束时间"
              aria-label="结束时间"
              variant="outlined"
              density="comfortable"
            />
          </div>
          <p class="stb-field-note">
            支持跨午夜，例如 22:00–06:00；开始与结束相同表示全天。
          </p>
          <h3 class="mt-5 mb-3">执行方式</h3>
          <v-btn-toggle
            v-model="draft.monitor_mode"
            mandatory
            color="primary"
            variant="outlined"
            divided
            class="mb-4"
            ><v-btn value="interval" prepend-icon="mdi-timer-outline"
              >周期执行</v-btn
            ><v-btn value="fixed" prepend-icon="mdi-clock-outline"
              >固定时刻</v-btn
            ></v-btn-toggle
          >
          <v-select
            v-if="draft.monitor_mode === 'interval'"
            v-model="draft.monitor_interval"
            :items="[
              { title: '每 15 分钟', value: 15 },
              { title: '每 30 分钟', value: 30 },
              { title: '每 1 小时', value: 60 },
              { title: '每 2 小时', value: 120 },
              { title: '每 4 小时', value: 240 },
              { title: '每 6 小时', value: 360 },
              { title: '每天一次', value: 1440 },
            ]"
            label="执行周期"
            aria-label="执行周期"
            variant="outlined"
            hint="周期从当天执行时段的开始时间起计算"
            persistent-hint
          />
          <v-text-field
            v-else
            v-model="fixedTimes"
            label="固定执行时刻"
            aria-label="固定执行时刻"
            placeholder="08:00, 20:00"
            variant="outlined"
            hint="多个时刻用逗号分隔，需位于允许执行的时段内"
            persistent-hint
          />
          <h3 class="mt-6 mb-1">检测模块</h3>
          <p class="stb-field-note">
            以下模块共用同一套执行计划和异常确认规则。
          </p>
          <div class="stb-module-options">
            <v-checkbox
              v-for="(label, key) in MODULES"
              :key="key"
              v-model="draft.monitor_modules"
              :value="key"
              :label="label"
              color="primary"
              hide-details
              density="comfortable"
            />
          </div>
          <div class="stb-threshold-setting">
            <div>
              <h3>异常确认天数</h3>
              <p>连续异常达到设定天数，才确认问题并提醒。</p>
            </div>
            <v-text-field
              v-model="draft.monitor_failure_days"
              type="number"
              min="1"
              max="365"
              label="所有模块共用"
              aria-label="异常确认天数"
              suffix="天"
              variant="outlined"
              hide-details
            />
          </div>
          <v-alert type="info" variant="tonal" density="compact" class="mt-4"
            >同一天重复异常只计一天；任何一次成功都会清零，漏测一天重新累计。记录会在重启后保留。</v-alert
          >
        </div>
        <div v-show="tab === 'scope'">
          <h3 class="mb-2">站点范围</h3>
          <p class="stb-field-note mb-4">
            不指定站点时，检测所有已启用站点。登录检查只对 M-Team 站点运行。
          </p>
          <v-autocomplete
            v-model="draft.site_ids"
            :items="options.sites || []"
            multiple
            clearable
            label="监控站点"
            variant="outlined"
            :menu-props="{ maxHeight: 320 }"
            ><template #selection="{ index }"
              ><span v-if="index === 0"
                >已选择 {{ draft.site_ids.length }} 个站点</span
              ></template
            ></v-autocomplete
          >
          <v-text-field
            v-model="draft.timeout"
            label="RSS 请求超时"
            suffix="秒"
            type="number"
            min="5"
            max="120"
            variant="outlined"
          />
          <v-switch
            v-model="draft.auto_discover"
            label="未配置 RSS 时自动获取"
            color="primary"
            hide-details
          /><v-switch
            v-model="draft.save_discovered"
            label="将自动获取的 RSS 保存到站点"
            color="primary"
            hide-details
          />
          <v-divider class="my-6" />
          <h3 class="mb-2">缺失种子</h3>
          <v-select
            v-model="draft.cleanup_downloader_names"
            :items="options.downloaders || []"
            multiple
            clearable
            label="扫描下载器"
            variant="outlined"
            class="mt-4"
            ><template #selection="{ index }"
              ><span v-if="index === 0"
                >已选择
                {{ draft.cleanup_downloader_names.length }} 个下载器</span
              ></template
            ></v-select
          >
          <v-switch
            v-model="draft.cleanup_auto_cleanup"
            label="自动清理已确认异常的缺失种子"
            color="primary"
            hide-details
          />
          <p class="stb-field-note">
            只处理达到全局异常天数阈值的种子，删除前再次核对 missingFiles 状态。
          </p>
          <v-switch
            v-model="draft.cleanup_delete_files"
            label="清理时同时删除数据文件"
            color="warning"
            hide-details
          /><v-alert
            v-if="draft.cleanup_delete_files"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-3"
            >已开启删除数据文件。手动清理前会再次说明影响范围。</v-alert
          >
          <v-divider class="my-6" />
          <h3 class="mb-2">检测条件</h3>
          <p class="stb-field-note mb-4">
            这些条件决定单次检测是否异常；连续几天才确认，统一使用“异常确认天数”。
          </p>
          <v-autocomplete
            v-model="draft.mteam_site_ids"
            :items="(options.sites || []).filter((site) => site.mteam)"
            multiple
            clearable
            label="馒头检查站点"
            aria-label="馒头检查站点"
            variant="outlined"
            hint="留空沿用上方监控站点范围"
            persistent-hint
            class="mb-4"
            ><template #selection="{ index }"
              ><span v-if="index === 0"
                >已选择 {{ draft.mteam_site_ids.length }} 个馒头站点</span
              ></template
            ></v-autocomplete
          >
          <div class="stb-form-pair">
            <v-text-field
              v-model="draft.userdata_stale_days"
              type="number"
              min="1"
              max="365"
              label="用户数据过期时间"
              suffix="天"
              variant="outlined"
            /><v-text-field
              v-model="draft.mteam_warning_days"
              type="number"
              min="1"
              max="365"
              label="网页登录记录最长间隔"
              suffix="天"
              variant="outlined"
            />
          </div>
        </div>
        <div v-show="tab === 'advanced'">
          <v-switch
            v-model="draft.enabled"
            color="primary"
            label="启用站点工具箱"
            hide-details
          /><v-switch
            v-model="draft.patch_userdata"
            color="primary"
            label="启用用户数据解析规则"
            hide-details
          />
          <v-select
            v-model="draft.error_retention_days"
            :items="[
              { title: '1 天', value: 1 },
              { title: '3 天', value: 3 },
              { title: '7 天', value: 7 },
            ]"
            label="系统错误保留时间"
            aria-label="系统错误保留时间"
            variant="outlined"
            class="mt-5"
          />
          <h3 class="mb-2">站点适配规则</h3>
          <p class="stb-field-note mb-4">
            兼容 JSON、JSON 数组和 domain|base64(JSON)。请勿将账号、Cookie
            或密钥写入规则。
          </p>
          <v-textarea
            v-model="draft.site_conf"
            label="规则内容"
            aria-label="规则内容"
            variant="outlined"
            rows="12"
            no-resize
            spellcheck="false"
            class="stb-code-input"
          />
        </div>
      </div>
      <footer class="stb-settings-footer">
        <span>{{ dirty ? "有未保存的修改" : "设置已同步" }}</span
        ><v-btn variant="text" @click="close">取消</v-btn
        ><v-btn
          type="submit"
          color="primary"
          variant="flat"
          prepend-icon="mdi-content-save-outline"
          :loading="saving"
          :disabled="busy || saving"
          >保存设置</v-btn
        >
      </footer>
    </v-form>
    <v-dialog v-model="discard" max-width="420"
      ><v-card
        title="放弃未保存的修改？"
        text="这次修改尚未保存，关闭后将恢复原设置。"
        ><v-card-actions
          ><v-spacer /><v-btn @click="discard = false">继续编辑</v-btn
          ><v-btn
            color="primary"
            @click="
              discard = false;
              emit('close');
            "
            >放弃修改</v-btn
          ></v-card-actions
        ></v-card
      ></v-dialog
    >
  </section>
</template>
