<script setup>
import {useTheme} from 'vuetify';
import Page from './components/Page.vue';
import {demoApi} from './demo';
const theme=useTheme();
</script>
<template><v-app><v-main><div style="display:flex;justify-content:space-between;padding:12px 28px;align-items:center;font-size:12px">MoviePilot / 刷流 · 隔离演示数据<v-btn variant="text" @click="theme.global.name.value=theme.global.current.value.dark?'light':'dark'">切换主题</v-btn></div><Page :api="demoApi"/></v-main></v-app></template>
<style>html,body,#app{margin:0;min-height:100%;font-family:Inter,"PingFang SC",sans-serif}</style>
