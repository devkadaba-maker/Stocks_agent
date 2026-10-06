"""Frozen BTC check requested after the original audit. No optimisation."""
from pathlib import Path
import concurrent.futures as cf, urllib.request, hashlib, json, zipfile, sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'crypto_v2'))
from independent_audit import averages, aggregate, transact

def fetch(job):
    period,kind=job
    filename=f'BTCUSDT-5m-{period}.zip'
    url=f'https://data.binance.vision/data/spot/{kind}/klines/BTCUSDT/5m/{filename}'
    path=ROOT/'data'/filename
    if path.exists():content=path.read_bytes()
    else:
        with urllib.request.urlopen(url,timeout=45) as r:content=r.read()
    with urllib.request.urlopen(url+'.CHECKSUM',timeout=45) as r:expected=r.read().decode().split()[0]
    sha=hashlib.sha256(content).hexdigest()
    assert sha==expected,'Source checksum mismatch'
    path.write_bytes(content)
    return dict(period=period,kind=kind,filename=filename,url=url,sha256=sha,bytes=len(content))

def main():
    (ROOT/'data').mkdir(exist_ok=True)
    frozen=hashlib.sha256((ROOT.parent/'crypto_v2/output/selected.json').read_bytes()).hexdigest()
    assert frozen=='bf0a37af5d94078094315608f2d4aae85e03808a6f1d9f0990da98c637cbd13e'
    jobs=[(str(p),'monthly') for p in pd.period_range('2022-01','2026-09',freq='M')]
    jobs += [(f'2026-10-{d:02d}','daily') for d in range(1,6)]
    with cf.ThreadPoolExecutor(max_workers=12) as pool:manifest=list(pool.map(fetch,jobs))
    (ROOT/'source_manifest.json').write_text(json.dumps(manifest,indent=2))
    frames=[]
    for m in manifest:
        with zipfile.ZipFile(ROOT/'data'/m['filename']) as z:
            d=pd.read_csv(z.open(z.namelist()[0]),header=None,usecols=range(6))
        d.columns=['time','open','high','low','close','volume']
        t=d.pop('time').to_numpy(np.int64)
        d.index=pd.to_datetime(t,unit='us' if t[0]>10**14 else 'ms',utc=True)
        frames.append(d)
    raw=pd.concat(frames).sort_index()
    assert not raw.index.has_duplicates
    assert ((raw.high>=raw[['open','low','close']].max(axis=1))&(raw.low<=raw[['open','high','close']].min(axis=1))).all()
    anchors={}
    for sm,fast,slow,warm in [(240,10,50,100),(1440,40,200,400)]:
        a=aggregate(raw,sm)
        f,s,atr=averages(a.close.to_numpy(float),a.high.to_numpy(float),a.low.to_numpy(float),fast,slow)
        entry=f>s;entry[:warm]=False
        anchors[sm]=(a.index.asi8+sm*60*10**9,entry,f>s,atr)
    rows=[];ledger=[]
    for stage,start,end in [('retrospective_overlap','2024-01-01','2026-10-01'),('unused_october_5days','2026-10-01','2026-10-06')]:
        for minutes in [5,10,60,240,1440]:
            b=aggregate(raw,minutes)
            d=b[(b.index>=pd.Timestamp(start,tz='UTC'))&(b.index<pd.Timestamp(end,tz='UTC'))]
            close_times=d.index.asi8+minutes*60*10**9
            arrays=[np.ascontiguousarray(d[k].to_numpy(float)) for k in ['open','high','low','close']]
            for model,sm,multiple,trailing in [('frozen_candidate',240,4.,False),('original_baseline',1440,3.,True)]:
                available,entry,trend,atr=anchors[sm]
                ix=np.searchsorted(available,close_times,side='right')-1
                assert (ix>=0).all() and (available[ix]<=close_times).all()
                gate=np.isin(close_times,available);gate[(~entry[ix])&trend[ix]]=False
                for cost in [1,2]:
                    curve,dd,trades=transact(*arrays,gate,trend[ix],atr[ix],multiple,trailing,cost)
                    daily=pd.Series(curve,index=d.index).groupby(d.index.floor('D')).last()
                    returns=daily.to_numpy()/np.r_[10000.,daily.to_numpy()[:-1]]-1
                    sh=returns.mean()/returns.std(ddof=1)*np.sqrt(365) if returns.std(ddof=1)>0 else 0.
                    net=curve[-1]/10000-1
                    assert abs(sum(t[8] for t in trades)-10000*net)<1e-7
                    rows.append(dict(symbol='BTCUSDT',stage=stage,start=start,end_exclusive=end,execution_minutes=minutes,model=model,cost_multiplier=cost,net_return=net,ending_equity=curve[-1],max_drawdown=dd,daily_sharpe=sh if stage=='retrospective_overlap' else np.nan,trades=len(trades)))
                    for t in trades:
                        entered,exited,buy,sell,qty,ef,xf,basis,pnl,reason,stop,decided=t
                        ledger.append(dict(stage=stage,execution_minutes=minutes,model=model,cost_multiplier=cost,entry_time=str(d.index[int(entered)]),exit_time=str(d.index[int(exited)]),quantity=qty,entry_fill=buy,exit_fill=sell,entry_fee=ef,exit_fee=xf,pnl=pnl,reason=['gap_stop','signal','stop','terminal'][int(reason)]))
        print('COMPLETED BTC',stage,flush=True)
    result=pd.DataFrame(rows);result.to_csv(ROOT/'BTC_results.csv',index=False)
    pd.DataFrame(ledger).to_csv(ROOT/'BTC_trades.csv',index=False)
    (ROOT/'BTC_check_protocol.json').write_text(json.dumps(dict(selected_sha256=frozen,parameters='EMA10/50 on confirmed 4h; fixed 4xATR14; risk .5%;95%spot cap; no trailing,ADX or cooldown; long only',costs='0.10% commission plus .05% adverse fill-price slippage per side; cost_multiplier2 doubles both',warmup='2022-2023 BTC, used only for causal indicator history',periods='2024-Sep2026 overlaps original study: retrospective frozen-parameter asset transfer; Oct1-5 unused short period: too short to validate edge',no_optimisation=True,cash_start='Each evaluation period starts flat at $10000',liquidation='Any remaining position liquidated at final observed close with costs'),indent=2))
    assert hashlib.sha256((ROOT.parent/'crypto_v2/output/selected.json').read_bytes()).hexdigest()==frozen
    print(result[(result.model=='frozen_candidate')&(result.execution_minutes.isin([5,10]))].to_string(index=False),flush=True)

if __name__=='__main__':main()
