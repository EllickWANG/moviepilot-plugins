<script setup>
import { computed } from "vue";
import { useTheme } from "vuetify";
import Page from "./components/Page.vue";
import { demoApi } from "./demo";
const theme = useTheme();
const dark = computed(() => theme.global.current.value.dark);
function toggleTheme() {
  theme.global.name.value = dark.value ? "light" : "dark";
}
</script>
<template>
  <v-app
    ><v-main
      ><div class="stb-preview-bar">
        <span
          ><v-icon icon="mdi-movie-open-outline" size="24" />MoviePilot
          <em>/</em>站点工具箱</span
        ><span class="stb-preview-note"
          >本地交互预览 · 演示数据<v-btn
            :icon="
              dark ? 'mdi-white-balance-sunny' : 'mdi-moon-waning-crescent'
            "
            variant="text"
            size="small"
            aria-label="切换深浅主题"
            @click="toggleTheme"
        /></span>
      </div>
      <Page :api="demoApi" plugin-id="sitetoolbox" /></v-main
  ></v-app>
</template>
<style>
html,
body,
#app {
  margin: 0;
  min-height: 100%;
  font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.stb-preview-bar {
  height: 60px;
  border-bottom: 1px solid rgba(150, 160, 180, 0.18);
  padding: 0 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  color: rgb(var(--v-theme-on-background));
}
.stb-preview-bar > span {
  display: flex;
  align-items: center;
  gap: 12px;
}
.stb-preview-bar em {
  font-style: normal;
  opacity: 0.4;
  margin: 0 15px;
}
.stb-preview-note {
  font-size: 12px;
  opacity: 0.8;
}
.stb-preview-bar .v-icon {
  color: rgb(var(--v-theme-primary));
}
@media (max-width: 650px) {
  .stb-preview-bar {
    padding: 0 14px;
    font-size: 12px;
    height: 52px;
  }
  .stb-preview-bar > span {
    gap: 7px;
  }
  .stb-preview-bar em {
    margin: 0 3px;
  }
  .stb-preview-note {
    font-size: 0;
  }
  .stb-preview-note .v-btn {
    font-size: 12px;
  }
}
</style>
