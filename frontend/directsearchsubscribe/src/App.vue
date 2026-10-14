<script setup>
// 本地预览壳：用内存假数据模拟插件接口，便于不连 MoviePilot 时调样式。
import { reactive, ref } from 'vue'
import Page from './components/Page.vue'
import Config from './components/Config.vue'

const view = ref('page')
const state = reactive({
  tasks: {
    demo1: {
      id: 'demo1', name: 'Re：从零开始的异世界生活 第四季', type: '电视剧', year: '2026', season: 4,
      media_category: '日番', enabled: true, auto_download: true, status: 'active',
      keywords: ['Re Zero S04'], aliases: ['Re:Zero'], include: [], exclude: ['预告'], sites: [],
      search_pages: 1, priority_mode: 'seeders', min_seeders: 0, dedupe_history: true, prefer_full_pack: true,
      strict_title_match: true, accept_unknown_episode: false, downloader: '', save_path: '', media_category: '日番',
      start_episode: 1, total_episode: 24, episodes: '', owned_episodes: '',
      target_count: 24, downloaded_count: 19, missing_count: 5, target_text: '1-24', downloaded_text: '1-19',
      missing_text: '20-24', progress: 79, tracking_mode: 'fixed',
      last_run_at: '2026-10-14 23:00:16', last_message: '候选 11，新增下载 0，跳过重复 9',
      last_transfer_status: 'completed', transfer_status_text: '整理完成', last_transfer_message: '',
      download_records: [{ time: '2026-10-12 09:00', title: 'Re ZERO S04E19 1080p', site: '高清视界', episodes: '19', size: 1.2e9, transfer_status: 'completed', hash: 'abcdef1234567890' }],
      results_count: 11, logs_count: 40, updated_at: '2026-10-14 23:00:16',
    },
    demo2: {
      id: 'demo2', name: 'Mushoku Tensei Jobless Reincarnation S03', type: '电视剧', year: '', season: 3,
      enabled: false, auto_download: false, status: 'paused', keywords: ['Mushoku Tensei S03'], aliases: [],
      include: [], exclude: [], sites: [], search_pages: 1, priority_mode: 'balanced', min_seeders: 0,
      dedupe_history: true, prefer_full_pack: true, strict_title_match: true, accept_unknown_episode: false,
      downloader: '', save_path: '', media_category: '', start_episode: 1, total_episode: null, episodes: '',
      owned_episodes: '', target_count: 0, downloaded_count: 12, missing_count: 0, target_text: '',
      downloaded_text: '1-12', missing_text: '', progress: null, tracking_mode: 'ongoing',
      last_run_at: '2026-10-14 23:00:35', last_message: '候选 1，新增下载 0', last_transfer_status: '',
      transfer_status_text: '', download_records: [], results_count: 1, logs_count: 8, updated_at: '2026-10-14 23:00:35',
    },
  },
  trash: [{ id: 'old1', name: '旧任务', type: '电视剧', season: 1, deleted_at: '2026-10-01 10:00:00', downloaded_text: '1-3' }],
  legacy: [{ key: '16', title: '从零开始的异世界生活 第四季' }],
})

const api = {
  async get(path) {
    if (path.endsWith('/overview')) {
      return {
        success: true,
        data: {
          plugin_version: '3.0.0-dev',
          settings: { enabled: true, cron: '*/30 * * * *', repair_enabled: true, repair_cron: '15 */6 * * *' },
          tasks: Object.values(state.tasks), running_task_ids: [], trash: state.trash, legacy: state.legacy,
          repair: { last_run_at: '2026-10-14 18:15:00', message: '核对 2 个任务、31 个文件；媒体索引缺 11 集', running: false, errors: [] },
          transfer_retry_running: false,
        },
      }
    }
    if (path.endsWith('/options')) return { success: true, data: { sites: [{ title: '高清视界 (1)', value: 1 }, { title: '馒头 (2)', value: 2 }] } }
    if (path.endsWith('/results')) return { success: true, data: [{ title: 'Re ZERO S04E20 1080p WEB-DL', site: '高清视界', seeders: 32, free: true, episodes: '20', size: 1.4e9, reason: '单集 20 覆盖缺集 20；做种 32', downloaded: false }] }
    if (path.endsWith('/logs')) return { success: true, data: [{ time: '2026-10-14 23:00:16', stage: '搜索', action: '返回', level: 'info', reason: '关键词“Re Zero S04”第 1 页返回 11 条' }] }
    if (path === 'download/clients') return [{ name: 'qbittorrent', type: 'qbittorrent' }]
    return { success: true, data: null }
  },
  async post(path, body) {
    console.log('POST', path, body)
    if (path.endsWith('/tasks')) {
      const id = `t${Date.now()}`
      state.tasks[id] = { ...state.tasks.demo2, ...body, id, keywords: String(body.keywords || '').split('\n'), include: [], exclude: [], sites: body.sites || [] }
      return { success: true, message: '插件任务已创建', data: state.tasks[id] }
    }
    if (path.includes('/delete')) { const id = path.split('/')[3]; delete state.tasks[id]; return { success: true, message: '任务已移入回收站' } }
    return { success: true, message: `已执行 ${path}` }
  },
  async put(path, body) { console.log('PUT', path, body); return { success: true, message: '任务已更新' } },
}
</script>

<template>
  <v-app>
    <v-main>
      <v-container style="max-width: 1200px">
        <v-btn-toggle v-model="view" class="mb-4" mandatory>
          <v-btn value="page">详情页</v-btn>
          <v-btn value="config">配置页</v-btn>
        </v-btn-toggle>
        <Page v-if="view === 'page'" :api="api" plugin-id="directsearchsubscribe" :show_switch="true" @switch="view = 'config'" />
        <Config v-else :api="api" plugin-id="directsearchsubscribe" :initial-config="{ enabled: true, cron: '*/30 * * * *' }" @switch="view = 'page'" @save="c => console.log('save', c)" />
      </v-container>
    </v-main>
  </v-app>
</template>
