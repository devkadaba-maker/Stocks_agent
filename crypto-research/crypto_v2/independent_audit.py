"""Read-only reproduction of the frozen final result. Does not import engine.py.

No strategy selection, tuning or new final-test evidence occurs here. All original
research outputs are treated as immutable comparison targets.
"""
from pathlib import Path
import hashlib, json, zipfile, datetime
import numpy as np
import pandas as pd
from numba import njit

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output'
PROOF = ROOT / 'proof'
PROOF.mkdir(exist_ok=True)

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

@njit
def averages(close, high, low, fast, slow):
    f = np.empty(len(close)); s = np.empty(len(close))
    atr = np.full(len(close), np.nan)
    f[0] = close[0]; s[0] = close[0]
    af = 2. / (fast + 1); ass = 2. / (slow + 1)
    total = 0.
    for j in range(len(close)):
        tr = high[j] - low[j]
        if j:
            tr = max(tr, abs(high[j]-close[j-1]), abs(low[j]-close[j-1]))
            f[j] = af*close[j] + (1-af)*f[j-1]
            s[j] = ass*close[j] + (1-ass)*s[j-1]
        if j < 14:
            total += tr
            if j == 13: atr[j] = total / 14
        else:
            atr[j] = (13*atr[j-1] + tr) / 14
    return f, s, atr

def source(symbol, manifests):
    tables = []
    # Exactly the coverage of the already-frozen assessment, including warmup.
    for m in manifests:
        if m['symbol'] != symbol or m['status'] != 'verified': continue
        p = ROOT/'data'/m['stage']/f"{symbol}-5m-{m['month']}.zip"
        with zipfile.ZipFile(p) as z:
            table = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=range(6))
        table.columns = ['timestamp','open','high','low','close','volume']
        stamp = table.pop('timestamp').to_numpy(np.int64)
        table.index = pd.to_datetime(stamp, unit='us' if stamp[0] > 10**14 else 'ms', utc=True)
        tables.append(table)
    raw = pd.concat(tables).sort_index()
    assert not raw.index.has_duplicates
    return raw

