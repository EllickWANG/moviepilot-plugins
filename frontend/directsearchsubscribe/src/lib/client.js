/**
 * 包装宿主传入的 api：统一拼插件前缀，并把返回值规整为 {success, message, data}。
 * 宿主的插件 api 客户端会原样返回插件自定义响应；失败时宿主自己会弹 Toast，
 * 所以读取类请求统一静默，只有写操作让宿主提示失败。
 */
export function createClient(api, pluginId) {
  const base = `plugin/${pluginId}`

  function unwrap(payload) {
    if (payload && typeof payload === 'object' && 'success' in payload) return payload
    return { success: true, message: '', data: payload }
  }

  async function request(method, path, body, silent) {
    const url = `${base}${path}`
    const config = silent ? { feedback: 'silent' } : {}
    if (method === 'get') return unwrap(await api.get(url, { ...config, params: body || undefined }))
    if (method === 'put') return unwrap(await api.put(url, body ?? {}, config))
    return unwrap(await api.post(url, body ?? {}, config))
  }

  return {
    get: (path, params) => request('get', path, params, true),
    post: (path, body) => request('post', path, body, false),
    put: (path, body) => request('put', path, body, false),
    /** 直接访问宿主接口，用于读取下载器等系统选项；失败时由调用方兜底。 */
    async host(path) {
      const payload = await api.get(path, { feedback: 'silent' })
      if (Array.isArray(payload)) return payload
      if (payload && typeof payload === 'object' && Array.isArray(payload.data)) return payload.data
      return []
    },
  }
}
