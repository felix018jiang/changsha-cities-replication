"""Rebuild the Changsha meal register and qualified destination scenarios from frozen public responses."""
from collections import Counter
from pathlib import Path
import ast
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
import unicodedata

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / 'work/round5/evidence/meal'
OFFICIAL = ROOT / 'work/round4/services/official_M144_admin_audit.csv'
PAGE = 'https://yanglao.mca.gov.cn/#/index/foodAreaDetail?ahbx4001='
CRS = Transformer.from_crs('EPSG:4326', 'EPSG:32649', always_xy=True)


def norm(value):
    return re.sub(r'[\s，,/－\-]', '', unicodedata.normalize('NFKC', str(value or '')))


def address(value):
    # Retain house/building numbers; remove only editorial prefixes that the two publishers insert.
    a = norm(value)
    return re.sub(r'^(湖南省)?(长沙市)?(湘江新区|芙蓉区|开福区|雨花区|岳麓区|天心区|望城区|长沙县|宁乡市|浏阳市)?', '', a)


def matching_address(a, b):
    a, b = address(a), address(b)
    return bool(min(len(a), len(b)) >= 5 and (a in b or b in a))


def positive_seats(seats):
    return isinstance(seats,(int,float)) and seats > 0


# Address comparisons reviewed from the two text fields; large offsets are retained in the audit.
ANCHOR_REASONS = {
    '长望社区善孝堂银龄餐厅': '谷山乐园D10栋1–4号门面',
    '白鹤咀社区银龄餐厅': '沁园春御院10栋一楼',
    '圭塘街道升达社区老年食堂': '欧亚达家居建材1栋1门7楼',
    '圭塘街道体院路社区老年食堂': '体院路303号体院巷公交站旁',
    '同升街道景环社区老年食堂': '新兴路富春山小区3栋底层',
    '同升街道新聚园社区老年食堂': '时代阳光大道粟塘小区新聚园社区',
    '浏阳市淮川街道北园社区老年助餐点': '北园社区金沙中路一巷9号',
    '雷锋街道亿汇银龄餐厅': '雷锋街道桥头家苑商业街5栋',
}


