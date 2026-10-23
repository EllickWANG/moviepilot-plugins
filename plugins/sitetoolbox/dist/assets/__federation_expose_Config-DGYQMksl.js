import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { c as createClient, _ as _sfc_main$1 } from './styles-B4AzQwru.js';

const {toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createVNode:_createVNode,createElementBlock:_createElementBlock} = await importShared('vue');


const _hoisted_1 = { class: "stb-root" };

const {onMounted,ref} = await importShared('vue');

const _sfc_main = {
  __name: 'Config',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: "sitetoolbox" },
  sourcePluginId: String,
},
  emits: ["save", "close", "switch", "layout"],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;
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

return (_ctx, _cache) => {
  const _component_v_alert = _resolveComponent("v-alert");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    (error.value)
      ? (_openBlock(), _createBlock(_component_v_alert, {
          key: 0,
          type: "error",
          variant: "tonal"
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(error.value), 1)
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    _createVNode(_sfc_main$1, {
      initial: __props.initialConfig,
      options: options.value,
      saving: saving.value,
      onSave: save,
      onClose: _cache[0] || (_cache[0] = $event => (emit('close')))
    }, null, 8, ["initial", "options", "saving"])
  ]))
}
}

};

export { _sfc_main as default };
