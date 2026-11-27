import { importShared } from './__federation_fn_import-JrT3xvdd.js';

const sizeText = value => {
  if(value === null || value === undefined) return '—';
  let n=Number(value)||0, i=0; const units=['B','KiB','MiB','GiB','TiB'];
  while(n>=1024 && i<4){n/=1024;i++;}
  return `${n.toFixed(i>1?2:0)} ${units[i]}`;
};
const speedText = value => value == null ? '—' : `${sizeText(value)}/s`;
const timeText = value => value ? new Date(value*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '尚未运行';
const stateText = value => ({downloading:'下载中',waiting:'等待连接',seeding:'做种中',paused:'已暂停',error:'异常'}[value]||'待确认');
function draftOf(c={}) {
  const range=(v,f)=>String(v||f).split('-').map(Number);
  const [sizeMin,sizeMax]=range(c.size,'1-40'),[seedsMin,seedsMax]=range(c.seeder,'2-500');
  return {...c,brushsites:[...(c.brushsites||[])],sizeMin,sizeMax:sizeMax??sizeMin,seedsMin:seedsMax===undefined?0:seedsMin,seedsMax:seedsMax??seedsMin,
    ageHours:c.pubtime ? Number(String(c.pubtime).split('-').at(-1))/60 : null,
    maxdlcount:c.maxdlcount||8,site_download_cap:c.site_download_cap||2,site_cycle_cap:c.site_cycle_cap||1,
    min_peers:c.min_peers??2,disksize:c.disksize||500,maxupspeed:c.maxupspeed??null,
    demand_priority:c.demand_priority??true,cron:c.cron||'*/10 * * * *'};
}
function changesOf(d, section='all') {
  if(section==='sites') return {brushsites:[...d.brushsites]};
  const integer=(v,min,max,label)=>{const n=Number(v);if(!Number.isInteger(n)||n<min||n>max)throw new Error(`${label}应为 ${min}～${max} 的整数`);return n;};
  const number=(v,min,max,label)=>{const n=Number(v);if(!Number.isFinite(n)||n<min||n>max)throw new Error(`${label}应在 ${min}～${max} 之间`);return n;};
  const total=integer(d.maxdlcount,1,100,'总下载上限'),cap=integer(d.site_download_cap,1,total,'单站上限');
  const lo=number(d.sizeMin,0,10000,'最小体积'),hi=number(d.sizeMax,lo,10000,'最大体积');
  const seedsLo=integer(d.seedsMin,1,100000,'最少做种人数'),seedsHi=integer(d.seedsMax,seedsLo,100000,'最多做种人数');
  const changes={enabled:!!d.enabled,notify:!!d.notify,downloader:d.downloader,cron:d.cron,
    maxdlcount:total,site_download_cap:cap,site_cycle_cap:integer(d.site_cycle_cap,1,cap,'每轮单站新增'),
    disksize:number(d.disksize,1,100000,'保种空间'),size:`${lo}-${hi}`,seeder:`${seedsLo}-${seedsHi}`,
    pubtime:d.ageHours==null||d.ageHours===''?null:`0-${number(d.ageHours,1,8760,'发布时间')*60}`,
    min_peers:integer(d.min_peers,1,100000,'最少下载人数'),demand_priority:!!d.demand_priority,
    maxupspeed:d.maxupspeed==null||d.maxupspeed===''?null:number(d.maxupspeed,1,100000000,'上传带宽阈值'),
    freeleech:d.freeleech,hr:d.hr,save_path:d.save_path||null,qb_category:d.qb_category||null,
    delete_except_tags:d.delete_except_tags||null,include:d.include||null,exclude:d.exclude||null,
    active_time_range:d.active_time_range||null};
  if(!d.downloader)throw new Error('请选择下载器');
  if(!String(d.cron||'').trim())throw new Error('请填写运行周期');
  if(section==='all')changes.brushsites=[...d.brushsites];
  return changes;
}
function mergeConfig(current, changes) {
  // One-shot actions and destructive legacy switches are never triggered by saving this UI.
  return {...current,...changes,onlyonce:false,diagnostic_once:false,clear_task:false};
}

function createClient(api,id='FlowBrush') {
  const base=`plugin/${id}`;
  const unwrap=r=>r&&typeof r==='object'&&'success' in r?r:{success:true,data:r};
  const check=r=>{r=unwrap(r);if(!r.success)throw new Error(r.message||'请求失败');return r.data;};
  return {
    async workspace(){return check(await api.get(`${base}/workspace`,{feedback:'silent'}));},
    async config(){return check(await api.get(base,{feedback:'silent'}))||{};},
    async run(){return check(await api.post(`${base}/run`,{},{feedback:'silent'}));},
    async merged(changes){const state=await this.workspace();if(state.job?.status==='running')throw new Error('选种正在运行，完成后即可保存');return mergeConfig(await this.config(),changes);},
    async save(changes){return check(await api.put(base,await this.merged(changes),{feedback:'silent'}));}
  };
}

const {toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementVNode:_createElementVNode,createVNode:_createVNode,Fragment:_Fragment,createElementBlock:_createElementBlock,renderList:_renderList,vModelCheckbox:_vModelCheckbox,withDirectives:_withDirectives,normalizeClass:_normalizeClass,withModifiers:_withModifiers} = await importShared('vue');


const _hoisted_1 = { class: "fb-panel" };
const _hoisted_2 = { class: "fb-section-title" };
const _hoisted_3 = { class: "fb-fields" };
const _hoisted_4 = { class: "fb-panel" };
const _hoisted_5 = { class: "fb-section-title" };
const _hoisted_6 = { class: "fb-fields fb-fields-four" };
const _hoisted_7 = { class: "fb-inline-field" };
const _hoisted_8 = { class: "fb-panel" };
const _hoisted_9 = { class: "fb-section-title" };
const _hoisted_10 = { class: "fb-fields" };
const _hoisted_11 = { class: "fb-fields fb-fields-four mt-5" };
const _hoisted_12 = {
  key: 2,
  class: "fb-panel"
};
const _hoisted_13 = { class: "fb-section-title" };
const _hoisted_14 = { class: "fb-pill" };
const _hoisted_15 = { class: "fb-site-picker" };
const _hoisted_16 = ["value"];
const _hoisted_17 = {
  key: 0,
  class: "fb-empty"
};
const _hoisted_18 = {
  key: 3,
  class: "fb-panel fb-advanced"
};
const _hoisted_19 = ["aria-expanded"];
const _hoisted_20 = {
  key: 0,
  class: "fb-fields mt-5"
};
const _hoisted_21 = { class: "fb-savebar" };

const {ref,watch,computed} = await importShared('vue');

const _sfc_main = {
  __name: 'PolicyEditor',
  props: {initial:{type:Object,default:()=>({})},sites:{type:Array,default:()=>[]},downloaders:{type:Array,default:()=>[]},section:{type:String,default:'all'},saving:Boolean},
  emits: ['save','cancel'],
  setup(__props, { emit: __emit }) {

const props=__props;
const emit=__emit;
const draft=ref(draftOf(props.initial)),error=ref(''),advanced=ref(false),query=ref('');
watch(()=>props.initial,v=>{draft.value=draftOf(v);error.value='';});
const filtered=computed(()=>props.sites.filter(s=>s.name.toLowerCase().includes(query.value.toLowerCase())));
const includesStrategy=computed(()=>props.section!=='sites');
const includesSites=computed(()=>props.section!=='strategy');
function save(){try{error.value='';emit('save',changesOf(draft.value,props.section));}catch(e){error.value=e.message;}}

return (_ctx, _cache) => {
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_switch = _resolveComponent("v-switch");
  const _component_v_select = _resolveComponent("v-select");
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_btn = _resolveComponent("v-btn");

  return (_openBlock(), _createElementBlock("form", {
    class: "fb-editor",
    onSubmit: _withModifiers(save, ["prevent"])
  }, [
    (error.value)
      ? (_openBlock(), _createBlock(_component_v_alert, {
          key: 0,
          type: "error",
          variant: "tonal",
          class: "mb-5"
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(error.value), 1)
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    (includesStrategy.value)
      ? (_openBlock(), _createElementBlock(_Fragment, { key: 1 }, [
          _createElementVNode("section", _hoisted_1, [
            _createElementVNode("div", _hoisted_2, [
              _cache[28] || (_cache[28] = _createElementVNode("div", null, [
                _createElementVNode("span", { class: "fb-eyebrow" }, "01 / 运行"),
                _createElementVNode("h3", null, "让刷流按计划运行"),
                _createElementVNode("p", null, "开启后按周期选种，已有任务按分站保种规则维护。")
              ], -1)),
              _createVNode(_component_v_switch, {
                modelValue: draft.value.enabled,
                "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((draft.value.enabled) = $event)),
                label: "自动刷流",
                color: "primary",
                "hide-details": "",
                inset: ""
              }, null, 8, ["modelValue"])
            ]),
            _createElementVNode("div", _hoisted_3, [
              _createVNode(_component_v_select, {
                modelValue: draft.value.downloader,
                "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((draft.value.downloader) = $event)),
                items: __props.downloaders,
                label: "下载器",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue", "items"]),
              _createVNode(_component_v_select, {
                modelValue: draft.value.cron,
                "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((draft.value.cron) = $event)),
                items: [{title:'每 10 分钟',value:'*/10 * * * *'},{title:'每 20 分钟',value:'*/20 * * * *'},{title:'每 30 分钟',value:'*/30 * * * *'},{title:'每小时',value:'0 * * * *'},...(!['*/10 * * * *','*/20 * * * *','*/30 * * * *','0 * * * *'].includes(draft.value.cron)?[{title:'自定义：'+draft.value.cron,value:draft.value.cron}]:[])],
                label: "选种周期",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue", "items"]),
              _createVNode(_component_v_switch, {
                modelValue: draft.value.notify,
                "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((draft.value.notify) = $event)),
                label: "运行通知",
                color: "primary",
                "hide-details": ""
              }, null, 8, ["modelValue"])
            ])
          ]),
          _createElementVNode("section", _hoisted_4, [
            _createElementVNode("div", _hoisted_5, [
              _cache[29] || (_cache[29] = _createElementVNode("div", null, [
                _createElementVNode("span", { class: "fb-eyebrow" }, "02 / 容量"),
                _createElementVNode("h3", null, "给不同站点留出名额"),
                _createElementVNode("p", null, "等待任务也占名额。降低上限只影响新增，已有任务继续运行。")
              ], -1)),
              _createVNode(_component_v_icon, { icon: "mdi-tune-variant" })
            ]),
            _createElementVNode("div", _hoisted_6, [
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.maxdlcount,
                "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((draft.value.maxdlcount) = $event)),
                type: "number",
                label: "总下载上限",
                suffix: "个",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.site_download_cap,
                "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((draft.value.site_download_cap) = $event)),
                type: "number",
                label: "单站同时下载",
                suffix: "个",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.site_cycle_cap,
                "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((draft.value.site_cycle_cap) = $event)),
                type: "number",
                label: "单站每轮新增",
                suffix: "个",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.disksize,
                "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((draft.value.disksize) = $event)),
                type: "number",
                label: "保种空间上限",
                suffix: "GiB",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"])
            ]),
            _createElementVNode("div", _hoisted_7, [
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.maxupspeed,
                "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((draft.value.maxupspeed) = $event)),
                type: "number",
                label: "上传达到此速度时停止新增",
                suffix: "KiB/s",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _cache[30] || (_cache[30] = _createElementVNode("p", null, "控制新增时机，不修改下载器的限速。", -1))
            ])
          ]),
          _createElementVNode("section", _hoisted_8, [
            _createElementVNode("div", _hoisted_9, [
              _cache[31] || (_cache[31] = _createElementVNode("div", null, [
                _createElementVNode("span", { class: "fb-eyebrow" }, "03 / 选种"),
                _createElementVNode("h3", null, "有种可下，有人需要"),
                _createElementVNode("p", null, "先筛选，再按下载人数与做种人数之比排序。")
              ], -1)),
              _createVNode(_component_v_switch, {
                modelValue: draft.value.demand_priority,
                "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((draft.value.demand_priority) = $event)),
                label: "需求优先",
                color: "primary",
                "hide-details": "",
                inset: ""
              }, null, 8, ["modelValue"])
            ]),
            _createElementVNode("div", _hoisted_10, [
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.ageHours,
                "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((draft.value.ageHours) = $event)),
                type: "number",
                label: "发布时间不超过",
                suffix: "小时",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.min_peers,
                "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((draft.value.min_peers) = $event)),
                type: "number",
                label: "至少有多少人下载",
                suffix: "人",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_select, {
                modelValue: draft.value.freeleech,
                "onUpdate:modelValue": _cache[12] || (_cache[12] = $event => ((draft.value.freeleech) = $event)),
                items: [{title:'只选免费',value:'free'},{title:'双倍上传且免费',value:'2xfree'},{title:'不限促销',value:''}],
                label: "下载促销",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"])
            ]),
            _createElementVNode("div", _hoisted_11, [
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.sizeMin,
                "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((draft.value.sizeMin) = $event)),
                type: "number",
                label: "最小体积",
                suffix: "GiB",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.sizeMax,
                "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((draft.value.sizeMax) = $event)),
                type: "number",
                label: "最大体积",
                suffix: "GiB",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.seedsMin,
                "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((draft.value.seedsMin) = $event)),
                type: "number",
                label: "最少做种人数",
                suffix: "人",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"]),
              _createVNode(_component_v_text_field, {
                modelValue: draft.value.seedsMax,
                "onUpdate:modelValue": _cache[16] || (_cache[16] = $event => ((draft.value.seedsMax) = $event)),
                type: "number",
                label: "最多做种人数",
                suffix: "人",
                variant: "outlined",
                "hide-details": ""
              }, null, 8, ["modelValue"])
            ]),
            _createVNode(_component_v_switch, {
              modelValue: draft.value.hr,
              "onUpdate:modelValue": _cache[17] || (_cache[17] = $event => ((draft.value.hr) = $event)),
              "true-value": "yes",
              "false-value": "no",
              label: "跳过标有 H&R 的新种",
              color: "primary",
              "hide-details": "",
              class: "mt-4"
            }, null, 8, ["modelValue"]),
            _cache[32] || (_cache[32] = _createElementVNode("p", { class: "fb-note" }, "列表未标记 H&R 不代表没有保种要求；已有任务继续遵循各站保种规则。", -1))
          ])
        ], 64))
      : _createCommentVNode("", true),
    (includesSites.value)
      ? (_openBlock(), _createElementBlock("section", _hoisted_12, [
          _createElementVNode("div", _hoisted_13, [
            _cache[33] || (_cache[33] = _createElementVNode("div", null, [
              _createElementVNode("span", { class: "fb-eyebrow" }, "站点范围"),
              _createElementVNode("h3", null, "选择允许新增的站点"),
              _createElementVNode("p", null, "取消选择只停止新增，保留该站已有任务与保种规则。")
            ], -1)),
            _createElementVNode("span", _hoisted_14, "已选 " + _toDisplayString(draft.value.brushsites.length) + " 站", 1)
          ]),
          _createVNode(_component_v_text_field, {
            modelValue: query.value,
            "onUpdate:modelValue": _cache[18] || (_cache[18] = $event => ((query).value = $event)),
            label: "搜索站点",
            "prepend-inner-icon": "mdi-magnify",
            variant: "outlined",
            density: "compact",
            "hide-details": "",
            class: "mb-4"
          }, null, 8, ["modelValue"]),
          _createElementVNode("div", _hoisted_15, [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(filtered.value, (site) => {
              return (_openBlock(), _createElementBlock("label", {
                key: site.id,
                class: _normalizeClass(["fb-pick", {selected:draft.value.brushsites.includes(site.id)}])
              }, [
                _withDirectives(_createElementVNode("input", {
                  type: "checkbox",
                  "onUpdate:modelValue": _cache[19] || (_cache[19] = $event => ((draft.value.brushsites) = $event)),
                  value: site.id
                }, null, 8, _hoisted_16), [
                  [_vModelCheckbox, draft.value.brushsites]
                ]),
                _createElementVNode("span", null, _toDisplayString(site.name), 1),
                _createElementVNode("small", null, _toDisplayString(site.policy?.seed_hours?`做种 ${site.policy.seed_hours} 小时`:'保留现有规则'), 1)
              ], 2))
            }), 128))
          ]),
          (!filtered.value.length)
            ? (_openBlock(), _createElementBlock("p", _hoisted_17, "没有匹配的站点"))
            : _createCommentVNode("", true)
        ]))
      : _createCommentVNode("", true),
    (includesStrategy.value)
      ? (_openBlock(), _createElementBlock("section", _hoisted_18, [
          _createElementVNode("button", {
            type: "button",
            class: "fb-accordion",
            "aria-expanded": advanced.value,
            onClick: _cache[20] || (_cache[20] = $event => (advanced.value=!advanced.value))
          }, [
            _createElementVNode("span", null, [
              _createVNode(_component_v_icon, {
                icon: "mdi-cog-outline",
                size: "20"
              }),
              _cache[34] || (_cache[34] = _createTextVNode("高级设置", -1)),
              _cache[35] || (_cache[35] = _createElementVNode("small", null, "路径、标签与匹配条件", -1))
            ]),
            _createVNode(_component_v_icon, {
              icon: advanced.value?'mdi-chevron-up':'mdi-chevron-down'
            }, null, 8, ["icon"])
          ], 8, _hoisted_19),
          (advanced.value)
            ? (_openBlock(), _createElementBlock("div", _hoisted_20, [
                _createVNode(_component_v_text_field, {
                  modelValue: draft.value.save_path,
                  "onUpdate:modelValue": _cache[21] || (_cache[21] = $event => ((draft.value.save_path) = $event)),
                  label: "下载保存路径（留空沿用下载器）",
                  variant: "outlined",
                  "hide-details": ""
                }, null, 8, ["modelValue"]),
                _createVNode(_component_v_text_field, {
                  modelValue: draft.value.qb_category,
                  "onUpdate:modelValue": _cache[22] || (_cache[22] = $event => ((draft.value.qb_category) = $event)),
                  label: "qB 分类",
                  variant: "outlined",
                  "hide-details": ""
                }, null, 8, ["modelValue"]),
                _createVNode(_component_v_text_field, {
                  modelValue: draft.value.delete_except_tags,
                  "onUpdate:modelValue": _cache[23] || (_cache[23] = $event => ((draft.value.delete_except_tags) = $event)),
                  label: "禁止清理的标签（逗号分隔）",
                  variant: "outlined",
                  "hide-details": ""
                }, null, 8, ["modelValue"]),
                _createVNode(_component_v_text_field, {
                  modelValue: draft.value.include,
                  "onUpdate:modelValue": _cache[24] || (_cache[24] = $event => ((draft.value.include) = $event)),
                  label: "标题包含（正则）",
                  variant: "outlined",
                  "hide-details": ""
                }, null, 8, ["modelValue"]),
                _createVNode(_component_v_text_field, {
                  modelValue: draft.value.exclude,
                  "onUpdate:modelValue": _cache[25] || (_cache[25] = $event => ((draft.value.exclude) = $event)),
                  label: "标题排除（正则）",
                  variant: "outlined",
                  "hide-details": ""
                }, null, 8, ["modelValue"]),
                _createVNode(_component_v_text_field, {
                  modelValue: draft.value.active_time_range,
                  "onUpdate:modelValue": _cache[26] || (_cache[26] = $event => ((draft.value.active_time_range) = $event)),
                  label: "允许运行时段（如 08:00-23:00）",
                  variant: "outlined",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]))
            : _createCommentVNode("", true)
        ]))
      : _createCommentVNode("", true),
    _createElementVNode("footer", _hoisted_21, [
      _cache[37] || (_cache[37] = _createElementVNode("span", null, "分站保种与清理规则保持不变", -1)),
      _createElementVNode("div", null, [
        _createVNode(_component_v_btn, {
          variant: "text",
          onClick: _cache[27] || (_cache[27] = $event => (emit('cancel'))),
          disabled: __props.saving
        }, {
          default: _withCtx(() => [...(_cache[36] || (_cache[36] = [
            _createTextVNode("取消修改", -1)
          ]))]),
          _: 1
        }, 8, ["disabled"]),
        _createVNode(_component_v_btn, {
          color: "primary",
          type: "submit",
          loading: __props.saving,
          "prepend-icon": "mdi-check"
        }, {
          default: _withCtx(() => [
            _createTextVNode("保存" + _toDisplayString(__props.section==='sites'?'站点范围':'策略'), 1)
          ]),
          _: 1
        }, 8, ["loading"])
      ])
    ])
  ], 32))
}
}

};

export { _sfc_main as _, sizeText as a, stateText as b, createClient as c, speedText as s, timeText as t };
