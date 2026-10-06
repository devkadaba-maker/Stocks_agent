import concurrent.futures as cf,urllib.request,urllib.error,zipfile,io,hashlib,json,time
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent
DEV=['XRPUSDT','ADAUSDT','LTCUSDT','LINKUSDT']
TEST=DEV+['DOGEUSDT','AVAXUSDT','ATOMUSDT','DOTUSDT']

def jobs():
    a=[(s,str(m),'development') for s in DEV for m in pd.period_range('2018-01','2023-12',freq='M')]
    a +=[(s,str(m),'sealed') for s in TEST for m in pd.period_range('2024-01','2026-09',freq='M')]
    a +=[(s,str(m),'sealed') for s in ['BTCUSDT','ETHUSDT'] for m in pd.period_range('2017-09','2019-12',freq='M')]
    for s,m,stage in a:
        if s in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT'] and '2020-01'<=m<='2026-09': raise RuntimeError('Previously used candle request blocked')
    return a

def fetch(job):
    s,m,stage=job; dest=ROOT/'data'/stage/f'{s}-5m-{m}.zip'
    u=f'https://data.binance.vision/data/spot/monthly/klines/{s}/5m/{dest.name}'
    meta=dict(symbol=s,month=m,stage=stage,url=u)
    for attempt in range(3):
        try:
            if dest.exists(): b=dest.read_bytes()
            else:
                with urllib.request.urlopen(u,timeout=25) as r:b=r.read()
            with urllib.request.urlopen(u+'.CHECKSUM',timeout=25) as r:exp=r.read().decode().split()[0]
            h=hashlib.sha256(b).hexdigest()
            if h!=exp:raise ValueError('Checksum mismatch')
            dest.write_bytes(b)
            meta.update(status='verified',sha256=h,bytes=len(b));return meta
        except urllib.error.HTTPError as e:
            if e.code==404:meta['status']='not_listed_or_unavailable';return meta
            error=str(e)
        except Exception as e:error=str(e)
    meta.update(status='error',error=error);return meta

def main():
    for d in ['development','sealed']:(ROOT/'data'/d).mkdir(parents=True,exist_ok=True)
    j=jobs();results=[]
    with cf.ThreadPoolExecutor(max_workers=32) as e:
        for i,m in enumerate(e.map(fetch,j),1):
            results.append(m)
            if i%40==0:print('verified requests',i,'/',len(j),flush=True)
    (ROOT/'data/manifest.json').write_text(json.dumps(results,indent=2))
    bad=[m for m in results if m['status']=='error']
    print('archive audit',pd.Series([m['status'] for m in results]).value_counts().to_dict(),flush=True)
    if bad:raise RuntimeError(f'{len(bad)} unresolved download errors; retry before testing')

if __name__=='__main__':main()
