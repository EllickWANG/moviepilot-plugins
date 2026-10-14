<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { MEDIA_TYPES, PRIORITY_MODES, joinLines, joinWords } from '../lib/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: null },
  client: { type: Object, required: true },
  mobile: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const DEFAULTS = {
  name: '', type: '电视剧', year: '', season: null, start_episode: 1, total_episode: null,
  episodes: '', owned_episodes: '',
  keywords: '', aliases: '', include: '', exclude: '', sites: [], search_pages: 1, strict_title_match: true,
  priority_mode: 'seeders', min_seeders: 0, dedupe_history: true, downloader: '', save_path: '',
  media_category: '', prefer_full_pack: true, accept_unknown_episode: false,
  enabled: true, auto_download: false,
}

const form = reactive({ ...DEFAULTS })
const formRef = ref(null)
const saving = ref(false)
const error = ref('')
const section = ref('scope')
const siteOptions = ref([])
const downloaderOptions = ref([])
const optionsLoaded = ref(false)

const isEdit = computed(() => Boolean(props.task?.id))
const isTv = computed(() => form.type === '电视剧')
const title = computed(() => (isEdit.value ? `编辑任务 · ${props.task?.name || ''}` : '新建直搜任务'))

const episodesRule = value => !value || /^[\d\s,，\-]+$/.test(String(value)) || '格式示例：1-12,14'
const requiredRule = value => Boolean(String(value || '').trim()) || '不能为空'
const positiveRule = value => value === null || value === '' || value === undefined || Number(value) >= 1 || '至少为 1'

function reset(task) {
  Object.assign(form, { ...DEFAULTS })
  if (!task) return
  Object.assign(form, {
    name: task.name || '',
    type: task.type || '电视剧',
    year: task.year || '',
    season: task.season ?? null,
    start_episode: task.start_episode ?? 1,
    total_episode: task.total_episode ?? null,
    episodes: task.episodes || '',
    owned_episodes: task.owned_episodes || '',
    keywords: joinLines(task.keywords),
    aliases: joinLines(task.aliases),
    include: joinWords(task.include),
    exclude: joinWords(task.exclude),
    sites: Array.isArray(task.sites) ? [...task.sites] : [],
    search_pages: task.search_pages ?? 1,
    strict_title_match: task.strict_title_match !== false,
    priority_mode: task.priority_mode || 'seeders',
    min_seeders: task.min_seeders ?? 0,
    dedupe_history: task.dedupe_history !== false,
    downloader: task.downloader || '',
    save_path: task.save_path || '',
    media_category: task.media_category || '',
    prefer_full_pack: task.prefer_full_pack !== false,
    accept_unknown_episode: Boolean(task.accept_unknown_episode),
    enabled: task.enabled !== false,
    auto_download: Boolean(task.auto_download),
  })
}

async function loadOptions() {
  if (optionsLoaded.value) return
  optionsLoaded.value = true
  try {
    const result = await props.client.get('/options')
    siteOptions.value = result.data?.sites || []
  } catch (err) {
    console.error('读取站点列表失败', err)
  }
  try {
    const clients = await props.client.host('download/clients')
    downloaderOptions.value = clients.map(item => item?.name).filter(Boolean)
  } catch (err) {
    console.error('读取下载器列表失败', err)
  }
}

watch(
  () => props.modelValue,
  open => {
    if (!open) return
    error.value = ''
    section.value = 'scope'
    reset(props.task)
    formRef.value?.resetValidation?.()
    loadOptions()
  },
)

function close() {
  emit('update:modelValue', false)
}

