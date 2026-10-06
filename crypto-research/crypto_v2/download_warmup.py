import concurrent.futures as cf,json
from pathlib import Path
import pandas as pd
from download import fetch,ROOT
j=[(s,str(m),'sealed') for s in ['DOGEUSDT','AVAXUSDT','ATOMUSDT','DOTUSDT'] for m in pd.period_range('2022-01','2023-12',freq='M')]
(ROOT/'data/sealed').mkdir(parents=True,exist_ok=True)
with cf.ThreadPoolExecutor(max_workers=16) as e:results=list(e.map(fetch,j))
(ROOT/'data/manifest_warmup.json').write_text(json.dumps(results,indent=2))
assert all(m['status']=='verified' for m in results)
print('Sealed indicator warmup archives verified',len(results))
