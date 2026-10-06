"""Restore losslessly packed research results, checking uncompressed SHA256."""
from pathlib import Path
import json,base64,gzip,hashlib
ROOT=Path(__file__).resolve().parent
def main():
    manifest=json.loads((ROOT/'packed/manifest.json').read_text())
    for item in manifest:
        blob=b''.join(base64.b64decode((ROOT/p).read_text(),validate=True) for p in item['parts'])
        data=gzip.decompress(blob)
        assert hashlib.sha256(data).hexdigest()==item['sha256'],item['file']
        target=ROOT/item['file'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        print('Restored and verified:',item['file'])
if __name__=='__main__':main()
