"""Read-only reproduction of frozen final metrics, including funding."""
import json
import pandas as pd,numpy as np
import engine as e
def main():
    expected=pd.read_csv(e.OUT/'final_results.csv');cfg=json.loads((e.OUT/'selected.json').read_text());frozen=e.hash_file(e.OUT/'selected.json')
    assert frozen==(e.OUT/'selected.sha256').read_text()
    maxdiff=0.;count=0
    for s in e.DEV+e.EXTRA+['BTCUSDT']:
        stage='btc' if s=='BTCUSDT' else 'final';raw,f,a=e.load(s,stage,'2026-09')
        if s in e.DEV:
            warm,wf,wa=e.load(s,'development','2023-12');raw=pd.concat([warm,raw]);f=np.r_[wf,f];a=np.r_[wa,a]
        anchors={m:e.anchor(raw,m) for m in set([cfg['signal_minutes'],240])}
        for clock in e.CLOCKS:
            for model,c,longonly in [('selected',cfg,False),('simple_reversal',e.BASE,False),('long_only',e.BASE,True)]:
                sig=e.mapped(raw,clock,anchors[c['signal_minutes']],c,longonly)
                for cost in [1,2,3]:
                    r=e.run(raw,f,a,clock,anchors[c['signal_minutes']],c,'2024-01-01','2026-10-01',cost,longonly,sig=sig)
                    old=expected[(expected.symbol==s)&(expected.clock==clock)&(expected.model==model)&(expected.cost==cost)].iloc[0]
                    for k in ['ret','sharpe','maxdd','trades','funding_net']:
                        diff=abs(r[k]-old[k]);maxdiff=max(maxdiff,diff);assert diff<1e-8,(s,clock,model,cost,k,diff)
                    count+=1
    assert frozen==e.hash_file(e.OUT/'selected.json')
    print('PASS:',count,'frozen cases reproduced; maximum numerical difference',maxdiff)
if __name__=='__main__':main()
