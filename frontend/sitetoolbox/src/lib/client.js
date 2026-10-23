/** 宿主负责鉴权，组件不读取或保存用户凭据。 */
export function createClient(api, pluginId = "sitetoolbox") {
  const base = `plugin/${pluginId}`;
  const iconCache = new Map();
  function unwrap(value) {
    if (value && typeof value === "object" && "success" in value) return value;
    return { success: true, data: value, message: "" };
  }
  async function get(path, params) {
    return unwrap(
      await api.get(`${base}${path}`, { feedback: "silent", params }),
    );
  }
  return {
    get,
    async post(path, body = {}) {
      return unwrap(
        await api.post(`${base}${path}`, body, { feedback: "silent" }),
      );
    },
    async settings() {
      const response = unwrap(await api.get(base, { feedback: "silent" }));
      if (!response.success)
        throw new Error(response.message || "读取配置失败");
      return response.data || {};
    },
    async saveSettings(changes) {
      // 运行结果不属于设置表单；保存前重新读取，保留服务器上的最新结果和未知配置。
      const current = await this.settings();
      if (
        Object.values(current.latest_job_states || {}).some(
          (job) => job.status === "running",
        )
      )
        throw new Error("检测正在运行，请完成后再保存设置");
      return unwrap(
        await api.put(base, { ...current, ...changes }, { feedback: "silent" }),
      );
    },
    async icon(id) {
      if (!iconCache.has(id))
        iconCache.set(
          id,
          Promise.resolve(api.get(`site/icon/${id}`, { feedback: "silent" }))
            .then(unwrap)
            .then((r) => r.data?.icon || "")
            .catch(() => ""),
        );
      return iconCache.get(id);
    },
  };
}
