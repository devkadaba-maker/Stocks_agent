"""Build bounded text chunks and GitHub tree batches; no market archives."""
from pathlib import Path
import json,gzip,base64,hashlib
ROOT=Path(__file__).resolve().parent
def main():
    packed=ROOT/'packed';packed.mkdir(exist_ok=True);manifest=[];excluded=set()
    for p in (ROOT/'output').glob('*.csv'):
        if p.stat().st_size<180000:continue
        data=p.read_bytes();blob=gzip.compress(data,compresslevel=9,mtime=0);parts=[]
        for i,start in enumerate(range(0,len(blob),196608)):
            part=packed/(p.stem+f'.{i:03d}.gz.b64');part.write_text(base64.b64encode(blob[start:start+196608]).decode());parts.append(str(part.relative_to(ROOT)))
        manifest.append(dict(file=str(p.relative_to(ROOT)),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),parts=parts));excluded.add(p)
    (packed/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (ROOT/'.gitignore').write_text('data/development/\ndata/final/\ndata/btc/\n__pycache__/\n'+''.join(x['file']+'\n' for x in manifest))
    files=[]
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file() or p in excluded or '__pycache__' in p.parts or p.suffix=='.zip':continue
        content=p.read_text();path='crypto-research/reversal-v3/'+str(p.relative_to(ROOT));b=p.read_bytes()
        files.append(dict(path=path,mode='100644',type='blob',content=content,sha=hashlib.sha1(f'blob {len(b)}\0'.encode()+b).hexdigest()))
    dest=ROOT.parent/'upload';dest.mkdir(exist_ok=True);batches=[];current=[];size=0
    for f in files:
        n=len(json.dumps(f))
        if current and size+n>700000:batches.append(current);current=[];size=0
        current.append(f);size+=n
    if current:batches.append(current)
    for i,batch in enumerate(batches):(dest/f'batch{i:02d}.json').write_text(json.dumps([{k:v for k,v in f.items() if k!='sha'} for f in batch]))
    (dest/'expected.json').write_text(json.dumps([{k:v for k,v in f.items() if k!='content'} for f in files]))
    print('Packaged',len(files),'files in',len(batches),'tree batches')
if __name__=='__main__':main()
