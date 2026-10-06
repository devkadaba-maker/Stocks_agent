"""Deterministic causal spot simulator. Separate search metrics from detailed logs."""
from pathlib import Path
import zipfile,json,hashlib
import numpy as np
import pandas as pd
from numba import njit
ROOT=Path(__file__).resolve().parent
DEV=['XRPUSDT','ADAUSDT','LTCUSDT','LINKUSDT']
EXTRA=['DOGEUSDT','AVAXUSDT','ATOMUSDT','DOTUSDT']
FAMILIES=['ema','donchian','momentum','keltner','macd','rsi_pullback','bollinger','supertrend']
EXEC=[5,10,60,240,1440]

def load(symbol,stage,through):
    if stage=='sealed' and not (ROOT/'output/selected.json').exists():
        raise RuntimeError('Final prices are sealed until selection freeze')
    paths=sorted((ROOT/'data'/stage).glob(symbol+'-5m-*.zip'))
    frames=[]
    for p in paths:
        month=p.name[-11:-4]
        if month>through:continue
        if symbol in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT'] and '2020-01'<=month<='2026-09':
            raise RuntimeError('Previously used candle blocked')
        with zipfile.ZipFile(p) as z:d=pd.read_csv(z.open(z.namelist()[0]),header=None).iloc[:,:6]
        d.columns=['time','open','high','low','close','volume'];d=d.apply(pd.to_numeric)
        d.time=pd.to_datetime(d.time,unit='us' if d.time.iloc[0]>1e14 else 'ms',utc=True)
        frames.append(d)
    if not frames:raise ValueError('Missing data for '+symbol)
    d=pd.concat(frames).sort_values('time').set_index('time')
    if d.index.has_duplicates:raise ValueError('Duplicate candle timestamps')
    if not ((d.high>=d[['open','close','low']].max(axis=1))&(d.low<=d[['open','close','high']].min(axis=1))&(d.volume>=0)&(d[['open','high','low','close']]>0).all(axis=1)).all():raise ValueError('Malformed candle')
    return d

def bars(d,minutes):
    if minutes==5:return d.copy()
    g=d.resample(f'{minutes}min',origin='epoch',label='left',closed='left')
    n=g.close.count();b=g.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    return b[n==minutes//5]

@njit(cache=True)
def wilder(a,n):
    o=np.full(len(a),np.nan);total=0.;count=0;seeded=False
    for i in range(len(a)):
        if not np.isfinite(a[i]):
            if seeded:o[i]=o[i-1]
            continue
        if not seeded:
            total+=a[i];count+=1
            if count==n:o[i]=total/n;seeded=True
        else:o[i]=(o[i-1]*(n-1)+a[i])/n
    return o

def rma(s,n):return pd.Series(wilder(s.to_numpy(float),n),index=s.index)
def ema(s,n):return s.ewm(span=n,adjust=False).mean()

def indicators(d):
    d=d.copy();c=d.close
    tr=pd.concat([d.high-d.low,(d.high-c.shift()).abs(),(d.low-c.shift()).abs()],axis=1).max(axis=1)
    d['atr']=rma(tr,14);delta=c.diff()
    gains=rma(delta.clip(lower=0),14);losses=rma((-delta).clip(lower=0),14)
    d['rsi']=100-100/(1+gains/losses)
    up=d.high.diff();dn=-d.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.),index=d.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.),index=d.index)
    pdi=100*rma(plus,14)/d.atr;mdi=100*rma(minus,14)/d.atr
    d['adx']=rma((100*(pdi-mdi).abs()/(pdi+mdi)).fillna(0),14)
    return d

@njit(cache=True)
def st_direction(c,u,l):
    bull=np.zeros(len(c),np.bool_);prev=np.nan;direction=1
    for i in range(1,len(c)):
        if not np.isfinite(u[i]):continue
        pu=u[i-1] if np.isfinite(u[i-1]) else u[i]
        pl=l[i-1] if np.isfinite(l[i-1]) else l[i]
        u[i]=u[i] if u[i]<pu or c[i-1]>pu else pu
        l[i]=l[i] if l[i]>pl or c[i-1]<pl else pl
        if not np.isfinite(prev):direction=1
        elif prev==pu:direction=-1 if c[i]>u[i] else 1
        else:direction=1 if c[i]<l[i] else -1
        prev=l[i] if direction==-1 else u[i];bull[i]=direction==-1
    return bull

