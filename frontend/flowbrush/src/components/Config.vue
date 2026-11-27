<script setup>
import {ref,onMounted} from 'vue';
import {createClient} from '../lib/client';
import PolicyEditor from './PolicyEditor.vue';
import '../styles.css';
const props=defineProps({initialConfig:{type:Object,default:()=>({})},api:{type:Object,default:()=>({})},pluginId:{type:String,default:'FlowBrush'},sourcePluginId:String});
const emit=defineEmits(['save','close','layout','switch']);
const client=createClient(props.api,props.pluginId),data=ref(null),error=ref(''),saving=ref(false);
async function load(){try{data.value=await client.workspace();error.value='';}catch(e){error.value=e.message;}}
async function save(changes){saving.value=true;try{emit('save',await client.merged(changes));}catch(e){error.value=e.message;}finally{saving.value=false;}}
onMounted(()=>{emit('layout',{maxWidth:'72rem'});load();});
</script>
<template><div class="fb-root"><header class="fb-page-title"><div><span class="fb-eyebrow">FLOWBRUSH / SETTINGS</span><h1>刷流策略</h1><p>设置容量、挑选新种、分配站点，一次完成。</p></div></header><v-alert v-if="error" type="error" variant="tonal">{{error}}<v-btn variant="text" @click="load">重试</v-btn></v-alert><PolicyEditor v-if="data" :initial="initialConfig" :sites="data.sites" :downloaders="data.downloaders" :saving="saving" @save="save" @cancel="emit('close')"/><div v-else class="fb-loading"><v-progress-circular indeterminate color="primary"/></div></div></template>
