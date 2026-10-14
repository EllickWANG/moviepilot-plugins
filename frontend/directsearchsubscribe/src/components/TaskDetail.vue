<script setup>
import { computed, ref, watch } from 'vue'
import { formatSize, levelColor, shortHash, transferColor, transferLabel } from '../lib/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: null },
  client: { type: Object, required: true },
  mobile: { type: Boolean, default: false },
  initialTab: { type: String, default: 'results' },
})
const emit = defineEmits(['update:modelValue'])

const tab = ref('results')
const loading = ref(false)
const results = ref([])
const logs = ref([])
const logFilter = ref('all')

const records = computed(() => props.task?.download_records || [])
const filteredLogs = computed(() =>
  logFilter.value === 'all' ? logs.value : logs.value.filter(item => item.level === logFilter.value),
)

function resultState(item) {
  if (item.downloaded) return { label: '已下载', color: 'success' }
  if (item.duplicate) return { label: '重复跳过', color: 'grey' }
  if (item.download_error) return { label: '下载失败', color: 'error' }
  if (item.skip_reason) return { label: '未下载', color: 'warning' }
  return { label: item.downloadable === false ? '不可下载' : '候选', color: 'info' }
}

function resultNote(item) {
  return item.download_error || item.skip_reason || item.selection_reason || item.reason || ''
}

