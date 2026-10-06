"""Download registered USD-M perpetual candles and realized funding, with hashes."""
from pathlib import Path
import json,hashlib,urllib.request,urllib.error,concurrent.futures as cf
import pandas as pd
ROOT=Path(__file__).resolve().parent
original_file=ROOT/'data/original_manifests.json'
ORIGINAL={r['url']:r['sha256'] for r in json.loads(original_file.read_text()) if r['status']=='verified'} if original_file.exists() else {}
def jobs(stage):
    p=json.loads((ROOT/'protocol.json').read_text());dev=p['development_assets'];extra=p['extra_final_assets']
    if stage=='development':groups=[(s,'2020-01','2023-12') for s in dev]
    elif stage=='final':groups=[(s,'2024-01','2026-09') for s in dev]+[(s,'2022-01','2026-09') for s in extra]
    elif stage=='btc':groups=[('BTCUSDT','2022-01','2026-09')]
    else:raise ValueError(stage)
    if stage!='development' and not (ROOT/'output/selected.json').exists():raise RuntimeError('Prices sealed before selection')
    return [(s,str(m),kind,stage) for s,a,b in groups for m in pd.period_range(a,b,freq='M') for kind in ['klines','fundingRate']]
def fetch(job):
    s,m,kind,stage=job;name=f'{s}-5m-{m}.zip' if kind=='klines' else f'{s}-fundingRate-{m}.zip'
    sub=f'klines/{s}/5m' if kind=='klines' else f'fundingRate/{s}'
    url=f'https://data.binance.vision/data/futures/um/monthly/{sub}/{name}'
    target=ROOT/'data'/stage/name;target.parent.mkdir(parents=True,exist_ok=True)
    result=dict(symbol=s,month=m,kind=kind,stage=stage,url=url,file=name)
    for attempt in range(3):
        try:
            if target.exists():data=target.read_bytes()
            else:
                with urllib.request.urlopen(url,timeout=40) as r:data=r.read()
            with urllib.request.urlopen(url+'.CHECKSUM',timeout=40) as r:expected=r.read().decode().split()[0]
            sha=hashlib.sha256(data).hexdigest();assert sha==expected
            if url in ORIGINAL and sha!=ORIGINAL[url]:raise RuntimeError('Upstream archive changed from frozen research manifest: '+url)
            target.write_bytes(data);result.update(status='verified',sha256=sha,bytes=len(data));return result
        except urllib.error.HTTPError as e:
            if e.code==404:result['status']='unavailable';return result
            err=str(e)
        except Exception as e:err=str(e)
    result.update(status='error',error=err);return result
def main(stage):
    j=jobs(stage);results=[]
    with cf.ThreadPoolExecutor(max_workers=24) as pool:
        for i,r in enumerate(pool.map(fetch,j),1):
            results.append(r)
            if i%80==0:print(stage,'requests',i,'/',len(j),flush=True)
    (ROOT/'data'/f'{stage}_manifest.json').write_text(json.dumps(results,indent=2))
    print(stage,pd.Series([r['status'] for r in results]).value_counts().to_dict(),flush=True)
    if any(r['status']=='error' for r in results):raise RuntimeError('Unresolved downloads')
if __name__=='__main__':
    import sys
    main(sys.argv[1])
