import { createApp } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import "@mdi/font/css/materialdesignicons.css";
import "vuetify/styles";
import App from "./App.vue";
const vuetify = createVuetify({
  components,
  directives,
  theme: {
    defaultTheme: "dark",
    themes: {
      dark: {
        dark: true,
        colors: {
          background: "#101725",
          surface: "#192231",
          primary: "#8855ee",
          success: "#54d28b",
          warning: "#ffbf45",
          error: "#ff6674",
        },
      },
      light: {
        dark: false,
        colors: {
          background: "#f5f6fb",
          surface: "#ffffff",
          primary: "#7544dc",
        },
      },
    },
  },
});
createApp(App).use(vuetify).mount("#app");