def main():
    data = json.loads((HERE/'sources/national_changsha.json').read_text())
    focus=json.loads((HERE/'sources/focus.json').read_text())
    by_id={r['id']:r for r in data['records']}
    for item in focus:
        if item.get('record'):
            by_id.setdefault(item['id'],item['record'])
    records = pd.DataFrame(by_id.values()).fillna('')
    assert records.id.is_unique and records.area_code.str.startswith('4301').all()
    services = json.loads((HERE/'sources/national_service.json').read_text())
    for item in focus:
        if item.get('service') and not services.get(item['id'],{}).get('ok'):
            services[item['id']]=item['service']
    districts = gpd.read_file(ROOT/'work/round2/analysis/district_boundaries.gpkg').set_index('adcode')
    districts.index = districts.index.astype(str)
    support = gpd.read_file(ROOT/'work/round4/spatial/data_support_domain.gpkg',layer='support_domain').geometry.iloc[0].buffer(1600)
    late = pd.read_csv(OLD/'icswb94_name_address_crosswalk.csv',keep_default_na=False)
    original = pd.read_csv(OFFICIAL,keep_default_na=False)
    oldrows=[]
    for r in original.itertuples():
        oldrows.append(dict(old_id=r.record_id,reference='municipal144',name=r.name,address=r.address,
                            lon=r.lon_wgs84,lat=r.lat_wgs84))
    for r in late.itertuples():
        oldrows.append(dict(old_id=r.source_record_id,reference='newspaper94',name=r.source_name,
                            address=r.source_address,lon=r.lon_wgs84,lat=r.lat_wgs84))
    refs=pd.DataFrame(oldrows)
    targets=gpd.read_file(ROOT/'work/round7/analysis/routing_targets.gpkg',layer='targets')
    main_m=json.loads((ROOT/'work/round7/analysis/evidence_sets.json').read_text())['uniform_updated']['M']
    r7=targets.loc[(targets.function=='M') & targets.record_id.isin(main_m)].copy()
    assert len(r7)==76
    r7_equal = set(r7['name'].map(norm))
    ns={'np':np}
    source=ROOT/'work/round2/process_sources.py'
    tree=ast.parse(source.read_text())
    funcs=[f for f in tree.body if isinstance(f,ast.FunctionDef) and f.name in ['gcj_to_wgs','bd_to_wgs']]
    exec(compile(ast.Module(body=funcs,type_ignores=[]),str(source),'exec'),ns)
    records['name_key']=records.name.map(norm)
    refs['name_key']=refs.name.map(norm)
    rows=[];audit=[]
    for rec in records.itertuples():
        detail=services.get(rec.id,{})
        seats=detail.get('seats')
        point_text=(rec.point or '').strip()
        loc=None; lon=None; lat=None
        try:
            a,b=map(float,point_text.split(','))
            if 109<=a<=116 and 26<=b<=31:
                lon,lat=a,b
                loc=Point(*CRS.transform(lon,lat))
        except (ValueError,TypeError):
            pass
        if not loc:
            district_off=None
        elif rec.area_code[:6] in districts.index:
            district_off=loc.distance(districts.loc[rec.area_code[:6],'geometry'])
        else:
            # The portal's separate High-Tech Zone spans Yuelu and Wangcheng.
            district_off=min(loc.distance(districts.loc[c,'geometry']) for c in ['430104','430112'])
        in_scope=bool(loc and support.covers(loc))
        old=refs[refs.name_key==rec.name_key]
        # Distinct current sources are not merged by fuzzy place matching. The crosswalk below
        # retains same-name rows and exact street addresses to support independent inspection.
        spatial_warning=False
        for ref in old.itertuples():
            address_anchor=ANCHOR_REASONS.get(rec.name,'') if ref.reference=='newspaper94' else ''
            raw_match=matching_address(rec.address,ref.address) or bool(address_anchor)
            distances={}
            if loc and ref.lon!='' and ref.lat!='':
                oldpoint=Point(*CRS.transform(float(ref.lon),float(ref.lat)))
                for label,coords in [('direct', (lon,lat)),('GCJ',ns['gcj_to_wgs'](lon,lat)),('BD',ns['bd_to_wgs'](lon,lat))]:
                    distances[label+'_m']=Point(*CRS.transform(*coords)).distance(oldpoint)
            if raw_match and distances.get('direct_m',0)>400:
                spatial_warning=True
            audit.append(dict(national_id=rec.id,reference=ref.reference,reference_id=ref.old_id,name=rec.name,
                              national_address=rec.address,reference_address=ref.address,address_concordance=raw_match,manual_anchor=address_anchor,**distances))
        content=' '.join(str(v or '')for v in [rec.name,rec.tags,detail.get('service_mode')])
        non_walk=bool(re.search('仅送餐|仅配送|纯送餐|只送餐|纯配送|仅限内部|不对外',content))
        explicit_takeout=bool(re.search('自提|到店取餐|到点取餐|到站取餐|堂食',content))
        served=positive_seats(seats) or explicit_takeout
        mode=('delivery_or_internal_only' if non_walk else 'on_site_eating_or_pickup_label' if explicit_takeout
              else 'on_site_seating_reported' if positive_seats(seats)
              else 'zero_seats_pickup_unknown' if detail.get('ok') and seats==0
              else 'mode_unresolved')
        located=bool(loc and district_off <= 1000)
        rows.append(dict(record_id='mca:'+rec.id,name=rec.name,address=rec.address,area_code=rec.area_code,
                         area=rec.area_name,registered_date_field=rec.registered_date_field,tags=rec.tags,
                         detail_status='returned' if detail.get('ok') else 'unavailable',seats=seats,
                         service_mode=detail.get('service_mode'),access_evidence=mode,
                         current_public_open_status='not_independently_verified',point_raw=point_text,
                         longitude_wgs84=lon,latitude_wgs84=lat,x_utm49n=loc.x if loc else None,y_utm49n=loc.y if loc else None,
                         reported_district_offset_m=district_off,within_support1600=in_scope,
                         located_in_district_tolerance=located,cross_source_coordinate_review=spatial_warning,
                         old_municipal_ids='|'.join(old.loc[old.reference=='municipal144','old_id']),
                         old_newspaper_ids='|'.join(old.loc[old.reference=='newspaper94','old_id']),
                         same_full_name_in_r7=norm(rec.name) in r7_equal,
                         within_source_name_count=int((records.name_key==rec.name_key).sum()),
                         direct_on_site_evidence=served and not non_walk,explicit_nonwalk=non_walk,
                         evidence_id=rec.id,source_detail=PAGE+rec.id))
    register=pd.DataFrame(rows)
    # Reused point across unrelated street addresses: keep for wide scenario, not the strict case.
    known=register.loc[register.point_raw.ne('')].copy()
    unrelated=known.groupby('point_raw').address.apply(lambda s:len(set(address(v) for v in s if address(v)))>1)
    register['shared_point_distinct_addresses']=register.point_raw.map(unrelated).eq(True)
    # The same exact name and same address with conflicting coordinates cannot represent multiple facilities.
    register['exact_identity_conflicting_points']=False
    for (n,addr),group in register.loc[register.located_in_district_tolerance].groupby(
        [register.name.map(norm),register.address.map(address)]):
        if not addr or len(group)<2:continue
        coords=group[['x_utm49n','y_utm49n']].to_numpy()
        if np.max(np.linalg.norm(coords[:,None]-coords[None,:],axis=2))>100:
            register.loc[group.index,'exact_identity_conflicting_points']=True
    register['included_wide']=(register.located_in_district_tolerance & ~register.explicit_nonwalk &
                               ~register.exact_identity_conflicting_points)
    from_directory=HERE.parent/'rules'
    sys.path.insert(0,str(from_directory))
    from review_action_sites import venue_roster
    site_decisions=pd.read_csv(from_directory/'action_match_decisions.csv')
    site_coords=venue_roster().drop_duplicates('site_id')[['site_id','x_utm49n','y_utm49n']]
    matches=site_decisions[site_decisions.entity_review=='same_venue'].merge(site_coords,on='site_id',validate='many_to_one')
    matches=matches.merge(register[['evidence_id','name','x_utm49n','y_utm49n']],left_on='national_id',right_on='evidence_id',
                          suffixes=('_venue','_national'),validate='many_to_one')
    matches['matched_venue_distance_m']=np.hypot(matches.x_utm49n_venue-matches.x_utm49n_national,
                                                matches.y_utm49n_venue-matches.y_utm49n_national)
    matches.to_csv(HERE/'reviewed_venue_spatial_offsets.csv',index=False)
    adverse_ids=matches.loc[matches.matched_venue_distance_m>400,'national_id']
    register['matched_venue_coordinate_conflict']=register.evidence_id.isin(adverse_ids)
    aliases=pd.read_csv(HERE/'reviewed_r7_aliases.csv')
    r7_coords=r7.set_index('record_id').geometry
    alias_offsets={}
    for pair in aliases.itertuples():
        rows=register[register.evidence_id==pair.national_id]
        if rows.empty or pd.isna(rows.iloc[0].x_utm49n): continue
        point=Point(rows.iloc[0].x_utm49n,rows.iloc[0].y_utm49n)
        alias_offsets[pair.national_id]=point.distance(r7_coords.loc[pair.r7_record_id])
    register['matched_r7_alias_distance_m']=register.evidence_id.map(alias_offsets)
    register['matched_r7_alias_conflict']=register.matched_r7_alias_distance_m.gt(400)
    register['dated_access_flag']=register.evidence_id.eq('7b41dc1667d34e3a97f7afd012b90d2e')
    register['included_primary']=(register.included_wide & ~register.dated_access_flag & register.direct_on_site_evidence & ~register.matched_venue_coordinate_conflict & ~register.matched_r7_alias_conflict &
                                  ~register.shared_point_distinct_addresses & ~register.cross_source_coordinate_review)
    register.to_csv(HERE/'national_register.csv',index=False)
    register.loc[register.included_wide].to_csv(HERE/'routing_points.csv',index=False)
    priority_ids=list(dict.fromkeys([item['id'] for item in focus]+site_decisions.loc[
        site_decisions.entity_review=='same_venue','national_id'].tolist()+register.loc[
        register.within_support1600,'evidence_id'].tolist()))
    (HERE/'service_priority_ids.json').write_text(json.dumps(priority_ids)+'\n')
    cross=pd.DataFrame(audit)
    cross.to_csv(HERE/'old_source_crosswalk.csv',index=False)
    support_df=register[register.within_support1600]
    numeric=register.longitude_wgs84.notna()
    refstats={}
    for (ref,agree),g in cross.groupby(['reference','address_concordance']):
        valid=g.dropna(subset=['direct_m'])
        if len(valid):
            refstats[ref+'_'+str(agree)]={'n':len(valid),**{col:round(float(valid[col].median()),1) for col in ['direct_m','GCJ_m','BD_m']}}
    day=datetime.fromisoformat(data.get('retrieved_at_utc','2026-11-08T00:00:00+00:00').replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    info={'retrieved_local_date':day,'portal_display_total':data['city_total'],'distinct_city_ids_recovered':len(register),
          'district_totals':{q['area_code']:q['total'] for q in data['queries'] if 'total'in q and 'area_code' in q},
          'missing_coordinate':int(register.point_raw.eq('').sum()),'nonempty_implausible_coordinate':int((~numeric & register.point_raw.ne('')).sum()),'with_numeric_plausible_point':int(numeric.sum()),'city_located_after_district_screen':int(register.located_in_district_tolerance.sum()),
          'references_same_name_paired_crs_median_m':refstats,'within_1600m':len(support_df),
          'within_1600m_positive_seats_or_explicit_pickup':int(support_df.direct_on_site_evidence.sum()),
          'within_1600m_service_detail_returned':int((support_df.detail_status=='returned').sum()),
          'within_1600m_included_primary':int(support_df.included_primary.sum()),
          'within_1600m_included_wide':int(support_df.included_wide.sum()),
          'area_eligible_distinct_primary_coordinates':len(support_df[support_df.included_primary].point_raw.unique()),
          'support_modes':support_df.access_evidence.value_counts().to_dict(),
          'district_counts_recovered':register.area_code.str[:6].value_counts().to_dict()}
    (HERE/'directory_audit.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(info,ensure_ascii=False))
if __name__=='__main__':
    main()
