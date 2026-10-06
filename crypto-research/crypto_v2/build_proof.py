"""Publish a readable audit and reproducible evidence package, not a retuned strategy."""
from pathlib import Path
import json, html, zipfile, hashlib
import pandas as pd

ROOT=Path(__file__).resolve().parent
P=ROOT/'proof';O=ROOT/'output'
s=json.loads((P/'proof_summary.json').read_text())
g=json.loads((P/'guard_checks.json').read_text())
comparison=pd.read_csv(P/'reproduction_comparison.csv')
final=pd.read_csv(O/'final_test.csv')
modern=final[(final.stage=='final')&(final.model=='optimized')&(final.cost==1)&(final.execution_minutes.isin([5,10]))].copy()
modern['Chart']=modern.execution_minutes.astype(str)+'m'
modern['Net return']=modern.ret.map(lambda v:f'{v:+.4%}')
modern['Drawdown']=modern.maxdd.map(lambda v:f'{v:.4%}')
modern=modern.rename(columns={'symbol':'Asset','trades':'Trades'})[['Asset','Chart','Net return','Drawdown','Trades']]
t=s['sample_trades'][0]
gross=t['quantity']*(t['exit_fill']-t['entry_fill'])
fees=t['entry_fee']+t['exit_fee']
stamp=s['audit_utc']
limitations=''.join(f'<li>{html.escape(v)}</li>' for v in s['limitations'])
guard_table=pd.DataFrame([{'Check':k.replace('_',' '),'Result':v} for k,v in g.items() if k!='scope']).to_html(index=False)
body=rf'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Crypto Strategy: Reproduction Evidence</title>
<style>body{{margin:0;background:#f5f6f8;color:#172c3e;font:16px/1.65 system-ui,sans-serif}}main{{max-width:1060px;margin:auto;padding:36px 22px}}h1{{font-size:36px;line-height:1.2}}h2{{margin-top:32px;font-size:23px}}.card{{background:white;padding:22px;border:1px solid #dae3ea;border-radius:12px;margin:18px 0}}.warning{{background:#fff2dc;border-left:5px solid #b97522}}table{{border-collapse:collapse;width:100%;font-size:13px;background:white}}th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid #e0e6ec;white-space:nowrap}}th{{background:#e7edf2}}.scroll{{overflow:auto}}pre{{background:#142b3d;color:white;padding:18px;border-radius:8px;overflow:auto;font-size:12px}}a{{color:#27596e}}code{{font-family:ui-monospace,monospace}}li{{margin:9px 0}}.label{{font-size:12px;font-weight:700;color:#416775;text-transform:uppercase;letter-spacing:1.4px}}</style><main>
<div class="label">Frozen-result audit · 6 October 2026</div><h1>The numbers reproduce.<br>The profitability claim does not pass.</h1>
<p>A separately written Python implementation rebuilt EMA, ATR, signal availability, fills, position sizing, stops, trailing exits, equity and trade accounting from the saved source candles. It did not import the original backtest engine. No parameters, original results or final-test rules were changed. Re-running frozen cases is a reproducibility check, not fresh out-of-sample evidence.</p>
<div class="card warning"><b>Conclusion:</b> all 200 reported final cases match to floating-point precision. All 4,728 saved primary-cost trades match in entry time, exit time, reason and P&amp;L. The same audit confirms only 20/40 modern candidate panels made money and the doubled-cost median return is −1.46%. This establishes arithmetic consistency under the model’s assumptions; it does not establish a broadly profitable live strategy.</div>
<h2>What was checked</h2><div class="scroll"><table><tr><th>Evidence</th><th>Observed</th></tr>
<tr><td>Final cases rebuilt</td><td>200: 10 assets × 5 execution charts × 2 models × 2 costs</td></tr>
<tr><td>Primary-cost trades matched</td><td>{s['matched_primary_trades']:,}; each entry/exit/reason/P&amp;L</td></tr>
<tr><td>Maximum return difference</td><td>{s['max_absolute_return_difference']:.3g} in decimal return</td></tr>
<tr><td>Maximum Sharpe difference</td><td>{s['max_absolute_sharpe_difference']:.3g}</td></tr>
<tr><td>Maximum drawdown difference</td><td>{s['max_absolute_drawdown_difference']:.3g} in decimal drawdown</td></tr>
<tr><td>Maximum trade-P&amp;L difference</td><td>${s['max_absolute_trade_pnl_difference_dollars']:.3g}</td></tr>
<tr><td>Maximum daily-equity difference</td><td>${s['max_absolute_daily_equity_difference_dollars']:.3g}</td></tr>
<tr><td>Source archive hashes</td><td>{s['verified_source_archives']} match recorded publisher checksums</td></tr>
<tr><td>Previously used BTC/ETH/BNB/SOL 2020–September 2026 requests</td><td>0 in the new data manifests</td></tr>
<tr><td>Search records</td><td>2,048 configurations; 98,304 training rows; 480 validation rows</td></tr>
<tr><td>Winner selection</td><td>Highest eligible validation score on the fixed 12-candidate shortlist</td></tr>
<tr><td>Original files</td><td>All original output and protocol file hashes unchanged during audit</td></tr></table></div>
<h2>A trade you can check by hand</h2><p>The first XRP 5-minute trade deliberately shows a loss. UTC entry decision: <b>2 January 2024 at 04:00</b>, at the previous candle’s close. It fills at the next candle’s opening boundary, also 04:00. Exit: <b>3 January 2024 at 12:00</b>, protective stop.</p>
<div class="scroll"><table><tr><th>Item</th><th>Value</th></tr><tr><td>Quantity</td><td>{t['quantity']:.12f} XRP</td></tr><tr><td>Adverse-slippage-adjusted entry</td><td>{t['entry_fill']:.12f} USDT</td></tr><tr><td>Initial stop</td><td>{t['initial_stop']:.12f} USDT</td></tr><tr><td>Adverse-slippage-adjusted exit</td><td>{t['exit_fill']:.12f} USDT</td></tr><tr><td>Entry fee</td><td>${t['entry_fee']:.12f}</td></tr><tr><td>Exit fee</td><td>${t['exit_fee']:.12f}</td></tr></table></div>
<pre>Gross P&amp;L = quantity × (exit fill − entry fill) = ${gross:.12f}
Fees      = entry fee + exit fee              = ${fees:.12f}
Net P&amp;L   = gross P&amp;L − fees                  = ${t['pnl']:.12f}</pre>
<p>Target initial stop risk was $50 before fees and slippage. Net loss is $52.42 because those costs also apply. The complete reproduced ledger contains quantities, fills, fees, decision times and stop prices for all 4,728 primary-cost trades.</p>
<h2>Fresh modern 5-minute and 10-minute results</h2><p>January 2024–September 2026. $10,000 starting equity; 0.10% commission plus 0.05% adverse fill-price slippage per side. Candidate: confirmed 4-hour EMA10/50, fixed 4×ATR14 stop, 0.5% stop-risk target, 95% spot cap, long only. Returns are cumulative, not annualized. Matching returns reflect common 4-hour decisions and fixed stop fills, not independent confirmations.</p><div class="scroll">{modern.to_html(index=False)}</div>
<h2>The failure reproduced too</h2><p>Across the 40 modern primary-cost candidate panels, 36 have higher Sharpe than the incumbent, but only 20 are profitable. Median return is −0.14%; doubled-cost median return is −1.46%; worst drawdown is 12.44%. The predeclared positive stressed-median requirement fails. The older BTC/ETH 2018–2019 cases are historical transfer checks, not contemporary BTC validation, and the incumbent’s longer warmup materially affects that comparison.</p>
<h2>Timing and guard checks</h2><div class="scroll">{guard_table}</div><p>Future prices were changed in synthetic inputs and earlier EMA/ATR outputs remained unchanged. Source signal close time was checked against every execution decision’s time. Explicit tests block final-data loading without selection, repeated final assessment, and training/validation after a frozen selection. These checks cover the implemented guards and test scenarios; local files are not an immutable external record.</p>
<h2>Limits of this evidence</h2><ul>{limitations}</ul><p>The next validation step is to compile the Pine Script in TradingView, reconcile its trades against this ledger with matched history/settings, then forward paper-test the unchanged rules on newly arriving data. No live orders were placed. TradingView’s <a href="https://www.tradingview.com/pine-script-docs/concepts/strategies/#overfitting">official strategy documentation</a> likewise explains that out-of-sample testing reduces fitting risk but cannot guarantee future performance.</p>
<h2>Inspect and reproduce</h2><p>The companion ZIP contains the separate audit implementation, original frozen research inputs, comparison CSV, detailed trade CSV, checksums, guard results and pinned requirements. Raw candles are not duplicated; the included downloader retrieves only the registered public archives.</p>
<p>After extracting the ZIP, open PowerShell in its extracted folder and run this single command. It installs requirements, downloads registered data, runs the independent audit, and checks the guards. It does not re-optimise.</p>
<pre>py -m pip install -r .\crypto_v2\requirements.txt; if ($LASTEXITCODE -eq 0) {{ py .\crypto_v2\download.py }}; if ($LASTEXITCODE -eq 0) {{ py .\crypto_v2\download_warmup.py }}; if ($LASTEXITCODE -eq 0) {{ py .\crypto_v2\independent_audit.py }}; if ($LASTEXITCODE -eq 0) {{ py .\crypto_v2\audit_guard_checks.py }}</pre>
<p>Use Python 3.12 for the pinned environment. Recorded audit UTC: {stamp}. Frozen selection SHA256:</p><pre>{s['selected_sha256']}</pre>
<details><summary>All 200 case comparisons</summary><div class="scroll">{comparison.to_html(index=False,float_format=lambda v:f'{v:.12g}')}</div></details></main></html>'''
report=P/'Crypto_Proof_Report.html';report.write_text(body)
(P/'README.md').write_text('''# Frozen crypto backtest audit

This is reproduction evidence, not new out-of-sample validation or proof of a live edge.
Read Crypto_Proof_Report.html. All original rules and results stay frozen.

Use Python 3.12. From the extracted folder, run the PowerShell command in the report.
On Linux/macOS: python -m pip install -r crypto_v2/requirements.txt && python crypto_v2/download.py && python crypto_v2/download_warmup.py && python crypto_v2/independent_audit.py && python crypto_v2/audit_guard_checks.py

No search/train/validate/final_test command should be run again. The bundled
original final_test guard intentionally rejects reuse of its final assessment.
independent_audit.py is the separate, read-only reproduction route.

reproduction_comparison.csv compares every final case.
reproduced_trade_ledger.csv gives fills, fees, quantity, decision time and P&L.
proof_summary.json records this audit; immutable_input_hashes.json records originals.
guard_checks.json records explicit guard/causality checks.
Data hashes are against recorded publisher checksums; original downloaders also
fetch current CHECKSUM files. A fresh download may fail if the publisher removes
an archive; missing data must not be silently substituted.

The original source archives are excluded from this ZIP to avoid a large duplicate.
The original Crypto_Research_Package.zip is also excluded to avoid nested packages.
Its SHA256 is retained in this audit's immutable_input_hashes.json.
''')
scripts=['independent_audit.py','audit_guard_checks.py','engine.py','verify.py','download.py','download_warmup.py','final_test.py','search.py','requirements.txt','protocol.json']
package=P/'Crypto_Proof_Package.zip'
with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as z:
    for name in scripts:z.write(ROOT/name,'crypto_v2/'+name)
    for name in ['manifest.json','manifest_warmup.json']:z.write(ROOT/'data'/name,'crypto_v2/data/'+name)
    for path in O.iterdir():
        if path.is_file() and path.suffix!='.zip':z.write(path,'crypto_v2/output/'+path.name)
    for path in P.iterdir():
        if path.is_file() and path!=package:z.write(path,'crypto_v2/proof/'+path.name)
    z.write(P/'README.md','README.md')
with zipfile.ZipFile(package) as z:
    assert z.testzip() is None
    assert z.read('crypto_v2/independent_audit.py')==(ROOT/'independent_audit.py').read_bytes()
print('Proof report:',report,'bytes',report.stat().st_size)
print('Proof package:',package,'bytes',package.stat().st_size)
