"""Protection removal must fail business assertions in isolated /tmp clones."""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(sys.argv[1]).resolve();OUT.mkdir(parents=True,exist_ok=True)
CASES=[
 ('no_idempotency','assistant_agent.py',"hashlib.sha256(identity.encode()).hexdigest()[:32]","hashlib.sha256((identity+uuid.uuid4().hex).encode()).hexdigest()[:32]",'test_one_slug_concurrent_retries_changed_arguments_and_restart'),
 ('modify_existing','assistant_agent.py',"if new_work_intent(job.get('prompt','')) in ('create','clarify') and name not in READ_TOOLS:","if False and new_work_intent(job.get('prompt','')) in ('create','clarify') and name not in READ_TOOLS:",'test_creation_preserves_current_work_and_facts'),
 ('history_authorizes','assistant_agent.py',"if new_work_intent(job.get('prompt','')) != 'create'", "if new_work_intent((self.turns[-1] if self.turns else job).get('prompt','')) != 'create'",'test_history_and_assistant_answer_never_authorize_creation'),
 ('no_failure_cleanup','assistant_agent.py',"if path.exists():shutil.rmtree(path)","if False and path.exists():shutil.rmtree(path)",'test_packaging_disk_and_collection_failure_cleanup'),
 ('lost_history_links','assistant_agent.py',"keys=('id','prompt','answer','actions','status','time','policy'", "keys=('id','prompt','answer','status','time','policy'",'test_start_duplicate_protocol_turn_is_idempotent'),
 ('archive_existing','work_store.py',"        path=self.article(slug)/'assistant-created.json'", "        return {'archived':False}\n        path=self.article(slug)/'assistant-created.json'",'test_archive_restore_reject_existing_and_preserve_versions'),
 ('only_first_line','assistant_policy.py',"instructions=_creation_instruction_lines(message)","instructions=_creation_instruction_lines(str(message or '').split('\\n',1)[0])",'R1Tests.test_r1_material_then_request'),
 ('ignore_user_negative','assistant_policy.py',"    if any(re.search(r'不要", "    if False and any(re.search(r'不要",'R1Tests.test_r1_negative_later_line'),
 ('generic_writing_is_article','assistant_policy.py',"(?:文章|长文|推文|稿子?|类似的)|写(?:一篇|篇)","(?:文章|长文|推文|稿子?|类似的)?|写(?:一篇|篇)",'R1Tests.test_r1_script'),

]
results=[]
for name,file,old,new,test in CASES:
 with tempfile.TemporaryDirectory(prefix='assistant-create-mutation-',dir='/tmp') as tmp:
  dest=Path(tmp)
  def ignore(directory,names):return set(names)&{'.git','node_modules','evidence','__pycache__','.pytest_cache'}
  shutil.copytree(ROOT,dest,dirs_exist_ok=True,ignore=ignore)
  (dest/'pi-runtime/node_modules').symlink_to(ROOT/'pi-runtime/node_modules',target_is_directory=True)
  source=dest/file;value=source.read_text();assert value.count(old)==1,(name,value.count(old));source.write_text(value.replace(old,new))
  result=subprocess.run([sys.executable,'-B','-m','unittest',('tests.test_assistant_create_work_r1.'+test if test.startswith('R1Tests.') else 'tests.test_assistant_create_work.CreateWorkTests.'+test)],cwd=dest,capture_output=True,text=True,timeout=45)
  log=result.stdout+result.stderr;(OUT/(name+'.log')).write_text(log)
  killed=result.returncode!=0 and 'AssertionError' in log and not any(x in log for x in ('SyntaxError','ModuleNotFoundError','ImportError'))
  results.append({'mutation':name,'target':test,'exit':result.returncode,'business_assertion_failed':killed})
(OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))
if not all(r['business_assertion_failed'] for r in results):sys.exit(1)
