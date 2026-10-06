import numpy as np,pandas as pd,tempfile,json
from pathlib import Path
import engine as e
def case(prices,directions,rates=None,lows=None,highs=None):
    o=np.array(prices,float);c=o.copy();h=o+.1 if highs is None else np.array(highs,float);l=o-.1 if lows is None else np.array(lows,float)
    n=len(o);r=np.zeros(n) if rates is None else np.array(rates,float)
    return e.simulate(o,h,l,c,r,np.zeros(n),np.array(directions,np.int64),np.ones(n,bool),np.ones(n),np.arange(n,dtype=np.int64),0,n,4.,0,1,True)
def verify():
    # A falling price generates positive short gross PnL after entry at the next open.
    v,_,tr=case([100,100,98,97],[-1,-1,-1,-1]);assert v[0]>0 and tr[0][2]==-1 and tr[0][0]==1
    # A rising price generates positive long PnL; costs are included on both sides.
    v,_,tr=case([100,100,102,103],[1,1,1,1]);assert v[0]>0 and tr[0][2]==1
    # Direct flip closes the long then opens one short at the next open.
    v,_,tr=case([100,100,100,99],[1,-1,-1,-1]);assert len(tr)==2 and tr[0][1]==2 and tr[1][0]==2 and tr[1][2]==-1
    # Funding at a boundary is applied to the previously held direction.
    a,_,_=case([100,100,100,100],[1,1,1,1],[0,0,.001,0]);b,_,_=case([100,100,100,100],[-1,-1,-1,-1],[0,0,.001,0]);assert a[6]<0 and b[6]>0
    # A short is protected on its entry candle, including adverse buy-to-cover slippage.
    v,_,tr=case([100,100,100],[-1,-1,-1],highs=[100.1,110,100.1]);assert tr[0][0]==tr[0][1]==1 and tr[0][6]==2 and tr[0][3]<-50
    # Opening gap uses observed opening price rather than granting the old stop fill.
    v,_,tr=case([100,100,110],[-1,-1,-1]);assert tr[0][6]==0 and tr[0][3]<-100
    assert abs(sum(t[3] for t in tr)/10000-v[0])<1e-10
    n=60000;cut=50000;idx=pd.date_range('2020-01-01',periods=n,freq='5min',tz='UTC');c=100+np.sin(np.arange(n)/30)
    raw=pd.DataFrame(dict(open=c,high=c+.3,low=c-.3,close=c,volume=1.),index=idx)
    changed=raw.copy();changed.iloc[cut:,:4]*=3
    for cfg in e.grid():
        if cfg['length']!=10 or cfg['stop_atr']!=4 or cfg['cooldown']!=0:continue
        for clock in e.CLOCKS:
            a=e.mapped(raw,clock,e.anchor(raw,cfg['signal_minutes']),cfg);b=e.mapped(changed,clock,e.anchor(changed,cfg['signal_minutes']),cfg)
            for x,y in zip(a,b):np.testing.assert_array_equal(x[:cut],y[:cut])
    previous=e.ROOT
    with tempfile.TemporaryDirectory() as temp:
        e.ROOT=Path(temp)
        try:
            try:e.load('BTCUSDT','development','2023-12')
            except RuntimeError:pass
            else:raise AssertionError('BTC training not blocked')
        finally:e.ROOT=previous
    (e.OUT/'verification.json').write_text(json.dumps(dict(short_profit=True,long_profit=True,next_open_reversal=True,funding_sign_and_priority=True,short_entry_stop=True,adverse_gap_stop=True,ledger_reconciliation=True,synthetic_future_shock=True,btc_excluded_from_training=True),indent=2))
    print('PASS: long/short accounting, reversal fills, funding signs, entry and gap stops, causality, ledger and BTC guard')
if __name__=='__main__':verify()
