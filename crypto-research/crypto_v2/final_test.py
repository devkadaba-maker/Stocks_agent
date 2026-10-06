"""One final assessment of a frozen winner and a frozen incumbent."""
from pathlib import Path
import json,datetime
import pandas as pd,numpy as np
import engine as e
ROOT=e.ROOT;OUT=ROOT/'output'

def main():
    if (OUT/'final_test_started.json').exists():raise RuntimeError('Final test already accessed; do not recycle it for selection')
    frozen=e.hash_file(OUT/'selected.json')
    if frozen!=(OUT/'selected.sha256').read_text():raise RuntimeError('Winner changed after freeze')
    for file in ['manifest.json','manifest_warmup.json']:
        manifest=json.loads((ROOT/'data'/file).read_text())
        if any(m['status']=='error' for m in manifest):raise RuntimeError('Data download unresolved')
    cfg=json.loads((OUT/'selected.json').read_text());rows=[];curves={};ledger=[];coverage=[]
    (OUT/'final_test_started.json').write_text(json.dumps(dict(selection_sha256=frozen,started_at=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2))
    groups=[(s,'2024-01-01','2026-10-01','final') for s in e.DEV+e.EXTRA]
    groups +=[(s,'2018-01-01','2020-01-01','historical_transfer') for s in ['BTCUSDT','ETHUSDT']]
    for symbol,start,end,stage in groups:
        raw=e.load(symbol,'sealed','2019-12' if stage=='historical_transfer' else '2026-09')
        if symbol in e.DEV:
            warm=e.load(symbol,'development','2023-12');raw=pd.concat([warm,raw]).sort_index()
        assert not raw.index.has_duplicates
        coverage.append(dict(symbol=symbol,stage=stage,bars=len(raw),first=str(raw.index[0]),last=str(raw.index[-1]),gaps=int((raw.index.to_series().diff()>pd.Timedelta(minutes=5)).sum())))
        anchors={m:e.indicators(e.bars(raw,m)) for m in set([cfg['signal_minutes'],1440])}
        for minutes in e.EXEC:
            d=e.bars(raw,minutes);prep=e.prepared(d,minutes)
            for model,c,baseline in [('optimized',cfg,False),('original',e.BASELINE,True)]:
                sig=e.mapped(d,minutes,anchors[c['signal_minutes']],c['signal_minutes'],c,baseline)
                for cost in [1,2]:
                    r,ser,trades=e.run(d,minutes,anchors[c['signal_minutes']],c,start,end,cost,baseline,True,sig,prep)
                    r.update(symbol=symbol,stage=stage,execution_minutes=minutes,model=model,cost=cost);rows.append(r)
                    assert abs(sum(t['pnl'] for t in trades)/10000-r['ret'])<1e-9
                    if cost==1:
                        curves[f'{symbol}_{minutes}_{model}']=ser
                        ledger.extend([dict(t,symbol=symbol,execution_minutes=minutes,model=model,stage=stage) for t in trades])
        print('one-time assessment completed',symbol,stage,flush=True)
    if frozen!=e.hash_file(OUT/'selected.json'):raise RuntimeError('Selection mutated')
    df=pd.DataFrame(rows);df.to_csv(OUT/'final_test.csv',index=False)
    pd.DataFrame(curves).to_csv(OUT/'daily_equity.csv');pd.DataFrame(ledger).to_csv(OUT/'trades.csv',index=False)
    pd.DataFrame(coverage).to_csv(OUT/'coverage.csv',index=False)
    normal=df[(df.stage=='final')&(df.cost==1)];stress=df[(df.stage=='final')&(df.cost==2)]
    n=normal[normal.model=='optimized'];b=normal[normal.model=='original']
    p=n.merge(b,on=['symbol','execution_minutes','stage','cost'],suffixes=('_new','_old'))
    p['cagr_delta']=p.cagr_new-p.cagr_old;p['sharpe_delta']=p.sharpe_new-p.sharpe_old;p['return_delta']=p.ret_new-p.ret_old
    p.to_csv(OUT/'paired_improvement.csv',index=False)
    ns=stress[stress.model=='optimized'];share=float((p.sharpe_delta>0).mean())
    promoted=bool(p.cagr_delta.median()>0 and p.sharpe_delta.median()>0 and share>=.6 and ns.ret.median()>0 and n.maxdd.max()<=.15)
    summary=dict(selection_sha256=frozen,improvement_supported=promoted,median_paired_cagr_gain=float(p.cagr_delta.median()),median_paired_sharpe_gain=float(p.sharpe_delta.median()),share_better_sharpe=share,median_new_sharpe=float(n.sharpe.median()),median_old_sharpe=float(b.sharpe.median()),median_new_return=float(n.ret.median()),median_old_return=float(b.ret.median()),profitable_primary=int((n.ret>0).sum()),profitable_stress=int((ns.ret>0).sum()),panels=len(n),worst_new_drawdown=float(n.maxdd.max()),median_stress_return=float(ns.ret.median()))
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    print('FINAL ASSESSMENT',json.dumps(summary),flush=True)
    print(n[['symbol','execution_minutes','ret','sharpe','maxdd','trades']].to_string(index=False),flush=True)

if __name__=='__main__':main()
