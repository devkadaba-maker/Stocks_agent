# Frozen crypto backtest audit

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
