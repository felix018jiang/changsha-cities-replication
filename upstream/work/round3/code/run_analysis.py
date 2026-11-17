"""Reproducible public-data scenario analysis for H/M/S/G.

Finite distances are conditional model distances, never observed route times.
Origins are 123 frozen OSM residential land-use representative points.
Service counts are source records; no unverified cross-source entity merges.
"""
from pathlib import Path
import argparse
import collections, hashlib, json, math, time
import geopandas as gp
import pandas as pd
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra, connected_components
from shapely.geometry import Point, LineString
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[3]
WORK=ROOT/'work/round3'; A=WORK/'analysis'
OUT=ROOT/'outputs/长沙养老研究_第三轮'
FNS=list('HMSG')
CONFIG={
 'scope':'123 frozen residential land-use representative points in four 1km anchor windows',
 'crs':'EPSG:32649','speeds_m_s':[0.6,0.8,1.0],'thresholds_min':[10,15,20],
 'main_speed_m_s':0.8,'main_threshold_min':15,
 'networks':['point_connection','park_mapped_paths','park_mapped_paths_no_steps'],
 'service_sets':['documented_inventory','original_meal_coords','public_S_only','broad_care_as_S','old_OSM_service_query'],
 'main_network':'park_mapped_paths','main_service_set':'documented_inventory',
 'main_service_meaning':'documented multivintage location/function candidates; no guarantee of current hours/eligibility',
 'restriction_policy':'known foot/access restrictions; foot-specific permission overrides generic restriction; vehicle oneway ignored, oneway:foot respected',
 'connector_policy':'straight line to nearest eligible segment, counted as distance, unverified; no claim of legal access',
 'park_policy':'mapped pedestrian-compatible nodes within polygon; fallback feature representative point if absent',
 'connection_audit_thresholds_m':[25,50,100],
 'renewal_action':'one hypothetical M or S function at mapped community-care coordinates',
 'renewal_constraint':'one added function, not equal monetary cost; candidate feasibility unknown',
 'renewal_objective_order':['reduction_origins_missing_at_least_2','gain_joint_coverage','reduction_missing_functions'],
 'actual_access_state':'U for all origins/functions: entrances, eligibility, network completeness not independently validated',
 'no_claims':['observed walking time','population coverage','city representativeness','capacity sufficiency','verified intervention feasibility']
}

def load_network():
    source=WORK/'raw/residential_network_round3.json'
    data=json.loads(source.read_text()); objects={}
    for e in data['elements']:
        key=(e['type'],e['id']);prev=objects.get(key,{})
        if 'tags' in prev and 'tags' not in e:e=dict(e,tags=prev['tags'])
        objects[key]=e
    nodes={e['id']:e for e in objects.values() if e['type']=='node'}
    tf=Transformer.from_crs(4326,32649,always_xy=True)
    xy={k:tf.transform(n['lon'],n['lat']) for k,n in nodes.items()}
    deny={'no','private','permit','customers','destination'}
    allow={'yes','designated','permissive'}
    def restricted(t):
        # destination/customers retained as conditional paths, not universal bans.
        if t.get('foot') in {'no','private','permit'}:return True
        return t.get('access') in {'no','private','permit'} and t.get('foot') not in allow
    blocked={k for k,n in nodes.items() if restricted(n.get('tags',{}))}
    edges=[];excluded=collections.Counter();waycounts=collections.Counter()
    for e in objects.values():
        t=e.get('tags',{});h=t.get('highway')
        if e['type']!='way' or not h:continue
        reason=None
        if h in {'motorway','motorway_link','trunk','trunk_link','construction','proposed','services'}:reason='nonwalking_road_class'
        elif restricted(t):reason='explicit_access_restriction'
        elif h=='cycleway' and t.get('foot') not in allow:reason='cycleway_without_mapped_foot_permission'
        if reason:excluded[reason]+=1;continue
        direction=t.get('oneway:foot','no')
        waycounts[h]+=1
        for j,(u,v) in enumerate(zip(e['nodes'][:-1],e['nodes'][1:])):
            if u==v or u not in xy or v not in xy or u in blocked or v in blocked:continue
            line=LineString([xy[u],xy[v]])
            if line.length<=0:continue
            edges.append(dict(edge_id=f'{e["id"]}:{j}',way_id=str(e['id']),u=u,v=v,
                              length_m=line.length,highway=h,steps=h=='steps',direction=direction,
                              access=t.get('access',''),foot=t.get('foot',''),
                              conditional=t.get('access:conditional',t.get('foot:conditional','')),
                              bridge=t.get('bridge',''),tunnel=t.get('tunnel',''),layer=t.get('layer',''),
                              geometry=line))
    gdf=gp.GeoDataFrame(edges,crs=32649)
    return gdf,xy,dict(source=str(source.relative_to(ROOT)),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                         timestamp=data['osm3s']['timestamp_osm_base'],blocked_node_count=len(blocked),
                         excluded_ways=dict(excluded),kept_way_types=dict(waycounts))

