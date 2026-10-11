import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isolation_patches import module_time
from publishing.local_jobs import LocalDeliveries

spec=importlib.util.spec_from_file_location('local_studio',Path(__file__).resolve().parents[1]/'server.py')
studio=importlib.util.module_from_spec(spec);spec.loader.exec_module(studio)

class LocalPublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=studio.Store(self.tmp.name)
        self.article=self.store.create({'title':'本机发布测试','type':'article'})
        self.service=LocalDeliveries(self.store)
    def tearDown(self):self.tmp.cleanup()
    def start(self):return self.service.start(self.article['slug'],{'version':'v0001','account':'@servasyy'})
    def test_double_click_and_restart_preserve_task(self):
        j=self.start();self.assertEqual(j['id'],self.start()['id'])
        self.assertEqual(j,LocalDeliveries(self.store).get(j['id']))
        bundle=self.service.bundle(j['id']);self.assertEqual(bundle['job']['version'],'v0001')
        self.assertEqual(bundle['title'],'本机发布测试')
    def test_wrong_account_and_rebinding_rejected(self):
        j=self.start()
        with self.assertRaises(ValueError):self.service.update(j['id'],{'status':'running','observed_account':'another'})
        self.service.update(j['id'],{'status':'running','observed_account':'servasyy','draft_url':'https://x.com/compose/articles/edit/123'})
        with self.assertRaises(ValueError):self.service.update(j['id'],{'status':'running','draft_url':'https://x.com/compose/articles/edit/456'})
    def test_switching_target_creates_an_independent_job_and_preserves_original_binding(self):
        original=self.start()
        self.service.update(original['id'],{'status':'blocked','draft_url':'https://x.com/compose/articles/edit/123',
            'error_code':'ACCOUNT_MISMATCH','message':'实际登录 servasyy_ai'})
        saved=self.service.get(original['id'])
        payload={'version':'v0001','account':' @Servasyy_AI '}
        other=self.service.start(self.article['slug'],payload)
        self.assertNotEqual(other['id'],original['id'])
        self.assertEqual(other['account'],'servasyy_ai')
        self.assertEqual(other['draft_url'],'')
        self.assertEqual(other['checkpoint'],{})
        self.assertEqual(self.service.get(original['id']),saved)
        self.assertEqual(self.service.start(self.article['slug'],payload)['id'],other['id'])
        self.assertEqual(self.start()['id'],original['id'])
    def test_confirmation_is_required_and_unknown_cannot_retry(self):
        j=self.start()
        with self.assertRaises(ValueError):self.service.update(j['id'],{'action':'confirm','account':'servasyy','version':'v0001','reviewed':True})
        self.service.update(j['id'],{'status':'review_ready','observed_account':'servasyy','draft_url':'https://x.com/compose/articles/edit/123','evidence':{'text_checked':True,'order_checked':True,'positions_checked':True,'cover_checked':True,'paragraphs_checked':True,'title':'本机发布测试','image_count':0}})
        with self.assertRaises(ValueError):self.service.update(j['id'],{'action':'confirm','account':'another','version':'v0001','reviewed':True})
        self.service.update(j['id'],{'action':'confirm','account':'servasyy','version':'v0001','reviewed':True})
        self.service.update(j['id'],{'action':'attempt'})
        self.service.update(j['id'],{'status':'unknown','message':'断开连接'})
        with self.assertRaises(ValueError):self.service.update(j['id'],{'action':'attempt'})
        with self.assertRaises(ValueError):self.service.update(j['id'],{'status':'running'})
    def test_success_requires_real_account_permalink(self):
        j=self.start()
        with self.assertRaises(ValueError):self.service.update(j['id'],{'status':'published','published_url':'https://x.com/servasyy/status/123'})
        self.service.update(j['id'],{'status':'review_ready','observed_account':'servasyy','draft_url':'https://x.com/compose/articles/edit/123','evidence':{'text_checked':True,'order_checked':True,'positions_checked':True,'cover_checked':True,'paragraphs_checked':True,'title':'本机发布测试','image_count':0}})
        self.service.update(j['id'],{'action':'confirm','account':'servasyy','version':'v0001','reviewed':True})
        self.service.update(j['id'],{'action':'attempt'})
        with self.assertRaises(ValueError):self.service.update(j['id'],{'status':'published','published_url':'https://x.com/other/status/123'})
        saved=self.service.update(j['id'],{'status':'published','published_url':'https://x.com/servasyy/status/123'})
        self.assertEqual(saved['status'],'published')
        record=self.store.delivery_records(self.article['slug'])[0]
        self.assertEqual(record['account'],'servasyy');self.assertEqual(record['kind'],'published_browser')
    def test_connection_expires_and_other_tabs_do_not_hide_connected_chrome(self):
        client='a'*36
        with module_time('publishing.local_jobs',monotonic=lambda:100):
            self.assertTrue(self.service.report_connection({'client':client,'connected':True})['connected'])
            status=self.service.report_connection({'client':'b'*36,'connected':False})
            self.assertTrue(status['connected']);self.assertFalse(status['account_verified'])
        with module_time('publishing.local_jobs',monotonic=lambda:136):
            self.assertFalse(self.service.connection_status()['connected'])

    def test_post_is_not_misreported_as_supported(self):
        post=self.store.create({'title':'推文','type':'post'})
        with self.assertRaises(ValueError):self.service.start(post['slug'],{'version':'v0001','account':'servasyy'})

    def test_explicit_new_draft_preserves_unavailable_original_and_deduplicates(self):
        j=self.start()
        old_url='https://x.com/compose/articles/edit/123/focus'
        self.service.update(j['id'],{'status':'blocked','draft_url':old_url,'error_code':'DRAFT_UNAVAILABLE','message':'原草稿未能加载'})
        with self.assertRaises(ValueError):
            self.service.update(j['id'],{'action':'new-draft'})
        new=self.service.update(j['id'],{'action':'new-draft','confirmed':True})
        old=self.service.get(j['id'])
        self.assertEqual(old['draft_url'],old_url)
        self.assertEqual(old['status'],'superseded')
        self.assertEqual(new['supersedes'],old['id'])
        self.assertEqual(new['draft_url'],'')
        self.assertEqual(new['checkpoint'],{})
        self.assertFalse(new['publish_attempted'])
        self.assertEqual(self.start()['id'],new['id'])
        with self.assertRaises(ValueError):self.service.update(old['id'],{'status':'running'})

    def test_account_failure_cannot_discard_draft_binding(self):
        j=self.start()
        self.service.update(j['id'],{'status':'blocked','error_code':'ACCOUNT_NOT_READY'})
        with self.assertRaises(ValueError):self.service.update(j['id'],{'action':'new-draft','confirmed':True})

    def test_unbound_blocked_task_can_adopt_manual_draft_without_new_job(self):
        j=self.start()
        self.service.update(j['id'],{'status':'blocked','message':'新建按钮不能识别'})
        bound=self.service.start(self.article['slug'],{'version':'v0001','account':'servasyy',
                    'draft_url':'https://x.com/compose/articles/edit/456/focus'})
        self.assertEqual(bound['id'],j['id'])
        self.assertEqual(bound['status'],'queued')
        self.assertEqual(bound['draft_url'],'https://x.com/compose/articles/edit/456/focus')
        self.assertEqual(len(self.service.listing(self.article['slug'],'v0001')),1)
        with self.assertRaises(ValueError):
            self.service.start(self.article['slug'],{'version':'v0001','account':'servasyy',
                        'draft_url':'https://x.com/compose/articles/edit/789'})

    def test_actual_upload_diagnostics_survive_service_restart(self):
        j=self.start();diagnostics={'phase':'text_check','body':{'expected_chars':10,'observed_chars':9},'uploaded_body_images':0}
        self.service.update(j['id'],{'status':'blocked','error_code':'DRAFT_CONTENT_MISMATCH','diagnostics':diagnostics})
        saved=LocalDeliveries(self.store).get(j['id'])
        self.assertEqual(saved['diagnostics'],diagnostics)
        self.assertEqual(saved['error_code'],'DRAFT_CONTENT_MISMATCH')

    def test_count_only_evidence_cannot_mark_draft_ready(self):
        j=self.start()
        with self.assertRaises(ValueError):
            self.service.update(j['id'],{'status':'review_ready','observed_account':'servasyy',
                'draft_url':'https://x.com/compose/articles/edit/123',
                'evidence':{'text_checked':True,'order_checked':True,'title':'本机发布测试','image_count':0}})
        self.assertEqual(self.service.get(j['id'])['status'],'queued')

    def test_invalid_manual_draft_url_is_not_silently_ignored_on_resume(self):
        self.start()
        with self.assertRaises(ValueError):
            self.service.start(self.article['slug'],{'version':'v0001','account':'servasyy',
                        'draft_url':'https://example.com/'})

    def test_unchecked_paragraph_structure_cannot_enable_publication(self):
        j=self.start()
        evidence={'text_checked':True,'order_checked':True,'positions_checked':True,
                  'cover_checked':True,'title':'本机发布测试','image_count':0}
        with self.assertRaises(ValueError):
            self.service.update(j['id'],{'status':'review_ready','observed_account':'servasyy',
                'draft_url':'https://x.com/compose/articles/edit/123','evidence':evidence})
        self.assertEqual(self.service.get(j['id'])['status'],'queued')
        # Old persisted ready records must also pass the newer confirmation gate.
        from publishing.core.common import write_json
        write_json(self.service.path(j['id']),dict(j,status='review_ready',
                   draft_url='https://x.com/compose/articles/edit/123',evidence=evidence))
        with self.assertRaises(ValueError):
            self.service.update(j['id'],{'action':'confirm','account':'servasyy',
                'version':'v0001','reviewed':True})

