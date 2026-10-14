<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: '' },
  sourcePluginId: { type: String, default: '' },
})
const emit = defineEmits(['save', 'close', 'switch', 'layout'])

const toast = inject('moviepilot:toast', null)

const DEFAULTS = {
  enabled: true,
  notify: true,
  cron: '*/30 * * * *',
  max_downloads: 3,
  task_gap: 2,
  repair_enabled: true,
  repair_cron: '15 */6 * * *',
  repair_grace_minutes: 30,
}

const config = reactive({ ...DEFAULTS })
const form = ref(null)
const valid = ref(true)
const saving = ref(false)

const cronRule = value => /^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$/.test(String(value || '')) || '需要 5 段 Cron 表达式'
const rangeRule = (min, max) => value => {
  const number = Number(value)
  return (Number.isInteger(number) && number >= min && number <= max) || `请输入 ${min}-${max} 的整数`
}

const summary = computed(() => {
  const parts = [config.enabled ? `定时检查 ${config.cron}` : '定时检查已关闭']
  parts.push(config.repair_enabled ? `丢失补偿 ${config.repair_cron}` : '丢失补偿已关闭')
  return parts.join(' · ')
})

onMounted(() => {
  emit('layout', { maxWidth: '56rem' })
  Object.keys(DEFAULTS).forEach(key => {
    if (props.initialConfig && props.initialConfig[key] !== undefined && props.initialConfig[key] !== null) {
      config[key] = props.initialConfig[key]
    }
  })
})

async function save() {
  const result = await form.value?.validate()
  if (result && result.valid === false) {
    toast?.warning('请先修正表单中的错误')
    return
  }
  saving.value = true
  try {
    emit('save', {
      ...config,
      max_downloads: Number(config.max_downloads),
      task_gap: Number(config.task_gap),
      repair_grace_minutes: Number(config.repair_grace_minutes),
    })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <v-card flat class="dss-config">
    <v-toolbar flat density="comfortable" color="transparent">
      <v-icon class="ms-4 me-2" color="primary">mdi-tune-variant</v-icon>
      <v-toolbar-title class="font-weight-bold">直搜订阅 · 运行设置</v-toolbar-title>
      <v-spacer />
      <v-btn icon="mdi-close" variant="text" @click="emit('close')" />
    </v-toolbar>

    <v-card-text class="pt-0">
      <v-alert type="info" variant="tonal" density="comfortable" rounded="lg" class="mb-4">
        节目任务在详情页里新建和编辑，这里只控制插件什么时候跑、每轮最多下载多少。
        <template #append>
          <v-btn size="small" variant="flat" color="primary" prepend-icon="mdi-view-list" @click="emit('switch')">
            打开任务页
          </v-btn>
        </template>
      </v-alert>

      <v-form ref="form" v-model="valid" @submit.prevent="save">
        <div class="text-subtitle-1 font-weight-bold mb-1">定时检查</div>
        <div class="text-caption text-medium-emphasis mb-3">所有启用的任务按顺序直搜站点，每个任务之间等待“任务间隔”秒。</div>
        <v-row dense>
          <v-col cols="12" md="6">
            <v-switch v-model="config.enabled" color="primary" inset hide-details label="启用定时检查" />
            <div class="text-caption text-medium-emphasis ms-1">关闭后保留任务，但不再自动执行；仍可在任务页手动检查。</div>
          </v-col>
          <v-col cols="12" md="6">
            <v-switch v-model="config.notify" color="primary" inset hide-details label="下载结果通知" />
            <div class="text-caption text-medium-emphasis ms-1">有新下载时发送插件通知。</div>
          </v-col>
          <v-col cols="12" md="4">
            <v-text-field v-model="config.cron" label="检查周期（Cron）" variant="outlined" density="comfortable"
                          placeholder="*/30 * * * *" :rules="[cronRule]" hint="默认每 30 分钟" persistent-hint />
          </v-col>
          <v-col cols="6" md="4">
            <v-text-field v-model.number="config.max_downloads" type="number" label="每轮最多新增下载" variant="outlined"
                          density="comfortable" :rules="[rangeRule(1, 20)]" hint="单个任务一轮的上限" persistent-hint />
          </v-col>
          <v-col cols="6" md="4">
            <v-text-field v-model.number="config.task_gap" type="number" label="任务间隔（秒）" variant="outlined"
                          density="comfortable" :rules="[rangeRule(0, 60)]" hint="减少对站点的瞬时请求" persistent-hint />
          </v-col>
        </v-row>

        <v-divider class="my-4" />

        <div class="text-subtitle-1 font-weight-bold mb-1">丢失补偿</div>
        <div class="text-caption text-medium-emphasis mb-3">
          定期核对媒体库成品、下载缓存和媒体服务器索引：缺索引时刷新媒体库，成品丢失时从缓存重新整理，缓存也没了才恢复为缺集重新直搜。
        </div>
        <v-row dense>
          <v-col cols="12" md="4">
            <v-switch v-model="config.repair_enabled" color="primary" inset hide-details label="启用丢失补偿" />
          </v-col>
          <v-col cols="12" md="4">
            <v-text-field v-model="config.repair_cron" label="补偿周期（Cron）" variant="outlined" density="comfortable"
                          placeholder="15 */6 * * *" :rules="[cronRule]" hint="默认每 6 小时，避开整点直搜" persistent-hint
                          :disabled="!config.repair_enabled" />
          </v-col>
          <v-col cols="12" md="4">
            <v-text-field v-model.number="config.repair_grace_minutes" type="number" label="整理宽限（分钟）" variant="outlined"
                          density="comfortable" :rules="[rangeRule(5, 1440)]" hint="刚整理的文件在此时间内不判定为丢失"
                          persistent-hint :disabled="!config.repair_enabled" />
          </v-col>
        </v-row>
      </v-form>
    </v-card-text>

    <v-divider />
    <v-card-actions class="px-4 py-3">
      <span class="text-caption text-medium-emphasis">{{ summary }}</span>
      <v-spacer />
      <v-btn variant="text" @click="emit('close')">取消</v-btn>
      <v-btn color="primary" variant="flat" prepend-icon="mdi-content-save" :loading="saving" @click="save">保存设置</v-btn>
    </v-card-actions>
  </v-card>
</template>
