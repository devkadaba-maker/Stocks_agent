"""Audit the original price/funding archives without selecting or tuning."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parent
def main():
    rows=json.loads((ROOT/'data/original_manifests.json').read_text())
    for r in rows:
        assert r['status']=='verified',(r['url'],r['status'])
        p=ROOT/'data'/r['stage']/r['file']
        assert hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256'],str(p)
    selected=ROOT/'output/selected.json';cfg=json.loads(selected.read_text())
    assert hashlib.sha256(selected.read_bytes()).hexdigest()==(ROOT/'output/selected.sha256').read_text()
    assert hashlib.sha256((ROOT/'protocol.json').read_bytes()).hexdigest()==cfg['protocol_sha256']
    result=dict(passed=True,verified_archives=len(rows),selection_and_protocol_hashes_unchanged=True)
    (ROOT/'output/archive_check.json').write_text(json.dumps(result,indent=2));print(result)
if __name__=='__main__':main()
