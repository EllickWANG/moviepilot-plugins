import { importShared } from './__federation_fn_import-JrT3xvdd.js';

/**
 * 包装宿主传入的 api：统一拼插件前缀，并把返回值规整为 {success, message, data}。
 * 宿主的插件 api 客户端会原样返回插件自定义响应；失败时宿主自己会弹 Toast，
 * 所以读取类请求统一静默，只有写操作让宿主提示失败。
 */
function createClient(api, pluginId) {
  const base = `plugin/${pluginId}`;

  function unwrap(payload) {
    if (payload && typeof payload === 'object' && 'success' in payload) return payload
    return { success: true, message: '', data: payload }
  }

  async function request(method, path, body, silent) {
    const url = `${base}${path}`;
    const config = silent ? { feedback: 'silent' } : {};
    if (method === 'get') return unwrap(await api.get(url, { ...config, params: body || undefined }))
    if (method === 'put') return unwrap(await api.put(url, body ?? {}, config))
    return unwrap(await api.post(url, body ?? {}, config))
  }

  return {
    get: (path, params) => request('get', path, params, true),
    post: (path, body) => request('post', path, body, false),
    put: (path, body) => request('put', path, body, false),
    /** 直接访问宿主接口，用于读取下载器等系统选项；失败时由调用方兜底。 */
    async host(path) {
      const payload = await api.get(path, { feedback: 'silent' });
      if (Array.isArray(payload)) return payload
      if (payload && typeof payload === 'object' && Array.isArray(payload.data)) return payload.data
      return []
    },
  }
}

const PRIORITY_MODES = [
  { title: '做种数优先', value: 'seeders' },
  { title: '综合优先', value: 'balanced' },
  { title: '免费优先', value: 'free' },
  { title: '发布时间优先', value: 'latest' },
  { title: '小体积优先', value: 'smallest' },
  { title: '大体积优先', value: 'largest' },
];

const MEDIA_TYPES = [
  { title: '电视剧', value: '电视剧' },
  { title: '电影', value: '电影' },
];

function priorityLabel(value) {
  return PRIORITY_MODES.find(item => item.value === value)?.title || '做种数优先'
}

/** 任务展示状态：运行中优先于持久化状态。 */
function taskState(task, running) {
  if (running) return { label: '检查中', color: 'primary', icon: 'mdi-progress-clock' }
  if (task.status === 'completed') return { label: '已完成', color: 'info', icon: 'mdi-check-decagram' }
  if (task.status === 'error') return { label: '异常', color: 'error', icon: 'mdi-alert-circle' }
  if (!task.enabled || task.status === 'paused') return { label: '已暂停', color: 'grey', icon: 'mdi-pause-circle' }
  return { label: '追更中', color: 'success', icon: 'mdi-play-circle' }
}

function needsAttention(task) {
  return task.status === 'error' || task.last_transfer_status === 'failed' || task.last_repair_status === 'warning'
}

function progressText(task) {
  if (task.type === '电影') return task.status === 'completed' ? '已下载' : '等待匹配资源'
  if (task.tracking_mode === 'fixed') {
    const base = `已获取 ${task.downloaded_count}/${task.target_count}`;
    return task.missing_text ? `${base} · 缺 ${task.missing_text}` : `${base} · 已齐全`
  }
  return `持续追更 · 已记录 ${task.downloaded_text || '无'}`
}

function seasonText(task) {
  if (task.type === '电影' || !task.season) return ''
  return `S${String(task.season).padStart(2, '0')}`
}

