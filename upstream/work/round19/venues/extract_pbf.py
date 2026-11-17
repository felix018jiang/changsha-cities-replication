"""Freeze the named OSM point and area POIs within the 16-circle search rectangle."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import zlib

import geopandas as gpd
import pandas as pd
import pyogrio

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CLASSES = ("restaurant","fast_food","food_court","community_centre","arts_centre","library","social_centre")
def varint(blob, offset):
    value = 0
    shift = 0
    while True:
        x = blob[offset]
        offset += 1
        value |= (x & 127) << shift
        if not x & 128:
            return value, offset
        shift += 7

def proto(blob):
    i = 0
    while i < len(blob):
        tag,i=varint(blob,i)
        if tag & 7 == 0:
            value,i=varint(blob,i)
        elif tag & 7 == 2:
            n,i=varint(blob,i); value=blob[i:i+n]; i+=n
        else:
            raise ValueError(tag)
        yield tag>>3,value

def header_time(path):
    with path.open("rb") as handle:
        header = dict(proto(handle.read(struct.unpack(">I",handle.read(4))[0])))
        encoded = dict(proto(handle.read(header[3])))
        block = dict(proto(zlib.decompress(encoded[3]) if 3 in encoded else encoded[1]))
    return datetime.datetime.fromtimestamp(block[32],datetime.timezone.utc).isoformat()

def main(path):
    cases = pd.read_csv(ROOT/'outputs/长沙养老研究_第十八轮/tables/circle_choice_summary.csv').case_id.unique()
    zones = gpd.read_file(ROOT/'work/round4/spatial/exclusive_circle_zones.gpkg')
    bbox = tuple(zones[zones.circle_id.isin(cases)].buffer(720).to_crs(4326).total_bounds)
    fields = ["name","amenity","access","name:zh","addr:full","addr:street","addr:housenumber","takeaway","indoor_seating","cuisine","disused","opening_hours","operator","building"]
    cfg = Path(pyogrio.__file__).parent/'gdal_data/osmconf.ini'
    contents = cfg.read_text()
    for layer in ["points","multipolygons"]:
        contents = re.sub(r'(\['+layer+r'\][^\[]*?\nattributes=)[^\n]*', lambda m:m.group(1)+",".join(fields),contents)
    query = "amenity IN ("+",".join("'"+v+"'" for v in CLASSES)+")"
    rows=[]
    metadata=[]
    contents=contents.replace("osm_timestamp=no", "osm_timestamp=yes").replace("osm_version=no", "osm_version=yes")
    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp)/'osmconf.ini'
        conf.write_text(contents)
        for layer in ["points","multipolygons"]:
            data = gpd.read_file(path,layer=layer,bbox=bbox,where=query,CONFIG_FILE=str(conf)).to_crs(32649)
            for _,row in data.iterrows():
                if not pd.notna(row['name']) and not pd.notna(row['name_zh']):
                    continue
                shape=row.geometry
                if shape.is_empty:continue
                point = shape if shape.geom_type=="Point" else shape.representative_point()
                lon,lat = gpd.GeoSeries([point],crs=32649).to_crs(4326).iloc[0].coords[0]
                way_id=row.get("osm_way_id");isway=pd.notna(way_id)
                osm_type="node" if layer=="points" else ("way" if isway else "relation")
                number=way_id if isway else row.osm_id
                metadata.append(dict(site_id=f"osm:{osm_type}:{number}",last_osm_edit_utc=row.osm_timestamp,version=row.osm_version))
                tags={f:str(row[f.replace(':','_')]) for f in fields if pd.notna(row.get(f.replace(':','_')))}
                rows.append(dict(type=osm_type,id=str(number),lon=lon,lat=lat,tags=tags))
    campus = gpd.read_file(path,layer='multipolygons',bbox=bbox,where="amenity IN ('school','university','college','kindergarten')")
    campus[['name','amenity','geometry']].to_file(HERE/'campus_footprints.geojson',driver='GeoJSON')
    pd.DataFrame(metadata).to_csv(HERE/'osm_edit_dates.csv',index=False,encoding='utf-8-sig')
    stamp = header_time(path)
    (HERE/'osm_raw.json').write_text(json.dumps({'timestamp':stamp,'elements':rows},ensure_ascii=False,separators=(',',':')))
    (HERE/'osm_source.json').write_text(json.dumps(dict(endpoint='https://download.geofabrik.de/asia/china/hunan-latest.osm.pbf',provider='Geofabrik / OpenStreetMap contributors',timestamp=stamp,bbox=bbox,classes=CLASSES,n=len(rows),pbf_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),extract_sha256=hashlib.sha256((HERE/'osm_raw.json').read_bytes()).hexdigest(),license='ODbL 1.0',retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2)+'\n')
    print(stamp,len(rows),flush=True)
if __name__=='__main__':main(Path(sys.argv[1]))
