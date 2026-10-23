<script setup>
import { computed } from "vue";
import { stateInfo } from "../lib/model";
const props = defineProps({ state: String, compact: Boolean });
const info = computed(() => stateInfo(props.state));
</script>
<template>
  <span
    class="stb-badge"
    :class="[`is-${info.color}`, { 'is-compact': compact }]"
    ><v-icon :icon="info.icon" :size="compact ? 17 : 18" /><span>{{
      info.label
    }}</span></span
  >
</template>
