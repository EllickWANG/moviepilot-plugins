import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { c as createClient, s as speedText, a as sizeText, t as timeText, _ as _sfc_main$1, b as stateText } from './styles-Cu-J1T5i.js';

const {resolveComponent:_resolveComponent,createVNode:_createVNode,createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,normalizeClass:_normalizeClass,withCtx:_withCtx,renderList:_renderList,Fragment:_Fragment,openBlock:_openBlock,createElementBlock:_createElementBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,unref:_unref,normalizeStyle:_normalizeStyle} = await importShared('vue');


const _hoisted_1 = { class: "fb-root" };
const _hoisted_2 = { class: "fb-header" };
const _hoisted_3 = { class: "fb-brand" };
const _hoisted_4 = { class: "fb-brandmark" };
const _hoisted_5 = { class: "fb-kicker" };
const _hoisted_6 = { class: "fb-header-actions" };
const _hoisted_7 = {
  class: "fb-tabs",
  "aria-label": "刷流工作台导航"
};
const _hoisted_8 = ["aria-current", "onClick"];
const _hoisted_9 = {
  key: 2,
  class: "fb-loading"
};
const _hoisted_10 = { class: "fb-metrics" };
const _hoisted_11 = { class: "fb-metric featured" };
const _hoisted_12 = { class: "fb-metric" };
const _hoisted_13 = { class: "fb-metric" };
const _hoisted_14 = { class: "fb-metric" };
const _hoisted_15 = { class: "fb-overview-grid" };
const _hoisted_16 = { class: "fb-panel" };
const _hoisted_17 = { class: "fb-section-title" };
const _hoisted_18 = {
  key: 0,
  class: "fb-allocation"
};
const _hoisted_19 = { class: "fb-site-dot" };
const _hoisted_20 = { class: "fb-quota" };
const _hoisted_21 = { class: "fb-meter" };
const _hoisted_22 = {
  key: 1,
  class: "fb-empty"
};
const _hoisted_23 = { class: "fb-panel fb-run" };
const _hoisted_24 = { class: "fb-run-facts" };
const _hoisted_25 = { class: "fb-policy-strip" };
const _hoisted_26 = { class: "fb-page-title" };
const _hoisted_27 = { class: "fb-site-filter" };
const _hoisted_28 = { class: "fb-site-grid" };
const _hoisted_29 = { class: "fb-site-dot" };
const _hoisted_30 = { class: "fb-site-reason" };
const _hoisted_31 = { class: "fb-site-stats" };
const _hoisted_32 = { class: "fb-retention" };
const _hoisted_33 = {
  key: 0,
  class: "fb-site-result"
};
const _hoisted_34 = ["aria-expanded", "onClick"];
const _hoisted_35 = { key: 1 };
const _hoisted_36 = { class: "fb-page-title" };
const _hoisted_37 = { class: "fb-table-tools" };
const _hoisted_38 = { class: "fb-panel fb-task-list" };
const _hoisted_39 = { class: "fb-task-main" };
const _hoisted_40 = { class: "fb-site-label" };
const _hoisted_41 = { class: "fb-task-meta" };
const _hoisted_42 = {
  key: 0,
  class: "fb-progress"
};
const _hoisted_43 = { class: "fb-task-traffic" };
const _hoisted_44 = { key: 0 };
const _hoisted_45 = {
  key: 0,
  class: "fb-empty"
};
const _hoisted_46 = { class: "fb-pagination" };
const _hoisted_47 = { class: "fb-footer" };

const {ref,computed,onMounted,onBeforeUnmount} = await importShared('vue');

