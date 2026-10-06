# Crypto reversal v3

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