def raw_signals(d,f,n,adx_min):
    c=d.close;atr=d.atr;slow=ema(c,n*5)
    if f=='ema':en=ema(c,n)>slow;ex=~en
    elif f=='donchian':en=c>d.high.rolling(n).max().shift();ex=c<d.low.rolling(max(1,n//2)).min().shift()
    elif f=='momentum':en=c>c.shift(n);ex=c<=c.shift(n)
    elif f=='keltner':mid=ema(c,n);en=c>mid+2*atr;ex=c<mid
    elif f=='macd':
        line=ema(c,n)-ema(c,n*2);hist=line-ema(line,max(2,round(n*.75)))
        en=(hist>0)&(c>slow);ex=(hist<=0)|(c<=slow)
    elif f=='rsi_pullback':en=(c>slow)&(d.rsi<40);ex=(d.rsi>60)|(c<=slow)
    elif f=='bollinger':mid=c.rolling(n).mean();en=c<mid-2*c.rolling(n).std(ddof=0);ex=c>=mid
    elif f=='supertrend':
        half=(d.high+d.low)/2
        bull=pd.Series(st_direction(c.to_numpy(),(half+3*atr).to_numpy(),(half-3*atr).to_numpy()),index=d.index)
        en=bull&(c>slow);ex=(~bull)|(c<=slow)
    else:raise ValueError(f)
    if adx_min:en=en&(d.adx>adx_min)
    en=en.fillna(False);ex=ex.fillna(False)
    en.iloc[:max(100,5*n)]=False
    return en.to_numpy(bool),ex.to_numpy(bool)

def mapped(exec_bars,minutes,anchor,signal_minutes,cfg,baseline=False):
    if baseline:
        en=(ema(anchor.close,40)>ema(anchor.close,200)).to_numpy(bool)
        ex=~en;en[:400]=False
    else:en,ex=raw_signals(anchor,cfg['family'],cfg['length'],cfg['adx_min'])
    close_times=exec_bars.index+pd.Timedelta(minutes=minutes)
    available=anchor.index+pd.Timedelta(minutes=signal_minutes)
    # Only the last confirmed signal-bar state is observable at execution close.
    idx=available.get_indexer(close_times,method='pad')
    ok=idx>=0;safe=np.maximum(idx,0)
    gate=close_times.isin(available)
    out_en=en[safe]&ok&gate;out_ex=ex[safe]&ok&gate
    atr=anchor.atr.to_numpy()[safe].copy();atr[~ok]=np.nan
    assert not (available.to_numpy()[safe[ok]]>close_times.to_numpy()[ok]).any()
    return out_en,out_ex,atr,np.asarray(gate,bool)

def grid():
    import itertools
    out=[]
    for f,sm,n,adx,stop,trail,cool in itertools.product(FAMILIES,[240,1440],[10,20,40,80],[0,20],[2.,3.,4.,6.],[False,True],[0,3]):
        cfg=dict(family=f,signal_minutes=sm,length=n,adx_min=adx,stop_atr=stop,trailing=trail,cooldown=cool)
        cfg['id']=f'{f}_S{sm}_N{n}_A{adx}_R{int(stop)}_T{int(trail)}_C{cool}'
        out.append(cfg)
    assert len(out)==2048
    return out

BASELINE=dict(id='original_daily_EMA40_200',family='ema',signal_minutes=1440,length=40,adx_min=0,stop_atr=3.,trailing=True,cooldown=0)

@njit(cache=True)
def simulate(op,hi,lo,cl,at,en,ex,gate,day,first,last,trail,mult,cool,cost,log=False):
    cash=10000.;qty=0.;pending=0;pending_qty=0.;dist=0.;stop=np.nan;basis=0.;entry_i=-1;wait=0
    fee=.001*cost;slip=.0005*cost;peak=10000.;maxdd=0.;count=0;wins=0;gp=0.;gl=0.;exposure=0
    curve=np.empty(last-first);ledger=[];daily=[];daily_day=[];last_day=day[first]
    for i in range(first,last):
        just_exited=False
        if qty>0 and op[i]<=stop:
            fill=op[i]*(1-slip);value=qty*fill*(1-fee);pnl=value-basis;cash+=value
            if log:ledger.append((entry_i,i,pnl,0))
            count+=1;wins+=int(pnl>0);gp+=max(pnl,0.);gl-=min(pnl,0.);qty=0.;pending=0;wait=cool;just_exited=True
        if pending==-1 and qty>0:
            fill=op[i]*(1-slip);value=qty*fill*(1-fee);pnl=value-basis;cash+=value
            if log:ledger.append((entry_i,i,pnl,1))
            count+=1;wins+=int(pnl>0);gp+=max(pnl,0.);gl-=min(pnl,0.);qty=0.;wait=cool;just_exited=True
        elif pending==1 and qty==0:
            fill=op[i]*(1+slip);qty=min(pending_qty,cash*.95/(fill*(1+fee)))
            basis=qty*fill*(1+fee);cash-=basis;stop=fill-dist;entry_i=i
        pending=0
        if qty>0:
            exposure+=1
            trough=cash+qty*max(stop,min(lo[i],cl[i]))
            maxdd=max(maxdd,1-trough/peak)
            if lo[i]<=stop:
                fill=stop*(1-slip);value=qty*fill*(1-fee);pnl=value-basis;cash+=value
                if log:ledger.append((entry_i,i,pnl,2))
                count+=1;wins+=int(pnl>0);gp+=max(pnl,0.);gl-=min(pnl,0.);qty=0.;wait=cool;just_exited=True
        eq=cash+qty*cl[i];peak=max(peak,eq);maxdd=max(maxdd,1-eq/peak);curve[i-first]=eq
        if qty>0:
            if trail and np.isfinite(at[i]):stop=max(stop,cl[i]-mult*at[i])
            if ex[i]:pending=-1
        elif gate[i]:
            if wait>0:
                if not just_exited:wait-=1
            elif en[i] and np.isfinite(at[i]) and at[i]>0:
                dist=mult*at[i]
                pending_qty=min(eq*.005/dist,eq*.95/(cl[i]*(1+fee)));pending=1
        if i==last-1 or day[i+1]!=day[i]:
            daily.append(eq);daily_day.append(day[i])
    if qty>0:
        fill=cl[last-1]*(1-slip);value=qty*fill*(1-fee);pnl=value-basis;cash+=value
        if log:ledger.append((entry_i,last-1,pnl,3))
        count+=1;wins+=int(pnl>0);gp+=max(pnl,0.);gl-=min(pnl,0.);curve[-1]=cash;daily[-1]=cash;maxdd=max(maxdd,1-cash/peak)
    previous=10000.;sr=0.;ss=0.
    for v in daily:
        r=v/previous-1;sr+=r;ss+=r*r;previous=v
    nr=len(daily);mean=sr/nr;var=max(0.,(ss-nr*mean*mean)/max(1,nr-1));sharpe=mean/np.sqrt(var)*np.sqrt(365.) if var>0 else 0.
    ret=curve[-1]/10000.-1
    metrics=np.array([ret,sharpe,maxdd,float(count),gp/gl if gl>0 else np.nan,float(wins)/count if count else 0.,exposure/(last-first)])
    return metrics,curve,np.array(daily),np.array(daily_day),ledger

def prepared(d,minutes):
    arrays=[np.ascontiguousarray(d[k].to_numpy(float)) for k in ['open','high','low','close']]
    day=(d.index.asi8//86_400_000_000_000).astype(np.int64)
    return arrays,day

def run(d,minutes,anchor,cfg,start,end,cost=1,baseline=False,log=False,sig=None,prep=None):
    if sig is None:sig=mapped(d,minutes,anchor,cfg['signal_minutes'],cfg,baseline)
    en,ex,at,gate=sig
    arrays,day=prepared(d,minutes) if prep is None else prep
    start=pd.Timestamp(start,tz='UTC');end=pd.Timestamp(end,tz='UTC')
    first=int(d.index.searchsorted(start));last=int(d.index.searchsorted(end))
    if last-first<100:raise ValueError('Insufficient observed bars')
    v,curve,daily,days,ledger=simulate(*arrays,at,en,ex,gate,day,first,last,cfg['trailing'],cfg['stop_atr'],cfg['cooldown'],cost,log)
    years=(d.index[last-1]-d.index[first]).total_seconds()/31557600
    m=dict(ret=float(v[0]),cagr=float((1+v[0])**(1/max(years,1/365))-1),sharpe=float(v[1]),maxdd=float(v[2]),trades=int(v[3]),profit_factor=float(v[4]),win_rate=float(v[5]),exposure=float(v[6]),first=str(d.index[first]),last=str(d.index[last-1]))
    fee=.001*cost;slip=.0005*cost
    q=9500/(arrays[0][first]*(1+slip)*(1+fee));m['buyhold_ret']=float((500+q*arrays[3][last-1]*(1-slip)*(1-fee))/10000-1)
    if log:
        logs=[dict(entry_time=str(d.index[int(t[0])]),exit_time=str(d.index[int(t[1])]),pnl=t[2],reason=['gap_stop','signal','stop','terminal'][int(t[3])]) for t in ledger]
        ser=pd.Series(daily,index=pd.to_datetime(days,unit='D',utc=True))
        return m,ser,logs
    return m

def score(rows):
    a=np.array([r['sharpe'] for r in rows]);draw=max(r['maxdd'] for r in rows)
    return float(np.median(a)-.5*a.std()+.2*np.quantile(a,.25)-max(0,draw-.15)*5)

def hash_file(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
