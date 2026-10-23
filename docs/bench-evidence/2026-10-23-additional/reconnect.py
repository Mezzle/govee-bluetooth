import subprocess,time,json,pathlib
out=pathlib.Path('docs/bench-evidence/2026-10-23-additional')
for model,address in [('H6159','A4:C1:38:9C:8E:17'),('H6181','60:74:F4:6A:FB:1E')]:
 for i in range(1,4):
  start=time.monotonic()
  command=f'~/rig/bin/python ~/govee-bluetooth/tools/govee_rig.py --log /tmp/additional-{model.lower()}.jsonl send {address} --model {model} power brightness mode'
  try:
   p=subprocess.run(['ssh','homelab-operator@discoball',command],capture_output=True,text=True,timeout=90)
   body=p.stdout+p.stderr;code=p.returncode
  except subprocess.TimeoutExpired as e:
   body=str(e.stdout)+str(e.stderr);code=124
  (out/f'{model.lower()}-reconnect-{i}.txt').write_text(body)
  success=code==0 and all(x in body for x in ['reply       power','reply       brightness','reply       mode'])
  record=dict(model=model,trial=i,elapsed_s=round(time.monotonic()-start,3),exit_code=code,reads_passed=success,disconnect_callbacks_before_connected=body.split('connected (mtu')[0].count('** light disconnected **'))
  with (out/'reconnect-summary.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
  print(json.dumps(record),flush=True)
  time.sleep(1)
