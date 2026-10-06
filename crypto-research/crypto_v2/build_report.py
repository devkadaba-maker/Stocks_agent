from pathlib import Path
import json,html,zipfile
import pandas as pd,numpy as np
import engine as e
ROOT=e.ROOT;OUT=ROOT/'output'
s=json.loads((OUT/'summary.json').read_text());cfg=json.loads((OUT/'selected.json').read_text());audit=json.loads((OUT/'audit_manifest.json').read_text())
df=pd.read_csv(OUT/'final_test.csv');normal=df[(df.stage=='final')&(df.cost==1)];stress=df[(df.stage=='final')&(df.cost==2)]
modern=normal[normal.model=='optimized'];inc=normal[normal.model=='original'];historical=df[(df.stage=='historical_transfer')&(df.cost==1)]
proxy=pd.read_csv(OUT/'pine_cost_proxy.csv');pn=df[(df.model=='optimized')&(df.cost==1)].merge(proxy,on=['symbol','execution_minutes'],suffixes=('_price','_proxy'));proxy_delta=float((pn.ret_price-pn.ret_proxy).abs().max())
labels={5:'5m',10:'10m',60:'1h',240:'4h',1440:'1D'}

def table(data,columns):
    d=data[columns].copy()
    rename={'symbol':'Asset','execution_minutes':'Execution chart','model':'Model','cost':'Cost multiplier','ret':'Net return','cagr':'Annual return','sharpe':'Daily Sharpe','maxdd':'Max drawdown','trades':'Trades','buyhold_ret':'Buy & hold','profit_factor':'Profit factor','win_rate':'Win rate','exposure':'Time in market','id':'Configuration','score':'Consistency score','plateau_score':'Plateau score','median_sharpe':'Median Sharpe','median_cagr':'Median annual return','eligible':'Trade-count eligible','year':'Year'}
    for c in columns:
        if c=='execution_minutes':d[c]=d[c].map(labels)
        elif c in ['ret','cagr','maxdd','buyhold_ret','win_rate','exposure','median_cagr']:d[c]=d[c].map(lambda v:f'{100*v:.2f}%')
        elif c in ['sharpe','profit_factor','score','plateau_score','median_sharpe']:d[c]=d[c].map(lambda v:f'{v:.3f}')
    return '<div class="table">'+d.rename(columns=rename).to_html(index=False,border=0)+'</div>'

paired=normal[normal.execution_minutes.isin([5,10])].pivot(index=['symbol','execution_minutes'],columns='model',values='ret').reset_index()
paired['execution_minutes']=paired.execution_minutes.map(labels)
for c in ['original','optimized']:paired[c]=paired[c].map(lambda v:f'{v*100:+.2f}%')
paired=paired.rename(columns={'symbol':'Asset','execution_minutes':'Chart','original':'Original','optimized':'Optimized'}).to_html(index=False,border=0)
coverage=pd.read_csv(OUT/'coverage.csv')
train=pd.read_csv(OUT/'training_ranking.csv').sort_values('plateau_score',ascending=False)
vr=pd.read_csv(OUT/'validation_ranking.csv').sort_values('score',ascending=False)
vp=pd.read_csv(OUT/'validation_panels.csv');vp=vp[vp.id==cfg['id']]
short=json.loads((OUT/'shortlist.json').read_text());near=[]
for field,vals in [('length',[10,20,40,80]),('stop_atr',[2.,3.,4.,6.])]:
    k=vals.index(cfg[field])
    for j in [k-1,k,k+1]:
        if 0<=j<len(vals):
            c=cfg.copy();c[field]=vals[j]
            match=next(x for x in e.grid() if all(x[q]==c[q] for q in ['family','signal_minutes','length','adx_min','stop_atr','trailing','cooldown']))
            r=train[train.id==match['id']].iloc[0].to_dict();near.append(dict(r,varied=field,value=vals[j]))