def augment(edges,xy,origins,facilities):
    """Split parent segments at all projections; parallel edges/steps kept intact."""
    ids=sorted({x for e in edges.itertuples() for x in (e.u,e.v)})
    index={k:i for i,k in enumerate(ids)}; coords=[xy[k] for k in ids]
    snap_rows=[];splits=collections.defaultdict(list);entity_nodes={}
    allpoints=[]
    for r in origins.itertuples(): allpoints.append((r.origin_id,'origin',r.geometry))
    for r in facilities.itertuples():allpoints.append((r.record_id,'facility',r.geometry.representative_point()))
    for key,role,p in allpoints:
        near=edges.sindex.nearest(p,return_all=True,return_distance=True)
        # Tie break is source-segment index, deterministic across runs.
        eidx=int(min(near[0][1])); e=edges.iloc[eidx]
        pos=e.geometry.project(p);q=e.geometry.interpolate(pos);dist=p.distance(q)
        n=len(coords);coords.append((p.x,p.y));entity_nodes[key]=n
        splits[eidx].append((pos,n,dist,key,role))
        snap_rows.append(dict(object_id=key,role=role,parent_edge=e.edge_id,parent_steps=bool(e.steps),
                              connector_m=dist,projected_x=q.x,projected_y=q.y,
                              object_x=p.x,object_y=p.y,connection_status='unverified_straight_line_proxy'))
    arcs=[]
    def add(u,v,length,steps,direction='no',kind='road',parent=''):
        if direction!='-1':arcs.append((u,v,length,steps,kind,parent))
        if direction not in ('yes','1','true'):arcs.append((v,u,length,steps,kind,parent))
    for ei,e in enumerate(edges.itertuples()):
        chain=[(0.0,index[e.u]),(e.length_m,index[e.v])]
        grouped=collections.defaultdict(list)
        for pos,node,dist,key,role in splits.get(ei,[]):grouped[round(pos,8)].append((node,dist))
        for pos,pts in grouped.items():
            if pos<=1e-7: pn=index[e.u]
            elif e.length_m-pos<=1e-7:pn=index[e.v]
            else:
                pn=len(coords);q=e.geometry.interpolate(pos);coords.append((q.x,q.y));chain.append((pos,pn))
            for node,dist in pts:
                # Connector linked to a step segment inherits exclusion in no-step case.
                add(node,pn,max(0.000001,dist),e.steps,kind='proxy_connector',parent=e.edge_id)
        chain.sort()
        for (a,u),(b,v) in zip(chain[:-1],chain[1:]):
            if b>a:add(u,v,b-a,e.steps,e.direction,parent=e.edge_id)
    # Park targets are actual mapped nodes on pedestrian-compatible path geometry.
    park_nodes={}; pedestrian={'footway','path','pedestrian','steps','living_street','service','track'}
    pathids={x for e in edges.itertuples() if e.highway in pedestrian for x in (e.u,e.v)}
    node_ids=np.array(sorted(pathids));points=gp.GeoSeries([Point(xy[k]) for k in node_ids],crs=32649)
    for f in facilities.itertuples():
        targets=[]
        if f.function=='G' and f.geometry.geom_type in ('Polygon','MultiPolygon'):
            hits=points.sindex.query(f.geometry,predicate='intersects')
            targets=[index[int(node_ids[h])] for h in hits]
        park_nodes[f.record_id]=targets or [entity_nodes[f.record_id]]
    return np.array(coords),arcs,entity_nodes,park_nodes,pd.DataFrame(snap_rows)