function formatSize(bytes) {
  const value = Number(bytes || 0);
  if (!value) return '-'
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(0)} MB`
  return `${(value / 1024).toFixed(0)} KB`
}

function shortHash(value) {
  const text = String(value || '');
  return text ? text.slice(0, 10) : '-'
}

function levelColor(level) {
  return { error: 'error', warning: 'warning', success: 'success' }[String(level || '')] || 'default'
}

function transferColor(status) {
  return { completed: 'success', failed: 'error', queued: 'primary', waiting: 'grey' }[String(status || '')] || 'default'
}

function transferLabel(status) {
  return { completed: '整理完成', failed: '整理失败', queued: '已加入整理队列', waiting: '等待下载完成' }[String(status || '')] || '未登记'
}

function joinLines(value) {
  return Array.isArray(value) ? value.join('\n') : String(value || '')
}

function joinWords(value) {
  return Array.isArray(value) ? value.join(', ') : String(value || '')
}

const {toDisplayString:_toDisplayString$2,createTextVNode:_createTextVNode$2,resolveComponent:_resolveComponent$2,withCtx:_withCtx$2,createVNode:_createVNode$2,openBlock:_openBlock$2,createBlock:_createBlock$2,createCommentVNode:_createCommentVNode$2,unref:_unref$2,Fragment:_Fragment$2,createElementBlock:_createElementBlock$2,createElementVNode:_createElementVNode$2,withModifiers:_withModifiers} = await importShared('vue');


const {computed: computed$2,reactive,ref: ref$2,watch: watch$1} = await importShared('vue');


const _sfc_main$2 = {
  __name: 'TaskEditor',
  props: {
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: null },
  client: { type: Object, required: true },
  mobile: { type: Boolean, default: false },
},
  emits: ['update:modelValue', 'saved'],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;

const DEFAULTS = {
  name: '', type: '电视剧', year: '', season: null, start_episode: 1, total_episode: null,
  episodes: '', owned_episodes: '',
  keywords: '', aliases: '', include: '', exclude: '', sites: [], search_pages: 1, strict_title_match: true,
  priority_mode: 'seeders', min_seeders: 0, dedupe_history: true, downloader: '', save_path: '',
  media_category: '', prefer_full_pack: true, accept_unknown_episode: false,
  enabled: true, auto_download: false,
};

const form = reactive({ ...DEFAULTS });
const formRef = ref$2(null);
const saving = ref$2(false);
const error = ref$2('');
const section = ref$2('scope');
const siteOptions = ref$2([]);
const downloaderOptions = ref$2([]);
const optionsLoaded = ref$2(false);

const isEdit = computed$2(() => Boolean(props.task?.id));
const isTv = computed$2(() => form.type === '电视剧');
const title = computed$2(() => (isEdit.value ? `编辑任务 · ${props.task?.name || ''}` : '新建直搜任务'));

const episodesRule = value => !value || /^[\d\s,，\-]+$/.test(String(value)) || '格式示例：1-12,14';
const requiredRule = value => Boolean(String(value || '').trim()) || '不能为空';
const positiveRule = value => value === null || value === '' || value === undefined || Number(value) >= 1 || '至少为 1';

function reset(task) {
  Object.assign(form, { ...DEFAULTS });
  if (!task) return
  Object.assign(form, {
    name: task.name || '',
    type: task.type || '电视剧',
    year: task.year || '',
    season: task.season ?? null,
    start_episode: task.start_episode ?? 1,
    total_episode: task.total_episode ?? null,
    episodes: task.episodes || '',
    owned_episodes: task.owned_episodes || '',
    keywords: joinLines(task.keywords),
    aliases: joinLines(task.aliases),
    include: joinWords(task.include),
    exclude: joinWords(task.exclude),
    sites: Array.isArray(task.sites) ? [...task.sites] : [],
    search_pages: task.search_pages ?? 1,
    strict_title_match: task.strict_title_match !== false,
    priority_mode: task.priority_mode || 'seeders',
    min_seeders: task.min_seeders ?? 0,
    dedupe_history: task.dedupe_history !== false,
    downloader: task.downloader || '',
    save_path: task.save_path || '',
    media_category: task.media_category || '',
    prefer_full_pack: task.prefer_full_pack !== false,
    accept_unknown_episode: Boolean(task.accept_unknown_episode),
    enabled: task.enabled !== false,
    auto_download: Boolean(task.auto_download),
  });
}

async function loadOptions() {
  if (optionsLoaded.value) return
  optionsLoaded.value = true;
  try {
    const result = await props.client.get('/options');
    siteOptions.value = result.data?.sites || [];
  } catch (err) {
    console.error('读取站点列表失败', err);
  }
  try {
    const clients = await props.client.host('download/clients');
    downloaderOptions.value = clients.map(item => item?.name).filter(Boolean);
  } catch (err) {
    console.error('读取下载器列表失败', err);
  }
}

watch$1(
  () => props.modelValue,
  open => {
    if (!open) return
    error.value = '';
    section.value = 'scope';
    reset(props.task);
    formRef.value?.resetValidation?.();
    loadOptions();
  },
);

function close() {
  emit('update:modelValue', false);
}

async function save() {
  const result = await formRef.value?.validate();
  if (result && result.valid === false) {
    error.value = '请先修正标红的字段';
    return
  }
  saving.value = true;
  error.value = '';
  const payload = {
    ...form,
    season: isTv.value ? form.season : null,
    total_episode: isTv.value ? form.total_episode : null,
    episodes: isTv.value ? form.episodes : '',
    owned_episodes: isTv.value ? form.owned_episodes : '',
  };
  try {
    const response = isEdit.value
      ? await props.client.put(`/tasks/${props.task.id}`, payload)
      : await props.client.post('/tasks', payload);
    if (!response.success) {
      error.value = response.message || '保存失败';
      return
    }
    emit('saved', { task: response.data?.task || response.data, message: response.message, created: !isEdit.value });
    close();
  } catch (err) {
    error.value = err?.message || '保存失败';
  } finally {
    saving.value = false;
  }
}

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent$2("v-icon");
  const _component_v_toolbar_title = _resolveComponent$2("v-toolbar-title");
  const _component_v_spacer = _resolveComponent$2("v-spacer");
  const _component_v_btn = _resolveComponent$2("v-btn");
  const _component_v_toolbar = _resolveComponent$2("v-toolbar");
  const _component_v_tab = _resolveComponent$2("v-tab");
  const _component_v_tabs = _resolveComponent$2("v-tabs");
  const _component_v_divider = _resolveComponent$2("v-divider");
  const _component_v_alert = _resolveComponent$2("v-alert");
  const _component_v_text_field = _resolveComponent$2("v-text-field");
  const _component_v_col = _resolveComponent$2("v-col");
  const _component_v_select = _resolveComponent$2("v-select");
  const _component_v_row = _resolveComponent$2("v-row");
  const _component_v_window_item = _resolveComponent$2("v-window-item");
  const _component_v_textarea = _resolveComponent$2("v-textarea");
  const _component_v_switch = _resolveComponent$2("v-switch");
  const _component_v_combobox = _resolveComponent$2("v-combobox");
  const _component_v_window = _resolveComponent$2("v-window");
  const _component_v_form = _resolveComponent$2("v-form");
  const _component_v_card_text = _resolveComponent$2("v-card-text");
  const _component_v_card_actions = _resolveComponent$2("v-card-actions");
  const _component_v_card = _resolveComponent$2("v-card");
  const _component_v_dialog = _resolveComponent$2("v-dialog");

  return (_openBlock$2(), _createBlock$2(_component_v_dialog, {
    "model-value": __props.modelValue,
    "max-width": 960,
    fullscreen: __props.mobile,
    scrollable: "",
    persistent: "",
    "onUpdate:modelValue": _cache[29] || (_cache[29] = value => !value && close())
  }, {
    default: _withCtx$2(() => [
      _createVNode$2(_component_v_card, { rounded: "lg" }, {
        default: _withCtx$2(() => [
          _createVNode$2(_component_v_toolbar, {
            flat: "",
            density: "comfortable",
            color: "transparent"
          }, {
            default: _withCtx$2(() => [
              _createVNode$2(_component_v_icon, {
                class: "ms-4 me-2",
                color: "primary"
              }, {
                default: _withCtx$2(() => [
                  _createTextVNode$2(_toDisplayString$2(isEdit.value ? 'mdi-pencil' : 'mdi-plus-box'), 1)
                ]),
                _: 1
              }),
              _createVNode$2(_component_v_toolbar_title, { class: "font-weight-bold text-truncate" }, {
                default: _withCtx$2(() => [
                  _createTextVNode$2(_toDisplayString$2(title.value), 1)
                ]),
                _: 1
              }),
              _createVNode$2(_component_v_spacer),
              _createVNode$2(_component_v_btn, {
                icon: "mdi-close",
                variant: "text",
                onClick: close
              })
            ]),
            _: 1
          }),
          _createVNode$2(_component_v_tabs, {
            modelValue: section.value,
            "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((section).value = $event)),
            color: "primary",
            density: "comfortable",
            class: "px-2"
          }, {
            default: _withCtx$2(() => [
              _createVNode$2(_component_v_tab, {
                value: "scope",
                "prepend-icon": "mdi-television-play"
              }, {
                default: _withCtx$2(() => [...(_cache[30] || (_cache[30] = [
                  _createTextVNode$2("节目范围", -1)
                ]))]),
                _: 1
              }),
              _createVNode$2(_component_v_tab, {
                value: "search",
                "prepend-icon": "mdi-magnify-scan"
              }, {
                default: _withCtx$2(() => [...(_cache[31] || (_cache[31] = [
                  _createTextVNode$2("搜索匹配", -1)
                ]))]),
                _: 1
              }),
              _createVNode$2(_component_v_tab, {
                value: "download",
                "prepend-icon": "mdi-download-box-outline"
              }, {
                default: _withCtx$2(() => [...(_cache[32] || (_cache[32] = [
                  _createTextVNode$2("下载整理", -1)
                ]))]),
                _: 1
              })
            ]),
            _: 1
          }, 8, ["modelValue"]),
          _createVNode$2(_component_v_divider),
          _createVNode$2(_component_v_card_text, null, {
            default: _withCtx$2(() => [
              (error.value)
                ? (_openBlock$2(), _createBlock$2(_component_v_alert, {
                    key: 0,
                    type: "error",
                    variant: "tonal",
                    density: "comfortable",
                    class: "mb-3"
                  }, {
                    default: _withCtx$2(() => [
                      _createTextVNode$2(_toDisplayString$2(error.value), 1)
                    ]),
                    _: 1
                  }))
                : _createCommentVNode$2("", true),
              _createVNode$2(_component_v_form, {
                ref_key: "formRef",
                ref: formRef,
                onSubmit: _withModifiers(save, ["prevent"])
              }, {
                default: _withCtx$2(() => [
                  _createVNode$2(_component_v_window, {
                    modelValue: section.value,
                    "onUpdate:modelValue": _cache[26] || (_cache[26] = $event => ((section).value = $event))
                  }, {
                    default: _withCtx$2(() => [
                      _createVNode$2(_component_v_window_item, { value: "scope" }, {
                        default: _withCtx$2(() => [
                          _createVNode$2(_component_v_row, { dense: "" }, {
                            default: _withCtx$2(() => [
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "8"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.name,
                                    "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((form.name) = $event)),
                                    label: "节目名称",
                                    variant: "outlined",
                                    density: "comfortable",
                                    placeholder: "从零开始的异世界生活",
                                    rules: [requiredRule],
                                    autofocus: ""
                                  }, null, 8, ["modelValue", "rules"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_select, {
                                    modelValue: form.type,
                                    "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((form.type) = $event)),
                                    items: _unref$2(MEDIA_TYPES),
                                    label: "类型",
                                    variant: "outlined",
                                    density: "comfortable"
                                  }, null, 8, ["modelValue", "items"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "6",
                                md: "3"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.year,
                                    "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((form.year) = $event)),
                                    label: "年份（可选）",
                                    variant: "outlined",
                                    density: "comfortable",
                                    placeholder: "2026",
                                    hint: "只排除标题里年份明显不同的资源",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              (isTv.value)
                                ? (_openBlock$2(), _createElementBlock$2(_Fragment$2, { key: 0 }, [
                                    _createVNode$2(_component_v_col, {
                                      cols: "6",
                                      md: "3"
                                    }, {
                                      default: _withCtx$2(() => [
                                        _createVNode$2(_component_v_text_field, {
                                          modelValue: form.season,
                                          "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((form.season) = $event)),
                                          modelModifiers: { number: true },
                                          type: "number",
                                          label: "季",
                                          variant: "outlined",
                                          density: "comfortable",
                                          rules: [positiveRule],
                                          hint: "留空按第 1 季处理",
                                          "persistent-hint": ""
                                        }, null, 8, ["modelValue", "rules"])
                                      ]),
                                      _: 1
                                    }),
                                    _createVNode$2(_component_v_col, {
                                      cols: "6",
                                      md: "3"
                                    }, {
                                      default: _withCtx$2(() => [
                                        _createVNode$2(_component_v_text_field, {
                                          modelValue: form.start_episode,
                                          "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((form.start_episode) = $event)),
                                          modelModifiers: { number: true },
                                          type: "number",
                                          label: "起始集",
                                          variant: "outlined",
                                          density: "comfortable",
                                          rules: [positiveRule]
                                        }, null, 8, ["modelValue", "rules"])
                                      ]),
                                      _: 1
                                    }),
                                    _createVNode$2(_component_v_col, {
                                      cols: "6",
                                      md: "3"
                                    }, {
                                      default: _withCtx$2(() => [
                                        _createVNode$2(_component_v_text_field, {
                                          modelValue: form.total_episode,
                                          "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((form.total_episode) = $event)),
                                          modelModifiers: { number: true },
                                          type: "number",
                                          label: "结束集",
                                          variant: "outlined",
                                          density: "comfortable",
                                          rules: [positiveRule],
                                          hint: "留空表示持续追更",
                                          "persistent-hint": ""
                                        }, null, 8, ["modelValue", "rules"])
                                      ]),
                                      _: 1
                                    }),
                                    _createVNode$2(_component_v_col, {
                                      cols: "12",
                                      md: "6"
                                    }, {
                                      default: _withCtx$2(() => [
                                        _createVNode$2(_component_v_text_field, {
                                          modelValue: form.episodes,
                                          "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((form.episodes) = $event)),
                                          label: "只追这些集（可选）",
                                          variant: "outlined",
                                          density: "comfortable",
                                          placeholder: "1-12,14",
                                          rules: [episodesRule],
                                          hint: "填写后优先于起始集和结束集",
                                          "persistent-hint": ""
                                        }, null, 8, ["modelValue", "rules"])
                                      ]),
                                      _: 1
                                    }),
                                    _createVNode$2(_component_v_col, {
                                      cols: "12",
                                      md: "6"
                                    }, {
                                      default: _withCtx$2(() => [
                                        _createVNode$2(_component_v_text_field, {
                                          modelValue: form.owned_episodes,
                                          "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((form.owned_episodes) = $event)),
                                          label: "已经拥有的集数",
                                          variant: "outlined",
                                          density: "comfortable",
                                          placeholder: "1-3",
                                          rules: [episodesRule],
                                          hint: "这些集数不会重复下载",
                                          "persistent-hint": ""
                                        }, null, 8, ["modelValue", "rules"])
                                      ]),
                                      _: 1
                                    })
                                  ], 64))
                                : _createCommentVNode$2("", true)
                            ]),
                            _: 1
                          }),
                          _createVNode$2(_component_v_alert, {
                            type: "info",
                            variant: "tonal",
                            density: "compact",
                            class: "mt-2"
                          }, {
                            default: _withCtx$2(() => [...(_cache[33] || (_cache[33] = [
                              _createTextVNode$2(" 集数支持 1-12,14 这样的写法。同名、同类型、同季的任务视为同一任务，不会重复创建。 ", -1)
                            ]))]),
                            _: 1
                          })
                        ]),
                        _: 1
                      }),
                      _createVNode$2(_component_v_window_item, { value: "search" }, {
                        default: _withCtx$2(() => [
                          _createVNode$2(_component_v_row, { dense: "" }, {
                            default: _withCtx$2(() => [
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_textarea, {
                                    modelValue: form.keywords,
                                    "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((form.keywords) = $event)),
                                    label: "站点搜索词（每行一个）",
                                    variant: "outlined",
                                    rows: "3",
                                    "auto-grow": "",
                                    placeholder: "Re Zero S04\n从零开始的异世界生活 第四季",
                                    hint: "实际发给 PT 站的关键词；留空时使用节目名称",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_textarea, {
                                    modelValue: form.aliases,
                                    "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((form.aliases) = $event)),
                                    label: "标题别名（每行一个）",
                                    variant: "outlined",
                                    rows: "3",
                                    "auto-grow": "",
                                    placeholder: "Re:Zero\nリゼロ",
                                    hint: "只用于校验候选标题，不会额外发起搜索",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.include,
                                    "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((form.include) = $event)),
                                    label: "必须包含",
                                    variant: "outlined",
                                    density: "comfortable",
                                    placeholder: "2160p, HEVC",
                                    hint: "逗号分隔，全部命中才保留",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.exclude,
                                    "onUpdate:modelValue": _cache[12] || (_cache[12] = $event => ((form.exclude) = $event)),
                                    label: "排除关键词",
                                    variant: "outlined",
                                    density: "comfortable",
                                    placeholder: "试看, 预告",
                                    hint: "逗号分隔，命中任意一个就跳过",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "8"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_select, {
                                    modelValue: form.sites,
                                    "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((form.sites) = $event)),
                                    items: siteOptions.value,
                                    label: "检查站点",
                                    variant: "outlined",
                                    density: "comfortable",
                                    multiple: "",
                                    chips: "",
                                    "closable-chips": "",
                                    clearable: "",
                                    hint: "留空时使用系统允许搜索的活动站点",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue", "items"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.search_pages,
                                    "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((form.search_pages) = $event)),
                                    modelModifiers: { number: true },
                                    type: "number",
                                    label: "每个关键词搜索页数",
                                    variant: "outlined",
                                    density: "comfortable",
                                    min: "1",
                                    max: "5"
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, { cols: "12" }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_switch, {
                                    modelValue: form.strict_title_match,
                                    "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((form.strict_title_match) = $event)),
                                    color: "primary",
                                    inset: "",
                                    "hide-details": "",
                                    label: "严格标题匹配"
                                  }, null, 8, ["modelValue"]),
                                  _cache[34] || (_cache[34] = _createElementVNode$2("div", { class: "text-caption text-medium-emphasis ms-1" }, "要求候选标题命中节目名称、别名或搜索词，建议保持开启。", -1))
                                ]),
                                _: 1
                              })
                            ]),
                            _: 1
                          })
                        ]),
                        _: 1
                      }),
                      _createVNode$2(_component_v_window_item, { value: "download" }, {
                        default: _withCtx$2(() => [
                          _createVNode$2(_component_v_row, { dense: "" }, {
                            default: _withCtx$2(() => [
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_select, {
                                    modelValue: form.priority_mode,
                                    "onUpdate:modelValue": _cache[16] || (_cache[16] = $event => ((form.priority_mode) = $event)),
                                    items: _unref$2(PRIORITY_MODES),
                                    label: "候选优先规则",
                                    variant: "outlined",
                                    density: "comfortable"
                                  }, null, 8, ["modelValue", "items"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "6",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.min_seeders,
                                    "onUpdate:modelValue": _cache[17] || (_cache[17] = $event => ((form.min_seeders) = $event)),
                                    modelModifiers: { number: true },
                                    type: "number",
                                    label: "最低做种数",
                                    variant: "outlined",
                                    density: "comfortable",
                                    min: "0"
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "6",
                                md: "4",
                                class: "d-flex align-center"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_switch, {
                                    modelValue: form.dedupe_history,
                                    "onUpdate:modelValue": _cache[18] || (_cache[18] = $event => ((form.dedupe_history) = $event)),
                                    color: "primary",
                                    inset: "",
                                    "hide-details": "",
                                    label: "下载历史去重"
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_combobox, {
                                    modelValue: form.downloader,
                                    "onUpdate:modelValue": _cache[19] || (_cache[19] = $event => ((form.downloader) = $event)),
                                    items: downloaderOptions.value,
                                    label: "指定下载器（可选）",
                                    variant: "outlined",
                                    density: "comfortable",
                                    clearable: "",
                                    hint: "留空使用站点或系统默认",
                                    "persistent-hint": ""
                                  }, null, 8, ["modelValue", "items"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.save_path,
                                    "onUpdate:modelValue": _cache[20] || (_cache[20] = $event => ((form.save_path) = $event)),
                                    label: "下载保存路径（可选）",
                                    variant: "outlined",
                                    density: "comfortable",
                                    placeholder: "/media/downloads"
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "4"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_text_field, {
                                    modelValue: form.media_category,
                                    "onUpdate:modelValue": _cache[21] || (_cache[21] = $event => ((form.media_category) = $event)),
                                    label: "媒体库二级分类（可选）",
                                    variant: "outlined",
                                    density: "comfortable",
                                    placeholder: "日番"
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_switch, {
                                    modelValue: form.prefer_full_pack,
                                    "onUpdate:modelValue": _cache[22] || (_cache[22] = $event => ((form.prefer_full_pack) = $event)),
                                    color: "primary",
                                    inset: "",
                                    "hide-details": "",
                                    label: "优先整包下载"
                                  }, null, 8, ["modelValue"]),
                                  _cache[35] || (_cache[35] = _createElementVNode$2("div", { class: "text-caption text-medium-emphasis ms-1" }, "整包覆盖缺集时优先选择整包，并记录其全部集数。", -1))
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_switch, {
                                    modelValue: form.accept_unknown_episode,
                                    "onUpdate:modelValue": _cache[23] || (_cache[23] = $event => ((form.accept_unknown_episode) = $event)),
                                    color: "warning",
                                    inset: "",
                                    "hide-details": "",
                                    label: "允许未知集数自动下载"
                                  }, null, 8, ["modelValue"]),
                                  _cache[36] || (_cache[36] = _createElementVNode$2("div", { class: "text-caption text-medium-emphasis ms-1" }, "高风险：标题无法解析集数时，每轮仍可选择一个候选。", -1))
                                ]),
                                _: 1
                              })
                            ]),
                            _: 1
                          }),
                          _createVNode$2(_component_v_divider, { class: "my-3" }),
                          _createVNode$2(_component_v_row, { dense: "" }, {
                            default: _withCtx$2(() => [
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_switch, {
                                    modelValue: form.enabled,
                                    "onUpdate:modelValue": _cache[24] || (_cache[24] = $event => ((form.enabled) = $event)),
                                    color: "primary",
                                    inset: "",
                                    "hide-details": "",
                                    label: "启用任务"
                                  }, null, 8, ["modelValue"]),
                                  _cache[37] || (_cache[37] = _createElementVNode$2("div", { class: "text-caption text-medium-emphasis ms-1" }, "关闭后不参与定时检查，仍可手动检查。", -1))
                                ]),
                                _: 1
                              }),
                              _createVNode$2(_component_v_col, {
                                cols: "12",
                                md: "6"
                              }, {
                                default: _withCtx$2(() => [
                                  _createVNode$2(_component_v_switch, {
                                    modelValue: form.auto_download,
                                    "onUpdate:modelValue": _cache[25] || (_cache[25] = $event => ((form.auto_download) = $event)),
                                    color: "warning",
                                    inset: "",
                                    "hide-details": "",
                                    label: "自动下载"
                                  }, null, 8, ["modelValue"]),
                                  _cache[38] || (_cache[38] = _createElementVNode$2("div", { class: "text-caption text-medium-emphasis ms-1" }, "关闭时只预览候选。建议先检查一轮候选再开启。", -1))
                                ]),
                                _: 1
                              })
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
              }, 512)
            ]),
            _: 1
          }),
          _createVNode$2(_component_v_divider),
          _createVNode$2(_component_v_card_actions, { class: "px-4 py-3" }, {
            default: _withCtx$2(() => [
              (section.value !== 'scope')
                ? (_openBlock$2(), _createBlock$2(_component_v_btn, {
                    key: 0,
                    variant: "text",
                    "prepend-icon": "mdi-chevron-left",
                    onClick: _cache[27] || (_cache[27] = $event => (section.value = section.value === 'download' ? 'search' : 'scope'))
                  }, {
                    default: _withCtx$2(() => [...(_cache[39] || (_cache[39] = [
                      _createTextVNode$2("上一步", -1)
                    ]))]),
                    _: 1
                  }))
                : _createCommentVNode$2("", true),
              _createVNode$2(_component_v_spacer),
              _createVNode$2(_component_v_btn, {
                variant: "text",
                onClick: close
              }, {
                default: _withCtx$2(() => [...(_cache[40] || (_cache[40] = [
                  _createTextVNode$2("取消", -1)
                ]))]),
                _: 1
              }),
              (section.value !== 'download')
                ? (_openBlock$2(), _createBlock$2(_component_v_btn, {
                    key: 1,
                    variant: "tonal",
                    color: "primary",
                    "append-icon": "mdi-chevron-right",
                    onClick: _cache[28] || (_cache[28] = $event => (section.value = section.value === 'scope' ? 'search' : 'download'))
                  }, {
                    default: _withCtx$2(() => [...(_cache[41] || (_cache[41] = [
                      _createTextVNode$2("下一步", -1)
                    ]))]),
                    _: 1
                  }))
                : _createCommentVNode$2("", true),
              _createVNode$2(_component_v_btn, {
                color: "primary",
                variant: "flat",
                "prepend-icon": "mdi-content-save",
                loading: saving.value,
                onClick: save
              }, {
                default: _withCtx$2(() => [
                  _createTextVNode$2(_toDisplayString$2(isEdit.value ? '保存修改' : '创建任务'), 1)
                ]),
                _: 1
              }, 8, ["loading"])
            ]),
            _: 1
          })
        ]),
        _: 1
      })
    ]),
    _: 1
  }, 8, ["model-value", "fullscreen"]))
}
}

};

const {createTextVNode:_createTextVNode$1,resolveComponent:_resolveComponent$1,withCtx:_withCtx$1,createVNode:_createVNode$1,toDisplayString:_toDisplayString$1,openBlock:_openBlock$1,createBlock:_createBlock$1,createCommentVNode:_createCommentVNode$1,createElementBlock:_createElementBlock$1,renderList:_renderList$1,Fragment:_Fragment$1,unref:_unref$1,createElementVNode:_createElementVNode$1} = await importShared('vue');


const _hoisted_1$1 = {
  key: 0,
  class: "pa-8 text-center text-medium-emphasis"
};
const _hoisted_2$1 = { key: 0 };
const _hoisted_3$1 = { style: {"max-width":"420px"} };
const _hoisted_4$1 = {
  class: "text-body-2",
  style: {"word-break":"break-all"}
};
const _hoisted_5$1 = {
  key: 0,
  class: "text-caption text-medium-emphasis"
};
const _hoisted_6$1 = { class: "text-right" };
const _hoisted_7$1 = { class: "text-right text-no-wrap" };
const _hoisted_8$1 = { class: "text-no-wrap" };
const _hoisted_9$1 = { style: {"max-width":"320px"} };
const _hoisted_10$1 = { class: "text-caption" };
const _hoisted_11$1 = {
  key: 0,
  class: "pa-8 text-center text-medium-emphasis"
};
const _hoisted_12$1 = { class: "text-no-wrap text-caption" };
const _hoisted_13$1 = { style: {"max-width":"380px"} };
const _hoisted_14$1 = {
  class: "text-body-2",
  style: {"word-break":"break-all"}
};
const _hoisted_15$1 = { class: "text-no-wrap" };
const _hoisted_16$1 = { class: "text-right text-no-wrap" };
const _hoisted_17$1 = {
  key: 0,
  class: "text-caption text-medium-emphasis"
};
const _hoisted_18$1 = { class: "text-caption font-mono" };
const _hoisted_19$1 = { class: "d-flex align-center ga-2 px-4 pt-3" };
const _hoisted_20$1 = {
  key: 0,
  class: "pa-8 text-center text-medium-emphasis"
};
const _hoisted_21$1 = { class: "text-no-wrap text-caption" };
const _hoisted_22$1 = { class: "text-no-wrap" };
const _hoisted_23$1 = { class: "text-no-wrap" };
const _hoisted_24 = {
  class: "text-body-2",
  style: {"word-break":"break-all"}
};
const _hoisted_25 = {
  key: 0,
  class: "text-caption text-medium-emphasis",
  style: {"word-break":"break-all"}
};
const _hoisted_26 = { key: 0 };
const _hoisted_27 = { key: 1 };

const {computed: computed$1,ref: ref$1,watch} = await importShared('vue');


const _sfc_main$1 = {
  __name: 'TaskDetail',
  props: {
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: null },
  client: { type: Object, required: true },
  mobile: { type: Boolean, default: false },
  initialTab: { type: String, default: 'results' },
},
  emits: ['update:modelValue'],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;

const tab = ref$1('results');
const loading = ref$1(false);
const results = ref$1([]);
const logs = ref$1([]);
const logFilter = ref$1('all');

const records = computed$1(() => props.task?.download_records || []);
const filteredLogs = computed$1(() =>
  logFilter.value === 'all' ? logs.value : logs.value.filter(item => item.level === logFilter.value),
);

function resultState(item) {
  if (item.downloaded) return { label: '已下载', color: 'success' }
  if (item.duplicate) return { label: '重复跳过', color: 'grey' }
  if (item.download_error) return { label: '下载失败', color: 'error' }
  if (item.skip_reason) return { label: '未下载', color: 'warning' }
  return { label: item.downloadable === false ? '不可下载' : '候选', color: 'info' }
}

function resultNote(item) {
  return item.download_error || item.skip_reason || item.selection_reason || item.reason || ''
}

async function load() {
  if (!props.task?.id) return
  loading.value = true;
  try {
    const [resultResponse, logResponse] = await Promise.all([
      props.client.get(`/tasks/${props.task.id}/results`),
      props.client.get(`/tasks/${props.task.id}/logs`),
    ]);
    results.value = resultResponse.data || [];
    logs.value = logResponse.data || [];
  } catch (err) {
    console.error('读取任务详情失败', err);
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.modelValue,
  open => {
    if (!open) return
    tab.value = props.initialTab || 'results';
    logFilter.value = 'all';
    results.value = [];
    logs.value = [];
    load();
  },
);

function close() {
  emit('update:modelValue', false);
}

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent$1("v-icon");
  const _component_v_toolbar_title = _resolveComponent$1("v-toolbar-title");
  const _component_v_spacer = _resolveComponent$1("v-spacer");
  const _component_v_btn = _resolveComponent$1("v-btn");
  const _component_v_toolbar = _resolveComponent$1("v-toolbar");
  const _component_v_tab = _resolveComponent$1("v-tab");
  const _component_v_tabs = _resolveComponent$1("v-tabs");
  const _component_v_divider = _resolveComponent$1("v-divider");
  const _component_v_progress_linear = _resolveComponent$1("v-progress-linear");
  const _component_v_list_item_title = _resolveComponent$1("v-list-item-title");
  const _component_v_chip = _resolveComponent$1("v-chip");
  const _component_v_list_item_subtitle = _resolveComponent$1("v-list-item-subtitle");
  const _component_v_list_item = _resolveComponent$1("v-list-item");
  const _component_v_list = _resolveComponent$1("v-list");
  const _component_v_table = _resolveComponent$1("v-table");
  const _component_v_window_item = _resolveComponent$1("v-window-item");
  const _component_v_chip_group = _resolveComponent$1("v-chip-group");
  const _component_v_window = _resolveComponent$1("v-window");
  const _component_v_card_text = _resolveComponent$1("v-card-text");
  const _component_v_card = _resolveComponent$1("v-card");
  const _component_v_dialog = _resolveComponent$1("v-dialog");

  return (_openBlock$1(), _createBlock$1(_component_v_dialog, {
    "model-value": __props.modelValue,
    "max-width": 1100,
    fullscreen: __props.mobile,
    scrollable: "",
    "onUpdate:modelValue": _cache[3] || (_cache[3] = value => !value && close())
  }, {
    default: _withCtx$1(() => [
      _createVNode$1(_component_v_card, { rounded: "lg" }, {
        default: _withCtx$1(() => [
          _createVNode$1(_component_v_toolbar, {
            flat: "",
            density: "comfortable",
            color: "transparent"
          }, {
            default: _withCtx$1(() => [
              _createVNode$1(_component_v_icon, {
                class: "ms-4 me-2",
                color: "primary"
              }, {
                default: _withCtx$1(() => [...(_cache[4] || (_cache[4] = [
                  _createTextVNode$1("mdi-text-box-search-outline", -1)
                ]))]),
                _: 1
              }),
              _createVNode$1(_component_v_toolbar_title, { class: "font-weight-bold text-truncate" }, {
                default: _withCtx$1(() => [
                  _createTextVNode$1(_toDisplayString$1(__props.task?.name || '任务详情'), 1)
                ]),
                _: 1
              }),
              _createVNode$1(_component_v_spacer),
              _createVNode$1(_component_v_btn, {
                icon: "mdi-refresh",
                variant: "text",
                loading: loading.value,
                onClick: load
              }, null, 8, ["loading"]),
              _createVNode$1(_component_v_btn, {
                icon: "mdi-close",
                variant: "text",
                onClick: close
              })
            ]),
            _: 1
          }),
          _createVNode$1(_component_v_tabs, {
            modelValue: tab.value,
            "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((tab).value = $event)),
            color: "primary",
            density: "comfortable",
            class: "px-2"
          }, {
            default: _withCtx$1(() => [
              _createVNode$1(_component_v_tab, { value: "results" }, {
                default: _withCtx$1(() => [
                  _createTextVNode$1("最近候选 (" + _toDisplayString$1(results.value.length) + ")", 1)
                ]),
                _: 1
              }),
              _createVNode$1(_component_v_tab, { value: "records" }, {
                default: _withCtx$1(() => [
                  _createTextVNode$1("下载记录 (" + _toDisplayString$1(records.value.length) + ")", 1)
                ]),
                _: 1
              }),
              _createVNode$1(_component_v_tab, { value: "logs" }, {
                default: _withCtx$1(() => [
                  _createTextVNode$1("运行日志 (" + _toDisplayString$1(logs.value.length) + ")", 1)
                ]),
                _: 1
              })
            ]),
            _: 1
          }, 8, ["modelValue"]),
          _createVNode$1(_component_v_divider),
          _createVNode$1(_component_v_card_text, { class: "pa-0" }, {
            default: _withCtx$1(() => [
              (loading.value)
                ? (_openBlock$1(), _createBlock$1(_component_v_progress_linear, {
                    key: 0,
                    indeterminate: "",
                    color: "primary"
                  }))
                : _createCommentVNode$1("", true),
              _createVNode$1(_component_v_window, {
                modelValue: tab.value,
                "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((tab).value = $event))
              }, {
                default: _withCtx$1(() => [
                  _createVNode$1(_component_v_window_item, { value: "results" }, {
                    default: _withCtx$1(() => [
                      (!results.value.length && !loading.value)
                        ? (_openBlock$1(), _createElementBlock$1("div", _hoisted_1$1, " 还没有候选记录，点击“立即检查”后再来看。 "))
                        : (__props.mobile)
                          ? (_openBlock$1(), _createBlock$1(_component_v_list, {
                              key: 1,
                              lines: "three",
                              density: "compact"
                            }, {
                              default: _withCtx$1(() => [
                                (_openBlock$1(true), _createElementBlock$1(_Fragment$1, null, _renderList$1(results.value, (item, index) => {
                                  return (_openBlock$1(), _createBlock$1(_component_v_list_item, { key: index }, {
                                    default: _withCtx$1(() => [
                                      _createVNode$1(_component_v_list_item_title, {
                                        class: "text-body-2",
                                        style: {"white-space":"normal"}
                                      }, {
                                        default: _withCtx$1(() => [
                                          _createTextVNode$1(_toDisplayString$1(item.title), 1)
                                        ]),
                                        _: 2
                                      }, 1024),
                                      _createVNode$1(_component_v_list_item_subtitle, null, {
                                        default: _withCtx$1(() => [
                                          _createTextVNode$1(_toDisplayString$1(item.site) + " · 做种 " + _toDisplayString$1(item.seeders ?? '-') + " · " + _toDisplayString$1(_unref$1(formatSize)(item.size)) + " ", 1),
                                          (item.episodes)
                                            ? (_openBlock$1(), _createElementBlock$1("span", _hoisted_2$1, " · 第 " + _toDisplayString$1(item.episodes) + " 集", 1))
                                            : _createCommentVNode$1("", true),
                                          (item.free)
                                            ? (_openBlock$1(), _createBlock$1(_component_v_chip, {
                                                key: 1,
                                                size: "x-small",
                                                color: "success",
                                                variant: "tonal",
                                                class: "ms-1"
                                              }, {
                                                default: _withCtx$1(() => [...(_cache[5] || (_cache[5] = [
                                                  _createTextVNode$1("免费", -1)
                                                ]))]),
                                                _: 1
                                              }))
                                            : _createCommentVNode$1("", true)
                                        ]),
                                        _: 2
                                      }, 1024),
                                      _createVNode$1(_component_v_list_item_subtitle, null, {
                                        default: _withCtx$1(() => [
                                          _createVNode$1(_component_v_chip, {
                                            size: "x-small",
                                            color: resultState(item).color,
                                            variant: "flat",
                                            class: "me-1"
                                          }, {
                                            default: _withCtx$1(() => [
                                              _createTextVNode$1(_toDisplayString$1(resultState(item).label), 1)
                                            ]),
                                            _: 2
                                          }, 1032, ["color"]),
                                          _createTextVNode$1(" " + _toDisplayString$1(resultNote(item)), 1)
                                        ]),
                                        _: 2
                                      }, 1024)
                                    ]),
                                    _: 2
                                  }, 1024))
                                }), 128))
                              ]),
                              _: 1
                            }))
                          : (_openBlock$1(), _createBlock$1(_component_v_table, {
                              key: 2,
                              density: "compact",
                              hover: ""
                            }, {
                              default: _withCtx$1(() => [
                                _cache[7] || (_cache[7] = _createElementVNode$1("thead", null, [
                                  _createElementVNode$1("tr", null, [
                                    _createElementVNode$1("th", null, "标题"),
                                    _createElementVNode$1("th", null, "站点"),
                                    _createElementVNode$1("th", { class: "text-right" }, "做种"),
                                    _createElementVNode$1("th", { class: "text-right" }, "大小"),
                                    _createElementVNode$1("th", null, "集数"),
                                    _createElementVNode$1("th", null, "结果")
                                  ])
                                ], -1)),
                                _createElementVNode$1("tbody", null, [
                                  (_openBlock$1(true), _createElementBlock$1(_Fragment$1, null, _renderList$1(results.value, (item, index) => {
                                    return (_openBlock$1(), _createElementBlock$1("tr", { key: index }, [
                                      _createElementVNode$1("td", _hoisted_3$1, [
                                        _createElementVNode$1("div", _hoisted_4$1, _toDisplayString$1(item.title), 1),
                                        (item.pubdate)
                                          ? (_openBlock$1(), _createElementBlock$1("div", _hoisted_5$1, _toDisplayString$1(item.pubdate), 1))
                                          : _createCommentVNode$1("", true)
                                      ]),
                                      _createElementVNode$1("td", null, [
                                        _createTextVNode$1(_toDisplayString$1(item.site), 1),
                                        (item.free)
                                          ? (_openBlock$1(), _createBlock$1(_component_v_chip, {
                                              key: 0,
                                              size: "x-small",
                                              color: "success",
                                              variant: "tonal",
                                              class: "ms-1"
                                            }, {
                                              default: _withCtx$1(() => [...(_cache[6] || (_cache[6] = [
                                                _createTextVNode$1("免费", -1)
                                              ]))]),
                                              _: 1
                                            }))
                                          : _createCommentVNode$1("", true)
                                      ]),
                                      _createElementVNode$1("td", _hoisted_6$1, _toDisplayString$1(item.seeders ?? '-'), 1),
                                      _createElementVNode$1("td", _hoisted_7$1, _toDisplayString$1(_unref$1(formatSize)(item.size)), 1),
                                      _createElementVNode$1("td", _hoisted_8$1, _toDisplayString$1(item.episodes || '-'), 1),
                                      _createElementVNode$1("td", _hoisted_9$1, [
                                        _createVNode$1(_component_v_chip, {
                                          size: "x-small",
                                          color: resultState(item).color,
                                          variant: "flat",
                                          class: "me-1"
                                        }, {
                                          default: _withCtx$1(() => [
                                            _createTextVNode$1(_toDisplayString$1(resultState(item).label), 1)
                                          ]),
                                          _: 2
                                        }, 1032, ["color"]),
                                        _createElementVNode$1("span", _hoisted_10$1, _toDisplayString$1(resultNote(item)), 1)
                                      ])
                                    ]))
                                  }), 128))
                                ])
                              ]),
                              _: 1
                            }))
                    ]),
                    _: 1
                  }),
                  _createVNode$1(_component_v_window_item, { value: "records" }, {
                    default: _withCtx$1(() => [
                      (!records.value.length)
                        ? (_openBlock$1(), _createElementBlock$1("div", _hoisted_11$1, "还没有通过本插件加入的下载。"))
                        : (__props.mobile)
                          ? (_openBlock$1(), _createBlock$1(_component_v_list, {
                              key: 1,
                              lines: "three",
                              density: "compact"
                            }, {
                              default: _withCtx$1(() => [
                                (_openBlock$1(true), _createElementBlock$1(_Fragment$1, null, _renderList$1(records.value, (item, index) => {
                                  return (_openBlock$1(), _createBlock$1(_component_v_list_item, { key: index }, {
                                    default: _withCtx$1(() => [
                                      _createVNode$1(_component_v_list_item_title, {
                                        class: "text-body-2",
                                        style: {"white-space":"normal"}
                                      }, {
                                        default: _withCtx$1(() => [
                                          _createTextVNode$1(_toDisplayString$1(item.title), 1)
                                        ]),
                                        _: 2
                                      }, 1024),
                                      _createVNode$1(_component_v_list_item_subtitle, null, {
                                        default: _withCtx$1(() => [
                                          _createTextVNode$1(_toDisplayString$1(item.time) + " · " + _toDisplayString$1(item.site) + " · 第 " + _toDisplayString$1(item.episodes || '?') + " 集 · " + _toDisplayString$1(_unref$1(formatSize)(item.size)), 1)
                                        ]),
                                        _: 2
                                      }, 1024),
                                      _createVNode$1(_component_v_list_item_subtitle, null, {
                                        default: _withCtx$1(() => [
                                          _createVNode$1(_component_v_chip, {
                                            size: "x-small",
                                            color: _unref$1(transferColor)(item.transfer_status),
                                            variant: "flat",
                                            class: "me-1"
                                          }, {
                                            default: _withCtx$1(() => [
                                              _createTextVNode$1(_toDisplayString$1(_unref$1(transferLabel)(item.transfer_status)), 1)
                                            ]),
                                            _: 2
                                          }, 1032, ["color"]),
                                          _createTextVNode$1(" " + _toDisplayString$1(item.transfer_message || item.repair_message || ''), 1)
                                        ]),
                                        _: 2
                                      }, 1024)
                                    ]),
                                    _: 2
                                  }, 1024))
                                }), 128))
                              ]),
                              _: 1
                            }))
                          : (_openBlock$1(), _createBlock$1(_component_v_table, {
                              key: 2,
                              density: "compact",
                              hover: ""
                            }, {
                              default: _withCtx$1(() => [
                                _cache[8] || (_cache[8] = _createElementVNode$1("thead", null, [
                                  _createElementVNode$1("tr", null, [
                                    _createElementVNode$1("th", null, "时间"),
                                    _createElementVNode$1("th", null, "标题"),
                                    _createElementVNode$1("th", null, "站点"),
                                    _createElementVNode$1("th", null, "集数"),
                                    _createElementVNode$1("th", { class: "text-right" }, "大小"),
                                    _createElementVNode$1("th", null, "整理"),
                                    _createElementVNode$1("th", null, "Hash")
                                  ])
                                ], -1)),
                                _createElementVNode$1("tbody", null, [
                                  (_openBlock$1(true), _createElementBlock$1(_Fragment$1, null, _renderList$1(records.value, (item, index) => {
                                    return (_openBlock$1(), _createElementBlock$1("tr", { key: index }, [
                                      _createElementVNode$1("td", _hoisted_12$1, _toDisplayString$1(item.time), 1),
                                      _createElementVNode$1("td", _hoisted_13$1, [
                                        _createElementVNode$1("div", _hoisted_14$1, _toDisplayString$1(item.title), 1)
                                      ]),
                                      _createElementVNode$1("td", null, _toDisplayString$1(item.site), 1),
                                      _createElementVNode$1("td", _hoisted_15$1, _toDisplayString$1(item.episodes || '-'), 1),
                                      _createElementVNode$1("td", _hoisted_16$1, _toDisplayString$1(_unref$1(formatSize)(item.size)), 1),
                                      _createElementVNode$1("td", null, [
                                        _createVNode$1(_component_v_chip, {
                                          size: "x-small",
                                          color: _unref$1(transferColor)(item.transfer_status),
                                          variant: "flat"
                                        }, {
                                          default: _withCtx$1(() => [
                                            _createTextVNode$1(_toDisplayString$1(_unref$1(transferLabel)(item.transfer_status)), 1)
                                          ]),
                                          _: 2
                                        }, 1032, ["color"]),
                                        (item.transfer_message || item.repair_message)
                                          ? (_openBlock$1(), _createElementBlock$1("div", _hoisted_17$1, _toDisplayString$1(item.transfer_message || item.repair_message), 1))
                                          : _createCommentVNode$1("", true)
                                      ]),
                                      _createElementVNode$1("td", _hoisted_18$1, _toDisplayString$1(_unref$1(shortHash)(item.hash)), 1)
                                    ]))
                                  }), 128))
                                ])
                              ]),
                              _: 1
                            }))
                    ]),
                    _: 1
                  }),
                  _createVNode$1(_component_v_window_item, { value: "logs" }, {
                    default: _withCtx$1(() => [
                      _createElementVNode$1("div", _hoisted_19$1, [
                        _createVNode$1(_component_v_chip_group, {
                          modelValue: logFilter.value,
                          "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((logFilter).value = $event)),
                          mandatory: "",
                          "selected-class": "text-primary"
                        }, {
                          default: _withCtx$1(() => [
                            _createVNode$1(_component_v_chip, {
                              value: "all",
                              size: "small",
                              filter: ""
                            }, {
                              default: _withCtx$1(() => [...(_cache[9] || (_cache[9] = [
                                _createTextVNode$1("全部", -1)
                              ]))]),
                              _: 1
                            }),
                            _createVNode$1(_component_v_chip, {
                              value: "warning",
                              size: "small",
                              filter: "",
                              color: "warning"
                            }, {
                              default: _withCtx$1(() => [...(_cache[10] || (_cache[10] = [
                                _createTextVNode$1("警告", -1)
                              ]))]),
                              _: 1
                            }),
                            _createVNode$1(_component_v_chip, {
                              value: "error",
                              size: "small",
                              filter: "",
                              color: "error"
                            }, {
                              default: _withCtx$1(() => [...(_cache[11] || (_cache[11] = [
                                _createTextVNode$1("错误", -1)
                              ]))]),
                              _: 1
                            })
                          ]),
                          _: 1
                        }, 8, ["modelValue"])
                      ]),
                      (!filteredLogs.value.length && !loading.value)
                        ? (_openBlock$1(), _createElementBlock$1("div", _hoisted_20$1, "没有日志。"))
                        : (_openBlock$1(), _createBlock$1(_component_v_table, {
                            key: 1,
                            density: "compact",
                            hover: ""
                          }, {
                            default: _withCtx$1(() => [
                              _cache[12] || (_cache[12] = _createElementVNode$1("thead", null, [
                                _createElementVNode$1("tr", null, [
                                  _createElementVNode$1("th", null, "时间"),
                                  _createElementVNode$1("th", null, "阶段"),
                                  _createElementVNode$1("th", null, "动作"),
                                  _createElementVNode$1("th", null, "说明")
                                ])
                              ], -1)),
                              _createElementVNode$1("tbody", null, [
                                (_openBlock$1(true), _createElementBlock$1(_Fragment$1, null, _renderList$1(filteredLogs.value, (item, index) => {
                                  return (_openBlock$1(), _createElementBlock$1("tr", { key: index }, [
                                    _createElementVNode$1("td", _hoisted_21$1, _toDisplayString$1(item.time), 1),
                                    _createElementVNode$1("td", _hoisted_22$1, _toDisplayString$1(item.stage), 1),
                                    _createElementVNode$1("td", _hoisted_23$1, [
                                      _createVNode$1(_component_v_chip, {
                                        size: "x-small",
                                        color: _unref$1(levelColor)(item.level),
                                        variant: "tonal"
                                      }, {
                                        default: _withCtx$1(() => [
                                          _createTextVNode$1(_toDisplayString$1(item.action), 1)
                                        ]),
                                        _: 2
                                      }, 1032, ["color"])
                                    ]),
                                    _createElementVNode$1("td", null, [
                                      _createElementVNode$1("div", _hoisted_24, _toDisplayString$1(item.reason), 1),
                                      (item.title)
                                        ? (_openBlock$1(), _createElementBlock$1("div", _hoisted_25, [
                                            _createTextVNode$1(_toDisplayString$1(item.title), 1),
                                            (item.site)
                                              ? (_openBlock$1(), _createElementBlock$1("span", _hoisted_26, " · " + _toDisplayString$1(item.site), 1))
                                              : _createCommentVNode$1("", true),
                                            (item.episodes)
                                              ? (_openBlock$1(), _createElementBlock$1("span", _hoisted_27, " · 第 " + _toDisplayString$1(item.episodes) + " 集", 1))
                                              : _createCommentVNode$1("", true)
                                          ]))
                                        : _createCommentVNode$1("", true)
                                    ])
                                  ]))
                                }), 128))
                              ])
                            ]),
                            _: 1
                          }))
                    ]),
                    _: 1
                  })
                ]),
                _: 1
              }, 8, ["modelValue"])
            ]),
            _: 1
          })
        ]),
        _: 1
      })
    ]),
    _: 1
  }, 8, ["model-value", "fullscreen"]))
}
}

};

const _export_sfc = (sfc, props) => {
  const target = sfc.__vccOpts || sfc;
  for (const [key, val] of props) {
    target[key] = val;
  }
  return target;
};

const {createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,toDisplayString:_toDisplayString,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,createBlock:_createBlock,renderList:_renderList,Fragment:_Fragment,createElementVNode:_createElementVNode,unref:_unref,normalizeClass:_normalizeClass,mergeProps:_mergeProps} = await importShared('vue');


const _hoisted_1 = {
  key: 0,
  class: "text-caption text-medium-emphasis ms-1"
};
const _hoisted_2 = { class: "text-h6 font-weight-bold lh-1" };
const _hoisted_3 = { class: "text-caption text-medium-emphasis" };
const _hoisted_4 = { class: "d-flex align-center ga-2 text-body-2" };
const _hoisted_5 = { class: "d-flex align-center ga-2 text-body-2" };
const _hoisted_6 = { key: 0 };
const _hoisted_7 = { key: 1 };
const _hoisted_8 = { key: 2 };
const _hoisted_9 = { class: "d-flex flex-wrap align-center ga-2 mb-3" };
const _hoisted_10 = { class: "pa-3 d-flex flex-wrap align-start ga-3" };
const _hoisted_11 = { class: "flex-grow-1 dss-task__body" };
const _hoisted_12 = { class: "d-flex flex-wrap align-center ga-2" };
const _hoisted_13 = { class: "text-subtitle-1 font-weight-bold dss-task__name" };
const _hoisted_14 = { class: "text-caption text-medium-emphasis mt-1" };
const _hoisted_15 = { class: "mt-2 dss-progress" };
const _hoisted_16 = { class: "d-flex justify-space-between text-caption mb-1" };
const _hoisted_17 = {
  key: 0,
  class: "text-medium-emphasis"
};
const _hoisted_18 = { class: "dss-task__message" };
const _hoisted_19 = {
  key: 0,
  class: "text-medium-emphasis"
};
const _hoisted_20 = {
  key: 0,
  class: "text-caption text-medium-emphasis d-flex align-start ga-1 mt-1"
};
const _hoisted_21 = { key: 0 };
const _hoisted_22 = { class: "d-flex align-center flex-wrap dss-task__actions" };
const _hoisted_23 = { class: "d-flex justify-end mt-2" };

const {computed,inject,onBeforeUnmount,onMounted,ref} = await importShared('vue');

const {useDisplay} = await importShared('vuetify');


const _sfc_main = {
  __name: 'Page',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: '' },
  sourcePluginId: { type: String, default: '' },
  nativeSubscribe: { type: Function, default: null },
  show_switch: { type: Boolean, default: true },
},
  emits: ['action', 'switch', 'close', 'layout'],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;

const toast = inject('moviepilot:toast', null);
const hostConfirm = inject('moviepilot:confirm', null);
const client = createClient(props.api, props.pluginId || 'directsearchsubscribe');
const display = useDisplay();
const mobile = computed(() => !display.mdAndUp.value);

const overview = ref(null);
const loading = ref(false);
const error = ref('');
const query = ref('');
const statusFilter = ref('all');
const busy = ref({});
const editorOpen = ref(false);
const editingTask = ref(null);
const detailOpen = ref(false);
const detailTask = ref(null);
const detailTab = ref('results');
const trashPanel = ref([]);
const snackbar = ref({ show: false, text: '', color: 'success' });

let pollTimer = null;
let pollDeadline = 0;

const tasks = computed(() => overview.value?.tasks || []);
const trash = computed(() => overview.value?.trash || []);
const legacy = computed(() => overview.value?.legacy || []);
const repair = computed(() => overview.value?.repair || {});
const settings = computed(() => overview.value?.settings || {});
const runningIds = computed(() => new Set(overview.value?.running_task_ids || []));
const version = computed(() => overview.value?.plugin_version || '');

const stats = computed(() => {
  const list = tasks.value;
  return [
    { label: '任务', value: list.length, icon: 'mdi-view-list', color: 'primary' },
    { label: '追更中', value: list.filter(t => t.enabled && t.status !== 'completed').length, icon: 'mdi-play-circle', color: 'success' },
    { label: '自动下载', value: list.filter(t => t.auto_download).length, icon: 'mdi-download', color: 'warning' },
    { label: '需要关注', value: list.filter(needsAttention).length, icon: 'mdi-alert-circle', color: 'error' },
  ]
});

const statusOptions = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '追更中' },
  { value: 'paused', label: '已暂停' },
  { value: 'completed', label: '已完成' },
  { value: 'attention', label: '需要关注' },
];

const filteredTasks = computed(() => {
  const text = query.value.trim().toLowerCase();
  return tasks.value.filter(task => {
    if (text) {
      const haystack = [task.name, ...(task.keywords || []), ...(task.aliases || []), task.media_category]
        .filter(Boolean).join(' ').toLowerCase();
      if (!haystack.includes(text)) return false
    }
    switch (statusFilter.value) {
      case 'active': return task.enabled && task.status !== 'completed'
      case 'paused': return !task.enabled || task.status === 'paused'
      case 'completed': return task.status === 'completed'
      case 'attention': return needsAttention(task)
      default: return true
    }
  })
});

const anythingRunning = computed(() =>
  runningIds.value.size > 0 || Boolean(repair.value.running) || Boolean(overview.value?.transfer_retry_running),
);

function notify(message, color = 'success') {
  if (!message) return
  if (toast) {
    const method = { success: 'success', error: 'error', warning: 'warning', info: 'info' }[color] || 'info';
    toast[method]?.(message);
    return
  }
  snackbar.value = { show: true, text: message, color };
}

async function confirm(options) {
  if (hostConfirm) {
    const result = await hostConfirm(options);
    return result === true
  }
  return window.confirm(`${options.title || '确认'}\n${options.content || ''}`)
}

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const response = await client.get('/overview');
    if (!response.success) {
      error.value = response.message || '读取插件数据失败';
      return
    }
    overview.value = response.data;
    error.value = '';
    if (detailTask.value) {
      detailTask.value = tasks.value.find(item => item.id === detailTask.value.id) || detailTask.value;
    }
  } catch (err) {
    error.value = err?.message || '读取插件数据失败';
  } finally {
    loading.value = false;
  }
}

/** 动作触发后短时间轮询，直到后台任务结束或超过 90 秒。 */
function schedulePolling(seconds = 90) {
  pollDeadline = Date.now() + seconds * 1000;
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await load(true);
    if (!anythingRunning.value && Date.now() > pollDeadline) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, 5000);
}

function isRunning(task) {
  return runningIds.value.has(task.id)
}

function isBusy(task, action) {
  return busy.value[task.id] === action
}

async function perform(task, action, request, { reload = true, poll = false } = {}) {
  const key = task?.id || action;
  busy.value = { ...busy.value, [key]: action };
  try {
    const response = await request();
    if (response.success) {
      notify(response.message);
      emit('action');
      if (reload) await load(true);
      if (poll) schedulePolling();
    }
    return response
  } catch (err) {
    notify(err?.message || '操作失败', 'error');
    return { success: false }
  } finally {
    const next = { ...busy.value };
    delete next[key];
    busy.value = next;
  }
}

const runTask = task => perform(task, 'run', () => client.post(`/tasks/${task.id}/run`), { poll: true });
const toggleTask = task => perform(task, 'toggle', () => client.post(`/tasks/${task.id}/toggle`));
const toggleAuto = task => perform(task, 'auto', () => client.post(`/tasks/${task.id}/auto`));

async function resetTask(task) {
  const ok = await confirm({ type: 'warn', title: '重置进度', content: `将清空“${task.name}”的已下载集数和下载记录，只保留“已经拥有的集数”。` });
  if (!ok) return
  await perform(task, 'reset', () => client.post(`/tasks/${task.id}/reset`));
}

async function cleanupTask(task, deleteFiles) {
  const scope = deleteFiles ? '从下载器移除本插件为该任务添加的下载，并删除已下载的文件' : '从下载器移除本插件为该任务添加的下载，保留已下载的文件';
  const ok = await confirm({ type: 'error', title: deleteFiles ? '清理任务和下载文件' : '清理任务（保留文件）',
                             content: `${scope}，然后重置进度并立即重新检查。媒体库中已整理的成品不会被删除。` });
  if (!ok) return
  await perform(task, 'cleanup', () => client.post(`/tasks/${task.id}/cleanup`, { delete_files: deleteFiles }), { poll: true });
}

async function trashTask(task) {
  const ok = await confirm({ type: 'warn', title: '移入回收站', content: `“${task.name}”将停止检查并移入回收站，可随时恢复。` });
  if (!ok) return
  await perform(task, 'delete', () => client.post(`/tasks/${task.id}/delete`));
}

async function purgeTask(task) {
  const ok = await confirm({ type: 'error', title: '彻底删除', content: `“${task.name}”将被直接删除，不进入回收站，且其下载记录不再参与历史去重。` });
  if (!ok) return
  await perform(task, 'purge', async () => {
    const moved = await client.post(`/tasks/${task.id}/delete`);
    if (!moved.success) return moved
    return client.post(`/trash/${task.id}/purge`)
  });
}

const restoreTrash = item => perform(item, 'restore', () => client.post(`/trash/${item.id}/restore`));

async function purgeTrash(item) {
  const ok = await confirm({ type: 'error', title: '彻底删除', content: `“${item.name}”将从回收站永久删除。` });
  if (!ok) return
  await perform(item, 'purge', () => client.post(`/trash/${item.id}/purge`));
}

async function purgeAllTrash() {
  const ok = await confirm({ type: 'error', title: '清空回收站', content: `将永久删除回收站中的 ${trash.value.length} 个任务。` });
  if (!ok) return
  await perform(null, 'purge-all', () => client.post('/trash/purge'));
}

async function purgeLegacy() {
  const ok = await confirm({ type: 'warn', title: '清除旧版映射', content: '只移除插件内 1.x 残留的订阅映射，不会改动系统订阅。' });
  if (!ok) return
  await perform(null, 'legacy', () => client.post('/legacy/purge'));
}

const runRepair = () => perform(null, 'repair', () => client.post('/repair/run'), { poll: true });
const retryTransfers = () => perform(null, 'retry', () => client.post('/transfers/retry-failed'), { poll: true });

function openEditor(task) {
  editingTask.value = task;
  editorOpen.value = true;
}

async function onSaved(payload) {
  notify(payload.message || (payload.created ? '任务已创建' : '任务已更新'));
  emit('action');
  await load(true);
  if (payload.created && payload.task?.id) {
    const ok = await confirm({ type: 'info', title: '现在检查一次？', content: '立即搜索站点并生成候选列表，不会自动下载（除非已开启自动下载）。',
                               confirmText: '立即检查', cancelText: '稍后' });
    if (ok) await runTask(payload.task);
  }
}

function openDetail(task, tab = 'results') {
  detailTask.value = task;
  detailTab.value = tab;
  detailOpen.value = true;
}

function metaLine(task) {
  const parts = [task.type];
  if (task.year) parts.push(task.year);
  const season = seasonText(task);
  if (season) parts.push(season);
  if (task.media_category) parts.push(task.media_category);
  parts.push(priorityLabel(task.priority_mode));
  if (task.sites?.length) parts.push(`${task.sites.length} 个指定站点`);
  return parts.join(' · ')
}

function messageTone(task) {
  if (task.status === 'error') return { icon: 'mdi-alert-circle-outline', cls: 'text-error' }
  if (task.last_download_count) return { icon: 'mdi-check-circle-outline', cls: 'text-success' }
  return { icon: 'mdi-information-outline', cls: 'text-medium-emphasis' }
}

function progressValue(task) {
  if (task.progress !== null && task.progress !== undefined) return task.progress
  if (task.status === 'completed') return 100
  return task.downloaded_count ? 100 : 0
}

function progressColor(task) {
  if (task.status === 'completed') return 'info'
  if (task.tracking_mode === 'fixed' && !task.missing_count) return 'success'
  return task.enabled ? 'primary' : 'grey'
}

onMounted(() => {
  emit('layout', { maxWidth: '92rem' });
  load();
});

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer);
});

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_toolbar_title = _resolveComponent("v-toolbar-title");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_toolbar = _resolveComponent("v-toolbar");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_skeleton_loader = _resolveComponent("v-skeleton-loader");
  const _component_v_sheet = _resolveComponent("v-sheet");
  const _component_v_col = _resolveComponent("v-col");
  const _component_v_row = _resolveComponent("v-row");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_chip = _resolveComponent("v-chip");
  const _component_v_chip_group = _resolveComponent("v-chip-group");
  const _component_v_avatar = _resolveComponent("v-avatar");
  const _component_v_progress_linear = _resolveComponent("v-progress-linear");
  const _component_v_tooltip = _resolveComponent("v-tooltip");
  const _component_v_badge = _resolveComponent("v-badge");
  const _component_v_list_item = _resolveComponent("v-list-item");
  const _component_v_list = _resolveComponent("v-list");
  const _component_v_menu = _resolveComponent("v-menu");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_expansion_panel_title = _resolveComponent("v-expansion-panel-title");
  const _component_v_expansion_panel_text = _resolveComponent("v-expansion-panel-text");
  const _component_v_expansion_panel = _resolveComponent("v-expansion-panel");
  const _component_v_expansion_panels = _resolveComponent("v-expansion-panels");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_snackbar = _resolveComponent("v-snackbar");

  return (_openBlock(), _createBlock(_component_v_card, {
    flat: "",
    class: "dss-page"
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
              _createTextVNode("mdi-magnify-scan", -1)
            ]))]),
            _: 1
          }),
          _createVNode(_component_v_toolbar_title, { class: "font-weight-bold" }, {
            default: _withCtx(() => [
              _cache[13] || (_cache[13] = _createTextVNode(" 直搜订阅 ", -1)),
              (version.value)
                ? (_openBlock(), _createElementBlock("span", _hoisted_1, "v" + _toDisplayString(version.value), 1))
                : _createCommentVNode("", true)
            ]),
            _: 1
          }),
          _createVNode(_component_v_spacer),
          _createVNode(_component_v_btn, {
            color: "primary",
            variant: "flat",
            "prepend-icon": "mdi-plus",
            class: "me-1",
            onClick: _cache[0] || (_cache[0] = $event => (openEditor(null)))
          }, {
            default: _withCtx(() => [
              _createTextVNode(_toDisplayString(mobile.value ? '新建' : '新建任务'), 1)
            ]),
            _: 1
          }),
          _createVNode(_component_v_btn, {
            icon: "mdi-refresh",
            variant: "text",
            loading: loading.value,
            onClick: _cache[1] || (_cache[1] = $event => (load()))
          }, null, 8, ["loading"]),
          (__props.show_switch)
            ? (_openBlock(), _createBlock(_component_v_btn, {
                key: 0,
                icon: "mdi-cog-outline",
                variant: "text",
                onClick: _cache[2] || (_cache[2] = $event => (emit('switch')))
              }))
            : _createCommentVNode("", true),
          _createVNode(_component_v_btn, {
            icon: "mdi-close",
            variant: "text",
            onClick: _cache[3] || (_cache[3] = $event => (emit('close')))
          })
        ]),
        _: 1
      }),
      _createVNode(_component_v_card_text, { class: "pt-0" }, {
        default: _withCtx(() => [
          (error.value)
            ? (_openBlock(), _createBlock(_component_v_alert, {
                key: 0,
                type: "error",
                variant: "tonal",
                density: "comfortable",
                class: "mb-4"
              }, {
                append: _withCtx(() => [
                  _createVNode(_component_v_btn, {
                    size: "small",
                    variant: "text",
                    onClick: _cache[4] || (_cache[4] = $event => (load()))
                  }, {
                    default: _withCtx(() => [...(_cache[14] || (_cache[14] = [
                      _createTextVNode("重试", -1)
                    ]))]),
                    _: 1
                  })
                ]),
                default: _withCtx(() => [
                  _createTextVNode(_toDisplayString(error.value) + " ", 1)
                ]),
                _: 1
              }))
            : _createCommentVNode("", true),
          (!overview.value && loading.value)
            ? (_openBlock(), _createBlock(_component_v_skeleton_loader, {
                key: 1,
                type: "article, list-item-three-line@3"
              }))
            : (overview.value)
              ? (_openBlock(), _createElementBlock(_Fragment, { key: 2 }, [
                  _createVNode(_component_v_row, {
                    dense: "",
                    class: "mb-2"
                  }, {
                    default: _withCtx(() => [
                      (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(stats.value, (item) => {
                        return (_openBlock(), _createBlock(_component_v_col, {
                          key: item.label,
                          cols: "6",
                          md: "3"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_sheet, {
                              rounded: "lg",
                              color: item.color,
                              class: "pa-3 d-flex align-center ga-3 dss-stat",
                              variant: "tonal"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_icon, {
                                  color: item.color
                                }, {
                                  default: _withCtx(() => [
                                    _createTextVNode(_toDisplayString(item.icon), 1)
                                  ]),
                                  _: 2
                                }, 1032, ["color"]),
                                _createElementVNode("div", null, [
                                  _createElementVNode("div", _hoisted_2, _toDisplayString(item.value), 1),
                                  _createElementVNode("div", _hoisted_3, _toDisplayString(item.label), 1)
                                ])
                              ]),
                              _: 2
                            }, 1032, ["color"])
                          ]),
                          _: 2
                        }, 1024))
                      }), 128))
                    ]),
                    _: 1
                  }),
                  (legacy.value.length)
                    ? (_openBlock(), _createBlock(_component_v_alert, {
                        key: 0,
                        type: "warning",
                        variant: "tonal",
                        density: "comfortable",
                        rounded: "lg",
                        class: "mb-3"
                      }, {
                        append: _withCtx(() => [
                          _createVNode(_component_v_btn, {
                            size: "small",
                            variant: "flat",
                            color: "warning",
                            loading: busy.value.legacy === 'legacy',
                            onClick: purgeLegacy
                          }, {
                            default: _withCtx(() => [...(_cache[15] || (_cache[15] = [
                              _createTextVNode("清除", -1)
                            ]))]),
                            _: 1
                          }, 8, ["loading"])
                        ]),
                        default: _withCtx(() => [
                          _createTextVNode(" 检测到 1.x 遗留映射 " + _toDisplayString(legacy.value.length) + " 条：" + _toDisplayString(legacy.value.map(item => item.title).slice(0, 5).join('、')) + "。 这些数据不再读取或执行；若系统订阅页已没有对应订阅，可直接清除。 ", 1)
                        ]),
                        _: 1
                      }))
                    : _createCommentVNode("", true),
                  _createVNode(_component_v_sheet, {
                    rounded: "lg",
                    border: "",
                    class: "pa-3 mb-3 d-flex flex-wrap align-center ga-3"
                  }, {
                    default: _withCtx(() => [
                      _createElementVNode("div", _hoisted_4, [
                        _createVNode(_component_v_icon, {
                          size: "18",
                          color: settings.value.enabled ? 'success' : 'grey'
                        }, {
                          default: _withCtx(() => [...(_cache[16] || (_cache[16] = [
                            _createTextVNode("mdi-clock-outline", -1)
                          ]))]),
                          _: 1
                        }, 8, ["color"]),
                        _createElementVNode("span", null, _toDisplayString(settings.value.enabled ? `定时检查 ${settings.value.cron}` : '定时检查已关闭'), 1)
                      ]),
                      _createVNode(_component_v_divider, {
                        vertical: "",
                        class: "d-none d-md-block"
                      }),
                      _createElementVNode("div", _hoisted_5, [
                        _createVNode(_component_v_icon, {
                          size: "18",
                          color: repair.value.running ? 'primary' : (repair.value.errors?.length ? 'warning' : 'grey')
                        }, {
                          default: _withCtx(() => [...(_cache[17] || (_cache[17] = [
                            _createTextVNode("mdi-shield-sync-outline", -1)
                          ]))]),
                          _: 1
                        }, 8, ["color"]),
                        (repair.value.running)
                          ? (_openBlock(), _createElementBlock("span", _hoisted_6, "丢失补偿运行中…"))
                          : (repair.value.last_run_at)
                            ? (_openBlock(), _createElementBlock("span", _hoisted_7, "补偿 " + _toDisplayString(repair.value.last_run_at) + " · " + _toDisplayString(repair.value.message || '完成'), 1))
                            : (_openBlock(), _createElementBlock("span", _hoisted_8, _toDisplayString(settings.value.repair_enabled ? `丢失补偿 ${settings.value.repair_cron}` : '丢失补偿已关闭'), 1))
                      ]),
                      _createVNode(_component_v_spacer),
                      _createVNode(_component_v_btn, {
                        size: "small",
                        variant: "tonal",
                        "prepend-icon": "mdi-shield-sync",
                        loading: busy.value.repair === 'repair' || repair.value.running,
                        onClick: runRepair
                      }, {
                        default: _withCtx(() => [...(_cache[18] || (_cache[18] = [
                          _createTextVNode("立即补偿", -1)
                        ]))]),
                        _: 1
                      }, 8, ["loading"]),
                      _createVNode(_component_v_btn, {
                        size: "small",
                        variant: "tonal",
                        "prepend-icon": "mdi-folder-sync",
                        loading: busy.value.retry === 'retry' || overview.value.transfer_retry_running,
                        onClick: retryTransfers
                      }, {
                        default: _withCtx(() => [...(_cache[19] || (_cache[19] = [
                          _createTextVNode("重试失败整理", -1)
                        ]))]),
                        _: 1
                      }, 8, ["loading"])
                    ]),
                    _: 1
                  }),
                  _createElementVNode("div", _hoisted_9, [
                    _createVNode(_component_v_text_field, {
                      modelValue: query.value,
                      "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((query).value = $event)),
                      density: "compact",
                      variant: "outlined",
                      "hide-details": "",
                      clearable: "",
                      "prepend-inner-icon": "mdi-magnify",
                      placeholder: "搜索节目、关键词或分类",
                      class: "dss-search"
                    }, null, 8, ["modelValue"]),
                    _createVNode(_component_v_chip_group, {
                      modelValue: statusFilter.value,
                      "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((statusFilter).value = $event)),
                      mandatory: "",
                      "selected-class": "text-primary"
                    }, {
                      default: _withCtx(() => [
                        (_openBlock(), _createElementBlock(_Fragment, null, _renderList(statusOptions, (option) => {
                          return _createVNode(_component_v_chip, {
                            key: option.value,
                            value: option.value,
                            size: "small",
                            filter: ""
                          }, {
                            default: _withCtx(() => [
                              _createTextVNode(_toDisplayString(option.label), 1)
                            ]),
                            _: 2
                          }, 1032, ["value"])
                        }), 64))
                      ]),
                      _: 1
                    }, 8, ["modelValue"])
                  ]),
                  (!tasks.value.length)
                    ? (_openBlock(), _createBlock(_component_v_sheet, {
                        key: 1,
                        rounded: "lg",
                        border: "",
                        class: "pa-10 text-center"
                      }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_icon, {
                            size: "56",
                            color: "primary",
                            class: "mb-3"
                          }, {
                            default: _withCtx(() => [...(_cache[20] || (_cache[20] = [
                              _createTextVNode("mdi-magnify-scan", -1)
                            ]))]),
                            _: 1
                          }),
                          _cache[22] || (_cache[22] = _createElementVNode("div", { class: "text-h6 font-weight-bold mb-1" }, "还没有直搜任务", -1)),
                          _cache[23] || (_cache[23] = _createElementVNode("div", { class: "text-body-2 text-medium-emphasis mb-4" }, " 填写节目名称、季和集数范围，插件会按你给的关键词定时直搜站点，并在下载完成后按人工信息整理。 ", -1)),
                          _createVNode(_component_v_btn, {
                            color: "primary",
                            variant: "flat",
                            "prepend-icon": "mdi-plus",
                            onClick: _cache[7] || (_cache[7] = $event => (openEditor(null)))
                          }, {
                            default: _withCtx(() => [...(_cache[21] || (_cache[21] = [
                              _createTextVNode("新建第一个任务", -1)
                            ]))]),
                            _: 1
                          })
                        ]),
                        _: 1
                      }))
                    : (!filteredTasks.value.length)
                      ? (_openBlock(), _createBlock(_component_v_sheet, {
                          key: 2,
                          rounded: "lg",
                          border: "",
                          class: "pa-8 text-center text-medium-emphasis"
                        }, {
                          default: _withCtx(() => [...(_cache[24] || (_cache[24] = [
                            _createTextVNode(" 没有符合筛选条件的任务。 ", -1)
                          ]))]),
                          _: 1
                        }))
                      : _createCommentVNode("", true),
                  (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(filteredTasks.value, (task) => {
                    return (_openBlock(), _createBlock(_component_v_card, {
                      key: task.id,
                      variant: "outlined",
                      rounded: "lg",
                      class: _normalizeClass(["mb-3 dss-task", { 'dss-task--running': isRunning(task), 'dss-task--paused': !task.enabled }])
                    }, {
                      default: _withCtx(() => [
                        _createElementVNode("div", _hoisted_10, [
                          _createVNode(_component_v_avatar, {
                            color: _unref(taskState)(task, isRunning(task)).color,
                            variant: "tonal",
                            size: "44",
                            class: "flex-shrink-0"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_icon, null, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(task.type === '电影' ? 'mdi-movie-open-outline' : 'mdi-television-play'), 1)
                                ]),
                                _: 2
                              }, 1024)
                            ]),
                            _: 2
                          }, 1032, ["color"]),
                          _createElementVNode("div", _hoisted_11, [
                            _createElementVNode("div", _hoisted_12, [
                              _createElementVNode("span", _hoisted_13, _toDisplayString(task.name), 1),
                              _createVNode(_component_v_chip, {
                                size: "x-small",
                                color: _unref(taskState)(task, isRunning(task)).color,
                                variant: "flat",
                                "prepend-icon": _unref(taskState)(task, isRunning(task)).icon
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(_unref(taskState)(task, isRunning(task)).label), 1)
                                ]),
                                _: 2
                              }, 1032, ["color", "prepend-icon"]),
                              _createVNode(_component_v_chip, {
                                size: "x-small",
                                color: task.auto_download ? 'warning' : undefined,
                                variant: "tonal"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(task.auto_download ? '自动下载' : '仅预览'), 1)
                                ]),
                                _: 2
                              }, 1032, ["color"])
                            ]),
                            _createElementVNode("div", _hoisted_14, _toDisplayString(metaLine(task)), 1),
                            _createElementVNode("div", _hoisted_15, [
                              _createElementVNode("div", _hoisted_16, [
                                _createElementVNode("span", {
                                  class: _normalizeClass(task.missing_count ? '' : 'text-success')
                                }, _toDisplayString(_unref(progressText)(task)), 3),
                                (task.progress !== null && task.progress !== undefined)
                                  ? (_openBlock(), _createElementBlock("span", _hoisted_17, _toDisplayString(task.progress) + "%", 1))
                                  : _createCommentVNode("", true)
                              ]),
                              _createVNode(_component_v_progress_linear, {
                                "model-value": progressValue(task),
                                color: progressColor(task),
                                height: "6",
                                rounded: "",
                                indeterminate: isRunning(task)
                              }, null, 8, ["model-value", "color", "indeterminate"])
                            ]),
                            _createElementVNode("div", {
                              class: _normalizeClass(["text-caption mt-2 d-flex align-start ga-1", messageTone(task).cls])
                            }, [
                              _createVNode(_component_v_icon, {
                                size: "15",
                                class: "mt-1 flex-shrink-0"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(messageTone(task).icon), 1)
                                ]),
                                _: 2
                              }, 1024),
                              _createElementVNode("span", _hoisted_18, [
                                _createTextVNode(_toDisplayString(task.last_message || '尚未运行，点击“立即检查”预览候选。') + " ", 1),
                                (task.last_run_at)
                                  ? (_openBlock(), _createElementBlock("span", _hoisted_19, " · " + _toDisplayString(task.last_run_at), 1))
                                  : _createCommentVNode("", true)
                              ])
                            ], 2),
                            (task.transfer_status_text)
                              ? (_openBlock(), _createElementBlock("div", _hoisted_20, [
                                  _createVNode(_component_v_icon, {
                                    size: "15",
                                    class: "mt-1 flex-shrink-0",
                                    color: task.last_transfer_status === 'failed' ? 'error' : undefined
                                  }, {
                                    default: _withCtx(() => [...(_cache[25] || (_cache[25] = [
                                      _createTextVNode("mdi-folder-move-outline", -1)
                                    ]))]),
                                    _: 1
                                  }, 8, ["color"]),
                                  _createElementVNode("span", null, [
                                    _createTextVNode("整理：" + _toDisplayString(task.transfer_status_text), 1),
                                    (task.last_transfer_message)
                                      ? (_openBlock(), _createElementBlock("span", _hoisted_21, " · " + _toDisplayString(task.last_transfer_message), 1))
                                      : _createCommentVNode("", true)
                                  ])
                                ]))
                              : _createCommentVNode("", true)
                          ]),
                          _createElementVNode("div", _hoisted_22, [
                            _createVNode(_component_v_btn, {
                              icon: "",
                              size: "small",
                              variant: "text",
                              color: "primary",
                              loading: isBusy(task, 'run'),
                              disabled: isRunning(task),
                              onClick: $event => (runTask(task))
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_icon, null, {
                                  default: _withCtx(() => [...(_cache[26] || (_cache[26] = [
                                    _createTextVNode("mdi-magnify", -1)
                                  ]))]),
                                  _: 1
                                }),
                                _createVNode(_component_v_tooltip, {
                                  activator: "parent",
                                  location: "top"
                                }, {
                                  default: _withCtx(() => [...(_cache[27] || (_cache[27] = [
                                    _createTextVNode("立即检查", -1)
                                  ]))]),
                                  _: 1
                                })
                              ]),
                              _: 1
                            }, 8, ["loading", "disabled", "onClick"]),
                            _createVNode(_component_v_btn, {
                              icon: "",
                              size: "small",
                              variant: "text",
                              onClick: $event => (openEditor(task))
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_icon, null, {
                                  default: _withCtx(() => [...(_cache[28] || (_cache[28] = [
                                    _createTextVNode("mdi-pencil-outline", -1)
                                  ]))]),
                                  _: 1
                                }),
                                _createVNode(_component_v_tooltip, {
                                  activator: "parent",
                                  location: "top"
                                }, {
                                  default: _withCtx(() => [...(_cache[29] || (_cache[29] = [
                                    _createTextVNode("编辑", -1)
                                  ]))]),
                                  _: 1
                                })
                              ]),
                              _: 1
                            }, 8, ["onClick"]),
                            _createVNode(_component_v_btn, {
                              icon: "",
                              size: "small",
                              variant: "text",
                              loading: isBusy(task, 'toggle'),
                              onClick: $event => (toggleTask(task))
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_icon, null, {
                                  default: _withCtx(() => [
                                    _createTextVNode(_toDisplayString(task.enabled ? 'mdi-pause' : 'mdi-play'), 1)
                                  ]),
                                  _: 2
                                }, 1024),
                                _createVNode(_component_v_tooltip, {
                                  activator: "parent",
                                  location: "top"
                                }, {
                                  default: _withCtx(() => [
                                    _createTextVNode(_toDisplayString(task.enabled ? '暂停' : '恢复'), 1)
                                  ]),
                                  _: 2
                                }, 1024)
                              ]),
                              _: 2
                            }, 1032, ["loading", "onClick"]),
                            _createVNode(_component_v_btn, {
                              icon: "",
                              size: "small",
                              variant: "text",
                              color: task.auto_download ? 'warning' : undefined,
                              loading: isBusy(task, 'auto'),
                              onClick: $event => (toggleAuto(task))
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_icon, null, {
                                  default: _withCtx(() => [
                                    _createTextVNode(_toDisplayString(task.auto_download ? 'mdi-download' : 'mdi-download-off-outline'), 1)
                                  ]),
                                  _: 2
                                }, 1024),
                                _createVNode(_component_v_tooltip, {
                                  activator: "parent",
                                  location: "top"
                                }, {
                                  default: _withCtx(() => [
                                    _createTextVNode(_toDisplayString(task.auto_download ? '关闭自动下载' : '开启自动下载'), 1)
                                  ]),
                                  _: 2
                                }, 1024)
                              ]),
                              _: 2
                            }, 1032, ["color", "loading", "onClick"]),
                            _createVNode(_component_v_btn, {
                              icon: "",
                              size: "small",
                              variant: "text",
                              onClick: $event => (openDetail(task, 'results'))
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_badge, {
                                  content: task.results_count,
                                  "model-value": task.results_count > 0,
                                  color: "primary",
                                  floating: ""
                                }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_icon, null, {
                                      default: _withCtx(() => [...(_cache[30] || (_cache[30] = [
                                        _createTextVNode("mdi-text-box-search-outline", -1)
                                      ]))]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                }, 8, ["content", "model-value"]),
                                _createVNode(_component_v_tooltip, {
                                  activator: "parent",
                                  location: "top"
                                }, {
                                  default: _withCtx(() => [...(_cache[31] || (_cache[31] = [
                                    _createTextVNode("候选、下载记录与日志", -1)
                                  ]))]),
                                  _: 1
                                })
                              ]),
                              _: 2
                            }, 1032, ["onClick"]),
                            _createVNode(_component_v_menu, null, {
                              activator: _withCtx(({ props: menuProps }) => [
                                _createVNode(_component_v_btn, _mergeProps({
                                  icon: "",
                                  size: "small",
                                  variant: "text"
                                }, { ref_for: true }, menuProps), {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_icon, null, {
                                      default: _withCtx(() => [...(_cache[32] || (_cache[32] = [
                                        _createTextVNode("mdi-dots-vertical", -1)
                                      ]))]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                }, 16)
                              ]),
                              default: _withCtx(() => [
                                _createVNode(_component_v_list, {
                                  density: "compact",
                                  "min-width": "220"
                                }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list_item, {
                                      "prepend-icon": "mdi-history",
                                      title: "运行日志",
                                      onClick: $event => (openDetail(task, 'logs'))
                                    }, null, 8, ["onClick"]),
                                    _createVNode(_component_v_list_item, {
                                      "prepend-icon": "mdi-restart",
                                      title: "重置进度",
                                      onClick: $event => (resetTask(task))
                                    }, null, 8, ["onClick"]),
                                    _createVNode(_component_v_divider, { class: "my-1" }),
                                    _createVNode(_component_v_list_item, {
                                      "prepend-icon": "mdi-broom",
                                      title: "清理任务（保留文件）",
                                      onClick: $event => (cleanupTask(task, false))
                                    }, null, 8, ["onClick"]),
                                    _createVNode(_component_v_list_item, {
                                      "prepend-icon": "mdi-delete-sweep-outline",
                                      title: "清理任务和下载文件",
                                      "base-color": "error",
                                      onClick: $event => (cleanupTask(task, true))
                                    }, null, 8, ["onClick"]),
                                    _createVNode(_component_v_divider, { class: "my-1" }),
                                    _createVNode(_component_v_list_item, {
                                      "prepend-icon": "mdi-delete-outline",
                                      title: "移入回收站",
                                      onClick: $event => (trashTask(task))
                                    }, null, 8, ["onClick"]),
                                    _createVNode(_component_v_list_item, {
                                      "prepend-icon": "mdi-delete-forever-outline",
                                      title: "彻底删除",
                                      "base-color": "error",
                                      onClick: $event => (purgeTask(task))
                                    }, null, 8, ["onClick"])
                                  ]),
                                  _: 2
                                }, 1024)
                              ]),
                              _: 2
                            }, 1024)
                          ])
                        ])
                      ]),
                      _: 2
                    }, 1032, ["class"]))
                  }), 128)),
                  (trash.value.length)
                    ? (_openBlock(), _createBlock(_component_v_expansion_panels, {
                        key: 3,
                        modelValue: trashPanel.value,
                        "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((trashPanel).value = $event)),
                        variant: "accordion",
                        class: "mt-4"
                      }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_expansion_panel, {
                            rounded: "lg",
                            elevation: "0",
                            class: "border"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_expansion_panel_title, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_icon, {
                                    class: "me-2",
                                    size: "20"
                                  }, {
                                    default: _withCtx(() => [...(_cache[33] || (_cache[33] = [
                                      _createTextVNode("mdi-delete-restore", -1)
                                    ]))]),
                                    _: 1
                                  }),
                                  _cache[34] || (_cache[34] = _createTextVNode(" 回收站 ", -1)),
                                  _createVNode(_component_v_chip, {
                                    size: "x-small",
                                    class: "ms-2",
                                    variant: "tonal"
                                  }, {
                                    default: _withCtx(() => [
                                      _createTextVNode(_toDisplayString(trash.value.length), 1)
                                    ]),
                                    _: 1
                                  })
                                ]),
                                _: 1
                              }),
                              _createVNode(_component_v_expansion_panel_text, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_list, {
                                    density: "compact",
                                    lines: "two",
                                    class: "pa-0"
                                  }, {
                                    default: _withCtx(() => [
                                      (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(trash.value, (item) => {
                                        return (_openBlock(), _createBlock(_component_v_list_item, {
                                          key: item.id,
                                          title: item.name,
                                          subtitle: `${item.type}${item.season ? ' S' + String(item.season).padStart(2, '0') : ''} · 删除于 ${item.deleted_at || '-'} · 已记录 ${item.downloaded_text || '无'}`
                                        }, {
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              variant: "tonal",
                                              color: "success",
                                              class: "me-2",
                                              loading: isBusy(item, 'restore'),
                                              onClick: $event => (restoreTrash(item))
                                            }, {
                                              default: _withCtx(() => [...(_cache[35] || (_cache[35] = [
                                                _createTextVNode("恢复", -1)
                                              ]))]),
                                              _: 1
                                            }, 8, ["loading", "onClick"]),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              variant: "tonal",
                                              color: "error",
                                              loading: isBusy(item, 'purge'),
                                              onClick: $event => (purgeTrash(item))
                                            }, {
                                              default: _withCtx(() => [...(_cache[36] || (_cache[36] = [
                                                _createTextVNode("彻底删除", -1)
                                              ]))]),
                                              _: 1
                                            }, 8, ["loading", "onClick"])
                                          ]),
                                          _: 2
                                        }, 1032, ["title", "subtitle"]))
                                      }), 128))
                                    ]),
                                    _: 1
                                  }),
                                  _createElementVNode("div", _hoisted_23, [
                                    _createVNode(_component_v_btn, {
                                      size: "small",
                                      variant: "text",
                                      color: "error",
                                      "prepend-icon": "mdi-delete-sweep",
                                      loading: busy.value['purge-all'] === 'purge-all',
                                      onClick: purgeAllTrash
                                    }, {
                                      default: _withCtx(() => [...(_cache[37] || (_cache[37] = [
                                        _createTextVNode("清空回收站", -1)
                                      ]))]),
                                      _: 1
                                    }, 8, ["loading"])
                                  ])
                                ]),
                                _: 1
                              })
                            ]),
                            _: 1
                          })
                        ]),
                        _: 1
                      }, 8, ["modelValue"]))
                    : _createCommentVNode("", true)
                ], 64))
              : _createCommentVNode("", true)
        ]),
        _: 1
      }),
      _createVNode(_sfc_main$2, {
        modelValue: editorOpen.value,
        "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((editorOpen).value = $event)),
        task: editingTask.value,
        client: _unref(client),
        mobile: mobile.value,
        onSaved: onSaved
      }, null, 8, ["modelValue", "task", "client", "mobile"]),
      _createVNode(_sfc_main$1, {
        modelValue: detailOpen.value,
        "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((detailOpen).value = $event)),
        task: detailTask.value,
        client: _unref(client),
        mobile: mobile.value,
        "initial-tab": detailTab.value
      }, null, 8, ["modelValue", "task", "client", "mobile", "initial-tab"]),
      _createVNode(_component_v_snackbar, {
        modelValue: snackbar.value.show,
        "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((snackbar.value.show) = $event)),
        color: snackbar.value.color,
        timeout: "3000"
      }, {
        default: _withCtx(() => [
          _createTextVNode(_toDisplayString(snackbar.value.text), 1)
        ]),
        _: 1
      }, 8, ["modelValue", "color"])
    ]),
    _: 1
  }))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-4918e317"]]);

export { Page as default };
