import json,datetime
import pandas as pd,numpy as np
import engine as e
def main():
    out=e.OUT
    if (out/'final_started.json').exists():raise RuntimeError('Final data already assessed; no retuning')
    frozen=e.hash_file(out/'selected.json');assert frozen==(out/'selected.sha256').read_text()
    cfg=json.loads((out/'selected.json').read_text());rows=[];ledger=[];coverage=[]
    (out/'final_started.json').write_text(json.dumps(dict(selection_sha256=frozen,started_at=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2))
    for s in e.DEV+e.EXTRA+['BTCUSDT']:
        stage='btc' if s=='BTCUSDT' else 'final';raw,f,a=e.load(s,stage,'2026-09')
        if s in e.DEV:
            warm,wf,wa=e.load(s,'development','2023-12');raw=pd.concat([warm,raw]);f=np.r_[wf,f];a=np.r_[wa,a]
        assert raw.index.is_monotonic_increasing and not raw.index.has_duplicates
        coverage.append(dict(symbol=s,first=str(raw.index[0]),last=str(raw.index[-1]),bars=len(raw),price_gaps=int((raw.index.to_series().diff()>pd.Timedelta(minutes=5)).sum()),nonzero_funding_bars=int((f!=0).sum())))
        anchors={m:e.anchor(raw,m) for m in set([cfg['signal_minutes'],240])}
        for clock in e.CLOCKS:
            for model,c,longonly in [('selected',cfg,False),('simple_reversal',e.BASE,False),('long_only',e.BASE,True)]:
                sig=e.mapped(raw,clock,anchors[c['signal_minutes']],c,longonly)
                for cost in [1,2,3]:
                    r,tr=e.run(raw,f,a,clock,anchors[c['signal_minutes']],c,'2024-01-01','2026-10-01',cost,longonly,True,sig)
                    r.update(symbol=s,clock=clock,model=model,cost=cost,stage='BTC_retrospective' if s=='BTCUSDT' else 'held_out');rows.append(r)
                    if cost==1:ledger.extend([dict(t,symbol=s,clock=clock,model=model) for t in tr])
        print('FINAL assessment completed',s,flush=True)
    assert frozen==e.hash_file(out/'selected.json')
    df=pd.DataFrame(rows);df.to_csv(out/'final_results.csv',index=False);pd.DataFrame(ledger).to_csv(out/'trades.csv',index=False);pd.DataFrame(coverage).to_csv(out/'coverage.csv',index=False)
    held=df[df.stage=='held_out'];primary=held[(held.model=='selected')&(held.cost==1)];doubled=held[(held.model=='selected')&(held.cost==2)];adverse=held[(held.model=='selected')&(held.cost==3)]
    baseline=held[(held.model=='simple_reversal')&(held.cost==1)];p=primary.merge(baseline,on=['symbol','clock'],suffixes=('_selected','_base'))
    promoted=bool(primary.ret.median()>0 and doubled.ret.median()>0 and adverse.ret.median()>0 and (primary.ret>0).mean()>=.6 and primary.maxdd.max()<=.2 and (p.sharpe_selected-p.sharpe_base).median()>0)
    summary=dict(selection_sha256=frozen,promotion_passed=promoted,primary_median_return=float(primary.ret.median()),double_cost_median_return=float(doubled.ret.median()),adverse_funding_median_return=float(adverse.ret.median()),profitable_panels=int((primary.ret>0).sum()),primary_panels=len(primary),worst_primary_drawdown=float(primary.maxdd.max()),median_sharpe=float(primary.sharpe.median()),median_paired_sharpe_improvement=float((p.sharpe_selected-p.sharpe_base).median()),primary_liquidation_proxy_events=int(primary.liquidations.sum()),ledger_rows=len(ledger))
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print('SUMMARY',json.dumps(summary),flush=True)
    print(df[(df.model=='selected')&(df.clock.isin([5,10]))][['symbol','clock','cost','ret','maxdd','trades','long_entries','short_entries','funding_net']].to_string(index=False),flush=True)
if __name__=='__main__':main()