const _sfc_main = {
  __name: 'Page',
  props: {api:{type:Object,default:()=>({})},pluginId:{type:String,default:'FlowBrush'},sourcePluginId:String},
  emits: ['close','switch','layout'],
  setup(__props, { emit: __emit }) {

const props=__props;
const emit=__emit;
const client=createClient(props.api,props.pluginId),data=ref(null),error=ref(''),loading=ref(true),refreshing=ref(false),tab=ref('overview'),config=ref({}),saving=ref(false),submitting=ref(false),siteEdit=ref(false),allSites=ref(false),toast=ref(''),query=ref(''),filter=ref('all'),historyMode=ref('live'),page=ref(1),expanded=ref(null);
let timer,sequence=0,destroyed=false;
const summary=computed(()=>data.value?.summary||{}),policy=computed(()=>data.value?.policy||{}),job=computed(()=>data.value?.job||{});
const running=computed(()=>job.value.status==='running');
const sites=computed(()=>[...(data.value?.sites||[])].sort((a,b)=>Number(b.enabled)-Number(a.enabled)||(b.downloading||0)-(a.downloading||0)||a.name.localeCompare(b.name,'zh-CN')));
const visibleSites=computed(()=>sites.value.filter(s=>s.enabled||s.downloading||s.seeding));
const filtered=computed(()=>{
 const rows=historyMode.value==='live'?data.value?.tasks||[]:data.value?.history||[];
 return rows.filter(t=>(filter.value==='all'||t.status===filter.value)&&(t.title+' '+t.site).toLowerCase().includes(query.value.toLowerCase()));
});
const paged=computed(()=>filtered.value.slice((page.value-1)*12,page.value*12));
async function load(initial=false){
 const seq=++sequence;refreshing.value=true;
 try{const d=await client.workspace();if(destroyed||seq!==sequence)return;data.value=d;error.value='';if(initial)config.value=await client.config();}
 catch(e){if(!destroyed&&seq===sequence)error.value=e.message||'读取工作台失败';}
 finally{if(!destroyed&&seq===sequence){loading.value=false;refreshing.value=false;}}
}
async function changeTab(value){tab.value=value;error.value='';if(value==='strategy'){try{config.value=await client.config();}catch(e){error.value=e.message;}}}
async function editSites(){try{config.value=await client.config();siteEdit.value=true;}catch(e){error.value=e.message;}}
async function save(changes){saving.value=true;error.value='';try{await client.save(changes);config.value=await client.config();siteEdit.value=false;toast.value='策略已保存并生效';await load();}catch(e){error.value=e.message;}finally{saving.value=false;}}
async function run(){submitting.value=true;try{await client.run();toast.value='已开始选种，运行结果会自动更新';await load();}catch(e){error.value=e.message;}finally{submitting.value=false;}}
function resetFilter(){page.value=1;}
function metric(v){return v==null?'—':v;}
onMounted(async()=>{emit('layout',{maxWidth:'86rem'});await load(true);timer=setInterval(()=>{if(!document.hidden&&!refreshing.value)load();},15000);});
onBeforeUnmount(()=>{destroyed=true;clearInterval(timer);sequence++;});

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_progress_circular = _resolveComponent("v-progress-circular");
  const _component_v_btn_toggle = _resolveComponent("v-btn-toggle");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_select = _resolveComponent("v-select");
  const _component_v_pagination = _resolveComponent("v-pagination");
  const _component_v_card_title = _resolveComponent("v-card-title");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_dialog = _resolveComponent("v-dialog");
  const _component_v_snackbar = _resolveComponent("v-snackbar");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("header", _hoisted_2, [
      _createElementVNode("div", _hoisted_3, [
        _createElementVNode("div", _hoisted_4, [
          _createVNode(_component_v_icon, {
            icon: "mdi-chart-timeline-variant-shimmer",
            size: "30"
          })
        ]),
        _createElementVNode("div", null, [
          _createElementVNode("div", _hoisted_5, [
            _cache[16] || (_cache[16] = _createTextVNode("FLOWBRUSH ", -1)),
            _createElementVNode("span", null, "· v" + _toDisplayString(data.value?.version||'4.5'), 1)
          ]),
          _cache[17] || (_cache[17] = _createElementVNode("h1", null, "刷流工作台", -1)),
          _cache[18] || (_cache[18] = _createElementVNode("p", null, "把下载名额留给有需求的种子。", -1))
        ])
      ]),
      _createElementVNode("div", _hoisted_6, [
        _createElementVNode("span", {
          class: _normalizeClass(["fb-pill", data.value?.enabled?'green':''])
        }, [
          _cache[19] || (_cache[19] = _createElementVNode("i", null, null, -1)),
          _createTextVNode(_toDisplayString(data.value?.enabled?'自动运行中':'自动运行已关闭'), 1)
        ], 2),
        _createVNode(_component_v_btn, {
          variant: "outlined",
          icon: "mdi-refresh",
          "aria-label": "刷新工作台",
          loading: refreshing.value,
          onClick: _cache[0] || (_cache[0] = $event => (load()))
        }, null, 8, ["loading"]),
        _createVNode(_component_v_btn, {
          color: "primary",
          "prepend-icon": "mdi-play",
          loading: submitting.value||running.value,
          disabled: !data.value,
          onClick: run
        }, {
          default: _withCtx(() => [...(_cache[20] || (_cache[20] = [
            _createTextVNode("运行一轮", -1)
          ]))]),
          _: 1
        }, 8, ["loading", "disabled"])
      ])
    ]),
    _createElementVNode("nav", _hoisted_7, [
      (_openBlock(), _createElementBlock(_Fragment, null, _renderList([{id:'overview',name:'运行概况',icon:'mdi-view-dashboard-outline'},{id:'strategy',name:'选种策略',icon:'mdi-tune-variant'},{id:'sites',name:'站点分配',icon:'mdi-domain'},{id:'history',name:'任务记录',icon:'mdi-format-list-bulleted'}], (item) => {
        return _createElementVNode("button", {
          key: item.id,
          class: _normalizeClass({active:tab.value===item.id}),
          "aria-current": tab.value===item.id?'page':undefined,
          onClick: $event => (changeTab(item.id))
        }, [
          _createVNode(_component_v_icon, {
            icon: item.icon,
            size: "19"
          }, null, 8, ["icon"]),
          _createTextVNode(_toDisplayString(item.name), 1)
        ], 10, _hoisted_8)
      }), 64))
    ]),
    (error.value)
      ? (_openBlock(), _createBlock(_component_v_alert, {
          key: 0,
          type: "error",
          variant: "tonal",
          class: "mb-5"
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(error.value), 1),
            _createVNode(_component_v_btn, {
              variant: "text",
              onClick: _cache[1] || (_cache[1] = $event => (load()))
            }, {
              default: _withCtx(() => [...(_cache[21] || (_cache[21] = [
                _createTextVNode("重试", -1)
              ]))]),
              _: 1
            })
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    (data.value?.live_error)
      ? (_openBlock(), _createBlock(_component_v_alert, {
          key: 1,
          type: "warning",
          variant: "tonal",
          class: "mb-5"
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(data.value.live_error) + "。统计未按零处理。", 1)
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    (loading.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_9, [
          _createVNode(_component_v_progress_circular, {
            indeterminate: "",
            color: "primary"
          }),
          _cache[22] || (_cache[22] = _createElementVNode("p", null, "正在读取下载器与站点状态", -1))
        ]))
      : (data.value)
        ? (_openBlock(), _createElementBlock(_Fragment, { key: 3 }, [
            (tab.value==='overview')
              ? (_openBlock(), _createElementBlock(_Fragment, { key: 0 }, [
                  _createElementVNode("div", _hoisted_10, [
                    _createElementVNode("article", _hoisted_11, [
                      _createElementVNode("span", null, [
                        _createVNode(_component_v_icon, {
                          icon: "mdi-arrow-up",
                          size: "18"
                        }),
                        _cache[23] || (_cache[23] = _createTextVNode("实时上传", -1))
                      ]),
                      _createElementVNode("strong", null, _toDisplayString(_unref(speedText)(summary.value.upload_speed)), 1),
                      _createElementVNode("small", null, "当前刷流任务累计上传 " + _toDisplayString(_unref(sizeText)(summary.value.uploaded)), 1)
                    ]),
                    _createElementVNode("article", _hoisted_12, [
                      _createElementVNode("span", null, [
                        _createVNode(_component_v_icon, {
                          icon: "mdi-arrow-down",
                          size: "18"
                        }),
                        _cache[24] || (_cache[24] = _createTextVNode("实时下载", -1))
                      ]),
                      _createElementVNode("strong", null, _toDisplayString(_unref(speedText)(summary.value.download_speed)), 1),
                      _createElementVNode("small", null, _toDisplayString(data.value.downloader||'未选择下载器'), 1)
                    ]),
                    _createElementVNode("article", _hoisted_13, [
                      _createElementVNode("span", null, [
                        _createVNode(_component_v_icon, {
                          icon: "mdi-layers-outline",
                          size: "18"
                        }),
                        _cache[25] || (_cache[25] = _createTextVNode("下载名额", -1))
                      ]),
                      _createElementVNode("strong", null, [
                        _createTextVNode(_toDisplayString(metric(summary.value.downloading)), 1),
                        _createElementVNode("em", null, "/ " + _toDisplayString(policy.value.max_downloads||'不限'), 1)
                      ]),
                      _cache[26] || (_cache[26] = _createElementVNode("small", null, "包含未完成的等待与暂停任务", -1))
                    ]),
                    _createElementVNode("article", _hoisted_14, [
                      _createElementVNode("span", null, [
                        _createVNode(_component_v_icon, {
                          icon: "mdi-seed-outline",
                          size: "18"
                        }),
                        _cache[27] || (_cache[27] = _createTextVNode("持续做种", -1))
                      ]),
                      _createElementVNode("strong", null, [
                        _createTextVNode(_toDisplayString(metric(summary.value.seeding)), 1),
                        _cache[28] || (_cache[28] = _createElementVNode("em", null, "个", -1))
                      ]),
                      _createElementVNode("small", null, "当前任务体积 " + _toDisplayString(_unref(sizeText)(summary.value.size)), 1)
                    ])
                  ]),
                  _createElementVNode("div", _hoisted_15, [
                    _createElementVNode("section", _hoisted_16, [
                      _createElementVNode("div", _hoisted_17, [
                        _createElementVNode("div", null, [
                          _cache[29] || (_cache[29] = _createElementVNode("span", { class: "fb-eyebrow" }, "资源分配", -1)),
                          _cache[30] || (_cache[30] = _createElementVNode("h2", null, "各站的下载名额", -1)),
                          _createElementVNode("p", null, "每站最多 " + _toDisplayString(policy.value.site_cap||'不限') + " 个，每轮新增 " + _toDisplayString(policy.value.cycle_cap||'不限') + " 个。", 1)
                        ]),
                        _createVNode(_component_v_btn, {
                          variant: "text",
                          color: "primary",
                          onClick: _cache[2] || (_cache[2] = $event => (changeTab('sites')))
                        }, {
                          default: _withCtx(() => [
                            _cache[31] || (_cache[31] = _createTextVNode("查看全部", -1)),
                            _createVNode(_component_v_icon, {
                              icon: "mdi-arrow-right",
                              size: "18"
                            })
                          ]),
                          _: 1
                        })
                      ]),
                      (visibleSites.value.length)
                        ? (_openBlock(), _createElementBlock("div", _hoisted_18, [
                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(visibleSites.value, (site) => {
                              return (_openBlock(), _createElementBlock("div", {
                                key: site.id,
                                class: "fb-allocation-row"
                              }, [
                                _createElementVNode("div", null, [
                                  _createElementVNode("span", _hoisted_19, _toDisplayString(site.name.slice(0,1)), 1),
                                  _createElementVNode("div", null, [
                                    _createElementVNode("strong", null, _toDisplayString(site.name), 1),
                                    _createElementVNode("small", null, _toDisplayString(site.reason), 1)
                                  ])
                                ]),
                                _createElementVNode("div", _hoisted_20, [
                                  _createElementVNode("div", _hoisted_21, [
                                    _createElementVNode("i", {
                                      style: _normalizeStyle({width:site.downloading==null?'0%':Math.min(100,(site.downloading/(site.cap||policy.value.site_cap||1))*100)+'%'})
                                    }, null, 4)
                                  ]),
                                  _createElementVNode("span", null, _toDisplayString(metric(site.downloading)) + " / " + _toDisplayString(site.cap||'∞'), 1)
                                ])
                              ]))
                            }), 128))
                          ]))
                        : (_openBlock(), _createElementBlock("p", _hoisted_22, [
                            _cache[32] || (_cache[32] = _createTextVNode("尚未选择站点。", -1)),
                            _createElementVNode("button", {
                              class: "fb-link",
                              onClick: editSites
                            }, "选择刷流站点")
                          ]))
                    ]),
                    _createElementVNode("aside", _hoisted_23, [
                      _cache[37] || (_cache[37] = _createElementVNode("span", { class: "fb-eyebrow" }, "最近一轮", -1)),
                      _createElementVNode("div", {
                        class: _normalizeClass(["fb-run-icon", job.value.status==='error'?'bad':''])
                      }, [
                        _createVNode(_component_v_icon, {
                          icon: running.value?'mdi-progress-clock':job.value.status==='error'?'mdi-alert-circle-outline':'mdi-check-circle-outline',
                          size: "34"
                        }, null, 8, ["icon"])
                      ], 2),
                      _createElementVNode("h2", null, _toDisplayString(running.value?'正在选种':job.value.message||'等待首次运行'), 1),
                      _createElementVNode("p", null, _toDisplayString(job.value.started_at?_unref(timeText)(job.value.started_at):'运行后可查看每个站点的筛选结果。'), 1),
                      _createElementVNode("div", _hoisted_24, [
                        _createElementVNode("span", null, [
                          _cache[33] || (_cache[33] = _createTextVNode("新增任务", -1)),
                          _createElementVNode("strong", null, _toDisplayString(job.value.added??'—') + " 个", 1)
                        ]),
                        _createElementVNode("span", null, [
                          _cache[34] || (_cache[34] = _createTextVNode("本轮检查站点", -1)),
                          _createElementVNode("strong", null, _toDisplayString(Object.keys(job.value.sites||{}).length) + " 站", 1)
                        ]),
                        _createElementVNode("span", null, [
                          _cache[35] || (_cache[35] = _createTextVNode("自动选种", -1)),
                          _createElementVNode("strong", null, _toDisplayString(data.value.enabled?'已开启':'已关闭'), 1)
                        ])
                      ]),
                      _createVNode(_component_v_btn, {
                        variant: "outlined",
                        block: "",
                        onClick: _cache[3] || (_cache[3] = $event => (changeTab('sites')))
                      }, {
                        default: _withCtx(() => [...(_cache[36] || (_cache[36] = [
                          _createTextVNode("查看筛选结果", -1)
                        ]))]),
                        _: 1
                      })
                    ])
                  ]),
                  _createElementVNode("section", _hoisted_25, [
                    _createElementVNode("div", null, [
                      _createVNode(_component_v_icon, { icon: "mdi-filter-variant" }),
                      _cache[38] || (_cache[38] = _createElementVNode("strong", null, "当前选种策略", -1))
                    ]),
                    _createElementVNode("span", null, _toDisplayString(policy.value.size||'不限') + " GiB", 1),
                    _createElementVNode("span", null, _toDisplayString(policy.value.pubtime?'近 '+Number(String(policy.value.pubtime).split('-').at(-1))/60+' 小时':'不限发布时间'), 1),
                    _createElementVNode("span", null, "下载者 ≥ " + _toDisplayString(policy.value.min_peers||0), 1),
                    _createElementVNode("span", null, _toDisplayString(policy.value.freeleech?'仅免费':'不限促销'), 1),
                    _createElementVNode("span", null, _toDisplayString(policy.value.demand_priority?'需求优先':'新种优先'), 1),
                    _createElementVNode("button", {
                      class: "fb-link",
                      onClick: _cache[4] || (_cache[4] = $event => (changeTab('strategy')))
                    }, "调整策略")
                  ])
                ], 64))
              : _createCommentVNode("", true),
            (tab.value==='strategy')
              ? (_openBlock(), _createElementBlock(_Fragment, { key: 1 }, [
                  _cache[39] || (_cache[39] = _createElementVNode("div", { class: "fb-page-title" }, [
                    _createElementVNode("div", null, [
                      _createElementVNode("h2", null, "选种策略"),
                      _createElementVNode("p", null, "筛选与容量集中管理，分站保种规则独立生效。")
                    ])
                  ], -1)),
                  _createVNode(_sfc_main$1, {
                    initial: config.value,
                    sites: sites.value,
                    downloaders: data.value.downloaders,
                    section: "strategy",
                    saving: saving.value,
                    onSave: save,
                    onCancel: _cache[5] || (_cache[5] = $event => (changeTab('overview')))
                  }, null, 8, ["initial", "sites", "downloaders", "saving"])
                ], 64))
              : _createCommentVNode("", true),
            (tab.value==='sites')
              ? (_openBlock(), _createElementBlock(_Fragment, { key: 2 }, [
                  _createElementVNode("div", _hoisted_26, [
                    _createElementVNode("div", null, [
                      _cache[40] || (_cache[40] = _createElementVNode("h2", null, "站点分配", -1)),
                      _createElementVNode("p", null, _toDisplayString(sites.value.filter(s=>s.enabled).length) + " 个站点允许新增 · 已停用站点的历史任务继续维护", 1)
                    ]),
                    _createVNode(_component_v_btn, {
                      color: "primary",
                      variant: "tonal",
                      "prepend-icon": "mdi-pencil-outline",
                      onClick: editSites
                    }, {
                      default: _withCtx(() => [...(_cache[41] || (_cache[41] = [
                        _createTextVNode("编辑站点范围", -1)
                      ]))]),
                      _: 1
                    })
                  ]),
                  _createElementVNode("div", _hoisted_27, [
                    _createVNode(_component_v_btn, {
                      variant: "text",
                      size: "small",
                      onClick: _cache[6] || (_cache[6] = $event => (allSites.value=!allSites.value))
                    }, {
                      default: _withCtx(() => [
                        _createTextVNode(_toDisplayString(allSites.value?'只看已启用及有任务的站点':'显示全部 '+sites.value.length+' 个站点'), 1)
                      ]),
                      _: 1
                    })
                  ]),
                  _createElementVNode("div", _hoisted_28, [
                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList((allSites.value?sites.value:visibleSites.value), (site) => {
                      return (_openBlock(), _createElementBlock("article", {
                        key: site.id,
                        class: _normalizeClass(["fb-panel fb-site-card", {muted:!site.enabled&&!site.downloading&&!site.seeding}])
                      }, [
                        _createElementVNode("header", null, [
                          _createElementVNode("div", null, [
                            _createElementVNode("span", _hoisted_29, _toDisplayString(site.name.slice(0,1)), 1),
                            _createElementVNode("h3", null, _toDisplayString(site.name), 1)
                          ]),
                          _createElementVNode("span", {
                            class: _normalizeClass(["fb-pill", site.enabled?'green':''])
                          }, _toDisplayString(site.enabled?'允许新增':'停止新增'), 3)
                        ]),
                        _createElementVNode("p", _hoisted_30, _toDisplayString(site.reason), 1),
                        _createElementVNode("div", _hoisted_31, [
                          _createElementVNode("span", null, [
                            _cache[42] || (_cache[42] = _createTextVNode("下载", -1)),
                            _createElementVNode("strong", null, [
                              _createTextVNode(_toDisplayString(metric(site.downloading)), 1),
                              _createElementVNode("small", null, " / " + _toDisplayString(site.cap||'∞'), 1)
                            ])
                          ]),
                          _createElementVNode("span", null, [
                            _cache[43] || (_cache[43] = _createTextVNode("做种", -1)),
                            _createElementVNode("strong", null, _toDisplayString(metric(site.seeding)), 1)
                          ]),
                          _createElementVNode("span", null, [
                            _cache[44] || (_cache[44] = _createTextVNode("上传", -1)),
                            _createElementVNode("strong", null, _toDisplayString(_unref(speedText)(site.upload_speed)), 1)
                          ])
                        ]),
                        _createElementVNode("div", _hoisted_32, [
                          _createVNode(_component_v_icon, {
                            icon: "mdi-shield-check-outline",
                            size: "17"
                          }),
                          _createElementVNode("span", null, _toDisplayString(site.policy.hr?'H&R 站点 · ':'') + _toDisplayString(site.policy.seed_hours?'完整下载后做种 '+site.policy.seed_hours+' 小时':'保留现有任务'), 1)
                        ]),
                        (site.last_result.message)
                          ? (_openBlock(), _createElementBlock("div", _hoisted_33, [
                              _cache[45] || (_cache[45] = _createElementVNode("strong", null, "最近一轮", -1)),
                              _createElementVNode("p", null, _toDisplayString(site.last_result.message), 1),
                              (Object.keys(site.last_result.rejected||{}).length)
                                ? (_openBlock(), _createElementBlock("button", {
                                    key: 0,
                                    class: "fb-link",
                                    "aria-expanded": expanded.value===site.id,
                                    onClick: $event => (expanded.value=expanded.value===site.id?null:site.id)
                                  }, _toDisplayString(expanded.value===site.id?'收起':'查看') + "筛选原因", 9, _hoisted_34))
                                : _createCommentVNode("", true),
                              (expanded.value===site.id)
                                ? (_openBlock(), _createElementBlock("ul", _hoisted_35, [
                                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(site.last_result.rejected, (count, reason) => {
                                      return (_openBlock(), _createElementBlock("li", { key: reason }, [
                                        _createElementVNode("span", null, _toDisplayString(reason), 1),
                                        _createElementVNode("b", null, _toDisplayString(count), 1)
                                      ]))
                                    }), 128))
                                  ]))
                                : _createCommentVNode("", true)
                            ]))
                          : _createCommentVNode("", true)
                      ], 2))
                    }), 128))
                  ])
                ], 64))
              : _createCommentVNode("", true),
            (tab.value==='history')
              ? (_openBlock(), _createElementBlock(_Fragment, { key: 3 }, [
                  _createElementVNode("div", _hoisted_36, [
                    _createElementVNode("div", null, [
                      _cache[46] || (_cache[46] = _createElementVNode("h2", null, "任务记录", -1)),
                      _createElementVNode("p", null, _toDisplayString(historyMode.value==='live'?'下载器实时状态，15 秒自动刷新':'最近 200 条刷流记录；删除标记以插件检查结果为准'), 1)
                    ]),
                    _createVNode(_component_v_btn_toggle, {
                      modelValue: historyMode.value,
                      "onUpdate:modelValue": [
                        _cache[7] || (_cache[7] = $event => ((historyMode).value = $event)),
                        _cache[8] || (_cache[8] = $event => {filter.value='all';resetFilter();})
                      ],
                      mandatory: "",
                      density: "comfortable",
                      color: "primary"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_btn, { value: "live" }, {
                          default: _withCtx(() => [...(_cache[47] || (_cache[47] = [
                            _createTextVNode("当前任务", -1)
                          ]))]),
                          _: 1
                        }),
                        _createVNode(_component_v_btn, { value: "history" }, {
                          default: _withCtx(() => [...(_cache[48] || (_cache[48] = [
                            _createTextVNode("历史记录", -1)
                          ]))]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }, 8, ["modelValue"])
                  ]),
                  _createElementVNode("div", _hoisted_37, [
                    _createVNode(_component_v_text_field, {
                      modelValue: query.value,
                      "onUpdate:modelValue": [
                        _cache[9] || (_cache[9] = $event => ((query).value = $event)),
                        resetFilter
                      ],
                      label: "搜索任务或站点",
                      "prepend-inner-icon": "mdi-magnify",
                      variant: "outlined",
                      density: "compact",
                      "hide-details": ""
                    }, null, 8, ["modelValue"]),
                    (historyMode.value==='live')
                      ? (_openBlock(), _createBlock(_component_v_select, {
                          key: 0,
                          modelValue: filter.value,
                          "onUpdate:modelValue": [
                            _cache[10] || (_cache[10] = $event => ((filter).value = $event)),
                            resetFilter
                          ],
                          label: "状态",
                          items: [{title:'全部状态',value:'all'},...['downloading','waiting','seeding','paused','error'].map(s=>({title:_unref(stateText)(s),value:s}))],
                          variant: "outlined",
                          density: "compact",
                          "hide-details": ""
                        }, null, 8, ["modelValue", "items"]))
                      : _createCommentVNode("", true)
                  ]),
                  _createElementVNode("div", _hoisted_38, [
                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(paged.value, (task, index) => {
                      return (_openBlock(), _createElementBlock("div", {
                        key: task.hash||index,
                        class: "fb-task"
                      }, [
                        _createElementVNode("div", _hoisted_39, [
                          _createElementVNode("span", _hoisted_40, _toDisplayString(task.site), 1),
                          _createElementVNode("h3", null, _toDisplayString(task.title), 1),
                          _createElementVNode("div", _hoisted_41, [
                            _createElementVNode("span", null, _toDisplayString(_unref(sizeText)(task.size)), 1),
                            _createElementVNode("span", null, _toDisplayString(_unref(timeText)(task.added_on)), 1),
                            _createElementVNode("span", {
                              class: _normalizeClass(["fb-status", task.status])
                            }, _toDisplayString(historyMode.value==='live'?_unref(stateText)(task.status):(task.deleted?'已删除':'记录保留')), 3)
                          ]),
                          (historyMode.value==='live')
                            ? (_openBlock(), _createElementBlock("div", _hoisted_42, [
                                _createElementVNode("i", {
                                  style: _normalizeStyle({width:(task.progress*100)+'%'})
                                }, null, 4)
                              ]))
                            : _createCommentVNode("", true)
                        ]),
                        _createElementVNode("div", _hoisted_43, [
                          (historyMode.value==='live')
                            ? (_openBlock(), _createElementBlock("strong", _hoisted_44, _toDisplayString((task.progress*100).toFixed(1)) + "%", 1))
                            : _createCommentVNode("", true),
                          _createElementVNode("span", null, "↑ " + _toDisplayString(historyMode.value==='live'?_unref(speedText)(task.upload_speed):_unref(sizeText)(task.uploaded)), 1),
                          _createElementVNode("span", null, "↓ " + _toDisplayString(historyMode.value==='live'?_unref(speedText)(task.download_speed):_unref(sizeText)(task.downloaded)), 1)
                        ])
                      ]))
                    }), 128)),
                    (!paged.value.length)
                      ? (_openBlock(), _createElementBlock("div", _hoisted_45, [
                          _createVNode(_component_v_icon, {
                            icon: "mdi-text-box-search-outline",
                            size: "34"
                          }),
                          _createElementVNode("h3", null, _toDisplayString(data.value.live_error&&historyMode.value==='live'?'暂时无法读取任务':'没有匹配的任务'), 1),
                          _cache[49] || (_cache[49] = _createElementVNode("p", null, "可调整搜索或切换记录范围。", -1))
                        ]))
                      : _createCommentVNode("", true)
                  ]),
                  _createElementVNode("div", _hoisted_46, [
                    _createElementVNode("span", null, "共 " + _toDisplayString(filtered.value.length) + " 条", 1),
                    _createVNode(_component_v_pagination, {
                      modelValue: page.value,
                      "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((page).value = $event)),
                      length: Math.max(1,Math.ceil(filtered.value.length/12)),
                      "total-visible": 4,
                      density: "comfortable"
                    }, null, 8, ["modelValue", "length"])
                  ])
                ], 64))
              : _createCommentVNode("", true),
            _createElementVNode("footer", _hoisted_47, [
              _createElementVNode("span", null, "刷流 v" + _toDisplayString(data.value.version) + " · " + _toDisplayString(data.value.downloader), 1),
              _createElementVNode("span", null, "最近刷新 " + _toDisplayString(_unref(timeText)(data.value.checked_at)) + " · 上传收益以站点统计为准", 1)
            ])
          ], 64))
        : _createCommentVNode("", true),
    _createVNode(_component_v_dialog, {
      modelValue: siteEdit.value,
      "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((siteEdit).value = $event)),
      "max-width": "850",
      scrollable: ""
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, { class: "fb-root fb-dialog" }, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "fb-dialog-title" }, {
              default: _withCtx(() => [
                _cache[50] || (_cache[50] = _createTextVNode("站点范围", -1)),
                _createVNode(_component_v_btn, {
                  icon: "mdi-close",
                  "aria-label": "关闭站点范围",
                  variant: "text",
                  onClick: _cache[12] || (_cache[12] = $event => (siteEdit.value=false))
                })
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_text, null, {
              default: _withCtx(() => [
                (error.value)
                  ? (_openBlock(), _createBlock(_component_v_alert, {
                      key: 0,
                      type: "error",
                      variant: "tonal",
                      class: "mb-4"
                    }, {
                      default: _withCtx(() => [
                        _createTextVNode(_toDisplayString(error.value), 1)
                      ]),
                      _: 1
                    }))
                  : _createCommentVNode("", true),
                _createVNode(_sfc_main$1, {
                  initial: config.value,
                  sites: sites.value,
                  downloaders: data.value?.downloaders||[],
                  section: "sites",
                  saving: saving.value,
                  onSave: save,
                  onCancel: _cache[13] || (_cache[13] = $event => (siteEdit.value=false))
                }, null, 8, ["initial", "sites", "downloaders", "saving"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"]),
    _createVNode(_component_v_snackbar, {
      "model-value": !!toast.value,
      "onUpdate:modelValue": _cache[15] || (_cache[15] = v=>{if(!v)toast.value='';}),
      timeout: 4000
    }, {
      default: _withCtx(() => [
        _createTextVNode(_toDisplayString(toast.value), 1)
      ]),
      _: 1
    }, 8, ["model-value"])
  ]))
}
}

};

export { _sfc_main as default };
