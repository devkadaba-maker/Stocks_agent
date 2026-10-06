"""Linear perpetual reversal research, 5m execution path and actual funding events."""
from pathlib import Path
import json,zipfile,hashlib,itertools
import numpy as np,pandas as pd
from numba import njit
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'output';OUT.mkdir(exist_ok=True)
DEV=['BCHUSDT','ETCUSDT','TRXUSDT','XLMUSDT'];EXTRA=['NEARUSDT','UNIUSDT','AAVEUSDT','FILUSDT'];CLOCKS=[5,10,60,240]
def hash_file(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(symbol,stage,through):
    if stage!='development' and not (OUT/'selected.json').exists():raise RuntimeError('Final access before freeze')
    if stage=='development' and symbol not in DEV:raise RuntimeError('Previously examined market blocked from selection')
    data=[];fund=[];months=[]
    for p in sorted((ROOT/'data'/stage).glob(symbol+'-5m-*.zip')):
        month=p.stem[-7:]
        if month>through:continue
        with zipfile.ZipFile(p) as z:d=pd.read_csv(z.open(z.namelist()[0]),header=None).iloc[:,:6]
        numeric=d.apply(pd.to_numeric,errors='coerce');invalid=numeric.isna().any(axis=1)
        if invalid.any():
            if list(np.flatnonzero(invalid.to_numpy()))==[0] and str(d.iloc[0,0])=='open_time':numeric=numeric.iloc[1:]
            else:raise ValueError('Malformed numeric candle '+str(p))
        d=numeric;d.columns=['time','open','high','low','close','volume']
        d.time=pd.to_datetime(d.time.astype('int64'),unit='ms',utc=True);data.append(d.set_index('time'));months.append(month)
        fp=p.with_name(f'{symbol}-fundingRate-{month}.zip')
        if not fp.exists():raise RuntimeError('Funding archive missing for priced month '+symbol+' '+month)
        with zipfile.ZipFile(fp) as z:f=pd.read_csv(z.open(z.namelist()[0]))
        if list(f.columns)!=['calc_time','funding_interval_hours','last_funding_rate']:raise ValueError('Funding schema changed')
        fund.append(f)
    if not data:raise RuntimeError('No candles '+symbol)
    raw=pd.concat(data).sort_index();f=pd.concat(fund).sort_values('calc_time')
    assert not raw.index.has_duplicates and not f.calc_time.duplicated().any()
    assert ((raw.high>=raw[['open','low','close']].max(axis=1))&(raw.low<=raw[['open','high','close']].min(axis=1))&(raw[['open','high','low','close']]>0).all(axis=1)).all()
    times=pd.to_datetime(f.calc_time,unit='ms',utc=True).dt.round('s')
    if not ((times.dt.minute==0)&(times.dt.second==0)).all():raise RuntimeError('Unmodelled intrahour funding event')
    ns=times.astype('int64').to_numpy();interval=f.funding_interval_hours.to_numpy(float)
    if np.any(np.diff(ns)>np.maximum(interval[1:],interval[:-1])*3600e9*1.1):raise RuntimeError('Funding gap '+symbol)
    index=np.searchsorted(raw.index.asi8,ns,side='left');ok=index<len(raw)
    rates=np.zeros(len(raw));adverse=np.zeros(len(raw))
    np.add.at(rates,index[ok],f.last_funding_rate.to_numpy(float)[ok]);np.add.at(adverse,index[ok],interval[ok]/8*.0001)
    return raw,rates,adverse
def bars(d,m):
    g=d.resample(f'{m}min',origin='epoch');counts=g.close.count();b=g.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    return b[counts==m//5]
@njit(cache=True)
def rma(a,n):
    result=np.full(len(a),np.nan)
    if len(a)<n:return result
    result[n-1]=a[:n].mean()
    for k in range(n,len(a)):result[k]=(result[k-1]*(n-1)+a[k])/n
    return result
def anchor(d,m):
    a=bars(d,m);c=a.close;tr=pd.concat([a.high-a.low,(a.high-c.shift()).abs(),(a.low-c.shift()).abs()],axis=1).max(axis=1)
    a['atr']=rma(tr.to_numpy(float),14);return a
def mapped(raw,clock,a,cfg,longonly=False):
    n=cfg['length'];slow=n*cfg['slow_ratio'];c=a.close
    fast=c.ewm(span=n,adjust=False).mean() if cfg['family']=='ema' else c.rolling(n).mean()
    slower=c.ewm(span=slow,adjust=False).mean() if cfg['family']=='ema' else c.rolling(slow).mean()
    direction=np.where(fast>slower,1,np.where(fast<slower,-1,0)).astype(np.int64)
    if longonly:direction=np.maximum(direction,0)
    direction[:max(100,2*slow)]=0
    available=a.index.asi8+cfg['signal_minutes']*60*10**9;close=raw.index.asi8+5*60*10**9
    ix=np.searchsorted(available,close,side='right')-1;ok=ix>=0;safe=np.maximum(ix,0)
    assert (available[safe[ok]]<=close[ok]).all()
    gate=ok&np.isin(close,available)&(close%(clock*60*10**9)==0)
    atr=a.atr.to_numpy()[safe].copy();atr[~ok]=np.nan
    return direction[safe],gate,atr
def grid():
    return [dict(id=f'{f}_S{sm}_N{n}_Q{r}_R{mult}_C{cool}',family=f,signal_minutes=sm,length=n,slow_ratio=r,stop_atr=float(mult),cooldown=cool) for f,sm,n,r,mult,cool in itertools.product(['ema','sma'],[60,240,1440],[5,10,20,40],[2,5],[2,3,4,6],[0,1,3])]
BASE=dict(id='fixed_EMA10_50_4h_reversal',family='ema',signal_minutes=240,length=10,slow_ratio=5,stop_atr=4.,cooldown=0)
@njit(cache=True)
def simulate(o,h,l,c,rates,adverse,target,gate,atr,day,start,end,mult,cool,costmode,log=False):
    fee=.001*(2 if costmode==2 else 1);slip=.0005*(2 if costmode==2 else 1)
    balance=10000.;qty=0.;entry=0.;stop=0.;pending=99;request=0.;distance=0.;wait=0;entry_i=-1
    begin_cash=0.;entry_fee=0.;fund_trade=0.;all_fund=0.;peak=10000.;dd=0.;count=0;liqs=0;longs=0;shorts=0;turnover=0.
    curve=np.empty(end-start);ledger=[]
    for i in range(start,end):
        # Settlement before orders at an opening boundary, with observed-open mark proxy.
        if qty:
            funding=-qty*o[i]*rates[i]
            if costmode==3:funding-=abs(qty)*o[i]*adverse[i]
            balance+=funding;fund_trade+=funding;all_fund+=funding
        close_price=0.;reason=-1
        if qty:
            direction=1 if qty>0 else -1
            eqopen=balance+qty*(o[i]-entry)
            if eqopen<=abs(qty)*o[i]*.005:close_price=o[i];reason=4
            elif (direction==1 and o[i]<=stop) or (direction==-1 and o[i]>=stop):close_price=o[i];reason=0
            elif pending!=99 and pending!=direction:close_price=o[i];reason=1
        if reason>=0:
            sell=close_price*(1-slip if qty>0 else 1+slip);exitfee=abs(qty)*sell*fee
            balance+=qty*(sell-entry)-exitfee;turnover+=abs(qty)*sell
            if log:ledger.append((entry_i,i,1 if qty>0 else -1,balance-begin_cash,fund_trade,entry_fee+exitfee,reason))
            count+=1;liqs+=int(reason==4);qty=0.
            if reason in [0,4]:
                wait=cool
                if pending==99 or pending==direction:pending=99
        if pending in [-1,1] and qty==0 and balance>0:
            direction=pending;entry=o[i]*(1+slip*direction)
            size=min(request,balance*.005/distance,balance*.95/(entry*(1+fee)))
            qty=size*direction;begin_cash=balance;entry_fee=size*entry*fee;balance-=entry_fee
            stop=entry-direction*distance;entry_i=i;fund_trade=0.;turnover+=size*entry
            longs+=int(direction==1);shorts+=int(direction==-1)
        pending=99
        if qty:
            # Approximate 0.5% maintenance liquidation threshold, usually beyond the stop.
            liq=(qty*entry-balance)/(qty-.005*abs(qty))
            trigger=max(stop,liq) if qty>0 else min(stop,liq)
            adverseprice=min(l[i],c[i]) if qty>0 else max(h[i],c[i])
            marked=balance+qty*((max(trigger,adverseprice) if qty>0 else min(trigger,adverseprice))-entry)
            dd=max(dd,1-marked/peak)
            hit=(qty>0 and l[i]<=trigger) or (qty<0 and h[i]>=trigger)
            if hit:
                reason=2 if trigger==stop else 4;sell=trigger*(1-slip if qty>0 else 1+slip);exitfee=abs(qty)*sell*fee
                balance+=qty*(sell-entry)-exitfee;turnover+=abs(qty)*sell
                if log:ledger.append((entry_i,i,1 if qty>0 else -1,balance-begin_cash,fund_trade,entry_fee+exitfee,reason))
                count+=1;liqs+=int(reason==4);qty=0.;wait=cool
        equity=balance+qty*(c[i]-entry);curve[i-start]=equity;peak=max(peak,equity);dd=max(dd,1-equity/peak)
        if gate[i] and equity>0:
            want=target[i];current=1 if qty>0 else (-1 if qty<0 else 0)
            if current!=want:
                if current==0 and wait>0:wait-=1
                elif want==0:pending=0
                elif np.isfinite(atr[i]) and atr[i]>0:
                    distance=mult*atr[i];request=min(equity*.005/distance,equity*.95/c[i]);pending=want
    if qty:
        sell=c[end-1]*(1-slip if qty>0 else 1+slip);exitfee=abs(qty)*sell*fee
        balance+=qty*(sell-entry)-exitfee;turnover+=abs(qty)*sell
        if log:ledger.append((entry_i,end-1,1 if qty>0 else -1,balance-begin_cash,fund_trade,entry_fee+exitfee,3))
        count+=1;curve[-1]=balance;dd=max(dd,1-balance/peak)
    daily=[]
    for i in range(start,end):
        if i==end-1 or day[i]!=day[i+1]:daily.append(curve[i-start])
    prev=10000.;rs=0.;r2=0.
    for v in daily:
        ret=v/prev-1;rs+=ret;r2+=ret*ret;prev=v
    n=len(daily);mean=rs/n;var=max(0,(r2-n*mean*mean)/max(1,n-1));sharpe=mean/np.sqrt(var)*np.sqrt(365) if var>0 else 0.
    return np.array([curve[-1]/10000-1,sharpe,dd,count,longs,shorts,all_fund,turnover,liqs]),curve,ledger
def run(raw,fund,adverse,clock,a,cfg,start,end,cost=1,longonly=False,log=False,sig=None):
    sig=mapped(raw,clock,a,cfg,longonly) if sig is None else sig
    arrays=[raw[k].to_numpy(float) for k in ['open','high','low','close']]
    first=raw.index.searchsorted(pd.Timestamp(start,tz='UTC'));last=raw.index.searchsorted(pd.Timestamp(end,tz='UTC'))
    v,curve,ledger=simulate(*arrays,fund,adverse,*sig,raw.index.asi8//86400_000_000_000,first,last,cfg['stop_atr'],cfg['cooldown'],cost,log)
    years=(pd.Timestamp(end)-pd.Timestamp(start)).total_seconds()/31557600
    result=dict(ret=float(v[0]),cagr=float((max(0,1+v[0]))**(1/years)-1),sharpe=float(v[1]),maxdd=float(v[2]),trades=int(v[3]),long_entries=int(v[4]),short_entries=int(v[5]),funding_net=float(v[6]),turnover=float(v[7]),liquidations=int(v[8]))
    if log:
        records=[dict(entry_time=str(raw.index[int(t[0])]),exit_time=str(raw.index[int(t[1])]),direction=int(t[2]),pnl=t[3],funding=t[4],fees=t[5],reason=['gap_stop','reverse_or_exit','stop','terminal','liquidation_proxy'][int(t[6])]) for t in ledger]
        assert abs(sum(t['pnl'] for t in records)/10000-result['ret'])<1e-9
        return result,records
    return result
def score(rows):
    a=np.array([r['sharpe'] for r in rows]);draw=max(r['maxdd'] for r in rows)
    return float(np.median(a)-.5*a.std()+.2*np.quantile(a,.25)-5*max(0,draw-.2))
