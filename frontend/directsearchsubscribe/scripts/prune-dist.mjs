// 构建后只保留联邦产物：index.html、index-*、date-* 和 __federation_shared_* 来自本地预览入口，
// 宿主运行时由 MoviePilot 提供 vue/vuetify 单例，这些文件不应随插件分发。
import { readdirSync, rmSync, statSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const dist = fileURLToPath(new URL('../../../plugins.v2/directsearchsubscribe/dist', import.meta.url))
const assets = join(dist, 'assets')
const removed = []

const indexHtml = join(dist, 'index.html')
if (existsSync(indexHtml)) {
  rmSync(indexHtml)
  removed.push('index.html')
}

for (const name of readdirSync(assets)) {
  const path = join(assets, name)
  const isDir = statSync(path).isDirectory()
  const drop = isDir ? name.startsWith('__federation_shared_') : /^(index|date|runtime)-/.test(name)
  if (drop) {
    rmSync(path, { recursive: true, force: true })
    removed.push(`assets/${name}`)
  }
}

const kept = readdirSync(assets)
if (!kept.includes('remoteEntry.js')) {
  console.error('prune-dist: remoteEntry.js missing after prune')
  process.exit(1)
}
console.log(`prune-dist: removed ${removed.join(', ') || 'nothing'}; kept ${kept.join(', ')}`)
