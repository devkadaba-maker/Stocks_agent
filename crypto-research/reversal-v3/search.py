import json,datetime
import numpy as np,pandas as pd
import engine as e
OUT=e.OUT
def datasets(through,stage='development',symbols=e.DEV):
    data={}
    for symbol in symbols:
        raw,f,a=e.load(symbol,stage,through);data[symbol]=(raw,f,a,{m:e.anchor(raw,m) for m in [60,240,1440]})
        print('loaded',symbol,stage,len(raw),flush=True)
    return data
def train():
    if (OUT/'selected.json').exists():raise RuntimeError('Already frozen; no retuning')
    grid=e.grid();assert len(grid)==576
    (OUT/'registry.json').write_text(json.dumps(grid,indent=2));data=datasets('2022-12');ranking=[];panels=[];key=None;cache={}
    for i,cfg in enumerate(grid):
        k=(cfg['family'],cfg['signal_minutes'],cfg['length'],cfg['slow_ratio'])
        if k!=key:
            key=k;cache={s:e.mapped(d[0],60,d[3][cfg['signal_minutes']],cfg) for s,d in data.items()}
        rows=[]
        for s,(raw,f,a,anchors) in data.items():
            for year in [2021,2022]:
                for cost in [1,2]:
                    r=e.run(raw,f,a,60,anchors[cfg['signal_minutes']],cfg,f'{year}-01-01',f'{year+1}-01-01',cost,sig=cache[s]);r.update(id=cfg['id'],symbol=s,year=year,cost=cost);rows.append(r);panels.append(r)
        ranking.append(dict(id=cfg['id'],score=e.score(rows),eligible=all(r['trades']>=5 for r in rows if r['cost']==1),median_ret=float(np.median([r['ret'] for r in rows])),median_sharpe=float(np.median([r['sharpe'] for r in rows])),worst_dd=max(r['maxdd'] for r in rows)))
        if (i+1)%48==0:print('screened',i+1,'/576',flush=True)
    lookup={r['id']:r for r in ranking}
    for r,c in zip(ranking,grid):
        neighbors=[]
        for field,values in [('length',[5,10,20,40]),('stop_atr',[2.,3.,4.,6.])]:
            ix=values.index(c[field])
            for j in [ix-1,ix+1]:
                if 0<=j<len(values):
                    other=next(x for x in grid if x[field]==values[j] and all(x[k]==c[k] for k in ['family','signal_minutes','length','slow_ratio','stop_atr','cooldown'] if k!=field))
                    neighbors.append(lookup[other['id']]['score'])
        r['plateau_score']=.75*r['score']+.25*np.median(neighbors)
    ranking=pd.DataFrame(ranking).sort_values('plateau_score',ascending=False);eligible=ranking[ranking.eligible]
    assert len(eligible)>=12
    shortlist=[dict(next(c for c in grid if c['id']==row.id),training=row.to_dict()) for _,row in eligible.head(12).iterrows()]
    (OUT/'shortlist.json').write_text(json.dumps(shortlist,indent=2));(OUT/'shortlist.sha256').write_text(e.hash_file(OUT/'shortlist.json'))
    pd.DataFrame(panels).to_csv(OUT/'training_panels.csv',index=False);ranking.to_csv(OUT/'training_ranking.csv',index=False)
    print(eligible.head(12).to_string(index=False),flush=True)
def validate():
    if (OUT/'selected.json').exists():raise RuntimeError('Already frozen')
    assert e.hash_file(OUT/'shortlist.json')==(OUT/'shortlist.sha256').read_text()
    shortlist=json.loads((OUT/'shortlist.json').read_text());data=datasets('2023-12');panels=[];ranks=[]
    for cfg in shortlist:
        rows=[]
        for s,(raw,f,a,anchors) in data.items():
            for clock in e.CLOCKS:
                sig=e.mapped(raw,clock,anchors[cfg['signal_minutes']],cfg)
                for cost in [1,2]:
                    r=e.run(raw,f,a,clock,anchors[cfg['signal_minutes']],cfg,'2023-01-01','2024-01-01',cost,sig=sig);r.update(id=cfg['id'],symbol=s,clock=clock,cost=cost);panels.append(r);rows.append(r)
        ranks.append(dict(id=cfg['id'],score=e.score(rows),eligible=all(r['trades']>=5 for r in rows if r['cost']==1),median_ret=float(np.median([r['ret'] for r in rows])),median_sharpe=float(np.median([r['sharpe'] for r in rows])),worst_dd=max(r['maxdd'] for r in rows)))
        print('validated',ranks[-1],flush=True)
    ranking=pd.DataFrame(ranks).sort_values('score',ascending=False);eligible=ranking[ranking.eligible];assert len(eligible)
    best=eligible.iloc[0];cfg=next(c for c in shortlist if c['id']==best.id);cfg['validation']=best.to_dict();cfg['protocol_sha256']=e.hash_file(e.ROOT/'protocol.json');cfg['frozen_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT/'selected.json').write_text(json.dumps(cfg,indent=2));(OUT/'selected.sha256').write_text(e.hash_file(OUT/'selected.json'))
    pd.DataFrame(panels).to_csv(OUT/'validation_panels.csv',index=False);ranking.to_csv(OUT/'validation_ranking.csv',index=False)
    print('FROZEN',json.dumps(cfg),flush=True)
if __name__=='__main__':
    import sys
    {'train':train,'validate':validate}[sys.argv[1]]()