def make_csr(arcs,n,no_steps=False):
    # sparse constructor sums duplicate cells; explicitly minimize parallel arcs.
    minimum={}
    for u,v,length,steps,kind,parent in arcs:
        if no_steps and steps:continue
        key=(u,v)
        if length<minimum.get(key,math.inf):minimum[key]=length
    u=[k[0] for k in minimum];v=[k[1] for k in minimum]
    return csr_matrix((list(minimum.values()),(u,v)),shape=(n,n))

def function_masks(facilities,service_set):
    active=facilities.nominal_main.astype(bool).to_numpy()
    if service_set=='original_meal_coords':
        active=active & ~facilities.meal_augmentation.astype(bool).to_numpy()
    elif service_set=='public_S_only':
        active=active & ~((facilities.function=='S') & (facilities.source_kind=='historical_research')).to_numpy()
    elif service_set=='broad_care_as_S':active=active | facilities.expanded_only.astype(bool).to_numpy()
    elif service_set=='old_OSM_service_query':active=active & ~facilities.absent_in_old_osm_query.astype(bool).to_numpy()
    return [active & facilities.function.eq(k).to_numpy() for k in FNS]

def stats_from_C(C):
    D=(~C).sum(axis=1);J=C.all(axis=1)
    return D,J

def main():
    global A,OUT
    parser=argparse.ArgumentParser();parser.add_argument('--extended-origins',action='store_true')
    args=parser.parse_args()
    if args.extended_origins:
        A=A/'sensitivity_124';OUT=OUT/'sensitivity_124'
        CONFIG['scope']='124 publicly mapped residential land-use representative points; adds one multipolygon relation to frozen 123'
    start=time.time();A.mkdir(parents=True,exist_ok=True)
    for p in ['tables','gis','figures']:(OUT/p).mkdir(parents=True,exist_ok=True)
    (A/'analysis_config.json').write_text(json.dumps(CONFIG,ensure_ascii=False,indent=2))
    if args.extended_origins:
        odf=pd.read_csv(WORK/'residential/origins_extended124.csv').fillna('')
        origins=gp.GeoDataFrame(odf,geometry=gp.points_from_xy(odf.x_utm49n,odf.y_utm49n),crs=32649)
    else:origins=gp.read_file(WORK/'residential/candidates.gpkg',layer='residential_origins_frozen')
    facilities=gp.read_file(WORK/'analysis/function_candidates.gpkg')
    edges,xy,network_meta=load_network()
    print('Network',len(edges),'Origins',len(origins),'Candidates',len(facilities),flush=True)
    coords,arcs,enodes,parks,snaps=augment(edges,xy,origins,facilities)
    origin_nodes=[enodes[k] for k in origins.origin_id]
    facility_nodes=[enodes[k] for k in facilities.record_id]
    distances={}; graphstats={}; graph_distances={}
    for name,no_steps in [('ordinary',False),('no_steps',True)]:
        graph=make_csr(arcs,len(coords),no_steps)
        ncomp,labels=connected_components(graph,directed=True,connection='weak')
        D=dijkstra(graph,directed=True,indices=origin_nodes)
        graph_distances[name]=D
        graphstats[name]={'nodes':len(coords),'directed_arcs':graph.nnz,'weak_components':int(ncomp)}
        if not no_steps:distances['point_connection']=D[:,facility_nodes]
        out=[]
        for k in facilities.record_id:out.append(np.min(D[:,parks[k]],axis=1))
        distances['park_mapped_paths_no_steps' if no_steps else 'park_mapped_paths']=np.array(out).T
        print(name,'dijkstra complete',flush=True)
    op=np.array([[p.x,p.y] for p in origins.geometry]);fp=np.array([[p.x,p.y] for p in facilities.geometry.representative_point()])
    euclidean=np.sqrt(((op[:,None,:]-fp[None,:,:])**2).sum(axis=2))
    np.savez_compressed(A/'candidate_od_distances.npz',**distances,euclidean=euclidean,
                        origin_ids=origins.origin_id.to_numpy(dtype=str),record_ids=facilities.record_id.to_numpy(dtype=str))
    rows=[];summary=[];C_lookup={};T_lookup={}
    for network,dmat in distances.items():
      for service_set in CONFIG['service_sets']:
        masks=function_masks(facilities,service_set)
        mins=np.stack([np.min(dmat[:,m],axis=1) if m.any() else np.full(len(origins),np.inf) for m in masks],axis=1)
        nearest=np.stack([np.where(m)[0][np.argmin(dmat[:,m],axis=1)] if m.any() else np.full(len(origins),-1) for m in masks],axis=1)
        for speed in CONFIG['speeds_m_s']:
          times=mins/speed/60
          for threshold in CONFIG['thresholds_min']:
            C=times<=threshold;D,J=stats_from_C(C)
            key=(network,service_set,speed,threshold);C_lookup[key]=C;T_lookup[key]=times
            counts=np.stack([(dmat[:,m]<=threshold*60*speed).sum(axis=1) for m in masks],axis=1)
            for i,o in origins.iterrows():
              r=dict(origin_id=o.origin_id,origin_name=o['name'],nearest_anchor=o.nearest_anchor,
                     network=network,service_set=service_set,speed_m_s=speed,threshold_min=threshold,
                     conditional_joint=int(J[i]),conditional_deficit=int(D[i]),
                     deficit_pattern=''.join(k for z,k in enumerate(FNS) if not C[i,z]) or 'none',
                     real_access_state='UUUU',eligibility_time_window_status='not_currently_verified')
              for z,k in enumerate(FNS):
                fidx=nearest[i,z]
                r.update({f't_{k}_min':float(times[i,z]),f'c_{k}':int(C[i,z]),f'n_{k}_candidate_records':int(counts[i,z]),
                          f'nearest_{k}_record_id':facilities.iloc[fidx].record_id if fidx>=0 and np.isfinite(times[i,z]) else '',
                          f'nearest_{k}_name':facilities.iloc[fidx]['name'] if fidx>=0 and np.isfinite(times[i,z]) else ''})
              rows.append(r)
            summary.append(dict(network=network,service_set=service_set,speed_m_s=speed,threshold_min=threshold,
                                origins=len(origins),joint=int(J.sum()),multiple_deficit=int((D>=2).sum()),
                                missing_function_sum=int(D.sum()),**{f'covered_{k}':int(C[:,z].sum()) for z,k in enumerate(FNS)}))
    result=pd.DataFrame(rows);totals=pd.DataFrame(summary)
    result.to_csv(OUT/'tables/住宅四功能_条件情景明细.csv',index=False,encoding='utf-8-sig')
    totals.to_csv(OUT/'tables/情景汇总.csv',index=False,encoding='utf-8-sig')
    mainkey=(CONFIG['main_network'],CONFIG['main_service_set'],.8,15)
    Cmain=C_lookup[mainkey];Dmain,Jmain=stats_from_C(Cmain)
    mainrows=result[(result.network==mainkey[0])&(result.service_set==mainkey[1])&(result.speed_m_s==.8)&(result.threshold_min==15)].copy()
    mainrows.to_csv(OUT/f'tables/主情景_{len(origins)}住宅.csv',index=False,encoding='utf-8-sig')
    # Paired contrasts isolate one explicit factor at a time, at fixed denominator.
    contrasts=[]
    comparisons=[('park_geometry',('point_connection','documented_inventory',.8,15),mainkey),
      ('remove_mapped_steps',mainkey,('park_mapped_paths_no_steps','documented_inventory',.8,15)),
      ('meal_coordinate_augmentation',('park_mapped_paths','original_meal_coords',.8,15),mainkey),
      ('drop_historical_S',mainkey,('park_mapped_paths','public_S_only',.8,15)),
      ('generic_care_S_assumption',mainkey,('park_mapped_paths','broad_care_as_S',.8,15)),
      ('old_OSM_service_query',mainkey,('park_mapped_paths','old_OSM_service_query',.8,15)),
      ('speed_0.8_to_0.6',mainkey,('park_mapped_paths','documented_inventory',.6,15)),
      ('speed_0.8_to_1.0',mainkey,('park_mapped_paths','documented_inventory',1.,15))]
    for label,a,b in comparisons:
      c0,c1=C_lookup[a],C_lookup[b];d0,j0=stats_from_C(c0);d1,j1=stats_from_C(c1)
      contrasts.append(dict(contrast=label,origins=len(origins),any_function_changed=int((c0!=c1).any(axis=1).sum()),
         joint_0_to_1=int((~j0&j1).sum()),joint_1_to_0=int((j0&~j1).sum()),joint_delta=int(j1.sum()-j0.sum()),
         deficit_sum_delta=int(d1.sum()-d0.sum()),**{f'changed_{k}':int((c0[:,z]!=c1[:,z]).sum()) for z,k in enumerate(FNS)}))
    pd.DataFrame(contrasts).to_csv(OUT/'tables/单因素配对比较.csv',index=False,encoding='utf-8-sig')
    # Single function omission is explicitly a set comparison, not evidence of bad decisions.
    severe=Dmain>=2;omissions=[]
    for z,k in enumerate(FNS):
        omissions.append(dict(function=k,multiple_deficit_total=int(severe.sum()),
                              multiple_deficit_but_this_function_covered=int((severe&Cmain[:,z]).sum()),
                              omitted_share=float((severe&Cmain[:,z]).sum()/severe.sum()) if severe.any() else None))
    pd.DataFrame(omissions).to_csv(OUT/'tables/单类覆盖遗漏多缺口.csv',index=False,encoding='utf-8-sig')
    patterns=mainrows.groupby('deficit_pattern').agg(origins=('origin_id','size')).reset_index()
    patterns.to_csv(OUT/'tables/缺口组合.csv',index=False,encoding='utf-8-sig')
    # Stability is within this finite scenario set; not a probabilistic confidence interval.
    subset=[v for k,v in C_lookup.items() if k[2]==.8 and k[3]==15 and k[1]!='broad_care_as_S']
    stack=np.stack(subset);ds=(~stack).sum(axis=2);js=stack.all(axis=2)
    robust=origins[['origin_id','name','nearest_anchor']].copy()
    robust['joint_in_all_tested']=js.all(axis=0);robust['joint_in_any_tested']=js.any(axis=0)
    robust['deficit_min_tested']=ds.min(axis=0);robust['deficit_max_tested']=ds.max(axis=0)
    robust['stable_full_vector']=np.all(stack==stack[0],axis=(0,2))
    robust['real_access_state']='UUUU'
    robust.to_csv(OUT/'tables/公开情景稳定性.csv',index=False,encoding='utf-8-sig')
    # Connection audits flag uncertainty, never turn a long connector into confirmed absence.
    snaps.to_csv(OUT/'tables/接网连接审计.csv',index=False,encoding='utf-8-sig')
    cs=snaps[snaps.role=='origin'].copy()
    for cap in CONFIG['connection_audit_thresholds_m']:cs[f'connection_unresolved_above_{cap}m']=cs.connector_m>cap
    cs.to_csv(OUT/'tables/住宅接网不确定性.csv',index=False,encoding='utf-8-sig')
    # One-function additions at fixed mapped community-care sites: scenario locations only.
    sites=facilities[facilities.expanded_only.astype(bool) | ((facilities.source_kind=='official_map') & facilities.function.eq('S'))].copy()
    sites=sites.drop_duplicates('entity_id').reset_index(drop=True)
    actions=[]
    # Restrict site pool geometrically to the longest possible model budget, before outcomes.
    for site in sites.itertuples():
      if origins.geometry.distance(site.geometry.representative_point()).min()>1200:continue
      for fn in ['M','S']:
        actions.append(dict(action_id=f'{site.entity_id}:{fn}',entity_id=site.entity_id,record_id=site.record_id,
                            name=site.name,added_function=fn,action_units=1,feasibility='unknown',
                            cost='not_available',geometry=site.geometry.representative_point()))
    action_gdf=gp.GeoDataFrame(actions,crs=32649)
    recordidx={x:i for i,x in enumerate(facilities.record_id)}
    action_results=[];selections=[];all_selections=[]
    # Fixed comparison at .8m/s / 15min; all input-model policies reevaluated on main model.
    for key,C in C_lookup.items():
      network,service_set,speed,threshold=key
      D,J=stats_from_C(C);local=[]
      for action in action_gdf.itertuples():
        idx=FNS.index(action.added_function)
        access=distances[network][:,recordidx[action.record_id]]<=threshold*60*speed
        after=C.copy();after[:,idx]|=access;Da,Ja=stats_from_C(after)
        row=dict(network=network,service_set=service_set,speed_m_s=speed,threshold_min=threshold,action_id=action.action_id,
                 name=action.name,added_function=action.added_function,
                 reduced_multiple_deficit=int((D>=2).sum()-(Da>=2).sum()),
                 gained_joint=int(Ja.sum()-J.sum()),reduced_missing_functions=int(D.sum()-Da.sum()))
        action_results.append(row);local.append(row)
      table=pd.DataFrame(local).sort_values(['reduced_multiple_deficit','gained_joint','reduced_missing_functions','action_id'],ascending=[False,False,False,True])
      scorecols=['reduced_multiple_deficit','gained_joint','reduced_missing_functions']
      best=table.iloc[0];ties=table[(table[scorecols]==best[scorecols]).all(axis=1)]
      for _,best in ties.iterrows():
        all_selections.append(dict(**best.to_dict(),n_tied_best=len(ties)))
        if speed!=.8 or threshold!=15:continue
        action=action_gdf[action_gdf.action_id==best.action_id].iloc[0];j=FNS.index(action.added_function)
        after=Cmain.copy();after[:,j]|=distances[mainkey[0]][:,recordidx[action.record_id]]<=720
        d,joint=stats_from_C(after)
        selections.append(dict(selected_under_network=network,selected_under_service_set=service_set,
          action_id=action.action_id,name=action['name'],added_function=action.added_function,n_tied_best=len(ties),
          main_reduced_multiple_deficit=int((Dmain>=2).sum()-(d>=2).sum()),
          main_gained_joint=int(joint.sum()-Jmain.sum()),main_reduced_missing_functions=int(Dmain.sum()-d.sum())))
    pd.DataFrame(action_results).to_csv(OUT/'tables/单项功能增补_条件收益.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(all_selections).to_csv(OUT/'tables/行动选择_全部能力阈值.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(selections).to_csv(OUT/'tables/相同目标选择_统一情景回评.csv',index=False,encoding='utf-8-sig')
    action_gdf.drop(columns='geometry').to_csv(OUT/'tables/假设增补位置.csv',index=False,encoding='utf-8-sig')
    gis=OUT/'gis/长沙四功能_公开数据条件分析.gpkg'
    if gis.exists():gis.unlink()
    combined=origins.merge(mainrows.drop(columns=['origin_name','nearest_anchor']),on='origin_id',validate='one_to_one')
    combined=combined.merge(robust.drop(columns=['name','nearest_anchor','real_access_state']),on='origin_id',validate='one_to_one')
    combined.to_file(gis,layer='residential_conditional',driver='GPKG')
    facilities.to_file(gis,layer='function_candidates',driver='GPKG')
    edges.to_file(gis,layer='walking_segments',driver='GPKG')
    action_gdf.to_file(gis,layer='hypothetical_function_sites',driver='GPKG')
    snaps_g=gp.GeoDataFrame(snaps,geometry=[LineString([(r.object_x,r.object_y),(r.projected_x,r.projected_y)]) for r in snaps.itertuples()],crs=32649)
    snaps_g.to_file(gis,layer='unverified_connectors',driver='GPKG')
    park_audit=[]
    for f in facilities[facilities.function=='G'].itertuples():
        targets=parks[f.record_id]
        park_audit.append(dict(record_id=f.record_id,name=f.name,mapped_path_node_targets=len(targets) if targets!=[enodes[f.record_id]] else 0,
                               fallback_representative_point=targets==[enodes[f.record_id]]))
    pd.DataFrame(park_audit).to_csv(OUT/'tables/公园接入几何审计.csv',index=False,encoding='utf-8-sig')
    meta={'config':CONFIG,'network':network_meta,'graph':graphstats,'origins':len(origins),'candidate_records':len(facilities),
          'scenario_rows':len(result),'scenario_count':len(totals),'action_count':len(action_gdf),
          'main':totals[(totals.network==mainkey[0])&(totals.service_set==mainkey[1])&(totals.speed_m_s==.8)&(totals.threshold_min==15)].iloc[0].to_dict(),
          'stable_origins':int(robust.stable_full_vector.sum()),'elapsed_s':round(time.time()-start,2)}
    (A/'run_summary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    (OUT/'tables/运行摘要.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    print(json.dumps(meta,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
