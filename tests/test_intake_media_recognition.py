"""Synthetic media jobs, no installed model invocation or GPU request."""
from isolation_patches import target_start
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from intake_store import Intake
from intake_media_recognition import MediaRecognizer,CapabilityUnavailable,ProfileVision
from knowledge_store import Knowledge
from server import Store

class IntakeRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.store._knowledge=Knowledge(self.store,sources=[])
        self.intake=Intake(self.store.knowledge,self.tmp.name)
    def tearDown(self):
        for worker in list(self.intake.recognition_workers.values()):worker.join(5)
        self.tmp.cleanup()
    def image(self):
        b=io.BytesIO();Image.new('RGB',(10,10),'red').save(b,format='PNG')
        return self.intake.receive_file('image','probe.png','  reason\n',io.BytesIO(b.getvalue()),len(b.getvalue()))
    def wait(self,ident):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=self.intake.detail(ident)
            if row['recognition'].get('status') not in ('running','queued'):return row
            time.sleep(.01)
        self.fail('worker not finished')
    def test_success_independent_field_does_not_modify_original_note_or_reason(self):
        self.intake.media_recognizer=MediaRecognizer(lambda path:{'text':'红色方块','model':'synthetic vision'})
        saved=self.image();done=self.intake.resolve(saved['id']);doc=self.store.knowledge.document(done['document_id'])
        before=doc['body'];row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'succeeded');self.assertEqual(row['recognition']['label'],'AI 描述，未核实')
        self.assertEqual(row['reason'],'  reason\n');self.assertEqual(self.store.knowledge.document(done['document_id'])['body'],before)
        self.assertEqual(self.intake._raw(saved['id'])['text'],'')
    def test_unavailable_failure_and_old_resolved_reparse(self):
        self.intake.media_recognizer=MediaRecognizer()
        saved=self.image();self.intake.resolve(saved['id']);row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'unavailable');self.assertIn('未接上图片识别模型',row['recognition']['error'])
        self.intake.media_recognizer=MediaRecognizer(lambda path:(_ for _ in ()).throw(ValueError('synthetic failure')))
        self.intake.resolve(saved['id']);row=self.wait(saved['id']);self.assertEqual(row['recognition']['status'],'failed')
        document=row['document_id']
        self.intake.media_recognizer=MediaRecognizer(lambda path:{'text':'later result','model':'synthetic'})
        self.intake.resolve(saved['id']);row=self.wait(saved['id']);self.assertEqual(row['recognition']['status'],'succeeded')
        self.assertEqual(row['document_id'],document)
    def test_duplicate_reparse_is_not_parallel(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def describe(path):calls.append(path);entered.set();release.wait(3);return {'text':'done','model':'synthetic'}
        self.intake.media_recognizer=MediaRecognizer(describe);saved=self.image();self.intake.resolve(saved['id'])
        self.assertTrue(entered.wait(2));self.intake.resolve(saved['id']);self.assertEqual(len(calls),1)
        release.set();self.wait(saved['id'])

    # ── 识别完成后自动排队一次分析（round5）─────────────────────────

    def test_recognition_success_queues_one_auto_analysis(self):
        requested=[]
        self.intake.analysis_after_recognition=lambda drop_id:requested.append(drop_id)
        self.intake.media_recognizer=MediaRecognizer(lambda path:{'text':'红色方块','model':'synthetic vision'})
        saved=self.image();self.intake.resolve(saved['id']);row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'succeeded')
        self.assertEqual(requested,[saved['id']])
    def test_recognition_without_new_material_does_not_auto_analyse(self):
        requested=[]
        self.intake.analysis_after_recognition=lambda drop_id:requested.append(drop_id)
        self.intake.media_recognizer=MediaRecognizer()          # 没接上模型 → unavailable
        saved=self.image();self.intake.resolve(saved['id']);self.wait(saved['id'])
        self.intake.media_recognizer=MediaRecognizer(lambda path:(_ for _ in ()).throw(ValueError('synthetic failure')))
        self.intake.resolve(saved['id']);self.wait(saved['id'])
        self.intake.media_recognizer=SimpleNamespace(recognize_image=lambda path,meta:{'status':'no_audio','text':'','scope':'无音轨'},recognize_video=lambda path,meta:{})
        self.intake.resolve(saved['id']);row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'no_audio')
        self.assertEqual(requested,[])
    def test_a_failing_auto_analysis_hook_keeps_the_recognition_result(self):
        def refuse(drop_id):raise ValueError('这条回收项还没有存入知识库')
        self.intake.analysis_after_recognition=refuse
        self.intake.media_recognizer=MediaRecognizer(lambda path:{'text':'红色方块','model':'synthetic vision'})
        saved=self.image();self.intake.resolve(saved['id']);row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'succeeded')
        self.assertEqual(row['recognition']['text'],'红色方块')
    def test_no_hook_installed_still_saves_recognition(self):
        self.intake.media_recognizer=MediaRecognizer(lambda path:{'text':'红色方块','model':'synthetic vision'})
        saved=self.image();self.intake.resolve(saved['id']);row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'succeeded')