def aggregate(raw, minutes):
    if minutes == 5: return raw
    grouping = raw.resample(f'{minutes}min', origin='epoch')
    count = grouping['close'].count()
    result = grouping.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    return result.loc[count == minutes//5]

@njit
def transact(o, h, l, c, decision, bullish, volatility, multiple, trailing, cost):
    # An order is recorded at a close, then processed at the next observed open.
    commission = cost * .001
    impact = cost * .0005
    cash = 10000.; units = 0.; requested = 0.; action = 0
    stop = 0.; distance = 0.; paid = 0.; buy = 0.; buyfee = 0.
    entered = -1; decided = -1; pending_decision = -1; initial_stop = 0.
    peak = 10000.; worst = 0.
    equity = np.empty(len(c))
    records = []
    for k in range(len(c)):
        # An opening gap through a stop has precedence over a close-triggered exit.
        sell = 0.; why = -1
        if units > 0:
            if o[k] <= stop:
                sell = o[k]*(1-impact); why = 0
            elif action == -1:
                sell = o[k]*(1-impact); why = 1
        if why >= 0:
            fee = units*sell*commission
            proceeds = units*sell-fee
            records.append((entered,k,buy,sell,units,buyfee,fee,paid,proceeds-paid,why,initial_stop,decided))
            cash += proceeds; units = 0.; action = 0
        if action == 1 and units == 0:
            buy = o[k]*(1+impact)
            units = min(requested, .95*cash/(buy*(1+commission)))
            buyfee = units*buy*commission
            paid = units*buy*(1+commission)
            cash -= paid
            stop = buy-distance; initial_stop = stop
            entered = k; decided = pending_decision
        action = 0
        if units > 0:
            marked_low = cash + units*max(stop,min(l[k],c[k]))
            worst = max(worst,1-marked_low/peak)
            if l[k] <= stop:
                sell = stop*(1-impact)
                fee = units*sell*commission
                proceeds = units*sell-fee
                records.append((entered,k,buy,sell,units,buyfee,fee,paid,proceeds-paid,2,initial_stop,decided))
                cash += proceeds; units = 0.
        marked = cash + units*c[k]
        equity[k] = marked
        peak = max(peak,marked); worst = max(worst,1-marked/peak)
        if units > 0:
            if trailing and np.isfinite(volatility[k]):
                stop = max(stop,c[k]-multiple*volatility[k])
            if decision[k] and not bullish[k]: action = -1
        elif decision[k] and bullish[k] and np.isfinite(volatility[k]) and volatility[k] > 0:
            distance = multiple*volatility[k]
            requested = min(marked*.005/distance, marked*.95/(c[k]*(1+commission)))
            action = 1; pending_decision = k
    if units > 0:
        sell = c[-1]*(1-impact)
        fee = units*sell*commission
        proceeds = units*sell-fee
        records.append((entered,len(c)-1,buy,sell,units,buyfee,fee,paid,proceeds-paid,3,initial_stop,decided))
        cash += proceeds; equity[-1] = cash; worst = max(worst,1-cash/peak)
    return equity, worst, records

def main():
    originals = {str(p.relative_to(ROOT)): digest(p) for p in [ROOT/'protocol.json', *OUT.glob('*')] if p.is_file()}
    audit = json.loads((OUT/'audit_manifest.json').read_text())
    assert digest(ROOT/'protocol.json') == audit['protocol']
    assert digest(OUT/'selected.json') == audit['selection'] == (OUT/'selected.sha256').read_text()
    assert digest(OUT/'shortlist.json') == audit['shortlist']
    assert digest(OUT/'candidate_registry.json') == audit['registry']
    manifests = sum([json.loads((ROOT/'data'/p).read_text()) for p in ['manifest.json','manifest_warmup.json']], [])
    prohibited = [m for m in manifests if m['symbol'] in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT'] and '2020-01' <= m['month'] <= '2026-09']
    assert not prohibited
    verified = [m for m in manifests if m['status'] == 'verified']
    assert len({(m['symbol'],m['month'],m['stage']) for m in verified}) == len(verified)
    for m in verified:
        p = ROOT/'data'/m['stage']/f"{m['symbol']}-5m-{m['month']}.zip"
        assert digest(p) == m['sha256']
    registry = json.loads((OUT/'candidate_registry.json').read_text())
    train = pd.read_csv(OUT/'training_panels.csv')
    validation = pd.read_csv(OUT/'validation_panels.csv')
    assert len(registry) == 2048 and len({c['id'] for c in registry}) == 2048
    assert len(train) == 98304 and train.id.nunique() == 2048
    assert len(validation) == 480 and validation.id.nunique() == 12
    vr = pd.read_csv(OUT/'validation_ranking.csv')
    cfg = json.loads((OUT/'selected.json').read_text())
    assert vr[vr.eligible].sort_values('score',ascending=False).iloc[0].id == cfg['id']
    assert (cfg['family'], cfg['signal_minutes'], cfg['length'], cfg['stop_atr'], cfg['trailing'],cfg['cooldown'],cfg['adx_min']) == ('ema',240,10,4.,False,0,0)
    expected = pd.read_csv(OUT/'final_test.csv')
    saved_trades = pd.read_csv(OUT/'trades.csv')
    saved_daily = pd.read_csv(OUT/'daily_equity.csv',index_col=0,parse_dates=True)
    comparisons=[]; detailed=[]; samples=[]; max_pnl_difference=0.; max_curve_difference=0.; checked_trades=0
    for symbol in expected.symbol.drop_duplicates():
        raw = source(symbol,manifests)
        start = pd.Timestamp('2018-01-01' if symbol in ['BTCUSDT','ETHUSDT'] else '2024-01-01',tz='UTC')
        end = pd.Timestamp('2020-01-01' if symbol in ['BTCUSDT','ETHUSDT'] else '2026-10-01',tz='UTC')
        anchors = {}
        for sm,fast,slow,warm in [(240,10,50,100),(1440,40,200,400)]:
            a = aggregate(raw,sm)
            f,s,atr = averages(a.close.to_numpy(float),a.high.to_numpy(float),a.low.to_numpy(float),fast,slow)
            up = f>s; up[:warm] = False
            # Warmup affects entries only; an EMA bearish state drives exits.
            anchors[sm] = (a.index.asi8 + sm*60*10**9, up, f>s, atr)
        for minutes in [5,10,60,240,1440]:
            full = aggregate(raw,minutes)
            d = full[(full.index >= start)&(full.index < end)]
            close_time = d.index.asi8 + minutes*60*10**9
            o,h,l,c = [np.ascontiguousarray(d[k].to_numpy(float)) for k in ['open','high','low','close']]
            for model,sm,mult,trailing in [('optimized',240,4.,False),('original',1440,3.,True)]:
                available,entry_up,ema_up,atr = anchors[sm]
                ix = np.searchsorted(available,close_time,side='right')-1
                assert (ix >= 0).all() and (available[ix] <= close_time).all()
                gate = np.isin(close_time,available)
                # Entry warmup has elapsed before any test start except original BTC/ETH.
                trend = ema_up[ix].copy()
                warm_not_ready = ~entry_up[ix] & trend
                decisions = gate.copy(); decisions[warm_not_ready] = False
                for cost in [1,2]:
                    curve,dd,trades = transact(o,h,l,c,decisions,trend,atr[ix],mult,trailing,cost)
                    days = pd.Series(curve,index=d.index).groupby(d.index.floor('D')).last()
                    dr = days.to_numpy()/np.r_[10000.,days.to_numpy()[:-1]]-1
                    sharpe = dr.mean()/dr.std(ddof=1)*np.sqrt(365) if dr.std(ddof=1)>0 else 0.
                    ret = curve[-1]/10000-1
                    old = expected[(expected.symbol==symbol)&(expected.execution_minutes==minutes)&(expected.model==model)&(expected.cost==cost)].iloc[0]
                    comp = dict(symbol=symbol,execution_minutes=minutes,model=model,cost=cost,reproduced_return=ret,saved_return=float(old.ret),return_difference=ret-old.ret,reproduced_sharpe=sharpe,saved_sharpe=float(old.sharpe),sharpe_difference=sharpe-old.sharpe,reproduced_drawdown=dd,saved_drawdown=float(old.maxdd),drawdown_difference=dd-old.maxdd,reproduced_trades=len(trades),saved_trades=int(old.trades))
                    assert abs(comp['return_difference']) < 1e-10, comp
                    assert abs(comp['sharpe_difference']) < 1e-8, comp
                    assert abs(comp['drawdown_difference']) < 1e-10, comp
                    assert len(trades) == old.trades, comp
                    assert abs(sum(t[8] for t in trades)-10000*ret)<1e-7
                    comparisons.append(comp)
                    if cost == 1:
                        target = saved_trades[(saved_trades.symbol==symbol)&(saved_trades.execution_minutes==minutes)&(saved_trades.model==model)].reset_index(drop=True)
                        assert len(target) == len(trades)
                        aligned = saved_daily[f'{symbol}_{minutes}_{model}'].dropna()
                        assert aligned.index.equals(days.index)
                        delta = float(np.max(np.abs(aligned.to_numpy()-days.to_numpy())))
                        assert delta < 1e-7
                        max_curve_difference = max(max_curve_difference,delta)
                        for j,t in enumerate(trades):
                            entered,exited,buy,sell,qty,buyfee,sellfee,basis,pnl,reason,stop,decided=t
                            why=['gap_stop','signal','stop','terminal'][int(reason)]
                            assert pd.Timestamp(target.iloc[j].entry_time)==d.index[int(entered)]
                            assert pd.Timestamp(target.iloc[j].exit_time)==d.index[int(exited)]
                            assert target.iloc[j].reason == why
                            pdiff=abs(target.iloc[j].pnl-pnl)
                            assert pdiff<1e-7
                            max_pnl_difference=max(max_pnl_difference,pdiff);checked_trades+=1
                            row=dict(symbol=symbol,execution_minutes=minutes,model=model,entry_decision_time=str(d.index[int(decided)]+pd.Timedelta(minutes=minutes)),entry_time=str(d.index[int(entered)]),exit_time=str(d.index[int(exited)]),quantity=qty,entry_fill=buy,exit_fill=sell,entry_fee=buyfee,exit_fee=sellfee,entry_total=basis,pnl=pnl,reason=why,initial_stop=stop)
                            assert pd.Timestamp(row['entry_decision_time']) <= pd.Timestamp(row['entry_time'])
                            detailed.append(row)
                            if symbol=='XRPUSDT' and minutes==5 and model=='optimized' and j<3:samples.append(row)
        print('INDEPENDENT REPRODUCTION PASS',symbol,flush=True)
    for p,h in originals.items(): assert digest(ROOT/p)==h, 'Original output changed: '+p
    comp=pd.DataFrame(comparisons);comp.to_csv(PROOF/'reproduction_comparison.csv',index=False)
    pd.DataFrame(detailed).to_csv(PROOF/'reproduced_trade_ledger.csv',index=False)
    modern = expected[(expected.stage=='final')&(expected.cost==1)]
    new=modern[modern.model=='optimized'];old=modern[modern.model=='original']
    paired=new.merge(old,on=['symbol','execution_minutes'],suffixes=('_new','_old'))
    stress=expected[(expected.stage=='final')&(expected.cost==2)&(expected.model=='optimized')]
    summary=dict(audit_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),method='Separate indicator and execution implementation; original engine not imported; frozen results only; no new strategy selection',verified_source_archives=len(verified),excluded_prior_candle_requests=len(prohibited),training_configurations=len(registry),training_rows=len(train),validation_rows=len(validation),reproduced_final_panels=len(comp),matched_primary_trades=checked_trades,max_absolute_return_difference=float(comp.return_difference.abs().max()),max_absolute_sharpe_difference=float(comp.sharpe_difference.abs().max()),max_absolute_drawdown_difference=float(comp.drawdown_difference.abs().max()),max_absolute_trade_pnl_difference_dollars=max_pnl_difference,max_absolute_daily_equity_difference_dollars=max_curve_difference,all_original_outputs_unchanged=True,selected_sha256=digest(OUT/'selected.json'),better_sharpe_panels=int((paired.sharpe_new>paired.sharpe_old).sum()),modern_primary_panels=len(new),profitable_modern_panels=int((new.ret>0).sum()),modern_median_return=float(new.ret.median()),modern_stressed_median_return=float(stress.ret.median()),modern_worst_drawdown=float(new.maxdd.max()),profitability_promotion_passed=False,sample_trades=samples,limitations=['A reproduction is not new independent out-of-sample evidence.','The two implementations share data and stated modelling assumptions.','Source hashes are compared against previously recorded publisher checksums; no fresh network verification is asserted.','Local hashes prove content consistency, not independently witnessed preregistration or an immutable access history.','The Pine Script has not been compiled or verified in TradingView; this audit covers the Python model.','Synthetic causality checks and timestamp availability tests do not establish live profitability.','Coins and execution timeframes are correlated; 40 panels are not 40 independent trials.'])
    (PROOF/'proof_summary.json').write_text(json.dumps(summary,indent=2))
    (PROOF/'immutable_input_hashes.json').write_text(json.dumps(originals,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in ['sample_trades','limitations']},indent=2),flush=True)

if __name__=='__main__': main()
