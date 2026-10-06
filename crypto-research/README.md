# Crypto strategy research and reproduction audit

Research candidate: **the broad profitability promotion test failed**. This project does not establish future profitability. The Pine Script has not yet been compiled or reconciled in TradingView.

## Start here

- [Pine Script v6](crypto_v2/output/Crypto_Strategy.pine)
- [Strategy report](crypto_v2/output/Crypto_Strategy_Report.html) — download and open locally.
- [Reproduction audit](crypto_v2/proof/Crypto_Proof_Report.html) — download and open locally.
- [Complete repository download](https://github.com/devkadaba-maker/Stocks_agent/archive/refs/heads/main.zip)
- [Compressed full research and audit evidence](compressed-results.json)
- [Final-test results](crypto_v2/output/final_test.csv)
- [Separate implementation comparisons](crypto_v2/proof/reproduction_comparison.csv)
- [Detailed reproduced trade ledger](compressed-results.json)

## Frozen strategy and results

Confirmed 4-hour EMA10/EMA50, fixed 4×ATR14 stop, 0.5% target initial stop risk, 95% spot cap, long only. The account starts at $10,000. Primary Python costs are 0.10% commission plus 0.05% adverse fill-price slippage **per side**; stressed costs double both. Risk targets exclude trading costs and can be exceeded by gaps.

The study screened 2,048 predeclared configurations, trained on 2020–2022, validated its fixed shortlist on 2023, then assessed the frozen winner on 2024–September 2026 across eight other coins. It excluded the BTC/ETH/BNB/SOL 2020–September 2026 candles used in the earlier study. BTC/ETH 2018–2019 checks are historical transfer checks with differing warmup requirements, not current BTC validation.

A separately written implementation reproduced all **200 final cases** and matched all **4,728 saved primary-cost trades** to numerical rounding. These cases span 10 assets, five execution charts, two models and two cost levels. They are correlated, not 200 independent trials.

Across the 40 modern primary-cost candidate panels, 36 had higher Sharpe than the incumbent, but only 20 were profitable. Median return was −0.14%, median doubled-cost return was −1.46%, and worst drawdown was 12.44%. The profitability requirement failed. Parameters were not changed after the final assessment. Reproduction is an audit of that frozen result, not new out-of-sample evidence.

## Reproduce the audit

Use Python 3.12. In PowerShell, from the repository root, run:

```powershell
Set-Location .\crypto-research; py -m pip install -r .\crypto_v2\requirements.txt; if ($LASTEXITCODE -eq 0) { py .\crypto_v2\restore_archived_results.py }; if ($LASTEXITCODE -eq 0) { py .\crypto_v2\download.py }; if ($LASTEXITCODE -eq 0) { py .\crypto_v2\download_warmup.py }; if ($LASTEXITCODE -eq 0) { py .\crypto_v2\independent_audit.py }; if ($LASTEXITCODE -eq 0) { py .\crypto_v2\audit_guard_checks.py }
```

Large research results are stored once as portable compressed parts. The setup command restores and checksum-checks every original CSV/JSON file, including the complete training panels, with no manual assembly. Raw price archives are downloaded from Binance public data and checked against publisher checksums. They are not duplicated in Git. Missing or changed source archives must not be silently substituted.

Do not rerun `search.py train`, `search.py validate` or `final_test.py` against these already-seen final data for further selection. Existing guards intentionally reject those actions. The audit command above only reproduces the frozen result.

The published HTML reports are the original delivered artifacts. All original research and audit code and data are included; large results are compressed rather than duplicated in both delivered ZIPs. Exporters can regenerate reports locally; doing so will change their file hashes and is unnecessary for reproduction. No broker integration or live execution is enabled by this folder.

## Additional BTC check

[BTC report and reproduction](btc-check/README.md): frozen candidate on 5m/10m returned +7.82% for Jan 2024–Sep 2026 (+5.17% at doubled costs), with 82 trades and about 4.50% drawdown. This period overlaps earlier BTC data and is labelled retrospective. The unused Oct 1–5 check returned +0.26% with only one trade; it is too short to establish robustness. No settings were changed.
