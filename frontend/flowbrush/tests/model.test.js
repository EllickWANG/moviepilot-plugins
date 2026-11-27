import test from 'node:test';
import assert from 'node:assert/strict';
import {draftOf,changesOf,mergeConfig,sizeText} from '../src/lib/model.js';
const original={downloader:'test',enabled:true,notify:true,cron:'*/10 * * * *',brushsites:[1,2],maxdlcount:8,site_download_cap:2,site_cycle_cap:1,size:'1-40',seeder:'2-500',pubtime:'0-2880',min_peers:2,demand_priority:true,disksize:500,freeleech:'free',hr:'yes',site_config:'// checked rules\n[]',seed_time:null,hr_seed_time:168,delete_same_content:false,unknown_future_key:42};
test('policy editing preserves verified retention rules and unknown configuration',()=>{
 const d=draftOf(original);d.maxdlcount=9;
 const merged=mergeConfig(original,changesOf(d,'strategy'));
 for(const k of ['site_config','seed_time','hr_seed_time','delete_same_content','unknown_future_key','brushsites'])assert.deepEqual(merged[k],original[k]);
 assert.equal(merged.maxdlcount,9);assert.equal(merged.pubtime,'0-2880');
});
test('saving never triggers stale one-shot or destructive actions',()=>{
 const merged=mergeConfig({...original,onlyonce:true,diagnostic_once:true,clear_task:true},{});
 for(const k of ['onlyonce','diagnostic_once','clear_task'])assert.equal(merged[k],false);
});
test('site-only changes do not rewrite global or retention rules',()=>{
 const d=draftOf(original);d.brushsites=[2];assert.deepEqual(changesOf(d,'sites'),{brushsites:[2]});assert.deepEqual(original.brushsites,[1,2]);
});
test('invalid quota and ranges cannot be submitted',()=>{
 for(const change of [{site_download_cap:9},{site_cycle_cap:3},{sizeMin:50},{min_peers:-1},{seedsMin:0},{ageHours:'bad'},{downloader:''}])assert.throws(()=>changesOf({...draftOf(original),...change}));
});
test('unknown statistics are not presented as zero',()=>{assert.equal(sizeText(null),'—');assert.equal(sizeText(0),'0 B');});
