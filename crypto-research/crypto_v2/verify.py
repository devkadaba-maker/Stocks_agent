import numpy as np,pandas as pd
import engine as e

def sample(n=1000,minutes=240):
    idx=pd.date_range('2017-01-01',periods=n,freq=f'{minutes}min',tz='UTC')
    c=100+np.arange(n)*.01+np.sin(np.arange(n)/15)*4
    d=pd.DataFrame({'open':c,'high':c+1,'low':c-1,'close':c,'volume':100.},index=idx)
    return e.indicators(d)

def verify():
    d=sample();future=d.copy();future.loc[d.index[800]:,['open','high','low','close']]*=4;future=e.indicators(future[['open','high','low','close','volume']])
    for f in e.FAMILIES:
        for n in [10,20,40,80]:
            for adx in [0,20]:
                a,b=e.raw_signals(d,f,n,adx);x,y=e.raw_signals(future,f,n,adx)
                assert np.array_equal(a[:800],x[:800]) and np.array_equal(b[:800],y[:800])
    for minutes in e.EXEC:
        ex=sample(1000,minutes=minutes);cfg=e.grid()[0]
        a=e.mapped(ex,minutes,d,240,cfg)
        # Availability is audited directly inside mapped().
        assert not a[0][~a[3]].any()
    # Independent order accounting with a deliberate entry-candle stop.
    d=sample(120,minutes=60);op,hi,lo,cl=[d[k].to_numpy().copy() for k in ['open','high','low','close']]
    at=np.full(120,2.);en=np.zeros(120,bool);ex=np.zeros(120,bool);gate=np.ones(120,bool);en[0]=True
    lo[1]=80;day=d.index.asi8//86_400_000_000_000
    m,curve,_,_,ledger=e.simulate(op,hi,lo,cl,at,en,ex,gate,day,0,120,False,3.,0,1,True)
    assert len(ledger)==1 and ledger[0][0]==1 and ledger[0][1]==1 and ledger[0][3]==2
    assert abs(sum(t[2] for t in ledger)/10000-m[0])<1e-10
    # Delayed gap exit at adverse opening price, not the old stop.
    lo[1]=op[1]-1;op[2]=70;hi[2]=71;lo[2]=69;cl[2]=70
    m,_,_,_,ledger=e.simulate(op,hi,lo,cl,at,en,ex,gate,day,0,120,False,3.,0,1,True)
    assert ledger[0][3]==0 and ledger[0][2]<-50
    # Sealed price access fails without a frozen winner.
    if not (e.ROOT/'output/selected.json').exists():
        try:e.load('BTCUSDT','sealed','2019-12')
        except RuntimeError:pass
        else:raise AssertionError('Seal did not block access')
    print('PASS: signal/ATR causality, all execution clocks, next-open fill, entry stop, gap stop, ledger accounting, sealed-price gate')

if __name__=='__main__':verify()
