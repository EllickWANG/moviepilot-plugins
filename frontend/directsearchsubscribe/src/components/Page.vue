<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { useDisplay } from 'vuetify'
import { createClient } from '../lib/client'
import { needsAttention, priorityLabel, progressText, seasonText, taskState } from '../lib/format'
import TaskEditor from './TaskEditor.vue'
import TaskDetail from './TaskDetail.vue'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: '' },
  sourcePluginId: { type: String, default: '' },
  nativeSubscribe: { type: Function, default: null },
  show_switch: { type: Boolean, default: true },
})
const emit = defineEmits(['action', 'switch', 'close', 'layout'])

const toast = inject('moviepilot:toast', null)
const hostConfirm = inject('moviepilot:confirm', null)
const client = createClient(props.api, props.pluginId || 'directsearchsubscribe')
const display = useDisplay()
const mobile = computed(() => !display.mdAndUp.value)

const overview = ref(null)
const loading = ref(false)
const error = ref('')
const query = ref('')
const statusFilter = ref('all')
const busy = ref({})
const editorOpen = ref(false)
const editingTask = ref(null)
const detailOpen = ref(false)
const detailTask = ref(null)
const detailTab = ref('results')
const trashPanel = ref([])
const snackbar = ref({ show: false, text: '', color: 'success' })

let pollTimer = null
let pollDeadline = 0

const tasks = computed(() => overview.value?.tasks || [])
const trash = computed(() => overview.value?.trash || [])
const legacy = computed(() => overview.value?.legacy || [])
const repair = computed(() => overview.value?.repair || {})
const settings = computed(() => overview.value?.settings || {})
const runningIds = computed(() => new Set(overview.value?.running_task_ids || []))
const version = computed(() => overview.value?.plugin_version || '')

const stats = computed(() => {
  const list = tasks.value
  return [
    { label: '任务', value: list.length, icon: 'mdi-view-list', color: 'primary' },
    { label: '追更中', value: list.filter(t => t.enabled && t.status !== 'completed').length, icon: 'mdi-play-circle', color: 'success' },
    { label: '自动下载', value: list.filter(t => t.auto_download).length, icon: 'mdi-download', color: 'warning' },
    { label: '需要关注', value: list.filter(needsAttention).length, icon: 'mdi-alert-circle', color: 'error' },
  ]
})

const statusOptions = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '追更中' },
  { value: 'paused', label: '已暂停' },
  { value: 'completed', label: '已完成' },
  { value: 'attention', label: '需要关注' },
]

const filteredTasks = computed(() => {
  const text = query.value.trim().toLowerCase()
  return tasks.value.filter(task => {
    if (text) {
      const haystack = [task.name, ...(task.keywords || []), ...(task.aliases || []), task.media_category]
        .filter(Boolean).join(' ').toLowerCase()
      if (!haystack.includes(text)) return false
    }
    switch (statusFilter.value) {
      case 'active': return task.enabled && task.status !== 'completed'
      case 'paused': return !task.enabled || task.status === 'paused'
      case 'completed': return task.status === 'completed'
      case 'attention': return needsAttention(task)
      default: return true
    }
  })
})

const anythingRunning = computed(() =>
  runningIds.value.size > 0 || Boolean(repair.value.running) || Boolean(overview.value?.transfer_retry_running),
)

function notify(message, color = 'success') {
  if (!message) return
  if (toast) {
    const method = { success: 'success', error: 'error', warning: 'warning', info: 'info' }[color] || 'info'
    toast[method]?.(message)
    return
  }
  snackbar.value = { show: true, text: message, color }
}

async function confirm(options) {
  if (hostConfirm) {
    const result = await hostConfirm(options)
    return result === true
  }
  return window.confirm(`${options.title || '确认'}\n${options.content || ''}`)
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const response = await client.get('/overview')
    if (!response.success) {
      error.value = response.message || '读取插件数据失败'
      return
    }
    overview.value = response.data
    error.value = ''
    if (detailTask.value) {
      detailTask.value = tasks.value.find(item => item.id === detailTask.value.id) || detailTask.value
    }
  } catch (err) {
    error.value = err?.message || '读取插件数据失败'
  } finally {
    loading.value = false
  }
}

