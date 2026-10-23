<script setup>
import { ref, watch } from "vue";
const props = defineProps({ site: Object, client: Object, large: Boolean });
const icon = ref("");
const failed = ref(false);
watch(
  () => props.site?.id,
  async (id, _previous, onCleanup) => {
    let cancelled = false;
    onCleanup(() => {
      cancelled = true;
    });
    icon.value = "";
    failed.value = false;
    if (!id || !props.client?.icon) return;
    try {
      const value = await props.client.icon(id);
      if (cancelled) return;
      if (/^(https?:\/\/|data:image\/|\/(?!\/))/i.test(value || ""))
        icon.value = value;
      else if (/^[a-zA-Z0-9+/=]+$/.test(value || ""))
        icon.value = `data:image/png;base64,${value}`;
    } catch {
      /* 图标缺失时使用与宿主一致的站点图标。 */
    }
  },
  { immediate: true },
);
</script>
<template>
  <span class="stb-avatar" :class="{ 'is-large': large }"
    ><img
      v-if="icon && !failed"
      :src="icon"
      alt=""
      @error="failed = true" /><v-icon
      v-else
      icon="mdi-domain"
      :size="large ? 34 : 24"
  /></span>
</template>