near_table=pd.DataFrame(near)[['varied','value','median_sharpe','median_cagr','score']].copy()
near_table.median_cagr=near_table.median_cagr.map(lambda v:f'{100*v:.2f}%');near_table.median_sharpe=near_table.median_sharpe.round(3);near_table.score=near_table.score.round(3)
manifests=[]
for file in ['manifest.json','manifest_warmup.json']:manifests+=json.loads((ROOT/'data'/file).read_text())
verified=len({m['url'] for m in manifests if m['status']=='verified'});unavailable=sum(m['status']=='not_listed_or_unavailable' for m in manifests)
criteria=pd.DataFrame([
    ['Paired median annual return improves',f"{100*s['median_paired_cagr_gain']:+.2f} percentage points",s['median_paired_cagr_gain']>0],
    ['Paired median Sharpe improves',f"{s['median_paired_sharpe_gain']:+.3f}",s['median_paired_sharpe_gain']>0],
    ['At least 60% of pairs have higher Sharpe',f"{100*s['share_better_sharpe']:.0f}%",s['share_better_sharpe']>=.6],
    ['Positive median return with doubled costs',f"{100*s['median_stress_return']:+.2f}%",s['median_stress_return']>0],
    ['No primary drawdown above 15%',f"{100*s['worst_new_drawdown']:.2f}% worst",s['worst_new_drawdown']<=.15]
],columns=['Predeclared check','Observed result','Passed']).to_html(index=False,border=0)
css='''body{margin:0;background:#f4f6f8;color:#142333;font:16px/1.65 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:44px 24px}h1{font-size:39px;line-height:1.12;letter-spacing:-1.4px}h2{font-size:24px;margin-top:38px}h3{font-size:19px}.eyebrow{font-weight:800;font-size:12px;letter-spacing:2px;color:#376b76;text-transform:uppercase}.card{background:#fff;padding:22px;border:1px solid #dde3e8;border-radius:14px;margin:18px 0}.result{background:#fff2de;border-left:5px solid #ca8230}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stats b{display:block;font-size:26px}.stats span{font-size:12px;color:#526778}p{max-width:1000px}.table{overflow:auto;margin:16px 0}table{border-collapse:collapse;background:#fff;width:100%;font-size:13px}td,th{padding:10px 12px;text-align:left;white-space:nowrap;border-bottom:1px solid #e3e7eb}th{background:#e9eef3}li{margin:8px 0}pre{overflow:auto;background:#142535;color:#e4f1f4;border-radius:12px;padding:22px;font:12px/1.6 ui-monospace,monospace}button{border:0;border-radius:8px;background:#285567;color:#fff;padding:12px 18px;cursor:pointer}code{font-family:ui-monospace,monospace}a{color:#275a71}@media(max-width:650px){.stats{grid-template-columns:1fr 1fr}h1{font-size:30px}main{padding:24px 14px}}@media print{body{background:white}main{padding:0}pre{white-space:pre-wrap}button{display:none}}'''
report=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Crypto Strategy — Fresh-data Optimisation</title><style>{css}</style><main>
<div class="eyebrow">Research revision 2 • 6 October 2026</div><h1>Stronger relative performance.<br>The clean final test still rejects a broad profit claim.</h1>
<p>2,048 predeclared configurations, eight families, fixed risk and costs, a locked validation shortlist and one final assessment. No BTC/ETH/BNB/SOL candles from the earlier 2020–September 2026 study were requested or reused.</p>
<div class="stats"><div class="card"><b>2,048</b><span>configurations screened</span></div><div class="card"><b>98,304</b><span>training panel runs, correlated</span></div><div class="card"><b>{100*s['share_better_sharpe']:.0f}%</b><span>fresh final pairs with better Sharpe</span></div><div class="card"><b>{s['profitable_primary']}/{s['panels']}</b><span>positive fresh final panels</span></div></div>
<div class="card result"><strong>Outcome: not promoted as a robust trading strategy.</strong> The candidate improves Sharpe against the original in 90% of the paired final cases and improves paired median annual return by {100*s['median_paired_cagr_gain']:.2f} percentage points. But only half the final panels are profitable; the median stressed return is {100*s['median_stress_return']:.2f}%. It fails the predeclared profitability requirement. I did not remove losing coins or change parameters after seeing this result.</div>
<h2>The frozen candidate</h2><ol><li>Use confirmed 4-hour EMA(10) and EMA(50). At a confirmed decision close, enter long when EMA10 is above EMA50 and the strategy is flat.</li><li>Market entry fills at the next execution-chart open. Initial protection is a <strong>fixed stop 4 × confirmed 4-hour ATR(14)</strong> below executed entry.</li><li>Exit at the next chart open after a confirmed 4-hour close where EMA10 is at or below EMA50, or when the protective stop triggers.</li><li>No trailing stop, ADX entry filter or cooldown in the selected settings. These were tested; validation chose zero/disabled values.</li><li>Keep the original 0.5% equity risk target to the initial stop and 95% spot notional cap. No leverage or shorts. This target excludes fees and can be exceeded by gaps.</li></ol>
<p>The shorter volatility horizon increases typical notional exposure without increasing the configured stop-risk target. Realized drawdowns can therefore rise: worst primary modern drawdown was {100*s['worst_new_drawdown']:.2f}% versus {100*inc.maxdd.max():.2f}% for the original. Equal risk targets do not mean equal realized volatility, exposure or drawdowns.</p>
<h2>5-minute and 10-minute execution results</h2><p>All modern rows below use January 2024–September 2026, $10,000 initial capital, idle cash earning zero, 0.10% commission and 0.05% adverse price slippage per side. Returns are cumulative account returns, not annualized.</p><div class="table">{paired}</div>
<p>For the frozen fixed-stop 4-hour strategy, 5m/10m/1h/4h fills often occur at the same signal-boundary opening price, and stops fill at the same fixed price. Consequently their returns can be identical, while intrabar drawdowns differ. Those charts are not independent replications, and the strategy does not generate new 5-minute scalping signals.</p>
<h2>Separate untouched BTC/ETH historical transfer check</h2><p>These are 2018–2019 results using September 2017 onward for available indicator warmup. No 2020 onward BTC/ETH values were loaded. The original needs 400 complete daily bars before entry, so it is initially in cash for much of 2018. The optimized signal needs at least 100 4-hour bars. Treat the comparison with that different warmup requirement in mind.</p>
{table(historical[historical.execution_minutes.isin([5,10])],['symbol','execution_minutes','model','ret','sharpe','maxdd','trades'])}
<p><strong>This does not establish current BTC profitability.</strong> It is an untouched historical transfer sample, not a chronological forecast trial: selection was trained on later dates for different coins. BTC/ETH’s multi-year contemporary sample was already used in the first task. Changing exchange or bar size would reuse the same underlying market period; a few new October days would be too short to validate this strategy.</p>
<h2>Predeclared promotion checks</h2><div class="table">{criteria}</div>
<p>Final results across 40 modern coin/chart panels: median candidate return {100*s['median_new_return']:.2f}% versus {100*s['median_old_return']:.2f}% original; median Sharpe {s['median_new_sharpe']:.3f} versus {s['median_old_sharpe']:.3f}. A positive paired improvement can still leave both systems losing money. Median paired improvement and difference of the two medians are different statistics.</p>
<h2>Every modern final panel</h2>{table(normal,['symbol','execution_minutes','model','ret','cagr','sharpe','maxdd','trades','buyhold_ret'])}
<p>Buy and hold puts 95% into the asset with the same costs. Its actual exposure differs substantially from risk-sized trading, so this is an opportunity-cost reference, not a matched-volatility claim. No portfolio return or crypto allocation recommendation is inferred from the individual panels.</p>
<h2>Doubled-cost final test</h2><p>Stress costs per side are 0.20% commission plus 0.10% adverse price slippage. {s['profitable_stress']}/40 candidate panels are positive. The median stressed return is negative, which blocks promotion.</p>
{table(stress[stress.model=='optimized'],['symbol','execution_minutes','ret','sharpe','maxdd','trades'])}
<h2>How overfitting was controlled</h2><ol><li><strong>Exclude prior task candles.</strong> BTCUSDT, ETHUSDT, BNBUSDT and SOLUSDT dated 2020-01 through 2026-09 are blocked in the downloader and reader. A request audit found zero overlap. Prior rules are used only as the incumbent model on fresh prices.</li><li><strong>Separate development, validation and final data.</strong> XRP, ADA, LTC and LINK supply 2020/2021/2022 calendar-year training folds. Earlier available 2018–2019 candles supply causal warmup. The fixed shortlist is evaluated on 2023. Final modern evaluation is 2024–September 2026 on those four assets plus previously unselected DOGE, AVAX, ATOM and DOT. Additional warmup for those four coins is sealed and opened only after selection.</li><li><strong>Register the finite search.</strong> Eight families × two signal clocks × four lookbacks × two ADX settings × four ATR stops × trailing on/off × cooldown 0/3 produces 2,048 candidates. The risk target, cap and costs never change during selection.</li><li><strong>Score consistency and costs.</strong> Training uses 1h and 4h execution, calendar folds, and primary/doubled costs. The score is median daily Sharpe minus half its standard deviation, plus 0.2 times lower-quartile Sharpe, with excess drawdown above 15% penalized. Trade-count requirements are fixed in the protocol.</li><li><strong>Prefer parameter regions.</strong> Each training score is combined with immediate lookback/stop neighbors at a 75/25 weight. The 12 highest eligible distinct configurations form a hashed shortlist before any 2023 validation result is calculated.</li><li><strong>One final winner.</strong> Validation evaluates only that shortlist over all five execution charts and both cost levels. One global rule wins. Its parameters and protocol hash are frozen before the final-price reader is permitted to open sealed archives.</li><li><strong>Do not recycle the final assessment.</strong> The final script writes a one-time access marker. The selected-rule hash stayed unchanged. The later Pine cost-proxy diagnostic checks only the frozen model, with no parameter or coin selection. The failed final outcome is retained.</li></ol>
<p>Training data is intentionally reused to compare candidates fairly; that is how optimisation works. Validation and final data have separate roles, and no prior-task candle enters this run. This reduces the opportunity for overfitting; it cannot make the process statistically immune to it. Assets share regimes, charts share candles, and the universe contains surviving liquid coins. The 98,304 training runs are highly dependent, not 98,304 independent pieces of evidence. Selecting among 2,048 candidates remains a multiple-comparison risk.</p>
<h2>Training and validation findings</h2><p>Selected candidate training median Sharpe: {cfg['training']['median_sharpe']:.3f}; median annual account return: {100*cfg['training']['median_cagr']:.2f}%. Validation median Sharpe: {cfg['validation']['median_sharpe']:.3f}; median annual account return: {100*cfg['validation']['median_cagr']:.2f}%. These development figures were not counted as final-test evidence.</p>
<h3>Validation shortlist, including rejected options</h3>{table(vr,['id','score','eligible','median_sharpe','median_cagr'])}
<details><summary>Highest training plateau scores</summary>{table(train.head(20),['id','plateau_score','eligible','median_sharpe','median_cagr'])}</details>
<h3>Selected-rule nearby training parameters</h3><div class="table">{near_table.to_html(index=False,border=0)}</div>
<p>This is a training diagnostic. No neighboring parameter was selected after the final test.</p>
<details><summary>Selected candidate validation panels</summary>{table(vp,['symbol','execution_minutes','cost','ret','sharpe','maxdd','trades'])}</details>
<h2>What was actually searched</h2><table><tr><th>Dimension</th><th>Values/rules</th></tr><tr><td>Signal clocks</td><td>4 hours and daily; confirmed values only</td></tr><tr><td>Lookbacks</td><td>10, 20, 40, 80 signal bars</td></tr><tr><td>EMA</td><td>Fast EMA versus 5× fast-length slow EMA</td></tr><tr><td>Donchian</td><td>Above prior high channel; exit below prior half-length low channel</td></tr><tr><td>Momentum</td><td>Positive lookback change; exit on non-positive change</td></tr><tr><td>Keltner</td><td>Above EMA + 2 ATR14; exit below EMA</td></tr><tr><td>MACD</td><td>Fast n, slow 2n, signal round(0.75n); bullish histogram and price above EMA5n</td></tr><tr><td>RSI pullback</td><td>RSI14 below 40 above EMA5n; exit RSI above 60 or loss of trend EMA</td></tr><tr><td>Bollinger mean reversion</td><td>Below SMA − 2 population standard deviations; exit at mean</td></tr><tr><td>Supertrend</td><td>ATR14 × 3 direction plus EMA5n trend; exit bearish direction or loss of trend</td></tr><tr><td>ADX entry threshold</td><td>Disabled or ADX14 above 20</td></tr><tr><td>Stops</td><td>2, 3, 4 or 6 × signal-clock ATR14; fixed or close-updated trailing</td></tr><tr><td>Cooldown</td><td>0 or 3 subsequent permitted decision closes after exit</td></tr></table>
<h2>Data and execution audit</h2><p>{verified} distinct publisher archives passed SHA256 verification; {unavailable} early monthly requests were unavailable. The full source/checksum manifest is in the package. Normalized millisecond/microsecond timestamps, validated OHLC relationships, rejected duplicate timestamps and retained actual gaps. Resampled bars must have every constituent 5m candle; no missing price was interpolated.</p><div class="table">{coverage.to_html(index=False,border=0)}</div>
<p>Confirmed signal availability timestamps are audited against execution candle closes. Entries and signal exits fill at the next observed open with adverse slippage. An existing stop gaps to an adverse opening price. Entry-candle stop protection is active immediately. A trailing stop, if enabled, updates only after a chart close and becomes active next candle. No intrabar profit targets are used. Fees, slippage and terminal liquidation are included. Every detailed ledger reconciles with its final account return. Idle cash earns no interest; USDT is treated as $1.</p>
<p>Drawdown includes closing peaks and adverse intrabar marks limited by existing protective stops. It is not a complete tick-path drawdown or liquidation simulation. There is no spread/order-book/queue model, no outage recovery model, and no proof that archive revisions equal the data available to traders at the original time. Verified checksums establish the downloaded files' integrity, not historical point-in-time publication. Missing-bar fills cannot be reconstructed.</p>
<h2>Updated Pine Script</h2><p>Paste the script into TradingView’s Pine Editor and select Add to chart. Use standard spot candles and retain the frozen defaults to inspect this experiment. On 5m/10m, enough history or deep backtesting is needed to reproduce multi-year results. Orders are simulated; no broker trades are submitted by this file.</p>
<p>Pine defaults to 0.15% commission per side as a cash-cost proxy for the Python model's 0.10% fee plus 0.05% adverse execution-price slippage. This is not an identical fill model. The frozen-model proxy diagnostic found a largest account-return difference of {100*proxy_delta:.3f} percentage points. If you add fixed tick slippage in Properties, first change commission to 0.10% to avoid double counting. Quantity/tick rounding, native partial bars, startup history and margin rejection on opening gaps can also produce differences.</p>
<p>The Pine code was reviewed against official v6 documentation and its frozen defaults/identifier references checked. It has not been compiled in TradingView or forward-tested. Current signal-clock values are traded only at matching confirmed closes; the prior ATR request uses an explicit [1] offset with lookahead_on. Indicator plots may form between closes, while orders remain gated. For daily execution with 4h signals, only the final 4h state at daily close can place an order; it is a different execution system from intraday trading.</p>
<button onclick="navigator.clipboard.writeText(document.getElementById('pine').textContent).then(()=>this.textContent='Copied').catch(()=>this.textContent='Select and copy the code below')">Copy Pine Script</button><pre id="pine">{html.escape((OUT/'Crypto_Strategy.pine').read_text())}</pre>
<h2>Practical conclusion</h2><p>This revision produces a stronger historical BTC/ETH transfer result and better relative Sharpe on most new crypto test panels. It does not pass the preregistered broad-market profitability criterion. The delivered script is a research candidate, not a recommendation for live deployment. A valid next test would use genuinely new future market observations with the rules fixed; returning to these final results to change the strategy would make them development data.</p>
<h2>Sources and reproducibility</h2><ul><li><a href="https://github.com/binance/binance-public-data">Binance public-data repository</a>: source formats, timestamps and checksums.</li><li><a href="https://www.tradingview.com/pine-script-docs/concepts/strategies/">TradingView strategies documentation</a>: fill timing, costs, selection bias, overfitting and testing limitations.</li><li><a href="https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/">TradingView timeframe/data documentation</a>: requests and confirmed-value handling.</li></ul>
<p>Protocol and rule hashes, every training/validation panel, final comparison, trade ledgers, daily equity, source manifests, runtime versions and source code are in the ZIP. Runtime: Python {audit['runtime']['python']}, NumPy {audit['runtime']['numpy']}, pandas {audit['runtime']['pandas']}, Numba {audit['runtime']['numba']}. Raw candles are reproducible from their checksum-identified archives and are not duplicated in the package. Final rule SHA256: <code>{s['selection_sha256']}</code>.</p></main></html>'''
(OUT/'Crypto_Strategy_Report.html').write_text(report)
readme='''# Fresh-data crypto optimisation, revision 2

