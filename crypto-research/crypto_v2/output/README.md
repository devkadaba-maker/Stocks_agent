# Fresh-data crypto optimisation, revision 2

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
