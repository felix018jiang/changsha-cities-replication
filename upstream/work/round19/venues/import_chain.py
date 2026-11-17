"""Extract publicly listed Changsha restaurant locations from the three Bawan directory pages."""
import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import re
import urllib.request

import numpy as np
from lxml import html

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = 'https://bawan.dexinmiaosheng.cn/official/'
PAGES = ['store.html','store/p-495-495.html','store/p-990-495.html']


def converter():
    source = ast.parse((ROOT/'work/round2/process_sources.py').read_text())
    functions = [n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in {'bd_to_wgs','gcj_to_wgs'}]
    scope = {'np':np}
    exec(compile(ast.Module(body=functions,type_ignores=[]),'<coordinate conversion>','exec'),scope)
    return scope['bd_to_wgs']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('html',nargs='*',type=Path,help='Three saved store-directory pages in published order')
    args=parser.parse_args()
    if args.html and len(args.html)!=3:
        parser.error('provide all three pages, or omit paths to refresh')
    convert=converter(); found={}; city_rows=0; included_rows=0; pages=[]
    for i,slug in enumerate(PAGES):
        url=BASE+slug
        raw=args.html[i].read_bytes() if args.html else urllib.request.urlopen(url,timeout=40).read()
        page=html.fromstring(raw.decode('utf-8'))
        assert page.xpath('//*[contains(@class,"e_mapbaidu-")]')
        rows=page.xpath('//div[contains(@class,"p_loopitem")][.//input[starts-with(@name,"hiddenValue27")]]')
        pages.append(dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),directory_rows=len(rows)))
        for row in rows:
            texts=lambda c:"".join(row.xpath(f'.//p[contains(@class,"{c}")]/text()')).strip()
            name,address=texts('e_text-4'),texts('e_text-6')
            if '长沙' not in address: continue
            city_rows+=1
            coords=row.xpath('.//input[starts-with(@name,"hiddenValue27")]/@value')
            if not coords or not re.fullmatch(r'[\d.]+,[\d.]+',coords[0]):continue
            lat,lon=map(float,coords[0].split(','))
            if not (111.7<lon<114.4 and 27.7<lat<29.1):continue
            x,y=map(float,convert(lon,lat))
            if re.search('外卖专|纯外卖|暂停营业|已闭店|仅配送|待开业|筹备|检票|候车室|航站楼|学生食堂|校内食堂',name+address):continue
            included_rows+=1
            key=hashlib.sha256((name+'|'+address).encode()).hexdigest()[:14]
            rec=dict(site_id='chain:bawan:'+key,site_name='霸碗盖码饭（'+name+'）',address=address.replace('湖南省长沙市湖南省长沙市','湖南省长沙市').replace('湖南省长沙市长沙市','湖南省长沙市'),function='M',
                lon_wgs84=x,lat_wgs84=y,source_url=url,raw_lon=lon,raw_lat=lat,
                raw_coordinate_basis='BD-09 inferred from the operator directory Baidu map widget',venue_type='具名中式快餐门店',
                evidence_tier='门店目录列有地址和地图点，待核适老供餐协议与开放条件',existing_service=0)
            found.setdefault(key,rec)
    with (HERE/'chain_raw.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(next(iter(found.values()))));w.writeheader();w.writerows(found.values())
    summary=dict(retrieval_date='2026-11-12',operator='湖南霸王餐饮管理有限公司',pages=pages,changsha_address_rows=city_rows,plausible_rows=included_rows,unique_records=len(found),coordinate_reading='Baidu component; datum inferred, only a mapped store point, not a surveyed public entrance.',eligibility='Ordinary restaurant considered for introducing a senior meal service. No agreement or operating capacity verified.')
    (HERE/'chain_source.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
