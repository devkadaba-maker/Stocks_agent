"""Restore exact original CSV/JSON results from portable compressed evidence."""
from pathlib import Path
import hashlib,json,base64,gzip
root=Path(__file__).resolve().parent.parent
for record in json.loads((root/'compressed-results.json').read_text()):
    encoded=b''.join(base64.b64decode((root/p).read_bytes(),validate=True) for p in record['parts'])
    data=gzip.decompress(encoded)
    assert len(data)==record['bytes'] and hashlib.sha256(data).hexdigest()==record['sha256'], 'Results checksum mismatch'
    target=root/record['target'];target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        assert hashlib.sha256(target.read_bytes()).hexdigest()==record['sha256'], 'Existing results differ; refusing overwrite'
    else:target.write_bytes(data)
print('All compressed results restored and checksum verified')
