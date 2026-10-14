import { importShared } from './__federation_fn_import-JrT3xvdd.js';

const {createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,createElementVNode:_createElementVNode,withModifiers:_withModifiers,toDisplayString:_toDisplayString,openBlock:_openBlock,createBlock:_createBlock} = await importShared('vue');


const _hoisted_1 = { class: "text-caption text-medium-emphasis" };

const {computed,inject,onMounted,reactive,ref} = await importShared('vue');



const _sfc_main = {
  __name: 'Config',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: '' },
  sourcePluginId: { type: String, default: '' },
},
  emits: ['save', 'close', 'switch', 'layout'],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;

const toast = inject('moviepilot:toast', null);

const DEFAULTS = {
  enabled: true,
  notify: true,
  cron: '*/30 * * * *',
  max_downloads: 3,
  task_gap: 2,
  repair_enabled: true,
  repair_cron: '15 */6 * * *',
  repair_grace_minutes: 30,
};

const config = reactive({ ...DEFAULTS });
const form = ref(null);
const valid = ref(true);
const saving = ref(false);

const cronRule = value => /^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$/.test(String(value || '')) || '需要 5 段 Cron 表达式';
const rangeRule = (min, max) => value => {
  const number = Number(value);
  return (Number.isInteger(number) && number >= min && number <= max) || `请输入 ${min}-${max} 的整数`
};

const summary = computed(() => {
  const parts = [config.enabled ? `定时检查 ${config.cron}` : '定时检查已关闭'];
  parts.push(config.repair_enabled ? `丢失补偿 ${config.repair_cron}` : '丢失补偿已关闭');
  return parts.join(' · ')
});

onMounted(() => {
  emit('layout', { maxWidth: '56rem' });
  Object.keys(DEFAULTS).forEach(key => {
    if (props.initialConfig && props.initialConfig[key] !== undefined && props.initialConfig[key] !== null) {
      config[key] = props.initialConfig[key];
    }
  });
});

