import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { c as createClient, _ as _sfc_main$1 } from './styles-Cu-J1T5i.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementBlock:_createElementBlock} = await importShared('vue');


const _hoisted_1 = { class: "fb-root" };
const _hoisted_2 = {
  key: 2,
  class: "fb-loading"
};

const {ref,onMounted} = await importShared('vue');

const _sfc_main = {
  __name: 'Config',
  props: {initialConfig:{type:Object,default:()=>({})},api:{type:Object,default:()=>({})},pluginId:{type:String,default:'FlowBrush'},sourcePluginId:String},
  emits: ['save','close','layout','switch'],
  setup(__props, { emit: __emit }) {

const props=__props;
const emit=__emit;
const client=createClient(props.api,props.pluginId),data=ref(null),error=ref(''),saving=ref(false);
async function load(){try{data.value=await client.workspace();error.value='';}catch(e){error.value=e.message;}}
async function save(changes){saving.value=true;try{emit('save',await client.merged(changes));}catch(e){error.value=e.message;}finally{saving.value=false;}}
onMounted(()=>{emit('layout',{maxWidth:'72rem'});load();});

return (_ctx, _cache) => {
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_progress_circular = _resolveComponent("v-progress-circular");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _cache[2] || (_cache[2] = _createElementVNode("header", { class: "fb-page-title" }, [
      _createElementVNode("div", null, [
        _createElementVNode("span", { class: "fb-eyebrow" }, "FLOWBRUSH / SETTINGS"),
        _createElementVNode("h1", null, "刷流策略"),
        _createElementVNode("p", null, "设置容量、挑选新种、分配站点，一次完成。")
      ])
    ], -1)),
    (error.value)
      ? (_openBlock(), _createBlock(_component_v_alert, {
          key: 0,
          type: "error",
          variant: "tonal"
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(error.value), 1),
            _createVNode(_component_v_btn, {
              variant: "text",
              onClick: load
            }, {
              default: _withCtx(() => [...(_cache[1] || (_cache[1] = [
                _createTextVNode("重试", -1)
              ]))]),
              _: 1
            })
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    (data.value)
      ? (_openBlock(), _createBlock(_sfc_main$1, {
          key: 1,
          initial: __props.initialConfig,
          sites: data.value.sites,
          downloaders: data.value.downloaders,
          saving: saving.value,
          onSave: save,
          onCancel: _cache[0] || (_cache[0] = $event => (emit('close')))
        }, null, 8, ["initial", "sites", "downloaders", "saving"]))
      : (_openBlock(), _createElementBlock("div", _hoisted_2, [
          _createVNode(_component_v_progress_circular, {
            indeterminate: "",
            color: "primary"
          })
        ]))
  ]))
}
}

};

export { _sfc_main as default };
