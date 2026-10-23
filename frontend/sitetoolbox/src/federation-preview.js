/** 用真实发布产物验证宿主共享 Vue / Vuetify 的加载路径，仅用于本地验收。 */
import * as Vue from "vue";
import * as Vuetify from "vuetify";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import "@mdi/font/css/materialdesignicons.css";
import "vuetify/styles";
import { demoApi } from "./demo";

const remote = await import(/* @vite-ignore */ __STB_REMOTE_ENTRY__);
remote.init({
  vue: { [Vue.version]: { get: async () => () => Vue, loaded: true } },
  vuetify: { "3.7.3": { get: async () => () => Vuetify, loaded: true } },
});
const Page = (await remote.get("./Page"))();
const Config = (await remote.get("./Config"))();
const config = (await demoApi.get("plugin/sitetoolbox")).data;
const vuetify = Vuetify.createVuetify({
  components,
  directives,
  theme: {
    defaultTheme: "dark",
    themes: {
      dark: {
        dark: true,
        colors: {
          primary: "#8855ee",
          background: "#101725",
          surface: "#192231",
        },
      },
    },
  },
});
Vue.createApp({
  setup() {
    const settings = Vue.ref(false);
    return () =>
      Vue.h(
        components.VApp,
        {},
        {
          default: () =>
            Vue.h(
              components.VMain,
              {},
              {
                default: () => [
                  Vue.h(
                    "div",
                    {
                      style:
                        "padding:12px 24px;display:flex;gap:12px;align-items:center",
                    },
                    [
                      Vue.h("span", "联邦构建验收 · 演示数据"),
                      Vue.h(
                        components.VBtn,
                        {
                          onClick: () => (settings.value = !settings.value),
                          variant: "outlined",
                          size: "small",
                        },
                        {
                          default: () =>
                            settings.value
                              ? "打开发布版页面"
                              : "打开发布版配置",
                        },
                      ),
                    ],
                  ),
                  Vue.h(settings.value ? Config : Page, {
                    api: demoApi,
                    pluginId: "sitetoolbox",
                    initialConfig: config,
                    onClose: () => (settings.value = false),
                    onSave: async (changes) => {
                      await demoApi.put("plugin/sitetoolbox", changes);
                      settings.value = false;
                    },
                  }),
                ],
              },
            ),
        },
      );
  },
})
  .use(vuetify)
  .mount("#app");