async function save() {
  const result = await form.value?.validate();
  if (result && result.valid === false) {
    toast?.warning('请先修正表单中的错误');
    return
  }
  saving.value = true;
  try {
    emit('save', {
      ...config,
      max_downloads: Number(config.max_downloads),
      task_gap: Number(config.task_gap),
      repair_grace_minutes: Number(config.repair_grace_minutes),
    });
  } finally {
    saving.value = false;
  }
}

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_toolbar_title = _resolveComponent("v-toolbar-title");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_toolbar = _resolveComponent("v-toolbar");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_switch = _resolveComponent("v-switch");
  const _component_v_col = _resolveComponent("v-col");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_row = _resolveComponent("v-row");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_form = _resolveComponent("v-form");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_card_actions = _resolveComponent("v-card-actions");
  const _component_v_card = _resolveComponent("v-card");

  return (_openBlock(), _createBlock(_component_v_card, {
    flat: "",
    class: "dss-config"
  }, {
    default: _withCtx(() => [
      _createVNode(_component_v_toolbar, {
        flat: "",
        density: "comfortable",
        color: "transparent"
      }, {
        default: _withCtx(() => [
          _createVNode(_component_v_icon, {
            class: "ms-4 me-2",
            color: "primary"
          }, {
            default: _withCtx(() => [...(_cache[12] || (_cache[12] = [
              _createTextVNode("mdi-tune-variant", -1)
            ]))]),
            _: 1
          }),
          _createVNode(_component_v_toolbar_title, { class: "font-weight-bold" }, {
            default: _withCtx(() => [...(_cache[13] || (_cache[13] = [
              _createTextVNode("直搜订阅 · 运行设置", -1)
            ]))]),
            _: 1
          }),
          _createVNode(_component_v_spacer),
          _createVNode(_component_v_btn, {
            icon: "mdi-close",
            variant: "text",
            onClick: _cache[0] || (_cache[0] = $event => (emit('close')))
          })
        ]),
        _: 1
      }),
      _createVNode(_component_v_card_text, { class: "pt-0" }, {
        default: _withCtx(() => [
          _createVNode(_component_v_alert, {
            type: "info",
            variant: "tonal",
            density: "comfortable",
            rounded: "lg",
            class: "mb-4"
          }, {
            append: _withCtx(() => [
              _createVNode(_component_v_btn, {
                size: "small",
                variant: "flat",
                color: "primary",
                "prepend-icon": "mdi-view-list",
                onClick: _cache[1] || (_cache[1] = $event => (emit('switch')))
              }, {
                default: _withCtx(() => [...(_cache[14] || (_cache[14] = [
                  _createTextVNode(" 打开任务页 ", -1)
                ]))]),
                _: 1
              })
            ]),
            default: _withCtx(() => [
              _cache[15] || (_cache[15] = _createTextVNode(" 节目任务在详情页里新建和编辑，这里只控制插件什么时候跑、每轮最多下载多少。 ", -1))
            ]),
            _: 1
          }),
          _createVNode(_component_v_form, {
            ref_key: "form",
            ref: form,
            modelValue: valid.value,
            "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((valid).value = $event)),
            onSubmit: _withModifiers(save, ["prevent"])
          }, {
            default: _withCtx(() => [
              _cache[18] || (_cache[18] = _createElementVNode("div", { class: "text-subtitle-1 font-weight-bold mb-1" }, "定时检查", -1)),
              _cache[19] || (_cache[19] = _createElementVNode("div", { class: "text-caption text-medium-emphasis mb-3" }, "所有启用的任务按顺序直搜站点，每个任务之间等待“任务间隔”秒。", -1)),
              _createVNode(_component_v_row, { dense: "" }, {
                default: _withCtx(() => [
                  _createVNode(_component_v_col, {
                    cols: "12",
                    md: "6"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_switch, {
                        modelValue: config.enabled,
                        "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.enabled) = $event)),
                        color: "primary",
                        inset: "",
                        "hide-details": "",
                        label: "启用定时检查"
                      }, null, 8, ["modelValue"]),
                      _cache[16] || (_cache[16] = _createElementVNode("div", { class: "text-caption text-medium-emphasis ms-1" }, "关闭后保留任务，但不再自动执行；仍可在任务页手动检查。", -1))
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, {
                    cols: "12",
                    md: "6"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_switch, {
                        modelValue: config.notify,
                        "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.notify) = $event)),
                        color: "primary",
                        inset: "",
                        "hide-details": "",
                        label: "下载结果通知"
                      }, null, 8, ["modelValue"]),
                      _cache[17] || (_cache[17] = _createElementVNode("div", { class: "text-caption text-medium-emphasis ms-1" }, "有新下载时发送插件通知。", -1))
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, {
                    cols: "12",
                    md: "4"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_text_field, {
                        modelValue: config.cron,
                        "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.cron) = $event)),
                        label: "检查周期（Cron）",
                        variant: "outlined",
                        density: "comfortable",
                        placeholder: "*/30 * * * *",
                        rules: [cronRule],
                        hint: "默认每 30 分钟",
                        "persistent-hint": ""
                      }, null, 8, ["modelValue", "rules"])
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, {
                    cols: "6",
                    md: "4"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_text_field, {
                        modelValue: config.max_downloads,
                        "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((config.max_downloads) = $event)),
                        modelModifiers: { number: true },
                        type: "number",
                        label: "每轮最多新增下载",
                        variant: "outlined",
                        density: "comfortable",
                        rules: [rangeRule(1, 20)],
                        hint: "单个任务一轮的上限",
                        "persistent-hint": ""
                      }, null, 8, ["modelValue", "rules"])
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, {
                    cols: "6",
                    md: "4"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_text_field, {
                        modelValue: config.task_gap,
                        "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((config.task_gap) = $event)),
                        modelModifiers: { number: true },
                        type: "number",
                        label: "任务间隔（秒）",
                        variant: "outlined",
                        density: "comfortable",
                        rules: [rangeRule(0, 60)],
                        hint: "减少对站点的瞬时请求",
                        "persistent-hint": ""
                      }, null, 8, ["modelValue", "rules"])
                    ]),
                    _: 1
                  })
                ]),
                _: 1
              }),
              _createVNode(_component_v_divider, { class: "my-4" }),
              _cache[20] || (_cache[20] = _createElementVNode("div", { class: "text-subtitle-1 font-weight-bold mb-1" }, "丢失补偿", -1)),
              _cache[21] || (_cache[21] = _createElementVNode("div", { class: "text-caption text-medium-emphasis mb-3" }, " 定期核对媒体库成品、下载缓存和媒体服务器索引：缺索引时刷新媒体库，成品丢失时从缓存重新整理，缓存也没了才恢复为缺集重新直搜。 ", -1)),
              _createVNode(_component_v_row, { dense: "" }, {
                default: _withCtx(() => [
                  _createVNode(_component_v_col, {
                    cols: "12",
                    md: "4"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_switch, {
                        modelValue: config.repair_enabled,
                        "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((config.repair_enabled) = $event)),
                        color: "primary",
                        inset: "",
                        "hide-details": "",
                        label: "启用丢失补偿"
                      }, null, 8, ["modelValue"])
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, {
                    cols: "12",
                    md: "4"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_text_field, {
                        modelValue: config.repair_cron,
                        "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((config.repair_cron) = $event)),
                        label: "补偿周期（Cron）",
                        variant: "outlined",
                        density: "comfortable",
                        placeholder: "15 */6 * * *",
                        rules: [cronRule],
                        hint: "默认每 6 小时，避开整点直搜",
                        "persistent-hint": "",
                        disabled: !config.repair_enabled
                      }, null, 8, ["modelValue", "rules", "disabled"])
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, {
                    cols: "12",
                    md: "4"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_text_field, {
                        modelValue: config.repair_grace_minutes,
                        "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((config.repair_grace_minutes) = $event)),
                        modelModifiers: { number: true },
                        type: "number",
                        label: "整理宽限（分钟）",
                        variant: "outlined",
                        density: "comfortable",
                        rules: [rangeRule(5, 1440)],
                        hint: "刚整理的文件在此时间内不判定为丢失",
                        "persistent-hint": "",
                        disabled: !config.repair_enabled
                      }, null, 8, ["modelValue", "rules", "disabled"])
                    ]),
                    _: 1
                  })
                ]),
                _: 1
              })
            ]),
            _: 1
          }, 8, ["modelValue"])
        ]),
        _: 1
      }),
      _createVNode(_component_v_divider),
      _createVNode(_component_v_card_actions, { class: "px-4 py-3" }, {
        default: _withCtx(() => [
          _createElementVNode("span", _hoisted_1, _toDisplayString(summary.value), 1),
          _createVNode(_component_v_spacer),
          _createVNode(_component_v_btn, {
            variant: "text",
            onClick: _cache[11] || (_cache[11] = $event => (emit('close')))
          }, {
            default: _withCtx(() => [...(_cache[22] || (_cache[22] = [
              _createTextVNode("取消", -1)
            ]))]),
            _: 1
          }),
          _createVNode(_component_v_btn, {
            color: "primary",
            variant: "flat",
            "prepend-icon": "mdi-content-save",
            loading: saving.value,
            onClick: save
          }, {
            default: _withCtx(() => [...(_cache[23] || (_cache[23] = [
              _createTextVNode("保存设置", -1)
            ]))]),
            _: 1
          }, 8, ["loading"])
        ]),
        _: 1
      })
    ]),
    _: 1
  }))
}
}

};

export { _sfc_main as default };