async function save() {
  const result = await formRef.value?.validate()
  if (result && result.valid === false) {
    error.value = '请先修正标红的字段'
    return
  }
  saving.value = true
  error.value = ''
  const payload = {
    ...form,
    season: isTv.value ? form.season : null,
    total_episode: isTv.value ? form.total_episode : null,
    episodes: isTv.value ? form.episodes : '',
    owned_episodes: isTv.value ? form.owned_episodes : '',
  }
  try {
    const response = isEdit.value
      ? await props.client.put(`/tasks/${props.task.id}`, payload)
      : await props.client.post('/tasks', payload)
    if (!response.success) {
      error.value = response.message || '保存失败'
      return
    }
    emit('saved', { task: response.data?.task || response.data, message: response.message, created: !isEdit.value })
    close()
  } catch (err) {
    error.value = err?.message || '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <v-dialog :model-value="modelValue" :max-width="960" :fullscreen="mobile" scrollable persistent
            @update:model-value="value => !value && close()">
    <v-card rounded="lg">
      <v-toolbar flat density="comfortable" color="transparent">
        <v-icon class="ms-4 me-2" color="primary">{{ isEdit ? 'mdi-pencil' : 'mdi-plus-box' }}</v-icon>
        <v-toolbar-title class="font-weight-bold text-truncate">{{ title }}</v-toolbar-title>
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="close" />
      </v-toolbar>

      <v-tabs v-model="section" color="primary" density="comfortable" class="px-2">
        <v-tab value="scope" prepend-icon="mdi-television-play">节目范围</v-tab>
        <v-tab value="search" prepend-icon="mdi-magnify-scan">搜索匹配</v-tab>
        <v-tab value="download" prepend-icon="mdi-download-box-outline">下载整理</v-tab>
      </v-tabs>
      <v-divider />

      <v-card-text>
        <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-3">{{ error }}</v-alert>
        <v-form ref="formRef" @submit.prevent="save">
          <v-window v-model="section">
            <v-window-item value="scope">
              <v-row dense>
                <v-col cols="12" md="8">
                  <v-text-field v-model="form.name" label="节目名称" variant="outlined" density="comfortable"
                                placeholder="从零开始的异世界生活" :rules="[requiredRule]" autofocus />
                </v-col>
                <v-col cols="12" md="4">
                  <v-select v-model="form.type" :items="MEDIA_TYPES" label="类型" variant="outlined" density="comfortable" />
                </v-col>
                <v-col cols="6" md="3">
                  <v-text-field v-model="form.year" label="年份（可选）" variant="outlined" density="comfortable" placeholder="2026"
                                hint="只排除标题里年份明显不同的资源" persistent-hint />
                </v-col>
                <template v-if="isTv">
                  <v-col cols="6" md="3">
                    <v-text-field v-model.number="form.season" type="number" label="季" variant="outlined" density="comfortable"
                                  :rules="[positiveRule]" hint="留空按第 1 季处理" persistent-hint />
                  </v-col>
                  <v-col cols="6" md="3">
                    <v-text-field v-model.number="form.start_episode" type="number" label="起始集" variant="outlined"
                                  density="comfortable" :rules="[positiveRule]" />
                  </v-col>
                  <v-col cols="6" md="3">
                    <v-text-field v-model.number="form.total_episode" type="number" label="结束集" variant="outlined"
                                  density="comfortable" :rules="[positiveRule]" hint="留空表示持续追更" persistent-hint />
                  </v-col>
                  <v-col cols="12" md="6">
                    <v-text-field v-model="form.episodes" label="只追这些集（可选）" variant="outlined" density="comfortable"
                                  placeholder="1-12,14" :rules="[episodesRule]" hint="填写后优先于起始集和结束集" persistent-hint />
                  </v-col>
                  <v-col cols="12" md="6">
                    <v-text-field v-model="form.owned_episodes" label="已经拥有的集数" variant="outlined" density="comfortable"
                                  placeholder="1-3" :rules="[episodesRule]" hint="这些集数不会重复下载" persistent-hint />
                  </v-col>
                </template>
              </v-row>
              <v-alert type="info" variant="tonal" density="compact" class="mt-2">
                集数支持 1-12,14 这样的写法。同名、同类型、同季的任务视为同一任务，不会重复创建。
              </v-alert>
            </v-window-item>

            <v-window-item value="search">
              <v-row dense>
                <v-col cols="12" md="6">
                  <v-textarea v-model="form.keywords" label="站点搜索词（每行一个）" variant="outlined" rows="3" auto-grow
                              placeholder="Re Zero S04&#10;从零开始的异世界生活 第四季"
                              hint="实际发给 PT 站的关键词；留空时使用节目名称" persistent-hint />
                </v-col>
                <v-col cols="12" md="6">
                  <v-textarea v-model="form.aliases" label="标题别名（每行一个）" variant="outlined" rows="3" auto-grow
                              placeholder="Re:Zero&#10;リゼロ" hint="只用于校验候选标题，不会额外发起搜索" persistent-hint />
                </v-col>
                <v-col cols="12" md="6">
                  <v-text-field v-model="form.include" label="必须包含" variant="outlined" density="comfortable"
                                placeholder="2160p, HEVC" hint="逗号分隔，全部命中才保留" persistent-hint />
                </v-col>
                <v-col cols="12" md="6">
                  <v-text-field v-model="form.exclude" label="排除关键词" variant="outlined" density="comfortable"
                                placeholder="试看, 预告" hint="逗号分隔，命中任意一个就跳过" persistent-hint />
                </v-col>
                <v-col cols="12" md="8">
                  <v-select v-model="form.sites" :items="siteOptions" label="检查站点" variant="outlined" density="comfortable"
                            multiple chips closable-chips clearable hint="留空时使用系统允许搜索的活动站点" persistent-hint />
                </v-col>
                <v-col cols="12" md="4">
                  <v-text-field v-model.number="form.search_pages" type="number" label="每个关键词搜索页数" variant="outlined"
                                density="comfortable" min="1" max="5" />
                </v-col>
                <v-col cols="12">
                  <v-switch v-model="form.strict_title_match" color="primary" inset hide-details label="严格标题匹配" />
                  <div class="text-caption text-medium-emphasis ms-1">要求候选标题命中节目名称、别名或搜索词，建议保持开启。</div>
                </v-col>
              </v-row>
            </v-window-item>

            <v-window-item value="download">
              <v-row dense>
                <v-col cols="12" md="4">
                  <v-select v-model="form.priority_mode" :items="PRIORITY_MODES" label="候选优先规则" variant="outlined"
                            density="comfortable" />
                </v-col>
                <v-col cols="6" md="4">
                  <v-text-field v-model.number="form.min_seeders" type="number" label="最低做种数" variant="outlined"
                                density="comfortable" min="0" />
                </v-col>
                <v-col cols="6" md="4" class="d-flex align-center">
                  <v-switch v-model="form.dedupe_history" color="primary" inset hide-details label="下载历史去重" />
                </v-col>
                <v-col cols="12" md="4">
                  <v-combobox v-model="form.downloader" :items="downloaderOptions" label="指定下载器（可选）" variant="outlined"
                              density="comfortable" clearable hint="留空使用站点或系统默认" persistent-hint />
                </v-col>
                <v-col cols="12" md="4">
                  <v-text-field v-model="form.save_path" label="下载保存路径（可选）" variant="outlined" density="comfortable"
                                placeholder="/media/downloads" />
                </v-col>
                <v-col cols="12" md="4">
                  <v-text-field v-model="form.media_category" label="媒体库二级分类（可选）" variant="outlined"
                                density="comfortable" placeholder="日番" />
                </v-col>
                <v-col cols="12" md="6">
                  <v-switch v-model="form.prefer_full_pack" color="primary" inset hide-details label="优先整包下载" />
                  <div class="text-caption text-medium-emphasis ms-1">整包覆盖缺集时优先选择整包，并记录其全部集数。</div>
                </v-col>
                <v-col cols="12" md="6">
                  <v-switch v-model="form.accept_unknown_episode" color="warning" inset hide-details label="允许未知集数自动下载" />
                  <div class="text-caption text-medium-emphasis ms-1">高风险：标题无法解析集数时，每轮仍可选择一个候选。</div>
                </v-col>
              </v-row>
              <v-divider class="my-3" />
              <v-row dense>
                <v-col cols="12" md="6">
                  <v-switch v-model="form.enabled" color="primary" inset hide-details label="启用任务" />
                  <div class="text-caption text-medium-emphasis ms-1">关闭后不参与定时检查，仍可手动检查。</div>
                </v-col>
                <v-col cols="12" md="6">
                  <v-switch v-model="form.auto_download" color="warning" inset hide-details label="自动下载" />
                  <div class="text-caption text-medium-emphasis ms-1">关闭时只预览候选。建议先检查一轮候选再开启。</div>
                </v-col>
              </v-row>
            </v-window-item>
          </v-window>
        </v-form>
      </v-card-text>

      <v-divider />
      <v-card-actions class="px-4 py-3">
        <v-btn v-if="section !== 'scope'" variant="text" prepend-icon="mdi-chevron-left"
               @click="section = section === 'download' ? 'search' : 'scope'">上一步</v-btn>
        <v-spacer />
        <v-btn variant="text" @click="close">取消</v-btn>
        <v-btn v-if="section !== 'download'" variant="tonal" color="primary" append-icon="mdi-chevron-right"
               @click="section = section === 'scope' ? 'search' : 'download'">下一步</v-btn>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-content-save" :loading="saving" @click="save">
          {{ isEdit ? '保存修改' : '创建任务' }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
