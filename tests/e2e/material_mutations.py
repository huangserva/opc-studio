"""Run destructive mutations ONLY in the isolated /tmp checkout; restore each one."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
if not str(ROOT).startswith(('/tmp/','/private/tmp/')):
    raise SystemExit('变异测试只能在 /tmp 独立副本执行')
EVIDENCE=ROOT/'coordination/evidence/material-mutations'
EVIDENCE.mkdir(parents=True,exist_ok=True)
UNIT=[sys.executable,'-m','unittest']
CASES=[
 ('raw-prompt','pi_runtime.py',"model_prompt(job['prompt'], job.get('materials', []))", "job['prompt'] + '\\n' + '\\n'.join(m['text'] for m in job.get('materials', []))",UNIT+['tests.test_assistant_materials.AssistantMaterialTests.test_samples_cannot_authorize_and_only_appear_in_reference_block']),
 ('policy-reads-material','assistant_agent.py','policy=policy_for(text, work_open=',"policy=policy_for(text+'\\n'+'\\n'.join(m['text'] for m in materials), work_open=",UNIT+['tests.test_assistant_materials.AssistantMaterialTests.test_samples_cannot_authorize_and_only_appear_in_reference_block']),
 ('history-loses-material','assistant_agent.py',"'materials':t.get('materials',[])","'materials':[]",UNIT+['tests.test_assistant_materials.AssistantMaterialTests.test_pending_history_and_restart_preserve_cards']),
 ('deleted-card-sent','public/assistant.js',".filter(m=>m.id!==b.dataset.materialRemove)",'.filter(m=>true)',['node','tests/e2e/material_cards.cjs']),
 ('limit-not-enforced','assistant_materials.py','if len(value) > MAX_MATERIALS:','if False:',UNIT+['tests.test_assistant_materials.MaterialValidationTests.test_invalid_types_count_lengths_and_empty']),
]
results=[]
for name,filename,old,new,command in CASES:
    path=ROOT/filename;original=path.read_bytes();source=original.decode()
    assert source.count(old)==1,(name,source.count(old))
    try:
        path.write_text(source.replace(old,new))
        result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90,env={**os.environ,'E2E_PYTHON':sys.executable})
        (EVIDENCE/(name+'.log')).write_text(result.stdout)
        # A mutation must fail through a semantic assertion, not syntax/import/setup errors.
        caught=result.returncode!=0 and ('AssertionError' in result.stdout or 'FAIL:' in result.stdout)
        results.append({'name':name,'returncode':result.returncode,'detected':caught})
    finally:path.write_bytes(original)
(EVIDENCE/'results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,ensure_ascii=False))
assert len(results)==5 and all(r['detected'] for r in results),'必须逐项检出 5 个变异'
