<script setup>
import {ref,watch,computed} from 'vue';
import {draftOf,changesOf} from '../lib/model';
const props=defineProps({initial:{type:Object,default:()=>({})},sites:{type:Array,default:()=>[]},downloaders:{type:Array,default:()=>[]},section:{type:String,default:'all'},saving:Boolean});
const emit=defineEmits(['save','cancel']);
const draft=ref(draftOf(props.initial)),error=ref(''),advanced=ref(false),query=ref('');
watch(()=>props.initial,v=>{draft.value=draftOf(v);error.value='';});
const filtered=computed(()=>props.sites.filter(s=>s.name.toLowerCase().includes(query.value.toLowerCase())));
const includesStrategy=computed(()=>props.section!=='sites');
const includesSites=computed(()=>props.section!=='strategy');
function save(){try{error.value='';emit('save',changesOf(draft.value,props.section));}catch(e){error.value=e.message;}}
</script>
<template>
<form class="fb-editor" @submit.prevent="save">
  <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{error}}</v-alert>
  <template v-if="includesStrategy">
    <section class="fb-panel"><div class="fb-section-title"><div><span class="fb-eyebrow">01 / 运行</span><h3>让刷流按计划运行</h3><p>开启后按周期选种，已有任务按分站保种规则维护。</p></div><v-switch v-model="draft.enabled" label="自动刷流" color="primary" hide-details inset /></div>
      <div class="fb-fields"><v-select v-model="draft.downloader" :items="downloaders" label="下载器" variant="outlined" hide-details/><v-select v-model="draft.cron" :items="[{title:'每 10 分钟',value:'*/10 * * * *'},{title:'每 20 分钟',value:'*/20 * * * *'},{title:'每 30 分钟',value:'*/30 * * * *'},{title:'每小时',value:'0 * * * *'},...(!['*/10 * * * *','*/20 * * * *','*/30 * * * *','0 * * * *'].includes(draft.cron)?[{title:'自定义：'+draft.cron,value:draft.cron}]:[])]" label="选种周期" variant="outlined" hide-details/><v-switch v-model="draft.notify" label="运行通知" color="primary" hide-details /></div>
    </section>
    <section class="fb-panel"><div class="fb-section-title"><div><span class="fb-eyebrow">02 / 容量</span><h3>给不同站点留出名额</h3><p>等待任务也占名额。降低上限只影响新增，已有任务继续运行。</p></div><v-icon icon="mdi-tune-variant"/></div>
      <div class="fb-fields fb-fields-four"><v-text-field v-model="draft.maxdlcount" type="number" label="总下载上限" suffix="个" variant="outlined" hide-details/><v-text-field v-model="draft.site_download_cap" type="number" label="单站同时下载" suffix="个" variant="outlined" hide-details/><v-text-field v-model="draft.site_cycle_cap" type="number" label="单站每轮新增" suffix="个" variant="outlined" hide-details/><v-text-field v-model="draft.disksize" type="number" label="保种空间上限" suffix="GiB" variant="outlined" hide-details/></div>
      <div class="fb-inline-field"><v-text-field v-model="draft.maxupspeed" type="number" label="上传达到此速度时停止新增" suffix="KiB/s" variant="outlined" hide-details/><p>控制新增时机，不修改下载器的限速。</p></div>
    </section>
    <section class="fb-panel"><div class="fb-section-title"><div><span class="fb-eyebrow">03 / 选种</span><h3>有种可下，有人需要</h3><p>先筛选，再按下载人数与做种人数之比排序。</p></div><v-switch v-model="draft.demand_priority" label="需求优先" color="primary" hide-details inset/></div>
      <div class="fb-fields"><v-text-field v-model="draft.ageHours" type="number" label="发布时间不超过" suffix="小时" variant="outlined" hide-details/><v-text-field v-model="draft.min_peers" type="number" label="至少有多少人下载" suffix="人" variant="outlined" hide-details/><v-select v-model="draft.freeleech" :items="[{title:'只选免费',value:'free'},{title:'双倍上传且免费',value:'2xfree'},{title:'不限促销',value:''}]" label="下载促销" variant="outlined" hide-details/></div>
      <div class="fb-fields fb-fields-four mt-5"><v-text-field v-model="draft.sizeMin" type="number" label="最小体积" suffix="GiB" variant="outlined" hide-details/><v-text-field v-model="draft.sizeMax" type="number" label="最大体积" suffix="GiB" variant="outlined" hide-details/><v-text-field v-model="draft.seedsMin" type="number" label="最少做种人数" suffix="人" variant="outlined" hide-details/><v-text-field v-model="draft.seedsMax" type="number" label="最多做种人数" suffix="人" variant="outlined" hide-details/></div>
      <v-switch v-model="draft.hr" true-value="yes" false-value="no" label="跳过标有 H&R 的新种" color="primary" hide-details class="mt-4"/>
      <p class="fb-note">列表未标记 H&R 不代表没有保种要求；已有任务继续遵循各站保种规则。</p>
    </section>
  </template>
  <section v-if="includesSites" class="fb-panel"><div class="fb-section-title"><div><span class="fb-eyebrow">站点范围</span><h3>选择允许新增的站点</h3><p>取消选择只停止新增，保留该站已有任务与保种规则。</p></div><span class="fb-pill">已选 {{draft.brushsites.length}} 站</span></div>
    <v-text-field v-model="query" label="搜索站点" prepend-inner-icon="mdi-magnify" variant="outlined" density="compact" hide-details class="mb-4"/>
    <div class="fb-site-picker"><label v-for="site in filtered" :key="site.id" class="fb-pick" :class="{selected:draft.brushsites.includes(site.id)}"><input type="checkbox" v-model="draft.brushsites" :value="site.id"/><span>{{site.name}}</span><small>{{site.policy?.seed_hours?`做种 ${site.policy.seed_hours} 小时`:'保留现有规则'}}</small></label></div>
    <p v-if="!filtered.length" class="fb-empty">没有匹配的站点</p>
  </section>
  <section v-if="includesStrategy" class="fb-panel fb-advanced"><button type="button" class="fb-accordion" :aria-expanded="advanced" @click="advanced=!advanced"><span><v-icon icon="mdi-cog-outline" size="20"/>高级设置<small>路径、标签与匹配条件</small></span><v-icon :icon="advanced?'mdi-chevron-up':'mdi-chevron-down'"/></button>
    <div v-if="advanced" class="fb-fields mt-5"><v-text-field v-model="draft.save_path" label="下载保存路径（留空沿用下载器）" variant="outlined" hide-details/><v-text-field v-model="draft.qb_category" label="qB 分类" variant="outlined" hide-details/><v-text-field v-model="draft.delete_except_tags" label="禁止清理的标签（逗号分隔）" variant="outlined" hide-details/><v-text-field v-model="draft.include" label="标题包含（正则）" variant="outlined" hide-details/><v-text-field v-model="draft.exclude" label="标题排除（正则）" variant="outlined" hide-details/><v-text-field v-model="draft.active_time_range" label="允许运行时段（如 08:00-23:00）" variant="outlined" hide-details/></div>
  </section>
  <footer class="fb-savebar"><span>分站保种与清理规则保持不变</span><div><v-btn variant="text" @click="emit('cancel')" :disabled="saving">取消修改</v-btn><v-btn color="primary" type="submit" :loading="saving" prepend-icon="mdi-check">保存{{section==='sites'?'站点范围':'策略'}}</v-btn></div></footer>
</form>
</template>