/** 动作触发后短时间轮询，直到后台任务结束或超过 90 秒。 */
function schedulePolling(seconds = 90) {
  pollDeadline = Date.now() + seconds * 1000
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await load(true)
    if (!anythingRunning.value && Date.now() > pollDeadline) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }, 5000)
}

function isRunning(task) {
  return runningIds.value.has(task.id)
}

function isBusy(task, action) {
  return busy.value[task.id] === action
}

async function perform(task, action, request, { reload = true, poll = false } = {}) {
  const key = task?.id || action
  busy.value = { ...busy.value, [key]: action }
  try {
    const response = await request()
    if (response.success) {
      notify(response.message)
      emit('action')
      if (reload) await load(true)
      if (poll) schedulePolling()
    }
    return response
  } catch (err) {
    notify(err?.message || '操作失败', 'error')
    return { success: false }
  } finally {
    const next = { ...busy.value }
    delete next[key]
    busy.value = next
  }
}

const runTask = task => perform(task, 'run', () => client.post(`/tasks/${task.id}/run`), { poll: true })
const toggleTask = task => perform(task, 'toggle', () => client.post(`/tasks/${task.id}/toggle`))
const toggleAuto = task => perform(task, 'auto', () => client.post(`/tasks/${task.id}/auto`))

async function resetTask(task) {
  const ok = await confirm({ type: 'warn', title: '重置进度', content: `将清空“${task.name}”的已下载集数和下载记录，只保留“已经拥有的集数”。` })
  if (!ok) return
  await perform(task, 'reset', () => client.post(`/tasks/${task.id}/reset`))
}

async function cleanupTask(task, deleteFiles) {
  const scope = deleteFiles ? '从下载器移除本插件为该任务添加的下载，并删除已下载的文件' : '从下载器移除本插件为该任务添加的下载，保留已下载的文件'
  const ok = await confirm({ type: 'error', title: deleteFiles ? '清理任务和下载文件' : '清理任务（保留文件）',
                             content: `${scope}，然后重置进度并立即重新检查。媒体库中已整理的成品不会被删除。` })
  if (!ok) return
  await perform(task, 'cleanup', () => client.post(`/tasks/${task.id}/cleanup`, { delete_files: deleteFiles }), { poll: true })
}

async function trashTask(task) {
  const ok = await confirm({ type: 'warn', title: '移入回收站', content: `“${task.name}”将停止检查并移入回收站，可随时恢复。` })
  if (!ok) return
  await perform(task, 'delete', () => client.post(`/tasks/${task.id}/delete`))
}

async function purgeTask(task) {
  const ok = await confirm({ type: 'error', title: '彻底删除', content: `“${task.name}”将被直接删除，不进入回收站，且其下载记录不再参与历史去重。` })
  if (!ok) return
  await perform(task, 'purge', async () => {
    const moved = await client.post(`/tasks/${task.id}/delete`)
    if (!moved.success) return moved
    return client.post(`/trash/${task.id}/purge`)
  })
}

const restoreTrash = item => perform(item, 'restore', () => client.post(`/trash/${item.id}/restore`))

async function purgeTrash(item) {
  const ok = await confirm({ type: 'error', title: '彻底删除', content: `“${item.name}”将从回收站永久删除。` })
  if (!ok) return
  await perform(item, 'purge', () => client.post(`/trash/${item.id}/purge`))
}

async function purgeAllTrash() {
  const ok = await confirm({ type: 'error', title: '清空回收站', content: `将永久删除回收站中的 ${trash.value.length} 个任务。` })
  if (!ok) return
  await perform(null, 'purge-all', () => client.post('/trash/purge'))
}

async function purgeLegacy() {
  const ok = await confirm({ type: 'warn', title: '清除旧版映射', content: '只移除插件内 1.x 残留的订阅映射，不会改动系统订阅。' })
  if (!ok) return
  await perform(null, 'legacy', () => client.post('/legacy/purge'))
}