The final candidate fails the preregistered broad-market profitability promotion check.
Read output/Crypto_Strategy_Report.html before using output/Crypto_Strategy.pine.

Do not rerun training/validation with the final results visible and call it an untouched test.
To reproduce the original computation, extract into a NEW directory and retain the supplied output directory as recorded_output before running:

```sh
python -m pip install -r requirements.txt
python download.py
python download_warmup.py
python verify.py
python search.py train
python search.py validate
python final_test.py
python build_pine.py
python cost_proxy_check.py
```

build_report.py uses audit_manifest.json generated at the original freeze. Recompute audit hashes/runtime versions if reproducing in another environment. Its included HTML is the authoritative report of this run.
Compare newly downloaded checksums with source_manifest.json and source_manifest_warmup.json; publisher archive revisions can change reproduction. A failed final assessment must not cause final-test reselection. Search intentionally reuses DEVELOPMENT samples across configurations for fair comparison, but no prior-task market candles are allowed and the final test is accessed once.

Baseline: daily EMA40/200, 3*daily ATR14 trailing, 400 daily bars warmup, 0.5% equity risk target, 95% spot cap. Candidate: confirmed 4h EMA10/50, fixed 4*4h ATR14 stop, same risk/cap, no trailing/ADX/cooldown.

Primary cost model is percentage adverse price slippage plus commission; Pine uses the explicitly documented cash-cost proxy. No TradingView compiler or live forward test was available.
'''
(OUT/'README.md').write_text(readme)
(ROOT/'requirements.txt').write_text('\n'.join([f'{k}=={audit["runtime"][k]}' for k in ['numpy','pandas','numba']])+'\n')
with zipfile.ZipFile(OUT/'Crypto_Research_Package.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ROOT.glob('*.py')):z.write(p,'crypto_research_v2/'+p.name)
    for p in [ROOT/'protocol.json',ROOT/'requirements.txt']:z.write(p,'crypto_research_v2/'+p.name)
    z.write(ROOT/'data/manifest.json','crypto_research_v2/source_manifest.json')
    z.write(ROOT/'data/manifest_warmup.json','crypto_research_v2/source_manifest_warmup.json')
    z.writestr('crypto_research_v2/README.md',readme)
    z.writestr('crypto_research_v2/original_baseline.json',json.dumps(e.BASELINE,indent=2))
    for p in sorted(OUT.iterdir()):
        if p.suffix in ['.json','.csv','.pine','.html','.md','.sha256']:z.write(p,'crypto_research_v2/output/'+p.name)
print('Report and package complete',len(report),'report characters',(OUT/'Crypto_Research_Package.zip').stat().st_size,'package bytes')
