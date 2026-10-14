export const PRIORITY_MODES = [
  { title: '做种数优先', value: 'seeders' },
  { title: '综合优先', value: 'balanced' },
  { title: '免费优先', value: 'free' },
  { title: '发布时间优先', value: 'latest' },
  { title: '小体积优先', value: 'smallest' },
  { title: '大体积优先', value: 'largest' },
]

export const MEDIA_TYPES = [
  { title: '电视剧', value: '电视剧' },
  { title: '电影', value: '电影' },
]

export function priorityLabel(value) {
  return PRIORITY_MODES.find(item => item.value === value)?.title || '做种数优先'
}

/** 任务展示状态：运行中优先于持久化状态。 */
export function taskState(task, running) {
  if (running) return { label: '检查中', color: 'primary', icon: 'mdi-progress-clock' }
  if (task.status === 'completed') return { label: '已完成', color: 'info', icon: 'mdi-check-decagram' }
  if (task.status === 'error') return { label: '异常', color: 'error', icon: 'mdi-alert-circle' }
  if (!task.enabled || task.status === 'paused') return { label: '已暂停', color: 'grey', icon: 'mdi-pause-circle' }
  return { label: '追更中', color: 'success', icon: 'mdi-play-circle' }
}

export function needsAttention(task) {
  return task.status === 'error' || task.last_transfer_status === 'failed' || task.last_repair_status === 'warning'
}

export function progressText(task) {
  if (task.type === '电影') return task.status === 'completed' ? '已下载' : '等待匹配资源'
  if (task.tracking_mode === 'fixed') {
    const base = `已获取 ${task.downloaded_count}/${task.target_count}`
    return task.missing_text ? `${base} · 缺 ${task.missing_text}` : `${base} · 已齐全`
  }
  return `持续追更 · 已记录 ${task.downloaded_text || '无'}`
}

export function seasonText(task) {
  if (task.type === '电影' || !task.season) return ''
  return `S${String(task.season).padStart(2, '0')}`
}

export function formatSize(bytes) {
  const value = Number(bytes || 0)
  if (!value) return '-'
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(0)} MB`
  return `${(value / 1024).toFixed(0)} KB`
}

export function shortHash(value) {
  const text = String(value || '')
  return text ? text.slice(0, 10) : '-'
}

export function levelColor(level) {
  return { error: 'error', warning: 'warning', success: 'success' }[String(level || '')] || 'default'
}

export function transferColor(status) {
  return { completed: 'success', failed: 'error', queued: 'primary', waiting: 'grey' }[String(status || '')] || 'default'
}

export function transferLabel(status) {
  return { completed: '整理完成', failed: '整理失败', queued: '已加入整理队列', waiting: '等待下载完成' }[String(status || '')] || '未登记'
}

export function joinLines(value) {
  return Array.isArray(value) ? value.join('\n') : String(value || '')
}

export function joinWords(value) {
  return Array.isArray(value) ? value.join(', ') : String(value || '')
}