const runRepair = () => perform(null, 'repair', () => client.post('/repair/run'), { poll: true })
const retryTransfers = () => perform(null, 'retry', () => client.post('/transfers/retry-failed'), { poll: true })

function openEditor(task) {
  editingTask.value = task
  editorOpen.value = true
}

async function onSaved(payload) {
  notify(payload.message || (payload.created ? '任务已创建' : '任务已更新'))
  emit('action')
  await load(true)
  if (payload.created && payload.task?.id) {
    const ok = await confirm({ type: 'info', title: '现在检查一次？', content: '立即搜索站点并生成候选列表，不会自动下载（除非已开启自动下载）。',
                               confirmText: '立即检查', cancelText: '稍后' })
    if (ok) await runTask(payload.task)
  }
}

function openDetail(task, tab = 'results') {
  detailTask.value = task
  detailTab.value = tab
  detailOpen.value = true
}

function metaLine(task) {
  const parts = [task.type]
  if (task.year) parts.push(task.year)
  const season = seasonText(task)
  if (season) parts.push(season)
  if (task.media_category) parts.push(task.media_category)
  parts.push(priorityLabel(task.priority_mode))
  if (task.sites?.length) parts.push(`${task.sites.length} 个指定站点`)
  return parts.join(' · ')
}

function messageTone(task) {
  if (task.status === 'error') return { icon: 'mdi-alert-circle-outline', cls: 'text-error' }
  if (task.last_download_count) return { icon: 'mdi-check-circle-outline', cls: 'text-success' }
  return { icon: 'mdi-information-outline', cls: 'text-medium-emphasis' }
}

function progressValue(task) {
  if (task.progress !== null && task.progress !== undefined) return task.progress
  if (task.status === 'completed') return 100
  return task.downloaded_count ? 100 : 0
}

function progressColor(task) {
  if (task.status === 'completed') return 'info'
  if (task.tracking_mode === 'fixed' && !task.missing_count) return 'success'
  return task.enabled ? 'primary' : 'grey'
}

