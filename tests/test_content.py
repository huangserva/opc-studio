import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import zipfile
from PIL import Image

spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parents[1] / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)

# Container signature fixture; actual codec playback is checked with a generated MP4 in the browser.
VIDEO = b'\x00\x00\x00\x20ftypisom' + b'\x00' * 256

class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = studio.Store(self.temp.name)
        self.item = self.store.create({'title': '独立视频', 'type': 'video'})
        self.slug = self.item['slug']

    def tearDown(self):
        self.temp.cleanup()

    def save_video(self):
        upload = self.store.accept_upload(self.slug, 'demo.mp4', io.BytesIO(VIDEO), len(VIDEO))
        raw = copy.deepcopy(self.item['studio'])
        raw.update(video=upload, script='第一版脚本', subtitles='1\n00:00:00,000 --> 00:00:02,000\n测试字幕')
        raw['platforms'] = {'douyin': {'title':'抖音专用标题', 'caption':'第一版文案', 'topics':'#测试', 'account':'演示账号', 'status':'ready'}}
        result = self.store.save(self.slug, {'base_version':'v0001','title':'独立视频','blocks':[],'studio':raw})
        self.assertEqual(result['version'], 'v0002')
        return self.store.detail(self.slug)

    def test_video_versions_restore_and_export_pin_exact_revision(self):
        detail = self.save_video()
        old = self.store.package(self.slug, 'v0002')
        raw = copy.deepcopy(detail['studio'])
        raw['platforms']['douyin']['caption'] = '第二版文案'
        self.store.save(self.slug, {'base_version':'v0002','title':'独立视频','blocks':[],'studio':raw})
        self.assertEqual(self.store.detail(self.slug, 'v0002')['studio']['platforms']['douyin']['caption'], '第一版文案')
        self.assertTrue(self.store.compare(self.slug,'v0002','v0003')['studio_changed'])
        result = self.store.export_delivery(self.slug, {'version':'v0002','platform':'douyin'})
        with zipfile.ZipFile(result['path']) as z:
            payload = json.loads(z.read('publish.json'))
            self.assertEqual(payload['caption'], '第一版文案')
            self.assertEqual(z.read(payload['video']['file']), VIDEO)
            self.assertIn('subtitles.srt', z.namelist())
        record = self.store.record_delivery(self.slug, {'version':'v0002','platform':'douyin','url':'https://www.douyin.com/video/123'})
        self.assertEqual(record['version'], 'v0002')
        self.assertEqual(record['kind'], 'published_manual')
        restored = self.store.restore(self.slug, {'base_version':'v0003','version':'v0002'})
        self.assertEqual(restored['version'], 'v0004')
        self.assertEqual(self.store.detail(self.slug)['studio']['platforms']['douyin']['caption'], '第一版文案')
        self.assertEqual(len(self.store.delivery_records(self.slug)),2)
        self.assertEqual((old/detail['studio']['video']['file']).read_bytes(),VIDEO)

    def test_upload_ownership_invalid_media_and_conflicts(self):
        with self.assertRaises(ValueError):
            self.store.accept_upload(self.slug,'bad.mp4',io.BytesIO(b'bad'),3)
        upload = self.store.accept_upload(self.slug,'demo.mp4',io.BytesIO(VIDEO),len(VIDEO))
        other = self.store.create({'title':'另一作品','type':'video'})
        raw = copy.deepcopy(other['studio']);raw['video']=upload
        with self.assertRaises(ValueError):
            self.store.save(other['slug'], {'base_version':'v0001','title':'另一作品','studio':raw})
        self.assertEqual(self.store.meta(other['slug'])['head'],'v0001')
        with self.assertRaises(ValueError):
            self.store.export_delivery(self.slug,{'version':'v0001','platform':'x'})
        with self.assertRaises(ValueError):
            self.store.record_delivery(self.slug,{'version':'v0001','platform':'douyin','url':'https://evil.example/test'})

    def test_four_types_and_native_video_intake(self):
        for kind in ('article','post','gallery','video'):
            self.assertEqual(self.store.create({'title':kind,'type':kind})['studio']['type'],kind)
        folder=self.store.inbox/'native-video';(folder/'assets').mkdir(parents=True)
        (folder/'assets/clip.mp4').write_bytes(VIDEO)
        raw={'schema_version':1,'title':'直接投递的视频','blocks':[], 'studio':{'type':'video','video':{'src':'assets/clip.mp4'},'script':'原始脚本'}}
        (folder/'content.json').write_text(json.dumps(raw))
        self.store.scan(force=True)
        self.assertEqual(self.store.detail('native-video')['studio']['script'],'原始脚本')
        raw['studio']['script']='修改脚本';(folder/'content.json').write_text(json.dumps(raw))
        self.store.scan(force=True)
        self.assertEqual(self.store.meta('native-video')['head'],'v0002')
        raw['studio']['video']['src']='../outside.mp4';(folder/'content.json').write_text(json.dumps(raw))
        self.store.scan(force=True)
        self.assertTrue(self.store.intake['native-video']['error'])
        self.assertEqual(self.store.meta('native-video')['head'],'v0002')

    def test_platform_video_overrides_and_poster_export(self):
        detail=self.save_video()
        variant=VIDEO+b'variant'; upload=self.store.accept_upload(self.slug,'variant.mp4',io.BytesIO(variant),len(variant))
        buf=io.BytesIO();Image.new('RGB',(20,10),'blue').save(buf,format='PNG');data=buf.getvalue()
        poster=self.store.accept_upload(self.slug,'cover.png',io.BytesIO(data),len(data))
        raw=detail['studio'];raw['platforms']['xiaohongshu']={'title':'小红书版','video':upload,'poster':poster}
        self.store.save(self.slug,{'base_version':'v0002','title':'独立视频','studio':raw})
        package=self.store.export_delivery(self.slug,{'version':'v0003','platform':'xiaohongshu'})
        with zipfile.ZipFile(package['path']) as z:
            published=json.loads(z.read('publish.json'))
            self.assertEqual(z.read(published['video']['file']),variant)
            self.assertEqual(z.read(published['poster']['file']),data)
        self.assertEqual(self.store.detail(self.slug)['studio']['video']['sha256'], detail['studio']['video']['sha256'])

    def test_existing_source_fingerprint_does_not_override_editor_after_restart(self):
        source=self.store.inbox/self.slug/'article.json'
        self.store.save(self.slug,{'base_version':'v0001','title':'页面中改过的标题'})
        expected=self.store.meta(self.slug)['head']
        reopened=studio.Store(self.temp.name);reopened.scan(force=True)
        self.assertEqual(reopened.meta(self.slug)['head'],expected)
        self.assertEqual(reopened.detail(self.slug)['title'],'页面中改过的标题')
        self.assertEqual(json.loads(source.read_text())['title'],'独立视频')

    def test_http_media_range_and_streaming_upload(self):
        server=studio.ThreadingHTTPServer(('127.0.0.1',0),studio.Handler);server.store=self.store
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            req=urllib.request.Request(base+'/api/upload/'+self.slug+'?name=clip.mp4',data=VIDEO,headers={'Origin':base,'X-Article-Studio':'1','Content-Type':'application/octet-stream'})
            with urllib.request.urlopen(req) as r: upload=json.load(r)
            req=urllib.request.Request(base+upload['preview_url'],headers={'Range':'bytes=4-11'})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status,206);self.assertEqual(r.read(),VIDEO[4:12])
                self.assertEqual(r.headers['Content-Range'],f'bytes 4-11/{len(VIDEO)}')
            with urllib.request.urlopen(urllib.request.Request(base+upload['preview_url'],method='HEAD')) as r:
                self.assertEqual(int(r.headers['Content-Length']),len(VIDEO));self.assertEqual(r.read(),b'')
            with self.assertRaises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(urllib.request.Request(base+upload['preview_url'],headers={'Range':'bytes=9999-'}))
            self.assertEqual(err.exception.code,416)
            err.exception.close()
        finally:
            server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
