"""Refresh public service descriptions of the source stations. No personal or contact fields saved."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import argparse
import csv
import json
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENUES = ROOT / 'work/round13/rules/extra_source_venues.csv'
API = 'https://ylfw.cszhmz.cn/psp/pc/service/getData.do'
DETAIL = 'https://ylfw.cszhmz.cn/psp/pc/jgxq_1692687688000/index.html?id='
TEMPLATE = HERE / 'station_profiles.json'

def clean(s):
    s = re.sub(r'<[^>]+>', ' ', str(s or ''))
    return re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)(?:0731[-－ ]?)?\d{8}(?!\d)', '[联系方式略]', s).strip()

def fetch(row):
    code = row['record_id'].split(':')[-1]
    payload = urlencode(dict(disposeCode='false', serviceCode='fwydjk', apiNum='CG3511034', orgid=code)).encode()
    out = dict(case_id=row['case_id'], site_id=row['site_id'], record_id=row['record_id'], url=DETAIL+code)
    for attempt in range(3):
        try:
            req = Request(API, data=payload, headers={'Accept':'application/json','Referer':DETAIL+code})
            with urlopen(req, timeout=16) as response:
                body = response.read()
            d = json.loads(body)['resultData']['data']['data']['orgDetail']['data']
            out.update(status='returned', raw_response_sha256=sha256(body).hexdigest(), name=clean(d.get('orgname')),
                       address=clean(d.get('addr')), service_description=clean(d.get('orgContent')),
                       listed_services=[{'id':str(r['id']), 'title':clean(r.get('name'))}
                                        for r in d.get('services',[]) or [] if r.get('id')])
            break
        except (OSError, ValueError, TypeError, KeyError) as err:
            out.update(status='failed', error=str(err))
            time.sleep(0.6*(attempt+1))
    out['retrieved_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    return out

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true', required=True, help='replace the saved public snapshot')
    parser.parse_args()
    with VENUES.open(encoding='utf-8-sig') as src:
        venues = list(csv.DictReader(src))
    with ThreadPoolExecutor(max_workers=3) as executor:
        records=list(executor.map(fetch, venues))
    TEMPLATE.write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')
    print(len(records), 'records;', sum(t['status']=='returned' for t in records),'returned')

if __name__ == '__main__':
    main()