class ASRBackendTests(unittest.TestCase):
    def test_no_audio_never_calls_host(self):
        backend=MediaRecognizer()
        with patch.object(backend,'_ssh') as ssh:
            self.assertEqual(backend.recognize_video(Path('synthetic.mp4'),{'has_audio':False})['status'],'no_audio');ssh.assert_not_called()
    def test_busy_gpu_is_deferred_without_model_or_upload(self):
        backend=MediaRecognizer()
        with patch('intake_media_recognition.shutil.which',return_value='/synthetic/bin'),patch('intake_media_recognition.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps({'streams':[{'codec_type':'audio'}],'format':{'duration':'12'}}))) as run,patch.object(backend,'_ssh',side_effect=[SimpleNamespace(stdout=''),SimpleNamespace(stdout='20')]) as ssh:
            with self.assertRaisesRegex(CapabilityUnavailable,'正在使用'):backend.recognize_video(Path('synthetic.mp4'),{})
            self.assertEqual(ssh.call_count,2);self.assertEqual(run.call_count,1)
    def test_verified_asr_pipeline_synthetic_success_and_bounded_scope(self):
        backend=MediaRecognizer()
        replies=[SimpleNamespace(stdout=''),SimpleNamespace(stdout='0'),SimpleNamespace(stdout='0'),SimpleNamespace(stdout=''),SimpleNamespace(stdout=json.dumps([{'text':'合成语音文本','timestamp':[[0,100]]}])),SimpleNamespace(stdout='')]
        with patch('intake_media_recognition.shutil.which',return_value='/synthetic/bin'),patch('intake_media_recognition.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps({'streams':[{'codec_type':'audio'}],'format':{'duration':'900'}}))) as run,patch.object(backend,'_ssh',side_effect=replies) as ssh:
            result=backend.recognize_video(Path('synthetic.mp4'),{})
        self.assertEqual(result['text'],'合成语音文本');self.assertEqual(result['coverage_seconds'],600);self.assertFalse(result['complete'])
        self.assertIn('剩余未读取',result['scope']);self.assertIn('第一条音轨',result['scope']);self.assertIn('OFFLINE=1',ssh.call_args_list[3].args[0])
    def test_gpu_becomes_busy_after_upload_skips_model_and_cleans_up(self):
        backend=MediaRecognizer()
        replies=[SimpleNamespace(stdout=''),SimpleNamespace(stdout='0'),SimpleNamespace(stdout='30'),SimpleNamespace(stdout='')]
        with patch('intake_media_recognition.shutil.which',return_value='/synthetic/bin'),patch('intake_media_recognition.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps({'streams':[{'codec_type':'audio'}]}))),patch.object(backend,'_ssh',side_effect=replies) as ssh:
            with self.assertRaises(CapabilityUnavailable):backend.recognize_video(Path('synthetic.mp4'),{})
        self.assertFalse(any('HF_HUB_OFFLINE' in call.args[0] for call in ssh.call_args_list))
        self.assertTrue(ssh.call_args_list[-1].args[0].startswith('rm -f /tmp/article-studio-intake-'))

    def test_no_ffmpeg_is_unavailable(self):
        with patch('intake_media_recognition.shutil.which',return_value=None):
            with self.assertRaises(CapabilityUnavailable):MediaRecognizer().recognize_video(Path('synthetic'),{})

class ProfileVisionTests(unittest.TestCase):
    """图片识别接当前模型配置：一次调用、先缩放、不读密钥；模型没配或正忙就不调。

    全部用假 PiRuntime：不起 node、不联网、不调真实模型。
    """

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.store._knowledge=Knowledge(self.store,sources=[])
        self.intake=Intake(self.store.knowledge,self.tmp.name)
        self.turns=[]

    def tearDown(self):
        for worker in list(self.intake.recognition_workers.values()):worker.join(5)
        self.tmp.cleanup()

    def png(self,size=(10,10)):
        path=Path(self.tmp.name)/'source.png'
        Image.new('RGB',size,'red').save(path,format='PNG')
        return path

    def wait(self,ident):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=self.intake.detail(ident)
            if row['recognition'].get('status') not in ('running','queued'):return row
            time.sleep(.01)
        self.fail('worker not finished')

    def runtime(self,answer='一张红色方块的图',status='done'):
        turns=self.turns
        class Fake:
            def __init__(self,owner):self.owner=owner
            def run(self,job,system,tools):
                # pi_runtime 会在这一步把附件读成 base64，所以当场留下字节再核对。
                image=self.owner.attachment_path(job['attachments'][0]['id'])
                session=self.owner.folder/'pi-sessions'/job['conversation_id']
                session.mkdir(parents=True,exist_ok=True)
                (session/'marker.txt').write_text('不应留给下一张图',encoding='utf-8')
                turns.append({'job':job,'owner':self.owner,'system':system,'tools':tools,
                              'image':image,'bytes':image.read_bytes()})
                return {'status':status,'answer':answer}
        return Fake

    def config(self):
        return {'model':'qwen-vision','endpoint':'https://example/v1/chat/completions','timeout_seconds':100}

    def test_no_model_config_is_honest_and_calls_nothing(self):
        vision=ProfileVision(lambda:None)
        with patch('pi_runtime.PiRuntime',self.runtime()):
            with self.assertRaisesRegex(CapabilityUnavailable,'未接上图片识别模型'):vision(self.png())
        self.assertEqual(self.turns,[])

    def test_busy_model_is_refused_before_any_call(self):
        from scheduled_tasks import ModelBusy
        vision=ProfileVision(lambda:self.config(),lambda config:True)
        with patch('pi_runtime.PiRuntime',self.runtime()):
            with self.assertRaises(ModelBusy):vision(self.png())
        self.assertEqual(self.turns,[])

    def test_one_call_only_and_the_image_goes_as_a_real_attachment(self):
        vision=ProfileVision(lambda:self.config(),lambda config:False,Path(self.tmp.name))
        with patch('pi_runtime.PiRuntime',self.runtime('红色方块，无文字')):
            result=vision(self.png((3000,1500)))
        self.assertEqual(len(self.turns),1)
        job=self.turns[0]['job']
        self.assertEqual(job['policy']['allowed_tools'],[])
        self.assertEqual(len(job['attachments']),1)
        turn=self.turns[0]
        self.assertEqual(turn['bytes'][:8],b'\x89PNG\r\n\x1a\n')          # 真 PNG，pi_runtime 会编成 image_url data URI
        with Image.open(io.BytesIO(turn['bytes'])) as prepared:
            self.assertEqual(prepared.size,(2048,1024))                      # 长边先缩到 2048 以内
        self.assertEqual(result['model'],'qwen-vision')
        self.assertEqual(result['text'],'红色方块，无文字')
        self.assertIn('已缩放至 2048×1024',result['scope'])
        self.assertNotEqual(job['conversation_id'],'intake-vision')
        self.assertTrue(job['conversation_id'].startswith('intake-vision-'))
        self.assertEqual(job['max_provider_requests'],1)
        self.assertIs(job['compaction_enabled'],False)
        self.assertFalse((Path(self.tmp.name)/'pi-sessions'/job['conversation_id']).exists())
        self.assertFalse(any(p.name.startswith('vision-') for p in Path(self.tmp.name).iterdir()))

    def test_empty_answer_is_a_failure_not_a_made_up_description(self):
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        with patch('pi_runtime.PiRuntime',self.runtime('   ')):
            with self.assertRaises(ValueError):vision(self.png())
        self.assertEqual(len(self.turns),1)

    def test_unreadable_image_never_reaches_the_model(self):
        path=Path(self.tmp.name)/'broken.png';path.write_bytes(b'not an image')
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        with patch('pi_runtime.PiRuntime',self.runtime()):
            with self.assertRaisesRegex(ValueError,'读不到'):vision(path)
        self.assertEqual(self.turns,[])

    def test_recognition_through_the_drop_path_uses_one_model_call(self):
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        self.intake.media_recognizer=MediaRecognizer(vision)
        requested=[]
        self.intake.analysis_after_recognition=lambda drop_id:requested.append(drop_id)
        buffer=io.BytesIO();Image.new('RGB',(4000,2000),'blue').save(buffer,format='PNG')
        saved=self.intake.receive_file('image','big.png','',io.BytesIO(buffer.getvalue()),len(buffer.getvalue()))
        with patch('pi_runtime.PiRuntime',self.runtime('蓝色背景，无文字')):
            self.intake.resolve(saved['id'])
            row=self.wait(saved['id'])
        self.assertEqual((row['recognition']['status'],row['recognition'].get('error')),('succeeded',None))
        self.assertEqual(len(self.turns),1)
        self.assertEqual(requested,[saved['id']])
        self.assertIn('图片内容已识别',row['state'])

    def test_busy_model_leaves_the_drop_unavailable_not_failed(self):
        from scheduled_tasks import ModelBusy
        def busy(path):raise ModelBusy('同一模型已有后台任务在运行，图片识别暂缓；可稍后重新解析')
        self.intake.media_recognizer=MediaRecognizer(busy)
        buffer=io.BytesIO();Image.new('RGB',(10,10),'blue').save(buffer,format='PNG')
        saved=self.intake.receive_file('image','busy.png','取景可参考',io.BytesIO(buffer.getvalue()),len(buffer.getvalue()))
        self.intake.resolve(saved['id'])
        row=self.wait(saved['id'])
        self.assertEqual(row['recognition']['status'],'unavailable')
        self.assertIn('图片识别暂缓',row['recognition']['error'])
        self.assertIn('图片内容未识别',row['state'])

    def test_each_image_opens_a_new_session(self):
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        with patch('pi_runtime.PiRuntime',self.runtime('第一张')):
            vision(self.png())
        with patch('pi_runtime.PiRuntime',self.runtime('第二张')):
            vision(self.png())
        ids=[turn['job']['conversation_id'] for turn in self.turns]
        self.assertEqual(len(ids),2)
        self.assertEqual(len(set(ids)),2)
        self.assertNotIn('intake-vision',ids)
        for ident in ids:
            self.assertFalse((Path(self.tmp.name)/'pi-sessions'/ident).exists())

    def _assert_scaled_file_is_gone(self):
        self.assertFalse(any(p.name.startswith('vision-') for p in Path(self.tmp.name).iterdir()))
        self.assertEqual(self.turns,[])

    def test_partial_write_still_removes_the_scaled_file(self):
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        original=Path.write_bytes
        def fail(target,data):
            if target.name.startswith('vision-'):
                with open(target,'wb') as handle:handle.write(b'partial')
                raise OSError('写盘中断')
            return original(target,data)
        with patch.object(Path,'write_bytes',fail),patch('pi_runtime.PiRuntime',self.runtime()):
            with self.assertRaisesRegex(OSError,'写盘中断'):vision(self.png())
        self._assert_scaled_file_is_gone()

    def test_chmod_failure_still_removes_the_scaled_file(self):
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        original=Path.chmod
        def fail(target,mode):
            if target.name.startswith('vision-'):raise OSError('权限设置失败')
            return original(target,mode)
        with patch.object(Path,'chmod',fail),patch('pi_runtime.PiRuntime',self.runtime()):
            with self.assertRaisesRegex(OSError,'权限设置失败'):vision(self.png())
        self._assert_scaled_file_is_gone()

    def test_watcher_start_failure_still_removes_the_scaled_file(self):
        vision=ProfileVision(lambda:self.config(),None,Path(self.tmp.name))
        original=threading.Thread.start
        def fail(target):
            callback=getattr(target,'_target',None)
            if getattr(callback,'__name__','')=='watch':
                raise RuntimeError('监视线程启动失败')
            return original(target)
        with target_start(lambda worker: getattr(getattr(worker, '_target', None), '__name__', '') == 'watch', fail),patch('pi_runtime.PiRuntime',self.runtime()):
            with self.assertRaisesRegex(RuntimeError,'监视线程启动失败'):vision(self.png())
        self._assert_scaled_file_is_gone()
