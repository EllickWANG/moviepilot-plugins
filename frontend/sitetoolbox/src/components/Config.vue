<script setup>
import { onMounted, ref } from "vue";
import { createClient } from "../lib/client";
import SettingsPanel from "./SettingsPanel.vue";
import "../styles.css";
const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: "sitetoolbox" },
  sourcePluginId: String,
});
const emit = defineEmits(["save", "close", "switch", "layout"]);
const options = ref({});
const error = ref("");
const saving = ref(false);
const client = createClient(props.api, props.pluginId);
async function save(changes) {
  saving.value = true;
  try {
    const [current, overview] = await Promise.all([
      client.settings(),
      client.get("/overview"),
    ]);
    if (!overview.success)
      throw new Error(overview.message || "读取运行状态失败");
    if (
      Object.values(overview.data.jobs || {}).some(
        (job) => job.status === "running",
      )
    )
      throw new Error("检测正在运行，请完成后再保存设置");
    emit("save", { ...current, ...changes });
  } catch (e) {
    error.value = e?.message || "保存失败，修改仍保留在表单中";
  } finally {
    saving.value = false;
  }
}
onMounted(async () => {
  emit("layout", { maxWidth: "64rem" });
  try {
    const response = await createClient(props.api, props.pluginId).get(
      "/options",
    );
    if (!response.success) throw new Error(response.message);
    options.value = response.data;
  } catch (e) {
    error.value = e?.message || "读取配置选项失败，请重新打开设置";
  }
});
</script>
<template>
  <div class="stb-root">
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert
    ><SettingsPanel
      :initial="initialConfig"
      :options="options"
      :saving="saving"
      @save="save"
      @close="emit('close')"
    />
  </div>
</template>
