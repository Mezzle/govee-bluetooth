import subprocess,json,pathlib
out=pathlib.Path('docs/bench-evidence/2026-10-23-additional')
for stem in ['additional-h6159','additional-h6181','exact-7558-h6159','exact-7558-h6181','exact-clean-h6181']:
 p=subprocess.run(['ssh','homelab-operator@discoball',f'cat /tmp/{stem}.jsonl'],capture_output=True,text=True,check=True)
 records=[]
 for line in p.stdout.splitlines():
  d=json.loads(line)
  if 'frame' in d:
   f=bytes.fromhex(d['frame']);assert len(f)==20 and f[0] in (0x33,0xaa) and f[1] in (1,4,5,6)
   checksum=0
   for b in f:checksum^=b
   assert checksum==0
  elif d.get('summary')!='verify':continue
  records.append(d)
 (out/(stem+'.jsonl')).write_text(''.join(json.dumps(d)+'\n' for d in records))
 print(stem,len(records))
