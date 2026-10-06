import json
import tempfile
import unittest
from unittest.mock import patch
from server import Store
from generation.models import build
from generation.progress import event_progress
from generation.media_http import MediaApiUnknown
from generation.service import Generation


class GenerationProgressTests(unittest.TestCase):
    def test_actual_events_filter_other_tasks_and_clear_sampling_on_decode(self):
        graph = build({'model':'qi21','prompt':'山'}, 'test')[3]
        self.assertIsNone(event_progress({'type':'progress','data':{'prompt_id':'other','node':'8','value':2,'max':20}}, 'ours', graph, 'image'))
        value=event_progress({'type':'progress','data':{'prompt_id':'ours','node':'8','value':3,'max':20}},'ours',graph,'image')
        self.assertEqual(value['percent'],15)
        decode=event_progress({'type':'executing','data':{'node':'9'}},'ours',graph,'image',value)
        self.assertEqual(decode['phase'],'decoding')
        self.assertNotIn('percent',decode)
        invalid=event_progress({'type':'progress','data':{'node':'8','value':50,'max':20}},'ours',graph,'image')
        self.assertNotIn('percent',invalid)

    def test_progress_state_and_video_do_not_claim_sampling_percent(self):
        graph=build({'model':'h3','prompt':'小猫走路'}, 'test')[3]
        event={'type':'progress_state','data':{'prompt_id':'ours','nodes':{'5':{'state':'running','value':4,'max':8}}}}
        value=event_progress(event,'ours',graph,'video')
        self.assertEqual(value['phase'],'video');self.assertNotIn('percent',value)

    def test_polling_persists_confirmation_and_recovery_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            g=Generation(Store(tmp))
            with patch.object(Generation, '_spawn', lambda _s, *a: None):
                job=g.start({'model':'qi21','prompt':'图'})
            g.update(job['id'], api_job_id='api0001', submit_state='confirmed',
                     transport='https', status='running')
            class Client:
                def __init__(self): self.calls=[]
                def submit(self, *a, **k): raise AssertionError('恢复轮询不得重新提交生成')
                def job(self, api_job_id):
                    self.calls.append(api_job_id)
                    if len(self.calls)==1: raise MediaApiUnknown('HTTPS interrupted')
                    if len(self.calls)==2: return {'id':api_job_id,'status':'running'}
                    return {'id':api_job_id,'status':'failed',
                            'error':'execution_error: test failure'}
            client=Client()
            with patch.object(Generation,'media',lambda _s: client), \
                 patch.object(Generation,'_spawn_observer',lambda *_: None), \
                 patch('generation.service.time.sleep'):
                g.run(job['id'],None)
            saved=json.loads((g.folder/job['id']/'job.json').read_text())
            self.assertEqual(client.calls,['api0001']*3)
            self.assertEqual(saved['status'],'error');self.assertIsNotNone(saved['backend_checked_at'])
            self.assertIsNotNone(saved['finished_at']);self.assertIsNone(saved['connection_warning'])
            self.assertIn('test failure',saved['error'])


if __name__=='__main__':unittest.main()
