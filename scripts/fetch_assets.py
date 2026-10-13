"""Download original Unitree assets at the verified model revision."""
from pathlib import Path
import urllib.request, xml.etree.ElementTree as ET, concurrent.futures, hashlib, json
ROOT=Path(__file__).resolve().parents[1]
REV='2fc4f3087920f4c309676e5389f6ede13e2448a8'
BASE=f'https://raw.githubusercontent.com/unitreerobotics/unitree_ros/{REV}/'
def download(item):
    url,path=item
    path.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        try:
            data=urllib.request.urlopen(url,timeout=60).read();path.write_bytes(data)
            return {'path':str(path.relative_to(ROOT)), 'url':url,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
        except Exception:
            if attempt==2: raise
urdf=ROOT/'public/model/g1_d.urdf'
records=[download((BASE+'robots/g1_d_description/g1_d.urdf',urdf))]
names=sorted(set(e.attrib['filename'] for e in ET.parse(urdf).iter('mesh')))
items=[(BASE+'robots/g1_d_description/'+n,ROOT/'public/model'/n) for n in names]
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for r in pool.map(download,items):
        records.append(r);print(r['path'],flush=True)
for url,path in [(BASE+'LICENSE',ROOT/'public/model/LICENSE'),('https://www.unitree.com/images/a9884c71bc514abe8d928b58fad573fe_585x1270.png',ROOT/'public/model/reference.png')]:
    try: records.append(download((url,path)))
    except Exception as e: print('Optional reference/license download:',e)
(ROOT/'public/model/provenance.json').write_text(json.dumps({'revision':REV,'assets':records},indent=2))
print('Downloaded',len(records),'assets')
