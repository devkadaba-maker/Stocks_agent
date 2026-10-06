"""Pine cash-cost proxy diagnostic. Does not alter or select any rules."""
import inspect,json
import pandas as pd,numpy as np
from numba import njit
import engine as e
ROOT=e.ROOT;OUT=ROOT/'output';cfg=json.loads((OUT/'selected.json').read_text());before=e.hash_file(OUT/'selected.json')
s=inspect.getsource(e.simulate.py_func).replace('@njit(cache=True)\n','').replace('fee=.001*cost;slip=.0005*cost','fee=.0015*cost;slip=0.')
ns={'np':np};exec(s,ns);e.simulate=njit(ns['simulate']);rows=[]
for symbol in e.DEV+e.EXTRA+['BTCUSDT','ETHUSDT']:
    old=symbol in ['BTCUSDT','ETHUSDT'];raw=e.load(symbol,'sealed','2019-12' if old else '2026-09')
    if symbol in e.DEV:raw=pd.concat([e.load(symbol,'development','2023-12'),raw]).sort_index()
    anchor=e.indicators(e.bars(raw,cfg['signal_minutes']))
    for minutes in e.EXEC:
        d=e.bars(raw,minutes)
        m=e.run(d,minutes,anchor,cfg,'2018-01-01' if old else '2024-01-01','2020-01-01' if old else '2026-10-01')
        m.update(symbol=symbol,execution_minutes=minutes);rows.append(m)
pd.DataFrame(rows).to_csv(OUT/'pine_cost_proxy.csv',index=False)
assert e.hash_file(OUT/'selected.json')==before
print('Frozen-rule Pine cost-proxy diagnostic complete',len(rows))
