// Targeted public records for the legacy changes-of-site examples. Store only public service fields.
const fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const records=[
  ['248fdbb05d5e45568484d2f29fc7b309','湖中社区银龄餐厅'],
  ['f26e049005eb494caf310d76c277a0e0','浏正街社区居家养老服务站'],
  ['feb7a7af7d3444198f06e79a29fff421','水风井社区助餐点'],
  ['59efa0fb4a2d4d2ba55b8fd13adf92e3','化龙池社区居家养老服务站'],
];
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const page=await browser.newPage();
    await page.goto('https://yanglao.mca.gov.cn/#/index/foodAreaList',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.body.innerText.includes('助餐点层级'));
    const out=await page.evaluate(async input=>{
      let loader;window.webpackJsonp.push([['focusedPublicMeals'],{focusedPublicMeals:(m,e,r)=>loader=r},[['focusedPublicMeals']]]);
      const api=loader('81d4'), data=[];
      for(const [id,name] of input){
        const item={id,name};
        for(const fn of ['h','j']){
          try{
            const d=(await api[fn]({ahbx4001:id})).data;
            if(fn==='h'){
              if(d.ahbx4001!==id||d.ahbx4002!==name||!d.areaCode.startsWith('4301'))throw Error('Identity filter failed');
              item.record={id,name:d.ahbx4002,address:d.ahbx4008,area_code:d.areaCode,area_name:d.areaCodeNameFiveLevel,point:d.ahbx4015,registered_date_field:d.axbe0017,tags:d.tag};
            }else item.service={ok:true,seats:d.ahbx4119,service_mode:d.ahbx4106,expense_per_meal:d.ahbx4019};
          }catch(e){item[fn+'_error']=String(e);}
          await new Promise(resolve=>setTimeout(resolve,600));
        }
        data.push(item);
      }
      return data;
    },records);
    const file=path.join(__dirname,'sources','focus.json');
    const old=fs.existsSync(file)?JSON.parse(fs.readFileSync(file)):[];
    for(const r of out){const prev=old.find(v=>v.id===r.id);if(prev){r.record=r.record||prev.record;r.service=r.service||prev.service;}}
    fs.writeFileSync(file,JSON.stringify(out,null,2)+'\n');
    console.log(out.map(v=>[v.name,!!v.record,v.service?.seats,v.j_error]));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
