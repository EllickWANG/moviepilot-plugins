export const sizeText = value => {
  if(value === null || value === undefined) return '—';
  let n=Number(value)||0, i=0; const units=['B','KiB','MiB','GiB','TiB'];
  while(n>=1024 && i<4){n/=1024;i++}
  return `${n.toFixed(i>1?2:0)} ${units[i]}`;
};
export const speedText = value => value == null ? '—' : `${sizeText(value)}/s`;
export const timeText = value => value ? new Date(value*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '尚未运行';
export const stateText = value => ({downloading:'下载中',waiting:'等待连接',seeding:'做种中',paused:'已暂停',error:'异常'}[value]||'待确认');
export function draftOf(c={}) {
  const range=(v,f)=>String(v||f).split('-').map(Number);
  const [sizeMin,sizeMax]=range(c.size,'1-40'),[seedsMin,seedsMax]=range(c.seeder,'2-500');
  return {...c,brushsites:[...(c.brushsites||[])],sizeMin,sizeMax:sizeMax??sizeMin,seedsMin:seedsMax===undefined?0:seedsMin,seedsMax:seedsMax??seedsMin,
    ageHours:c.pubtime ? Number(String(c.pubtime).split('-').at(-1))/60 : null,
    maxdlcount:c.maxdlcount||8,site_download_cap:c.site_download_cap||2,site_cycle_cap:c.site_cycle_cap||1,
    min_peers:c.min_peers??2,disksize:c.disksize||500,maxupspeed:c.maxupspeed??null,
    demand_priority:c.demand_priority??true,cron:c.cron||'*/10 * * * *'};
}
export function changesOf(d, section='all') {
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
export function mergeConfig(current, changes) {
  // One-shot actions and destructive legacy switches are never triggered by saving this UI.
  return {...current,...changes,onlyonce:false,diagnostic_once:false,clear_task:false};
}
