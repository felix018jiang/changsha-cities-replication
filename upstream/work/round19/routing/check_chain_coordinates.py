"""Check published restaurant address street names against the frozen OSM geometry."""
import ast
from collections import defaultdict
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "work/round4/raw"


def named_roads():
    raw_by_way = {}
    for path in sorted(RAW.glob("roads_?_?.json")):
        for way in json.loads(path.read_text())["elements"]:
            if way.get("type") == "way":
                raw_by_way[way["id"]] = way
    roads = defaultdict(list)
    tf = Transformer.from_crs(4326, 32649, always_xy=True)
    for way in raw_by_way.values():
        tags = way.get("tags", {})
        names = {tags.get(k,"").strip() for k in ("name","name:zh","official_name","alt_name")}
        names = {n for n in names for n in n.split(";") if re.fullmatch(r"[\u4e00-\u9fff]{2,}(大道|路|道|巷|街|里)", n)}
        coords = [(p["lon"],p["lat"]) for p in way.get("geometry",[]) if p]
        if not names or len(coords) < 2:
            continue
        line = transform(tf.transform, LineString(coords))
        for name in names:
            roads[name].append(line)
    return roads


def main():
    roads = named_roads()
    stores = pd.read_csv(HERE.parent/"venues/chain_raw.csv")
    code=ast.parse((ROOT/"work/round2/process_sources.py").read_text())
    scope={"np":np}
    funcs=[n for n in code.body if isinstance(n,ast.FunctionDef) and n.name=="gcj_to_wgs"]
    exec(compile(ast.Module(body=funcs,type_ignores=[]),"<gcj conversion>","exec"),scope)
    tf = Transformer.from_crs(4326,32649,always_xy=True)
    checks = []
    for r in stores.itertuples():
        matched = [name for name in roads if name in r.address]
        if not matched:
            continue
        matched = [name for name in matched if not any(name!=other and name in other for other in matched)]
        lines = [line for name in matched for line in roads[name]]
        coords = {"bd09":(r.lon_wgs84,r.lat_wgs84), "gcj02":scope["gcj_to_wgs"](r.raw_lon,r.raw_lat),
                  "wgs84":(r.raw_lon,r.raw_lat)}
        distances = {crs:min(line.distance(Point(tf.transform(float(lon),float(lat)))) for line in lines)
                     for crs,(lon,lat) in coords.items()}
        checks.append(dict(site_id=r.site_id,site_name=r.site_name,address=r.address,matching_streets=" | ".join(matched),
                           **{f"distance_{crs}_m":dist for crs,dist in distances.items()},
                           closest_reading=min(distances,key=distances.get)))
    df = pd.DataFrame(checks)
    df.to_csv(HERE/"chain_coordinate_street_check.csv",index=False,encoding="utf-8-sig",float_format="%.2f")
    meta = {"tested":len(df),"available_stores":len(stores),"named_road_count":len(roads),
            "match":"Exact Chinese road names in the operator's street address; substring aliases dropped. Minimum distance to any geometrically matched named road.",
            "medians_m":{crs:round(df[f"distance_{crs}_m"].median(),2) for crs in coords},
            "within_100_m":{crs:int(df[f"distance_{crs}_m"].le(100).sum()) for crs in coords},
            "best_reading":df.closest_reading.value_counts().to_dict()}
    print(meta)
    (HERE/"chain_coordinate_check.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n")


if __name__ == "__main__":
    main()
