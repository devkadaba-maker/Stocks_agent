from pathlib import Path
import json,time
import numpy as np
import pandas as pd
import engine as e
ROOT=e.ROOT;OUT=ROOT/'output';OUT.mkdir(exist_ok=True)

def datasets(through,execs):
    d={};anchors={}
    for s in e.DEV:
        raw=e.load(s,'development',through)
        if raw.index[-1]<pd.Timestamp(through+'-01',tz='UTC')+pd.offsets.MonthEnd(0):raise RuntimeError('Coverage ends early')
        for m in execs:
            b=e.bars(raw,m);d[s,m]=(b,e.prepared(b,m))
        for m in [240,1440]:anchors[s,m]=e.indicators(e.bars(raw,m))
        print('loaded development',s,through,len(raw),flush=True)
    return d,anchors

def train():
    if (OUT/'selected.json').exists():raise RuntimeError('Do not retune after final selection')
    configs=e.grid();(OUT/'candidate_registry.json').write_text(json.dumps(configs,indent=2))
    data,anchors=datasets('2022-12',[60,240]);ranking=[];allrows=[];key=None;cache={}
    for i,cfg in enumerate(configs,1):
        nk=(cfg['family'],cfg['signal_minutes'],cfg['length'],cfg['adx_min'])
        if nk!=key:
            key=nk;cache={}
            for (s,m),(d,prep) in data.items():cache[s,m]=e.mapped(d,m,anchors[s,cfg['signal_minutes']],cfg['signal_minutes'],cfg)
        rows=[]
        for (s,m),(d,prep) in data.items():
            for year in [2020,2021,2022]:
                for cost in [1,2]:
                    r=e.run(d,m,anchors[s,cfg['signal_minutes']],cfg,f'{year}-01-01',f'{year+1}-01-01',cost,sig=cache[s,m],prep=prep)
                    r.update(id=cfg['id'],symbol=s,execution_minutes=m,year=year,cost=cost);rows.append(r);allrows.append(r)
        primary=[r for r in rows if r['execution_minutes']==60 and r['cost']==1]
        enough=all(r['trades']>=2 for r in primary) and all(sum(r['trades'] for r in primary if r['symbol']==s)>=12 for s in e.DEV)
        ranking.append(dict(id=cfg['id'],score=e.score(rows),eligible=bool(enough),median_sharpe=float(np.median([r['sharpe'] for r in rows])),median_cagr=float(np.median([r['cagr'] for r in rows])),worst_drawdown=max(r['maxdd'] for r in rows)))
        if i%128==0:print('screened',i,'/',len(configs),flush=True)
    lookup={r['id']:r for r in ranking};bycfg={c['id']:c for c in configs}
    for r in ranking:
        c=bycfg[r['id']];neighbors=[]
        for field,vals in [('length',[10,20,40,80]),('stop_atr',[2.,3.,4.,6.])]:
            index=vals.index(c[field])
            for ni in [index-1,index+1]:
                if 0<=ni<len(vals):
                    nc=c.copy();nc[field]=vals[ni]
                    match=next(k for k in configs if all(k[f]==nc[f] for f in ['family','signal_minutes','length','adx_min','stop_atr','trailing','cooldown']))
                    neighbors.append(lookup[match['id']]['score'])
        r['plateau_score']=.75*r['score']+.25*float(np.median(neighbors))
    eligible=sorted([r for r in ranking if r['eligible']],key=lambda r:r['plateau_score'],reverse=True)
    if len(eligible)<12:raise RuntimeError('Too few candidates meet declared trade-count rules; no automatic rule relaxation')
    shortlist=[dict(bycfg[r['id']],training=r) for r in eligible[:12]]
    (OUT/'shortlist.json').write_text(json.dumps(shortlist,indent=2))
    (OUT/'shortlist.sha256').write_text(e.hash_file(OUT/'shortlist.json'))
    pd.DataFrame(ranking).to_csv(OUT/'training_ranking.csv',index=False)
    pd.DataFrame(allrows).to_csv(OUT/'training_panels.csv',index=False)
    print('FIXED SHORTLIST',len(shortlist),'eligible training configs',len(eligible),flush=True)
    print(pd.DataFrame(eligible[:12])[['id','plateau_score','median_sharpe','median_cagr']].to_string(index=False),flush=True)

def validate():
    if (OUT/'selected.json').exists():raise RuntimeError('Final selection already exists')
    if e.hash_file(OUT/'shortlist.json')!=(OUT/'shortlist.sha256').read_text():raise RuntimeError('Shortlist changed')
    shortlist=json.loads((OUT/'shortlist.json').read_text());data,anchors=datasets('2023-12',e.EXEC);ranking=[];allrows=[]
    for cfg in shortlist:
        rows=[]
        for (s,m),(d,prep) in data.items():
            sig=e.mapped(d,m,anchors[s,cfg['signal_minutes']],cfg['signal_minutes'],cfg)
            for cost in [1,2]:
                r=e.run(d,m,anchors[s,cfg['signal_minutes']],cfg,'2023-01-01','2024-01-01',cost,sig=sig,prep=prep)
                r.update(id=cfg['id'],symbol=s,execution_minutes=m,cost=cost);rows.append(r);allrows.append(r)
        enough=all(r['trades']>=5 for r in rows if r['cost']==1)
        ranking.append(dict(id=cfg['id'],score=e.score(rows),eligible=bool(enough),median_cagr=float(np.median([r['cagr'] for r in rows])),median_sharpe=float(np.median([r['sharpe'] for r in rows])),worst_drawdown=max(r['maxdd'] for r in rows)))
        print('validated',cfg['id'],'score',ranking[-1]['score'],'eligible',enough,flush=True)
    eligible=[r for r in ranking if r['eligible']]
    if not eligible:raise RuntimeError('No validation candidate meets declared trade-count rules')
    best=max(eligible,key=lambda r:r['score']);selected=next(c for c in shortlist if c['id']==best['id'])
    selected['validation']=best;selected['protocol_sha256']=e.hash_file(ROOT/'protocol.json')
    (OUT/'selected.json').write_text(json.dumps(selected,indent=2));(OUT/'selected.sha256').write_text(e.hash_file(OUT/'selected.json'))
    pd.DataFrame(allrows).to_csv(OUT/'validation_panels.csv',index=False);pd.DataFrame(ranking).to_csv(OUT/'validation_ranking.csv',index=False)
    print('FROZEN WINNER',json.dumps(selected),flush=True)

if __name__=='__main__':
    import sys
    {'train':train,'validate':validate}[sys.argv[1]]()
