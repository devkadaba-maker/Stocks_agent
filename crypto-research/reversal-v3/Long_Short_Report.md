# Long / short reversal study — frozen results

The validation winner is **daily SMA 5/10**, reversing long when SMA5 > SMA10 and short when SMA5 < SMA10. Equality requests flat. Orders execute at the next observed open after a confirmed decision; a fixed **3 × daily ATR14** stop protects each position. After a stop, skip **three signal decisions** before re-entry. This is a directional moving-average reversal, not a price mean-reversion strategy.

Risk is 0.5% equity to the stop, capped at 95% position notional. Initial capital is $10,000; no leverage increase or compounding-risk increase was used to inflate results. Net results include historical funding, 0.10% commission and 0.05% adverse fill slippage **per side**. Each panel has a separate account; panel returns are not a portfolio result.

**Registered promotion decision: FAIL.** This assessment does not establish future profitability or the best possible strategy.

## Final evidence, January 2024–September 2026

| Registered measure | Result |
|---|---:|
| Eight-coin median primary return | -0.05% |
| Median return with doubled execution costs | -1.45% |
| Median return with extra adverse funding | -1.39% |
| Profitable primary panels | 16 / 32 |
| Worst primary drawdown | 10.80% |
| Median primary daily Sharpe | 0.014 |
| Median paired Sharpe improvement over fixed reversal baseline | 0.230 |
| Primary liquidation-proxy events | 0 |

Primary results on the requested charts:

| Coin | Chart | Net return | Max DD | Trades | Long / short entries |
|---|---:|---:|---:|---:|---:|
| BCHUSDT | 5m | -7.85% | 10.80% | 123 | 62 / 61 |
| BCHUSDT | 10m | -7.85% | 10.80% | 123 | 62 / 61 |
| ETCUSDT | 5m | -3.27% | 3.74% | 119 | 60 / 59 |
| ETCUSDT | 10m | -3.27% | 3.74% | 119 | 60 / 59 |
| TRXUSDT | 5m | -0.31% | 6.74% | 108 | 54 / 54 |
| TRXUSDT | 10m | -0.31% | 6.74% | 108 | 54 / 54 |
| XLMUSDT | 5m | 22.89% | 8.79% | 103 | 52 / 51 |
| XLMUSDT | 10m | 22.89% | 8.79% | 103 | 52 / 51 |
| NEARUSDT | 5m | 0.85% | 4.50% | 119 | 59 / 60 |
| NEARUSDT | 10m | 0.85% | 4.50% | 119 | 59 / 60 |
| UNIUSDT | 5m | 5.73% | 2.89% | 105 | 53 / 52 |
| UNIUSDT | 10m | 5.73% | 2.89% | 105 | 53 / 52 |
| AAVEUSDT | 5m | 0.21% | 3.75% | 107 | 54 / 53 |
| AAVEUSDT | 10m | 0.21% | 3.75% | 107 | 54 / 53 |
| FILUSDT | 5m | -3.10% | 6.70% | 112 | 56 / 56 |
| FILUSDT | 10m | -3.10% | 6.70% | 112 | 56 / 56 |

BTC is an **excluded-from-selection retrospective check**. Its underlying period was already examined in the previous research, so it is not fresh proof.

| Coin | Chart | Net return | Max DD | Trades | Long / short entries |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 5m | -0.89% | 4.33% | 112 | 56 / 56 |
| BTCUSDT | 10m | -0.89% | 4.33% | 112 | 56 / 56 |

BTC doubled-cost return: **-2.44%**; adverse-funding return: **-2.28%**. These remain retrospective checks.

Matched primary baseline comparison across eight coins and four charts:

| Model | Median net return | Median Sharpe | Worst DD |
|---|---:|---:|---:|
| long_only | 1.97% | 0.192 | 14.21% |
| selected | -0.05% | 0.014 | 10.80% |
| simple_reversal | -0.93% | -0.048 | 18.12% |

