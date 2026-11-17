"""Screen named premises within 720 m of each circle's polygon before routing."""
from pathlib import Path
import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DOI = 'https://doi.org/10.6084/m9.figshare.28468649.v1'
YUELU = 'https://whhlyt.hunan.gov.cn/whhlyt/news/sxxw/202112/t20211228_21325506.html'
HEPLUS = 'https://whhlyt.hunan.gov.cn/whhlyt/ggwh/202007/t20200722_13084269.html'
FOOD = {'restaurant', 'fast_food', 'food_court'}
DATE = '2026-11-12'
TO_METRES = Transformer.from_crs(4326, 32649, always_xy=True)
TO_DEGREES = Transformer.from_crs(32649, 4326, always_xy=True)
cases = pd.read_csv(ROOT/'outputs/长沙养老研究_第十八轮/tables/circle_choice_summary.csv').case_id.unique()
zones = gpd.read_file(ROOT/'work/round4/spatial/exclusive_circle_zones.gpkg').set_index('circle_id').loc[cases]
supports = zones.geometry.buffer(720)
campuses = gpd.read_file(HERE/'campus_footprints.geojson').to_crs(32649)
supplies = pd.read_csv(ROOT/'work/round4/services/uniform_HMS_inventory.csv',low_memory=False)
stations = pd.read_csv(ROOT/'work/round4/services/official_all1196_admin_audit.csv')
stations = stations.loc[stations.category.isin(['社区养老服务设施','养老机构']) | stations.category_code.astype(int).isin([4,6,7])]
stations = stations.rename(columns={'record_id':'ref'})
stations['x'], stations['y'] = TO_METRES.transform(stations.lon_wgs84,stations.lat_wgs84)
activity = supplies.loc[supplies.function.eq('S') & supplies.eligibility_main.eq(1)].copy()
national = pd.read_csv(ROOT/'outputs/长沙养老研究_第十三轮/tables/national_routing_scope.csv',low_memory=False)
national = national.loc[national.included_wide & national.inside_routing_support]
meal = pd.concat([national, supplies.loc[supplies.function.eq('M') & supplies.eligibility_main.eq(1)]])
service = {f:t.rename(columns={'record_id':'ref','x_utm49n':'x','y_utm49n':'y'}).copy() for f,t in [('M',meal),('S',activity)]}
by_name = pd.read_csv(HERE/'venue_review.csv').set_index('site_name')
audit = []
accepted = []
def text(v):
    return '' if pd.isna(v) else str(v).strip()

def key(n):
    m = re.search(r'([\u4e00-\u9fff]{2,8})社区', n)
    if not m: return ''
    return re.split('街道|区|（|市',m[1])[-1]

def feed(source,site_id,site_name,function,lon,lat,address,url,venue_type,tier,vintage,rule='',base=False,extra='',coordinate_basis=''):
    x,y = TO_METRES.transform(lon,lat); p=Point(x,y)
    circles = supports[supports.intersects(p)].index.to_list()
    if not circles: return
    if not rule and site_name in by_name.index:
        review=by_name.loc[site_name]
        extra=review.source_url
        if review.decision=='existing':
            base=True
            tier=review.site_specific_evidence
        else:
            rule=review.site_specific_evidence
    row = dict(site_id=site_id,site_name=site_name,function=function,lon_wgs84=lon,lat_wgs84=lat,address=text(address),source_url=url,venue_type=venue_type,evidence_tier=tier,source_family=source,source_vintage=vintage,retrieval_date=DATE,existing_service=int(base),scenario='both',secondary_url=extra,coordinate_basis=coordinate_basis,agreement='已有服务' if base else '未核实协议、容量及实施条件')
    reference = ''
    k = key(site_name)
    if not rule:
        for t,label in [(service[function],'同功能登记'),(stations,'养老站点')]:
            ds=np.hypot(t.x-x,t.y-y)
            if not len(ds): continue
            matched=(ds.le(25)) if not base else (ds.le(3))
            if function=='S' and k:
                same=t.name.fillna('').str.contains(k+'社区',regex=False)
                matched=matched | (same & ds.le(150 if not base else 80))
            if matched.any():
                near = t.loc[matched].iloc[np.nanargmin(ds[matched].to_numpy())]
                reference = str(near.ref)+' '+str(near['name'])
                rule='近邻同址或同社区同功能身份待区分，已保留原模型登记（'+label+'）'; break
    if not rule:
        for previous in accepted:
            d=np.hypot(previous['x']-x,previous['y']-y)
            if function!=previous['function']: continue
            identity = re.sub(r'[（(].*|\s','',site_name)==re.sub(r'[（(].*|\s','',previous['site_name'])
            if (identity and d<90) or (('霸碗' in site_name and '霸碗' in previous['site_name']) and d<120) or (function=='S' and k and k==key(previous['site_name']) and d<60):
                rule='重复或近邻同名场所，保留优先来源'; reference=previous['site_id']; break
    row['adjudication']='排除' if rule else ('补入共同现状' if base else '拟增服务')
    row['reason']=rule
    row['crosscheck_reference']=reference
    for cid in circles:
        audit.append(dict(case_id=cid,**row))
    if not rule:
        accepted.append(dict(x=x,y=y,**row))