if __name__=='__main__':unittest.main()


class MediaAdaptationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=studio.Store(self.tmp.name)
        self.article=self.store.create({'title':'本机发布测试','type':'article'});self.service=LocalDeliveries(self.store)
        job=self.service.start(self.article['slug'],{'version':'v0001','account':'servasyy'})
        self.service.update(job['id'],{'status':'blocked','error_code':'MEDIA_LIMIT','checkpoint':{'native_limits':{'account':'servasyy','body_images':2,'message':'Your latest media has not been added as it exceeds the media limit per Article.'}}})
        import base64,io
        from PIL import Image
        blocks=[{'kind':'text','html':'<p><strong>开头</strong></p>'}]
        for i in range(5):
            if i==2:blocks.append({'kind':'text','html':'<h2>下一章节</h2>'})
            image=Image.new('RGB',(3,3),(i*40,100,200));buf=io.BytesIO();image.save(buf,'PNG')
            blocks.extend([{'kind':'image','data':base64.b64encode(buf.getvalue()).decode()},{'kind':'text','html':f'<p>段落{i}</p>'}])
        self.version=self.store.save(self.article['slug'],{'base_version':'v0001','title':'五张配图测试','blocks':blocks})['version']
        self.payload={'version':self.version,'account':'servasyy'}
    def tearDown(self):self.tmp.cleanup()
    def test_account_specific_preflight_blocks_oversized_source_before_upload(self):
        p=self.service.preflight(self.article['slug'],self.payload)
        self.assertTrue(p['needs_split']);self.assertEqual(p['source_images'],5)
        self.assertTrue(p['over_limit']);self.assertEqual(p['excess_images'],3)
        count=len(self.service.listing(self.article['slug'],self.version))
        with self.assertRaisesRegex(ValueError,'图片数量超限.*正文 5 张.*上限 2 张.*超出 3 张'):
            self.service.start(self.article['slug'],self.payload)
        self.assertEqual(len(self.service.listing(self.article['slug'],self.version)),count)
        other=self.service.preflight(self.article['slug'],{**self.payload,'account':'another'})
        self.assertIsNone(other['limit']);self.assertFalse(other['needs_split'])
        self.assertEqual(other['reference_limit']['account'],'servasyy')
        self.assertEqual(other['reference_limit']['body_images'],2)
    def test_limit_learned_during_upload_blocks_resume_bundle_and_new_draft(self):
        payload={**self.payload,'account':'another'}
        job=self.service.start(self.article['slug'],payload)
        self.service.update(job['id'],{'status':'blocked','error_code':'MEDIA_LIMIT',
            'checkpoint':{'native_limits':{'account':'another','body_images':2,
            'message':'Your latest media has not been added as it exceeds the media limit per Article.'}}})
        with self.assertRaisesRegex(ValueError,'图片数量超限'):self.service.bundle(job['id'])
        with self.assertRaisesRegex(ValueError,'图片数量超限'):self.service.start(self.article['slug'],payload)
        with self.assertRaisesRegex(ValueError,'图片数量超限'):
            self.service.update(job['id'],{'action':'new-draft','confirmed':True})
        self.assertEqual(self.service.get(job['id'])['status'],'blocked')
    def test_split_is_idempotent_preserves_all_text_images_and_original_version(self):
        original,_,_=self.store.info(self.article['slug'],self.version)
        plan=self.service.adapt(self.article['slug'],self.payload)
        self.assertEqual(plan,self.service.adapt(self.article['slug'],self.payload))
        self.assertEqual(len(plan['parts']),3)
        combined=[]
        for part in plan['parts']:
            manifest,_,_=self.store.info(part['slug'],part['version'])
            self.assertLessEqual(len(manifest['body_media']),2);combined.extend(manifest['blocks'])
        signature=lambda blocks:[(b['kind'],b.get('html'),b.get('sha256')) for b in blocks]
        self.assertEqual(signature(combined),signature(original['blocks']))
        self.assertEqual(self.store.meta(self.article['slug'])['head'],self.version)
        self.assertEqual(self.store.info(self.article['slug'],self.version)[0]['package_id'],original['package_id'])
