"""Retrieve the extension's municipal station records and service descriptions."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import csv
import json
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "work/round14/activity"))
from fetch_station_profiles import fetch as profile
from fetch_service_items import fetch as service, KEYWORDS

def update(path, rows, id_key, fetch, workers, refresh):
    records = {} if refresh or not path.exists() else {
        x[id_key]: x for x in json.loads(path.read_text()) if x["status"] == "returned"
    }
    pending = [r for r in rows if r[id_key] not in records]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(fetch, x): x[id_key] for x in pending}
        for n, future in enumerate(as_completed(tasks), 1):
            records[tasks[future]] = future.result()
            if n % 15 == 0:
                print(path.name, n, "/", len(tasks), flush=True)
                save(path, records)
    save(path, records)
    saved = list(records.values())
    print(path.name, len(saved), "returned", sum(x["status"]=="returned" for x in saved), flush=True)
    return saved

def save(path, records):
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps([records[k] for k in sorted(records)], ensure_ascii=False, indent=2)+"\n")
    tmp.replace(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--all-items",action="store_true", help="retrieve every item named in the station directory")
    parser.add_argument("--refresh",action="store_true")
    parser.add_argument("--venue-file",type=Path,default=HERE.parent/"inputs/extra_source_venues.csv")
    args=parser.parse_args()
    rows=list(csv.DictReader(args.venue_file.open(encoding="utf-8-sig")))
    profiles=update(HERE/"station_profiles.json",rows,"record_id",profile,4,args.refresh)
    targets={}
    for p in profiles:
        for i in p.get("listed_services",[]):
            if args.all_items or KEYWORDS.search(i["title"]):
                r=targets.setdefault(i["id"],dict(id=i["id"], title=i["title"], orgids=[]))
                r["orgids"].append(p["record_id"].split(":")[-1])
    update(HERE/"service_items.json",list(targets.values()),"id",service,5,args.refresh)

if __name__=="__main__":
    main()
