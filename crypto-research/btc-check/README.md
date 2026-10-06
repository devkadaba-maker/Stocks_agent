# BTC frozen-parameter check

Tested after the original final study and reproduction audit. The selected strategy hash stayed unchanged. No BTC optimisation or parameter selection occurred.

**The 2024–September 2026 period overlaps BTC data used by the earlier study. These are retrospective results, not a fresh chronological holdout. October 1–5 is an unused short sample, with only one candidate trade on 5m/10m.**

The strategy uses confirmed 4-hour EMA10/50 signals and a fixed 4×ATR14 stop, 0.5% target stop risk, 95% spot cap, long only. Every evaluation window starts flat with $10,000 and any open position is liquidated at the final observed close with costs.

Primary costs: 0.10% commission plus 0.05% adverse fill-price slippage per side. Stress doubles both. January 2022–December 2023 provides causal warmup. Raw monthly/daily data were checked against Binance publisher checksums.

## Candidate results

| Period | Chart | Net return | Double-cost return | Primary drawdown | Trades |
|---|---|---:|---:|---:|---:|
| Jan 2024–Sep 2026 (retrospective) | 5m | +7.82% | +5.17% | 4.50% | 82 |
| Jan 2024–Sep 2026 (retrospective) | 10m | +7.82% | +5.17% | 4.50% | 82 |
| Jan 2024–Sep 2026 (retrospective) | 60m | +7.82% | +5.17% | 4.50% | 82 |
| Jan 2024–Sep 2026 (retrospective) | 240m | +7.82% | +5.17% | 4.42% | 82 |
| Jan 2024–Sep 2026 (retrospective) | 1440m | +8.63% | +6.41% | 4.12% | 70 |
| Oct 1–5, 2026 (5 days) | 5m | +0.26% | +0.22% | 0.47% | 1 |
| Oct 1–5, 2026 (5 days) | 10m | +0.26% | +0.22% | 0.47% | 1 |
| Oct 1–5, 2026 (5 days) | 60m | +0.26% | +0.22% | 0.43% | 1 |
| Oct 1–5, 2026 (5 days) | 240m | +0.26% | +0.22% | 0.37% | 1 |
| Oct 1–5, 2026 (5 days) | 1440m | +0.09% | +0.05% | 0.22% | 1 |

Identical 5m/10m returns reflect shared 4-hour signals and fixed stop fills. They are not independent replications. Five-day Sharpe values are intentionally suppressed because this is too short a period for a meaningful risk-adjusted assessment. These results do not change the failed broad-market promotion outcome.

## Files

- [All 40 cases](BTC_results.csv), including the original baseline, primary costs and doubled costs.
- [Complete trade ledger](BTC_trades.csv).
- [Frozen check protocol](BTC_check_protocol.json).
- [Source checksums](source_manifest.json).
- [Reproduction script](test_btc.py).

From the repository root, using Python 3.12:

```powershell
py -m pip install -r .\crypto-research\crypto_v2\requirements.txt; if ($LASTEXITCODE -eq 0) { py .\crypto-research\btc-check\test_btc.py }
```

This replays fixed rules only. It does not choose new parameters.
