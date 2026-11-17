"""Public OSM graph and park candidates for the frozen macro support domain.

Topology uses OSM node IDs, never planarizes bridges. Unknown legal access is
not verified. Existing round3 projection/splitting routines are reused.
"""
from pathlib import Path
import collections, json, hashlib, importlib.util
import numpy as np
import pandas as pd
import geopandas as gp
import shapely
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import polygonize, unary_union, transform
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[3]
W=ROOT/'work/round4'; A=W/'analysis'; A.mkdir(parents=True,exist_ok=True)
RAW=W/'raw'

def load_round3_graph_functions():
    spec=importlib.util.spec_from_file_location('tested_graph',ROOT/'work/round3/code/run_analysis.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def restricted(tags):
    return tags.get('foot') in {'no','private','permit'} or (tags.get('access') in {'no','private','permit'} and tags.get('foot') not in {'yes','designated','permissive'})

def build_network():
    domain=gp.read_file(W/'spatial/data_support_domain.gpkg').geometry.union_all()
    scope=domain.buffer(1600)
    ways={}; timestamps=[]
    for p in sorted(RAW.glob('roads_?_?.json')):
        d=json.loads(p.read_text());timestamps.append(d['osm3s']['timestamp_osm_base'])
        for e in d['elements']:
            if e['type']=='way':ways[e['id']]=e
    assert len(timestamps)==6,'All six extraction tiles must succeed before macro routing'
    tagged=json.loads((RAW/'access_nodes_macro.json').read_text())['elements']
    blocked={e['id'] for e in tagged if restricted(e.get('tags',{}))}
    ll={}
    for w in ways.values():
        for nid,pos in zip(w['nodes'],w['geometry']):
            if pos:ll[nid]=(pos['lon'],pos['lat'])
    tf=Transformer.from_crs(4326,32649,always_xy=True)
    ids=list(ll); lon,lat=np.asarray(list(ll.values())).T
    x,y=tf.transform(lon,lat);xy=dict(zip(ids,zip(x,y)))
    rows=[];excluded=collections.Counter()
    for w in ways.values():
        tags=w.get('tags',{});h=tags.get('highway')
        if h in {'motorway','motorway_link','trunk','trunk_link','construction','proposed','services'}:
            excluded['nonwalking_road_class']+=1;continue
        if restricted(tags):excluded['explicit_access_restriction']+=1;continue
        if h=='cycleway' and tags.get('foot') not in {'yes','designated','permissive'}:
            excluded['cycleway_without_foot_permission']+=1;continue
        for j,(u,v) in enumerate(zip(w['nodes'][:-1],w['nodes'][1:])):
            if u==v or u not in xy or v not in xy or u in blocked or v in blocked:continue
            line=LineString([xy[u],xy[v]])
            if line.length<=0:continue
            rows.append({'edge_id':f'{w["id"]}:{j}','way_id':str(w['id']),'u':u,'v':v,
              'length_m':line.length,'highway':h,'steps':h=='steps','direction':tags.get('oneway:foot','no'),
              'access':tags.get('access',''),'foot':tags.get('foot',''),'bridge':tags.get('bridge',''),
              'tunnel':tags.get('tunnel',''),'layer':tags.get('layer',''),
              'conditional':tags.get('access:conditional',tags.get('foot:conditional','')),'geometry':line})
    edges=gp.GeoDataFrame(rows,crs=32649)
    edges=edges[edges.geometry.intersects(scope)].reset_index(drop=True)
    edges.to_file(A/'macro_network.gpkg',layer='walking_segments',driver='GPKG')
    meta={'raw_unique_highway_ways':len(ways),'segments_in_domain_buffer':len(edges),
          'road_km':float(edges.length.sum()/1000),'mapped_restricted_nodes':len(blocked),
          'excluded_ways':dict(excluded),'tile_osm_timestamps':timestamps,
          'scope_buffer_m':1600,'legal_access':'unknown except explicit tags',
          'crossings':'OSM node topology; no inferred intersection at planar crossing',
          'source':'https://www.openstreetmap.org/copyright'}
    meta['input_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
      list(sorted(RAW.glob('roads_?_?.json')))+[RAW/'access_nodes_macro.json',W/'spatial/data_support_domain.gpkg']}
    (A/'network_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    return edges,xy,meta

def read_network():
    p=A/'macro_network.gpkg'
    if not p.exists():return build_network()
    meta=json.loads((A/'network_meta.json').read_text())
    if any(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()!=v for k,v in meta.get('input_hashes',{}).items()):return build_network()
    edges=gp.read_file(p,layer='walking_segments');xy={}
    for e in edges.itertuples():
        xy[e.u]=e.geometry.coords[0];xy[e.v]=e.geometry.coords[-1]
    return edges,xy,meta

def parse_parks():
    d=json.loads((RAW/'parks_macro.json').read_text())
    tf=Transformer.from_crs(4326,32649,always_xy=True)
    domain=gp.read_file(W/'spatial/data_support_domain.gpkg').geometry.union_all().buffer(1600)
    rows=[];fail=[]
    for e in d['elements']:
        try:
            if e['type']=='node':geom=Point(e['lon'],e['lat'])
            elif e['type']=='way':
                coords=[(q['lon'],q['lat']) for q in e.get('geometry',[]) if q]
                geom=Polygon(coords) if len(coords)>3 and e['nodes'][0]==e['nodes'][-1] else LineString(coords)
            else:
                outer=[];inner=[]
                for m in e.get('members',[]):
                    cc=[(q['lon'],q['lat']) for q in m.get('geometry',[]) if q]
                    if len(cc)<2:continue
                    (inner if m.get('role')=='inner' else outer).append(LineString(cc))
                shells=unary_union(list(polygonize(unary_union(outer))))
                holes=unary_union(list(polygonize(unary_union(inner)))) if inner else None
                geom=shells.difference(holes) if holes is not None else shells
            geom=shapely.make_valid(transform(tf.transform,geom))
            if geom.is_empty or not geom.intersects(domain):continue
            t=e.get('tags',{})
            rows.append({'record_id':f'osm:{e["type"]}/{e["id"]}:G','name':t.get('name',t.get('name:zh','')),
              'function':'G','source':f'https://www.openstreetmap.org/{e["type"]}/{e["id"]}',
              'source_family':'OSM_park','source_date':d['osm3s']['timestamp_osm_base'],
              'geometry_role':'mapped_park','eligible':not restricted(t),
              'tags_json':json.dumps(t,ensure_ascii=False),'geometry':geom})
        except Exception as exc:fail.append({'type':e['type'],'id':e['id'],'error':str(exc)})
    parks=gp.GeoDataFrame(rows,crs=32649)
    parks.to_file(A/'macro_parks.gpkg',layer='parks',driver='GPKG')
    meta={'raw_features':len(d['elements']),'within_support_buffer':len(parks),
          'eligible_candidates':int(parks.eligible.sum()),'geometry_failures':fail,
          'current_public_entry':'unknown; leisure=park is a candidate category, not entrance verification'}
    (A/'parks_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    return parks,meta
