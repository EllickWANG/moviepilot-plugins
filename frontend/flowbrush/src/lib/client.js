import {mergeConfig} from './model';
export function createClient(api,id='FlowBrush') {
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
