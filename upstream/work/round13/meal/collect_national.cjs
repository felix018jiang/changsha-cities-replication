// Public directory snapshot. The service's own read-only query client is loaded from its published page.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const sourceDir = path.join(__dirname, 'sources');
const file = path.join(sourceDir, 'national_changsha.json');
const detailFile = path.join(sourceDir, 'national_service.json');
const base = 'https://yanglao.mca.gov.cn/#/index/foodAreaList';
const districtCodes = ['430102','430103','430104','430105','430111','430112','430121','430181','430182','430191'];
const nap = ms => new Promise(resolve => setTimeout(resolve, ms));
function save(name, value) {
  fs.writeFileSync(name+'.new',JSON.stringify(value,null,2)+'\n');
  fs.renameSync(name+'.new',name);
}
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage();
    await page.goto(base,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.body.innerText.includes('助餐点层级'),null,{timeout:30000});
    await page.evaluate(()=>{
      let webpack;
      window.webpackJsonp.push([['publicdirectoryread'],{publicdirectoryread:(m,e,r)=>{webpack=r}},[['publicdirectoryread']]]);
      window.mealQueries=webpack('81d4');
    });
    if(process.argv.includes('--regions')){
      const regions=await page.evaluate(async()=>{
        let moduleLoader;
        window.webpackJsonp.push([['publicregionsread'],{publicregionsread:(m,e,r)=>{moduleLoader=r}},[['publicregionsread']]]);
        const api=moduleLoader('1a78').b;
        const children=async entry=>(await api(entry.code.split(':')[0])).data;
        const province=(await api(-1)).data.find(r=>r.name==='湖南省');
        const changsha=(await children(province)).find(r=>r.name==='长沙市');
        const result=[];
        for(const district of await children(changsha)){
          const localities=await children(district);
          result.push({id:district.code.split(':').pop(),name:district.name,children:localities.map(r=>({id:r.code.split(':').pop(),name:r.name}))});
        }
        return result;
      });
      save(path.join(sourceDir,'official_regions.json'),regions);
      return;
    }
    async function call(args) {
      for(let attempt=0;attempt<5;attempt++){
        try {
          return await page.evaluate(async({kind,params})=>{
            let response;
            try{response=await window.mealQueries[kind](params);}
            catch(e){throw Error('HTTP '+(e.response?.status||'network')+' Retry-After '+(e.response?.headers?.['retry-after']||'unspecified'));}
            const {data}=response;
            if(kind==='i'){
              return {total:data.total,current:data.current,size:data.size,rows:data.records.map(r=>({
                id:r.ahbx4001,name:r.ahbx4002,address:r.ahbx4008,area_code:r.areaCode,
                area_name:r.areaCodeNameFiveLevel,point:r.ahbx4015,registered_date_field:r.axbe0017,tags:r.tag
              }))};
            }
            return {seats:data.ahbx4119,service_mode:data.ahbx4106,expense_per_meal:data.ahbx4019};
          },args);
        } catch(error) {if(attempt===4)throw error;
          const header=String(error).match(/Retry-After (\d+)/);
          const delay=header?1000*Number(header[1]):String(error).includes('429')?15000*(attempt+1):500*(attempt+1);
          await nap(delay);}
      }
    }
    async function pool(tasks,consumer,n=2){const work=[...tasks];await Promise.all(Array.from({length:n},async()=>{while(work.length){await consumer(work.shift());await nap(400);}}));}
    if(process.argv.includes('--services')){
      const entries=JSON.parse(fs.readFileSync(file)).records;
      const focusPath=path.join(sourceDir,'focus.json');
      const focus=fs.existsSync(focusPath)?JSON.parse(fs.readFileSync(focusPath)):[];
      const byId=new Map(entries.map(r=>[r.id,r]));
      for(const r of focus)if(r.record&&!byId.has(r.id))byId.set(r.id,r.record);
      const details=fs.existsSync(detailFile)?JSON.parse(fs.readFileSync(detailFile)):{};
      const priorityFile=path.join(__dirname,'service_priority_ids.json');
      const priority=fs.existsSync(priorityFile)?JSON.parse(fs.readFileSync(priorityFile)):[];
      const order=new Map(priority.map((id,i)=>[id,i]));
      for(const r of focus)if(r.service?.ok&&!details[r.id]?.ok)details[r.id]=r.service;
      const missing=[...byId.values()].filter(r=>!details[r.id]?.ok && (process.argv.includes('--prioritized-only')?order.has(r.id):(r.point||order.has(r.id)))).sort((a,b)=>(order.get(a.id)??1e7)-(order.get(b.id)??1e7));
      await pool(missing,async r=>{
        try{const value=await call({kind:'j',params:{ahbx4001:r.id}});details[r.id]={ok:true,...value};}
        catch(e){details[r.id]={ok:false,error:String(e)};}
        save(detailFile,details);
      },2);
      return;
    }
    const cityFirst=await call({kind:'i',params:{current:1,size:10,areaCode:'430100000000'}});
    const checkpoint=fs.existsSync(file)?JSON.parse(fs.readFileSync(file)):{records:[],queries:[]};
    const store=new Map(checkpoint.records.map(r=>[r.id,r]));const queries=checkpoint.queries;let partial=[];
    const focusedPath=path.join(sourceDir,'focus.json');
    if(fs.existsSync(focusedPath)){
      for(const item of JSON.parse(fs.readFileSync(focusedPath)))if(item.record&&!store.has(item.id))store.set(item.id,item.record);
    }
    async function getPage(areaCode,current){
      const r=await call({kind:'i',params:{current,size:10,areaCode}});
      const digits=areaCode.slice(6,9)==='000'?6:9;
      if(!r.rows.every(v=>v.area_code?.startsWith(areaCode.slice(0,digits))))throw Error('Area filter failed '+areaCode);
      for(const row of r.rows)store.set(row.id,row);
      return r;
    }
    if(process.argv.includes('--repair')){
      const dir=new Map(queries.filter(q=>q.area_code&&q.total!==undefined).map(q=>[q.area_code,q.total]));
      const towns=new Map(queries.filter(q=>q.street&&q.total!==undefined).map(q=>[q.street,q.total]));
      const count=(area)=>[...store.values()].filter(r=>r.area_code===area).length;
      for(const size of [6,7,8,9]){
        const pending=[...towns].filter(([c,n])=>count(c)<n);
        if(!pending.length)break;
        for(const [areaCode,total] of pending){
          for(let current=1; current<=Math.ceil(total/size)&&count(areaCode)<total; current++){
            try{
              const r=await call({kind:'i',params:{current,size,areaCode}});
              if(r.total!==total||!r.rows.every(v=>v.area_code===areaCode))continue;
              for(const v of r.rows)store.set(v.id,v);
            }catch(e){queries.push({street_retry:areaCode,page:current,size,error:String(e)});}
            await nap(400);
          }
        }
        save(file,{source:base,retrieved_at_utc:new Date().toISOString(),city_code:'430100000000',city_total:cityFirst.total,queries,records:[...store.values()].sort((a,b)=>a.id.localeCompare(b.id))});
        console.log('repair',size,store.size,dir.size);
      }
      return;
    }
    async function district(code){
      const areaCode=code.padEnd(12,'0'); const ids=new Set();
      const first=await getPage(areaCode,1);
      first.rows.forEach(r=>ids.add(r.id));const pages=Math.ceil(first.total/10);
      const failures=[];
      await pool(Array.from({length:Math.max(0,pages-1)},(_,i)=>i+2),async n=>{
        try{let r=await getPage(areaCode,n);r.rows.forEach(a=>ids.add(a.id));}
        catch(e){failures.push({page:n,error:String(e)});}
      },2);
      queries.push({area_code:areaCode,total:first.total,pages,unique:ids.size,failures});
      console.log(code,'directory total',first.total,'distinct records',ids.size);
      save(file,{source:base,retrieved_at_utc:new Date().toISOString(),city_code:'430100000000',city_total:cityFirst.total,queries,records:[...store.values()]});
      if(ids.size<first.total)partial.push(code);
    }
    for(const d of districtCodes){
      const done=queries.find(v=>v.area_code===d.padEnd(12,'0')&&v.total!==undefined);
      if(done){if(done.unique<done.total)partial.push(d);continue;}
      try{await district(d);}catch(e){console.error('district',d,String(e));}
    }
    // Sorting is not stable for records that share a district/rank. Repeat in known street/town
    // strata when ordinary city/district page traversal did not recover all listed ids.
    for(const code of partial){
      const tree=JSON.parse(fs.readFileSync(path.join(sourceDir,'official_regions.json')));
      const known=tree.find(v=>v.id.startsWith(code))?.children.map(v=>v.id)||[];
      const subcodes=[...new Set([...known,...[...store.values()].filter(r=>r.area_code.startsWith(code)).map(r=>r.area_code)])];
      for(const areaCode of subcodes){
        if(areaCode===code.padEnd(12,'0'))continue;
        try{
          const r=await getPage(areaCode,1);const n=Math.ceil(r.total/10);
          queries.push({street:areaCode,total:r.total});
          if(n>1){await pool(Array.from({length:n-1},(_,i)=>i+2),async k=>{try{await getPage(areaCode,k)}catch(e){queries.push({area_code:areaCode,failed_page:k});}});}
        }catch(e){queries.push({area_code:areaCode,error:String(e)});}
      }
      console.log('post-street',code,[...store.values()].filter(r=>r.area_code.startsWith(code)).length);
      save(file,{source:base,retrieved_at_utc:new Date().toISOString(),city_code:'430100000000',city_total:cityFirst.total,queries,records:[...store.values()]});
    }
    save(file,{source:base,retrieved_at_utc:new Date().toISOString(),city_code:'430100000000',city_total:cityFirst.total,queries,records:[...store.values()].sort((a,b)=>a.id.localeCompare(b.id))});
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