onMounted(() => {
  emit('layout', { maxWidth: '92rem' })
  load()
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <v-card flat class="dss-page">
    <v-toolbar flat density="comfortable" color="transparent">
      <v-icon class="ms-4 me-2" color="primary">mdi-magnify-scan</v-icon>
      <v-toolbar-title class="font-weight-bold">
        直搜订阅
        <span v-if="version" class="text-caption text-medium-emphasis ms-1">v{{ version }}</span>
      </v-toolbar-title>
      <v-spacer />
      <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" class="me-1" @click="openEditor(null)">
        {{ mobile ? '新建' : '新建任务' }}
      </v-btn>
      <v-btn icon="mdi-refresh" variant="text" :loading="loading" @click="load()" />
      <v-btn v-if="show_switch" icon="mdi-cog-outline" variant="text" @click="emit('switch')" />
      <v-btn icon="mdi-close" variant="text" @click="emit('close')" />
    </v-toolbar>

    <v-card-text class="pt-0">
      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-4">
        {{ error }}
        <template #append><v-btn size="small" variant="text" @click="load()">重试</v-btn></template>
      </v-alert>

      <v-skeleton-loader v-if="!overview && loading" type="article, list-item-three-line@3" />

      <template v-else-if="overview">
        <!-- 概览 -->
        <v-row dense class="mb-2">
          <v-col v-for="item in stats" :key="item.label" cols="6" md="3">
            <v-sheet rounded="lg" :color="item.color" class="pa-3 d-flex align-center ga-3 dss-stat" variant="tonal">
              <v-icon :color="item.color">{{ item.icon }}</v-icon>
              <div>
                <div class="text-h6 font-weight-bold lh-1">{{ item.value }}</div>
                <div class="text-caption text-medium-emphasis">{{ item.label }}</div>
              </div>
            </v-sheet>
          </v-col>
        </v-row>

        <!-- 旧版映射 -->
        <v-alert v-if="legacy.length" type="warning" variant="tonal" density="comfortable" rounded="lg" class="mb-3">
          检测到 1.x 遗留映射 {{ legacy.length }} 条：{{ legacy.map(item => item.title).slice(0, 5).join('、') }}。
          这些数据不再读取或执行；若系统订阅页已没有对应订阅，可直接清除。
          <template #append>
            <v-btn size="small" variant="flat" color="warning" :loading="busy.legacy === 'legacy'" @click="purgeLegacy">清除</v-btn>
          </template>
        </v-alert>

        <!-- 运行状态 -->
        <v-sheet rounded="lg" border class="pa-3 mb-3 d-flex flex-wrap align-center ga-3">
          <div class="d-flex align-center ga-2 text-body-2">
            <v-icon size="18" :color="settings.enabled ? 'success' : 'grey'">mdi-clock-outline</v-icon>
            <span>{{ settings.enabled ? `定时检查 ${settings.cron}` : '定时检查已关闭' }}</span>
          </div>
          <v-divider vertical class="d-none d-md-block" />
          <div class="d-flex align-center ga-2 text-body-2">
            <v-icon size="18" :color="repair.running ? 'primary' : (repair.errors?.length ? 'warning' : 'grey')">mdi-shield-sync-outline</v-icon>
            <span v-if="repair.running">丢失补偿运行中…</span>
            <span v-else-if="repair.last_run_at">补偿 {{ repair.last_run_at }} · {{ repair.message || '完成' }}</span>
            <span v-else>{{ settings.repair_enabled ? `丢失补偿 ${settings.repair_cron}` : '丢失补偿已关闭' }}</span>
          </div>
          <v-spacer />
          <v-btn size="small" variant="tonal" prepend-icon="mdi-shield-sync" :loading="busy.repair === 'repair' || repair.running" @click="runRepair">立即补偿</v-btn>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-folder-sync" :loading="busy.retry === 'retry' || overview.transfer_retry_running" @click="retryTransfers">重试失败整理</v-btn>
        </v-sheet>

        <!-- 筛选 -->
        <div class="d-flex flex-wrap align-center ga-2 mb-3">
          <v-text-field v-model="query" density="compact" variant="outlined" hide-details clearable
                        prepend-inner-icon="mdi-magnify" placeholder="搜索节目、关键词或分类" class="dss-search" />
          <v-chip-group v-model="statusFilter" mandatory selected-class="text-primary">
            <v-chip v-for="option in statusOptions" :key="option.value" :value="option.value" size="small" filter>{{ option.label }}</v-chip>
          </v-chip-group>
        </div>

        <!-- 空状态 -->
        <v-sheet v-if="!tasks.length" rounded="lg" border class="pa-10 text-center">
          <v-icon size="56" color="primary" class="mb-3">mdi-magnify-scan</v-icon>
          <div class="text-h6 font-weight-bold mb-1">还没有直搜任务</div>
          <div class="text-body-2 text-medium-emphasis mb-4">
            填写节目名称、季和集数范围，插件会按你给的关键词定时直搜站点，并在下载完成后按人工信息整理。
          </div>
          <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openEditor(null)">新建第一个任务</v-btn>
        </v-sheet>
        <v-sheet v-else-if="!filteredTasks.length" rounded="lg" border class="pa-8 text-center text-medium-emphasis">
          没有符合筛选条件的任务。
        </v-sheet>

        <!-- 任务卡片 -->
        <v-card v-for="task in filteredTasks" :key="task.id" variant="outlined" rounded="lg" class="mb-3 dss-task"
                :class="{ 'dss-task--running': isRunning(task), 'dss-task--paused': !task.enabled }">
          <div class="pa-3 d-flex flex-wrap align-start ga-3">
            <v-avatar :color="taskState(task, isRunning(task)).color" variant="tonal" size="44" class="flex-shrink-0">
              <v-icon>{{ task.type === '电影' ? 'mdi-movie-open-outline' : 'mdi-television-play' }}</v-icon>
            </v-avatar>

            <div class="flex-grow-1 dss-task__body">
              <div class="d-flex flex-wrap align-center ga-2">
                <span class="text-subtitle-1 font-weight-bold dss-task__name">{{ task.name }}</span>
                <v-chip size="x-small" :color="taskState(task, isRunning(task)).color" variant="flat"
                        :prepend-icon="taskState(task, isRunning(task)).icon">{{ taskState(task, isRunning(task)).label }}</v-chip>
                <v-chip size="x-small" :color="task.auto_download ? 'warning' : undefined" variant="tonal">
                  {{ task.auto_download ? '自动下载' : '仅预览' }}
                </v-chip>
              </div>
              <div class="text-caption text-medium-emphasis mt-1">{{ metaLine(task) }}</div>

              <div class="mt-2 dss-progress">
                <div class="d-flex justify-space-between text-caption mb-1">
                  <span :class="task.missing_count ? '' : 'text-success'">{{ progressText(task) }}</span>
                  <span v-if="task.progress !== null && task.progress !== undefined" class="text-medium-emphasis">{{ task.progress }}%</span>
                </div>
                <v-progress-linear :model-value="progressValue(task)" :color="progressColor(task)" height="6" rounded
                                   :indeterminate="isRunning(task)" />
              </div>

              <div class="text-caption mt-2 d-flex align-start ga-1" :class="messageTone(task).cls">
                <v-icon size="15" class="mt-1 flex-shrink-0">{{ messageTone(task).icon }}</v-icon>
                <span class="dss-task__message">
                  {{ task.last_message || '尚未运行，点击“立即检查”预览候选。' }}
                  <span v-if="task.last_run_at" class="text-medium-emphasis"> · {{ task.last_run_at }}</span>
                </span>
              </div>
              <div v-if="task.transfer_status_text" class="text-caption text-medium-emphasis d-flex align-start ga-1 mt-1">
                <v-icon size="15" class="mt-1 flex-shrink-0" :color="task.last_transfer_status === 'failed' ? 'error' : undefined">mdi-folder-move-outline</v-icon>
                <span>整理：{{ task.transfer_status_text }}<span v-if="task.last_transfer_message"> · {{ task.last_transfer_message }}</span></span>
              </div>
            </div>

            <div class="d-flex align-center flex-wrap dss-task__actions">
              <v-btn icon size="small" variant="text" color="primary" :loading="isBusy(task, 'run')" :disabled="isRunning(task)" @click="runTask(task)">
                <v-icon>mdi-magnify</v-icon><v-tooltip activator="parent" location="top">立即检查</v-tooltip>
              </v-btn>
              <v-btn icon size="small" variant="text" @click="openEditor(task)">
                <v-icon>mdi-pencil-outline</v-icon><v-tooltip activator="parent" location="top">编辑</v-tooltip>
              </v-btn>
              <v-btn icon size="small" variant="text" :loading="isBusy(task, 'toggle')" @click="toggleTask(task)">
                <v-icon>{{ task.enabled ? 'mdi-pause' : 'mdi-play' }}</v-icon>
                <v-tooltip activator="parent" location="top">{{ task.enabled ? '暂停' : '恢复' }}</v-tooltip>
              </v-btn>
              <v-btn icon size="small" variant="text" :color="task.auto_download ? 'warning' : undefined" :loading="isBusy(task, 'auto')" @click="toggleAuto(task)">
                <v-icon>{{ task.auto_download ? 'mdi-download' : 'mdi-download-off-outline' }}</v-icon>
                <v-tooltip activator="parent" location="top">{{ task.auto_download ? '关闭自动下载' : '开启自动下载' }}</v-tooltip>
              </v-btn>
              <v-btn icon size="small" variant="text" @click="openDetail(task, 'results')">
                <v-badge :content="task.results_count" :model-value="task.results_count > 0" color="primary" floating>
                  <v-icon>mdi-text-box-search-outline</v-icon>
                </v-badge>
                <v-tooltip activator="parent" location="top">候选、下载记录与日志</v-tooltip>
              </v-btn>
              <v-menu>
                <template #activator="{ props: menuProps }">
                  <v-btn icon size="small" variant="text" v-bind="menuProps">
                    <v-icon>mdi-dots-vertical</v-icon>
                  </v-btn>
                </template>
                <v-list density="compact" min-width="220">
                  <v-list-item prepend-icon="mdi-history" title="运行日志" @click="openDetail(task, 'logs')" />
                  <v-list-item prepend-icon="mdi-restart" title="重置进度" @click="resetTask(task)" />
                  <v-divider class="my-1" />
                  <v-list-item prepend-icon="mdi-broom" title="清理任务（保留文件）" @click="cleanupTask(task, false)" />
                  <v-list-item prepend-icon="mdi-delete-sweep-outline" title="清理任务和下载文件" base-color="error" @click="cleanupTask(task, true)" />
                  <v-divider class="my-1" />
                  <v-list-item prepend-icon="mdi-delete-outline" title="移入回收站" @click="trashTask(task)" />
                  <v-list-item prepend-icon="mdi-delete-forever-outline" title="彻底删除" base-color="error" @click="purgeTask(task)" />
                </v-list>
              </v-menu>
            </div>
          </div>
        </v-card>

        <!-- 回收站 -->
        <v-expansion-panels v-if="trash.length" v-model="trashPanel" variant="accordion" class="mt-4">
          <v-expansion-panel rounded="lg" elevation="0" class="border">
            <v-expansion-panel-title>
              <v-icon class="me-2" size="20">mdi-delete-restore</v-icon>
              回收站
              <v-chip size="x-small" class="ms-2" variant="tonal">{{ trash.length }}</v-chip>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <v-list density="compact" lines="two" class="pa-0">
                <v-list-item v-for="item in trash" :key="item.id" :title="item.name"
                             :subtitle="`${item.type}${item.season ? ' S' + String(item.season).padStart(2, '0') : ''} · 删除于 ${item.deleted_at || '-'} · 已记录 ${item.downloaded_text || '无'}`">
                  <template #append>
                    <v-btn size="small" variant="tonal" color="success" class="me-2" :loading="isBusy(item, 'restore')" @click="restoreTrash(item)">恢复</v-btn>
                    <v-btn size="small" variant="tonal" color="error" :loading="isBusy(item, 'purge')" @click="purgeTrash(item)">彻底删除</v-btn>
                  </template>
                </v-list-item>
              </v-list>
              <div class="d-flex justify-end mt-2">
                <v-btn size="small" variant="text" color="error" prepend-icon="mdi-delete-sweep" :loading="busy['purge-all'] === 'purge-all'" @click="purgeAllTrash">清空回收站</v-btn>
              </div>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
      </template>
    </v-card-text>

    <TaskEditor v-model="editorOpen" :task="editingTask" :client="client" :mobile="mobile" @saved="onSaved" />
    <TaskDetail v-model="detailOpen" :task="detailTask" :client="client" :mobile="mobile" :initial-tab="detailTab" />

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">{{ snackbar.text }}</v-snackbar>
  </v-card>
</template>

<style scoped>
.dss-search {
  max-width: 320px;
  flex: 1 1 240px;
}
.dss-task {
  transition: border-color 0.2s ease;
}
.dss-task--running {
  border-color: rgb(var(--v-theme-primary));
}
.dss-task--paused .dss-task__name {
  opacity: 0.7;
}
.dss-task__body {
  min-width: 240px;
  flex-basis: 0;
}
.dss-task__message {
  word-break: break-word;
}
.dss-task__actions {
  margin-inline-start: auto;
}
.dss-progress {
  max-width: 560px;
}
.lh-1 {
  line-height: 1.1;
}
@media (max-width: 959px) {
  .dss-task__actions {
    width: 100%;
    justify-content: flex-end;
    margin-inline-start: 0;
  }
}
</style>