The daily signal deliberately retains the same parameters across 5m, 10m, 1h and 4h charts. Coinciding decision boundaries and the shared 5m execution path can produce identical results. These charts are correlated checks, not independent replications and not a five-minute scalping model. Drawdown uses the 5m path with an approximate within-bar adverse-price calculation.

## Selection and contamination controls

The protocol was registered before training values. It tests **576** EMA/SMA configurations: 1h/4h/daily signals, fast lengths 5/10/20/40, slow ratios 2/5, fixed ATR stops 2/3/4/6 and cooldowns 0/1/3. This is a finite search of simple reversal rules; it does not test every possible indicator or strategy.

Training: 2021 and 2022 annual folds on BCH, ETC, TRX and XLM, with causal warmup from 2020, primary and doubled costs; 9,216 evaluated panels. A fixed top-12 shortlist incorporates neighbouring length/stop performance. Validation: 2023 on those four coins, four execution charts and two costs; 384 panels. One global winner was frozen before opening final values. Previously used BTC, ETH, BNB, SOL, XRP, ADA, LTC, LINK, DOGE, AVAX, ATOM and DOT were blocked from training and validation.

Final: January 2024–September 2026 on the four development coins plus previously unselected NEAR, UNI, AAVE and FIL. BTC was excluded from selection. There are 324 final cases including all charts, three models and three cost/funding modes. Final outcomes were not used for retuning. Current listed coins share market regimes and introduce survivorship bias; a fresh forward paper-trading period is still necessary.

Score: median daily-return Sharpe − 0.5 × dispersion + 0.2 × lower-quartile Sharpe − penalty for drawdown above 20%. The promotion criteria require positive median return under all three cost modes, at least 60% profitable primary panels, worst drawdown ≤20%, and a positive median paired Sharpe improvement over the fixed EMA10/50 four-hour reversal baseline. The same perpetual prices and funding also test an EMA10/50 long-only baseline; those figures should not be compared directly with the earlier spot-market study.

Freeze: `2026-10-06T10:59:43.027594+00:00`. Selection SHA256: `aec69230dfcda8450a692ac1240efafab7e8e90272de8c099ce3fd38eec442df`. The protocol, registry, shortlist, rankings, final metrics, and net trade ledger are retained.

## Execution assumptions and Pine limits

Price and realized funding archives come from Binance public USD-M monthly data, with publisher CHECKSUM verification and a retained manifest. Incomplete higher-timeframe candles are excluded, gaps remain, and orders use the next observed open. Funding is charged to the position held immediately before an opening boundary, using that trade open as a historical mark-price proxy. Actual exchange mark prices and order/settlement priority can differ. Funding data gaps raise errors rather than silently becoming zero.

Adverse funding adds a 0.01% charge per eight hours to both directions, scaled to the recorded funding interval; it is separate from doubled execution costs. Derivative equity accounting, entry fees, funding signs, short stops, next-open reversals, gap stops and ledger totals have synthetic checks. Every logged final trade reconciles to its account result. A deterministic rerun uses the same engine and is not an independent implementation audit.

Liquidation uses a constant 0.5% maintenance proxy and trade prices, with no exact exchange tiers, mark prices, insurance or ADL. The cap applies at entry. Tick/lot rounding, available liquidity and gap fills can change live results.

`Long_Short_Reversal.pine` uses Pine v6 and frozen defaults, confirmed signal-close orders, both directions, fixed stops and cooldown. TradingView commission is set to a **0.15% cash-cost proxy per side** with zero tick slippage. Its tester does not apply the historical funding ledger. Python caps position size again at the opening fill; Pine sends its size at decision time. TradingView margin, stop priority, bar magnifier coverage, tick/lot rounding and gap handling can differ. **The Pine file has not been compiled or run in TradingView.** Do not claim its tester will reproduce the Python numbers exactly.

Use standard Binance perpetual-price charts, load enough history for at least 100 complete daily candles, and keep defaults when comparing with this study. Changing an input invalidates the frozen-default evidence. No broker orders were placed.
