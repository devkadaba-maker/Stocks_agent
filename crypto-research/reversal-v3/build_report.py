from pathlib import Path
import json
import pandas as pd
R=Path(__file__).resolve().parent;O=R/'output'
def table(d):
    lines=['| Coin | Chart | Net return | Max DD | Trades | Long / short entries |','|---|---:|---:|---:|---:|---:|']
    for _,v in d.iterrows():lines.append(f'| {v.symbol} | {v.clock}m | {v.ret:.2%} | {v.maxdd:.2%} | {v.trades} | {v.long_entries} / {v.short_entries} |')
    return '\n'.join(lines)
def main():
    s=json.loads((O/'summary.json').read_text());cfg=json.loads((O/'selected.json').read_text());d=pd.read_csv(O/'final_results.csv')
    p=d[(d.model=='selected')&(d.cost==1)&d.clock.isin([5,10])]
    models=d[(d.stage=='held_out')&(d.cost==1)].groupby('model').agg(median_return=('ret','median'),median_sharpe=('sharpe','median'),worst_dd=('maxdd','max'))
    comparison='| Model | Median net return | Median Sharpe | Worst DD |\n|---|---:|---:|---:|\n'+'\n'.join(f'| {name} | {v.median_return:.2%} | {v.median_sharpe:.3f} | {v.worst_dd:.2%} |' for name,v in models.iterrows())
    btc=d[(d.symbol=='BTCUSDT')&(d.model=='selected')&(d.clock==5)].set_index('cost')
    text=f'''# Long / short reversal study — frozen results

The validation winner is **daily SMA 5/10**, reversing long when SMA5 > SMA10 and short when SMA5 < SMA10. Equality requests flat. Orders execute at the next observed open after a confirmed decision; a fixed **3 × daily ATR14** stop protects each position. After a stop, skip **three signal decisions** before re-entry. This is a directional moving-average reversal, not a price mean-reversion strategy.

Risk is 0.5% equity to the stop, capped at 95% position notional. Initial capital is $10,000; no leverage increase or compounding-risk increase was used to inflate results. Net results include historical funding, 0.10% commission and 0.05% adverse fill slippage **per side**. Each panel has a separate account; panel returns are not a portfolio result.

**Registered promotion decision: {'PASS' if s['promotion_passed'] else 'FAIL'}.** This assessment does not establish future profitability or the best possible strategy.

## Final evidence, January 2024–September 2026

| Registered measure | Result |
|---|---:|
| Eight-coin median primary return | {s['primary_median_return']:.2%} |
| Median return with doubled execution costs | {s['double_cost_median_return']:.2%} |
| Median return with extra adverse funding | {s['adverse_funding_median_return']:.2%} |
| Profitable primary panels | {s['profitable_panels']} / {s['primary_panels']} |
| Worst primary drawdown | {s['worst_primary_drawdown']:.2%} |
| Median primary daily Sharpe | {s['median_sharpe']:.3f} |
| Median paired Sharpe improvement over fixed reversal baseline | {s['median_paired_sharpe_improvement']:.3f} |
| Primary liquidation-proxy events | {s['primary_liquidation_proxy_events']} |

Primary results on the requested charts:

{table(p[p.symbol!='BTCUSDT'])}

BTC is an **excluded-from-selection retrospective check**. Its underlying period was already examined in the previous research, so it is not fresh proof.

{table(p[p.symbol=='BTCUSDT'])}

BTC doubled-cost return: **{btc.loc[2,'ret']:.2%}**; adverse-funding return: **{btc.loc[3,'ret']:.2%}**. These remain retrospective checks.

Matched primary baseline comparison across eight coins and four charts:

{comparison}

The daily signal deliberately retains the same parameters across 5m, 10m, 1h and 4h charts. Coinciding decision boundaries and the shared 5m execution path can produce identical results. These charts are correlated checks, not independent replications and not a five-minute scalping model. Drawdown uses the 5m path with an approximate within-bar adverse-price calculation.

## Selection and contamination controls

The protocol was registered before training values. It tests **576** EMA/SMA configurations: 1h/4h/daily signals, fast lengths 5/10/20/40, slow ratios 2/5, fixed ATR stops 2/3/4/6 and cooldowns 0/1/3. This is a finite search of simple reversal rules; it does not test every possible indicator or strategy.

Training: 2021 and 2022 annual folds on BCH, ETC, TRX and XLM, with causal warmup from 2020, primary and doubled costs; 9,216 evaluated panels. A fixed top-12 shortlist incorporates neighbouring length/stop performance. Validation: 2023 on those four coins, four execution charts and two costs; 384 panels. One global winner was frozen before opening final values. Previously used BTC, ETH, BNB, SOL, XRP, ADA, LTC, LINK, DOGE, AVAX, ATOM and DOT were blocked from training and validation.

Final: January 2024–September 2026 on the four development coins plus previously unselected NEAR, UNI, AAVE and FIL. BTC was excluded from selection. There are 324 final cases including all charts, three models and three cost/funding modes. Final outcomes were not used for retuning. Current listed coins share market regimes and introduce survivorship bias; a fresh forward paper-trading period is still necessary.

Score: median daily-return Sharpe − 0.5 × dispersion + 0.2 × lower-quartile Sharpe − penalty for drawdown above 20%. The promotion criteria require positive median return under all three cost modes, at least 60% profitable primary panels, worst drawdown ≤20%, and a positive median paired Sharpe improvement over the fixed EMA10/50 four-hour reversal baseline. The same perpetual prices and funding also test an EMA10/50 long-only baseline; those figures should not be compared directly with the earlier spot-market study.

Freeze: `{cfg['frozen_at']}`. Selection SHA256: `{s['selection_sha256']}`. The protocol, registry, shortlist, rankings, final metrics, and net trade ledger are retained.

## Execution assumptions and Pine limits

Price and realized funding archives come from Binance public USD-M monthly data, with publisher CHECKSUM verification and a retained manifest. Incomplete higher-timeframe candles are excluded, gaps remain, and orders use the next observed open. Funding is charged to the position held immediately before an opening boundary, using that trade open as a historical mark-price proxy. Actual exchange mark prices and order/settlement priority can differ. Funding data gaps raise errors rather than silently becoming zero.

Adverse funding adds a 0.01% charge per eight hours to both directions, scaled to the recorded funding interval; it is separate from doubled execution costs. Derivative equity accounting, entry fees, funding signs, short stops, next-open reversals, gap stops and ledger totals have synthetic checks. Every logged final trade reconciles to its account result. A deterministic rerun uses the same engine and is not an independent implementation audit.

Liquidation uses a constant 0.5% maintenance proxy and trade prices, with no exact exchange tiers, mark prices, insurance or ADL. The cap applies at entry. Tick/lot rounding, available liquidity and gap fills can change live results.

`Long_Short_Reversal.pine` uses Pine v6 and frozen defaults, confirmed signal-close orders, both directions, fixed stops and cooldown. TradingView commission is set to a **0.15% cash-cost proxy per side** with zero tick slippage. Its tester does not apply the historical funding ledger. Python caps position size again at the opening fill; Pine sends its size at decision time. TradingView margin, stop priority, bar magnifier coverage, tick/lot rounding and gap handling can differ. **The Pine file has not been compiled or run in TradingView.** Do not claim its tester will reproduce the Python numbers exactly.

Use standard Binance perpetual-price charts, load enough history for at least 100 complete daily candles, and keep defaults when comparing with this study. Changing an input invalidates the frozen-default evidence. No broker orders were placed.
'''
    (R/'Long_Short_Report.md').write_text(text)
    (R/'README.md').write_text('''# Crypto reversal v3

Long and short Pine strategy plus a reproducible, frozen research package. Start with [the results and limitations](Long_Short_Report.md), then [the Pine Script](output/Long_Short_Reversal.pine). All previous research remains separate.

**Research outcome: failed the registered robustness criteria.** The eight-coin median return was −0.05%, versus +1.97% for the matched long-only baseline. BTC returned −0.89%. Adding shorts did not establish an improvement over long-only trading. The Pine file is a research candidate and has not been compiled in TradingView.

## Reproduce the frozen results

Install Python 3.12 and run these commands from this folder:

```sh
python -m pip install -r requirements.txt
python restore_results.py
python download.py development
python download.py final
python download.py btc
python audit_data.py
python verify.py
python reproduce.py
```

Downloading all archives requires network access and substantial disk space. Downloader verifies publisher checksums. Manifests record the original archive hashes; if an archive is revised upstream, compare it with the original manifest before claiming an identical reproduction. `reproduce.py` only checks frozen cases; it does not select parameters again.

`search.py` documents selection, but do not rerun it against the already revealed final period. `final_test.py` refuses a second assessment when `output/final_started.json` exists. Large original CSV results are losslessly stored in gzip/base64 chunks under `packed/`; `restore_results.py` validates SHA256 before restoring them. Raw market archives are fetched from their documented public source rather than committed.

Outputs include full training/validation rankings, all 324 final cases, primary trade ledgers, coverage, selected parameters and checks. Daily SMA5/10 is the validation winner, not a universal optimum or a future-return guarantee. Pine funding and fill limitations are explained in the report.
''')
if __name__=='__main__':main()