async function load() {
  if (!props.task?.id) return
  loading.value = true
  try {
    const [resultResponse, logResponse] = await Promise.all([
      props.client.get(`/tasks/${props.task.id}/results`),
      props.client.get(`/tasks/${props.task.id}/logs`),
    ])
    results.value = resultResponse.data || []
    logs.value = logResponse.data || []
  } catch (err) {
    console.error('读取任务详情失败', err)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.modelValue,
  open => {
    if (!open) return
    tab.value = props.initialTab || 'results'
    logFilter.value = 'all'
    results.value = []
    logs.value = []
    load()
  },
)

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <v-dialog :model-value="modelValue" :max-width="1100" :fullscreen="mobile" scrollable
            @update:model-value="value => !value && close()">
    <v-card rounded="lg">
      <v-toolbar flat density="comfortable" color="transparent">
        <v-icon class="ms-4 me-2" color="primary">mdi-text-box-search-outline</v-icon>
        <v-toolbar-title class="font-weight-bold text-truncate">{{ task?.name || '任务详情' }}</v-toolbar-title>
        <v-spacer />
        <v-btn icon="mdi-refresh" variant="text" :loading="loading" @click="load" />
        <v-btn icon="mdi-close" variant="text" @click="close" />
      </v-toolbar>
      <v-tabs v-model="tab" color="primary" density="comfortable" class="px-2">
        <v-tab value="results">最近候选 ({{ results.length }})</v-tab>
        <v-tab value="records">下载记录 ({{ records.length }})</v-tab>
        <v-tab value="logs">运行日志 ({{ logs.length }})</v-tab>
      </v-tabs>
      <v-divider />

      <v-card-text class="pa-0">
        <v-progress-linear v-if="loading" indeterminate color="primary" />
        <v-window v-model="tab">
          <!-- 候选 -->
          <v-window-item value="results">
            <div v-if="!results.length && !loading" class="pa-8 text-center text-medium-emphasis">
              还没有候选记录，点击“立即检查”后再来看。
            </div>
            <template v-else-if="mobile">
              <v-list lines="three" density="compact">
                <v-list-item v-for="(item, index) in results" :key="index">
                  <v-list-item-title class="text-body-2" style="white-space: normal">{{ item.title }}</v-list-item-title>
                  <v-list-item-subtitle>
                    {{ item.site }} · 做种 {{ item.seeders ?? '-' }} · {{ formatSize(item.size) }}
                    <span v-if="item.episodes"> · 第 {{ item.episodes }} 集</span>
                    <v-chip v-if="item.free" size="x-small" color="success" variant="tonal" class="ms-1">免费</v-chip>
                  </v-list-item-subtitle>
                  <v-list-item-subtitle>
                    <v-chip size="x-small" :color="resultState(item).color" variant="flat" class="me-1">{{ resultState(item).label }}</v-chip>
                    {{ resultNote(item) }}
                  </v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </template>
            <v-table v-else density="compact" hover>
              <thead>
                <tr>
                  <th>标题</th><th>站点</th><th class="text-right">做种</th><th class="text-right">大小</th><th>集数</th><th>结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in results" :key="index">
                  <td style="max-width: 420px">
                    <div class="text-body-2" style="word-break: break-all">{{ item.title }}</div>
                    <div v-if="item.pubdate" class="text-caption text-medium-emphasis">{{ item.pubdate }}</div>
                  </td>
                  <td>{{ item.site }}<v-chip v-if="item.free" size="x-small" color="success" variant="tonal" class="ms-1">免费</v-chip></td>
                  <td class="text-right">{{ item.seeders ?? '-' }}</td>
                  <td class="text-right text-no-wrap">{{ formatSize(item.size) }}</td>
                  <td class="text-no-wrap">{{ item.episodes || '-' }}</td>
                  <td style="max-width: 320px">
                    <v-chip size="x-small" :color="resultState(item).color" variant="flat" class="me-1">{{ resultState(item).label }}</v-chip>
                    <span class="text-caption">{{ resultNote(item) }}</span>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-window-item>

          <!-- 下载记录 -->
          <v-window-item value="records">
            <div v-if="!records.length" class="pa-8 text-center text-medium-emphasis">还没有通过本插件加入的下载。</div>
            <template v-else-if="mobile">
              <v-list lines="three" density="compact">
                <v-list-item v-for="(item, index) in records" :key="index">
                  <v-list-item-title class="text-body-2" style="white-space: normal">{{ item.title }}</v-list-item-title>
                  <v-list-item-subtitle>{{ item.time }} · {{ item.site }} · 第 {{ item.episodes || '?' }} 集 · {{ formatSize(item.size) }}</v-list-item-subtitle>
                  <v-list-item-subtitle>
                    <v-chip size="x-small" :color="transferColor(item.transfer_status)" variant="flat" class="me-1">{{ transferLabel(item.transfer_status) }}</v-chip>
                    {{ item.transfer_message || item.repair_message || '' }}
                  </v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </template>
            <v-table v-else density="compact" hover>
              <thead>
                <tr><th>时间</th><th>标题</th><th>站点</th><th>集数</th><th class="text-right">大小</th><th>整理</th><th>Hash</th></tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in records" :key="index">
                  <td class="text-no-wrap text-caption">{{ item.time }}</td>
                  <td style="max-width: 380px"><div class="text-body-2" style="word-break: break-all">{{ item.title }}</div></td>
                  <td>{{ item.site }}</td>
                  <td class="text-no-wrap">{{ item.episodes || '-' }}</td>
                  <td class="text-right text-no-wrap">{{ formatSize(item.size) }}</td>
                  <td>
                    <v-chip size="x-small" :color="transferColor(item.transfer_status)" variant="flat">{{ transferLabel(item.transfer_status) }}</v-chip>
                    <div v-if="item.transfer_message || item.repair_message" class="text-caption text-medium-emphasis">{{ item.transfer_message || item.repair_message }}</div>
                  </td>
                  <td class="text-caption font-mono">{{ shortHash(item.hash) }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-window-item>

          <!-- 日志 -->
          <v-window-item value="logs">
            <div class="d-flex align-center ga-2 px-4 pt-3">
              <v-chip-group v-model="logFilter" mandatory selected-class="text-primary">
                <v-chip value="all" size="small" filter>全部</v-chip>
                <v-chip value="warning" size="small" filter color="warning">警告</v-chip>
                <v-chip value="error" size="small" filter color="error">错误</v-chip>
              </v-chip-group>
            </div>
            <div v-if="!filteredLogs.length && !loading" class="pa-8 text-center text-medium-emphasis">没有日志。</div>
            <v-table v-else density="compact" hover>
              <thead>
                <tr><th>时间</th><th>阶段</th><th>动作</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in filteredLogs" :key="index">
                  <td class="text-no-wrap text-caption">{{ item.time }}</td>
                  <td class="text-no-wrap">{{ item.stage }}</td>
                  <td class="text-no-wrap">
                    <v-chip size="x-small" :color="levelColor(item.level)" variant="tonal">{{ item.action }}</v-chip>
                  </td>
                  <td>
                    <div class="text-body-2" style="word-break: break-all">{{ item.reason }}</div>
                    <div v-if="item.title" class="text-caption text-medium-emphasis" style="word-break: break-all">
                      {{ item.title }}<span v-if="item.site"> · {{ item.site }}</span><span v-if="item.episodes"> · 第 {{ item.episodes }} 集</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-window-item>
        </v-window>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