culture = pd.read_csv(ROOT/'work/round3/evidence/public_culture_candidates.csv')
for r in culture.itertuples():
    conflict='区文化部门公布为雨花侯家塘设施，地图点落入岳麓观沙岭；坐标冲突' if '水院' in r.name else ''
    feed('雨花文旅场馆目录',r.entity_id,r.name,'S',r.lon_wgs84,r.lat_wgs84,r.address,r.source_url,'区级公共文化场馆','目录公开活动房间配置；现行时段待核',str(r.retrieval_date),conflict,True,coordinate_basis='官方百度组件坐标转WGS84；入口未踏勘')

for r in pd.read_csv(HERE/'chain_raw.csv').itertuples():
    reason = '地图点与地址中标注道路位置有显著分歧；具体楼栋未获得独立门牌交叉确认' if r.site_id=='chain:bawan:7ed24f3c077621' else ''
    feed('霸碗运营方门店目录',r.site_id,r.site_name,'M',r.lon_wgs84,r.lat_wgs84,r.address,r.source_url,r.venue_type,r.evidence_tier,DATE,reason,coordinate_basis='运营方百度组件坐标转WGS84；入口未踏勘')

snapshot=json.loads((HERE/'osm_raw.json').read_text())
def osm_type(r):
    return 'M' if r['tags']['amenity'] in FOOD else 'S'

for r in sorted(snapshot['elements'], key=lambda e: (osm_type(e)=='M',e['id']!='10020233824')):
    t=r['tags']; name=t.get('name:zh') or t['name']; source_url=f"https://www.openstreetmap.org/{r['type']}/{r['id']}"
    ident='osm:'+r['type']+':'+r['id']; f=osm_type(r); reason=''; extra=''; base=False
    location=Point(*TO_METRES.transform(r['lon'],r['lat']))
    if not supports.intersects(location).any(): continue
    if f=='M':
        tier='地图标注具名餐饮实体；堂食、营业、适老餐及合作待核'
        if t.get('access','') in ('private','no','permit','students','employees') or t.get('disused','')=='yes' or t.get('takeaway','')=='only' or t.get('indoor_seating','')=='no':
            reason='限制进入、停用、仅外卖或无室内座位'
        elif re.search(r'大学|校区|学生|职工|机关|师大|学院|德智食堂|[AB一二三四五六七八九十0-9]+座?食堂',name) or campuses.intersects(location).any():
            reason='校内或单位食堂；对社区居民开放待核'
        elif re.search(r'公益|敬老|助老|老年|长者|爱心食堂',name):
            reason='可能已有长者或公益供餐，现有服务资格、普适性及年龄边界待核'
            if name=='雨花斋公益互助餐厅': extra='https://ts.voc.com.cn/question/view/2343343.html'
        elif re.search(r'夜宵|深夜|生日蛋糕|饼屋|茶馆|嘻饮力|杯茶|IM茶|鸭脖|临榆炸鸡腿|麻辣鸡架|炸炸炸|烤饼|卤味店|鸭霸王|加哆堡|小串烧烤|烧烤|口口团子|爽吧小吃屋',name) or name in {'烤鱼','麻辣香锅','韩国炸鸡','陈树立','叫了个炸鸡','春风自选店','北京脆皮鸭','德园包点','沙田包子','裕泽元包子','婷婷小吃店'}:
            reason='仅凭名称无法确认主营完整日间餐食、堂食场地或可辨别的具体店铺'
    else:
        tier='社区服务办公场所，有无可供长者共同活动的房间与时段待核'
        if r['id']=='10020233824':
            name='岳麓图书馆（岳麓区文化馆同址）'; extra=YUELU; base=True; tier='政府报道确认公共图书馆与文化馆共址开放及活动功能'
        elif r['id']=='10020233825':
            reason='岳麓图书馆与岳麓文化馆共址，合并为一个既有场址'; extra=YUELU
        elif r['id']=='1516149810':
            extra=HEPLUS; base=True; tier='省文旅厅公布该纯公益共享图书馆及活动记录'
        elif r['id']!='1538945979':
            reason='限定学校或干部人群、商业课程、专门演艺展览，或社区活动场址身份未证实'
    address=t.get('addr:full','') or ''.join(t.get(tag,'') for tag in ['addr:street','addr:housenumber'])
    feed('OpenStreetMap',ident,name,f,r['lon'],r['lat'],address,source_url,t['amenity'],tier,snapshot['timestamp'],reason,base,extra,'WGS84地图点' if r['type']=='node' else 'WGS84面内代表点；非入口')

facilities=gpd.read_file(ROOT/'work/round2/analysis/public_facilities_2023.gpkg')
for i,r in facilities.loc[facilities['类型'].eq('社区公共服务中心')].iterrows():
    name=text(r['设施名称']).split('   ')[0].strip()
    lon,lat=TO_DEGREES.transform(r.geometry.x,r.geometry.y)
    existing=bool(re.search(r'活动室|文化室',name))
    reason=''
    if r['是否开放']!='是': reason='历史记录未标开放'
    elif ((facilities['设施名称']==name) & (facilities['是否开放']=='否') & (facilities.geometry.distance(r.geometry)<=150)).any(): reason='同一调查中同名近邻记录标注未开放，开放状态冲突'
    elif '（旧）' in name: reason='旧址已有新址记录'
    elif not existing and not re.search('公共服务中心|社区服务中心|共服务中心|公共服务设施中心|党群服务中心',name):
        reason='名称为行政或其他专门办事点；可接待的设施类型待核'
    tier = '2023年公共设施调查登记开放；当期可用房间、活动排期及合作待核' if not existing else '2023年调查登记开放的活动室或文化室'
    feed('Fu等社区公共设施调查',f'figshare:centre:{i:05d}',name,'S',lon,lat,'',DOI,'社区公共服务中心',tier,'2023',reason,existing,coordinate_basis='同包配准后的WGS84点；非独立入口测量')

all_rows=pd.DataFrame(audit).sort_values(['case_id','function','source_family','site_id'])
assert set(all_rows.case_id)==set(cases)
all_rows.loc[all_rows.reason.eq('')].to_csv(HERE/'offsite_candidates.csv',index=False,encoding='utf-8-sig')
all_rows.loc[all_rows.reason.ne('')].to_csv(HERE/'excluded_venues.csv',index=False,encoding='utf-8-sig')
summary=all_rows.groupby(['case_id','function','adjudication']).size().unstack(fill_value=0)
summary=summary.reindex(pd.MultiIndex.from_product([sorted(cases),['M','S']],names=['case_id','function']), fill_value=0)
summary.to_csv(HERE/'venue_screen_counts.csv',encoding='utf-8-sig')
print(summary)
print(all_rows.groupby(['source_family','adjudication']).site_id.nunique())
