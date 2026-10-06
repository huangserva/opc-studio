import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image

spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parents[1] / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = studio.Store(self.root)
        self.folder = self.store.inbox / 'test-article'
        self.folder.mkdir()
        Image.new('RGB', (20, 10), 'red').save(self.folder / 'image.png')
        self.source = {'schema_version': 1, 'title': '测试标题', 'blocks': [
            {'type': 'paragraph', 'text': '原始段落'}, {'type': 'image', 'src': 'image.png'}]}
        self.write_source()
        self.store.scan(force=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self):
        (self.folder / 'article.json').write_text(json.dumps(self.source, ensure_ascii=False))

    def test_file_changes_snapshot_text_and_image_and_preserve_old_version(self):
        first, _, path = self.store.info('test-article')
        old_bytes = (path / first['body_media'][0]['file']).read_bytes()
        self.source['blocks'][0]['text'] = '修改后的段落'
        self.write_source()
        Image.new('RGB', (20, 10), 'blue').save(self.folder / 'image.png')
        self.store.scan(force=True)
        self.assertEqual(self.store.meta('test-article')['head'], 'v0002')
        self.assertEqual((path / first['body_media'][0]['file']).read_bytes(), old_bytes)
        diff = self.store.compare('test-article', 'v0001', 'v0002')
        self.assertTrue(any(b['kind']=='image' for c in diff['changes'] for b in c['after']))
        self.assertIn('修改后的段落', str(diff))
        self.store.scan(force=True)
        self.assertEqual(self.store.meta('test-article')['head'], 'v0002')

    def test_edit_conflict_restore_and_restart_persistence(self):
        result = self.store.save('test-article', {'base_version': 'v0001', 'title': '新标题', 'text_blocks': {'0': '<p>新内容</p>'}, 'summary': '编辑测试'})
        self.assertEqual(result['version'], 'v0002')
        with self.assertRaises(studio.Conflict):
            self.store.save('test-article', {'base_version': 'v0001'})
        restored = self.store.restore('test-article', {'base_version': 'v0002', 'version': 'v0001'})
        self.assertEqual(restored['version'], 'v0003')
        reopened = studio.Store(self.root)
        self.assertEqual(reopened.detail('test-article')['title'], '测试标题')
        self.assertEqual(len(reopened.detail('test-article')['versions']), 3)

    def test_broken_intake_does_not_replace_existing_article(self):
        (self.folder / 'article.json').write_text('{broken')
        self.store.scan(force=True)
        self.assertEqual(self.store.meta('test-article')['head'], 'v0001')
        self.assertTrue(self.store.intake['test-article']['error'])

    def test_create_write_images_and_handoff_preserves_versions(self):
        import base64
        created = self.store.create({'title': '全新图文'})
        slug = created['slug']
        self.assertEqual(created['blocks'], [])
        self.assertTrue((Path(created['intake_path']) / 'article.json').exists())
        self.assertNotEqual(self.store.create({'title': '全新图文'})['slug'], slug)
        data = base64.b64encode((self.folder / 'image.png').read_bytes()).decode()
        result = self.store.save(slug, {'base_version': 'v0001', 'title': '全新图文', 'blocks': [
            {'kind': 'text', 'html': '<h2>第一节</h2><p>从零写作</p>'},
            {'kind': 'image', 'data': data}, {'kind': 'text', 'html': '<p><br></p>'}]})
        self.assertEqual(result['version'], 'v0002')
        detail = self.store.detail(slug)
        self.assertEqual(detail['image_count'], 1)
        self.assertEqual(len(detail['blocks']), 2)
        self.assertEqual(self.store.detail(slug, 'v0001')['blocks'], [])
        self.store.scan(force=True)
        self.assertEqual(self.store.meta(slug)['head'], 'v0002')
        self.assertTrue(self.store.compare(slug, 'v0001', 'v0002')['changes'])
        with self.assertRaises(ValueError):
            self.store.save(slug, {'base_version': 'v0002', 'title': '坏图片', 'blocks': [{'kind':'image','data':'bad'}]})
        self.assertEqual(self.store.meta(slug)['head'], 'v0002')
        unchanged = self.store.save(slug, {'base_version': 'v0002', 'title': '全新图文', 'blocks': [{'index':0},{'index':1}]})
        self.assertTrue(unchanged['unchanged'])

    def test_collections_membership_new_article_and_restart(self):
        first = self.store.save_collection({'name': '技术实测', 'add': ['test-article']})
        second = self.store.save_collection({'name': 'AI 工具', 'add': ['test-article']})
        self.assertEqual(first['articles'], ['test-article'])
        self.assertEqual(self.store.meta('test-article')['head'], 'v0001')
        self.store.save_collection({'id': first['id'], 'name': '技术教程'})
        self.store.save_collection({'id': second['id'], 'remove': ['test-article']})
        created = self.store.create({'title': '合集里的新图文', 'collection': first['id']})
        restarted = studio.Store(self.root)
        records = restarted.listing()['collections']
        group = next(c for c in records if c['id'] == first['id'])
        self.assertEqual(group['name'], '技术教程')
        self.assertEqual(group['articles'], ['test-article', created['slug']])
        self.assertEqual(next(c for c in records if c['id'] == second['id'])['articles'], [])
        with self.assertRaises(ValueError):
            self.store.save_collection({'name': '技术教程'})
        with self.assertRaises(ValueError):
            self.store.create({'title': '错误合集', 'collection': 'missing'})
        with self.assertRaises(OSError):
            self.store.save_collection({'id': first['id'], 'add': ['missing-article']})
        self.assertEqual(next(c for c in self.store.collections() if c['id'] == first['id'])['articles'], group['articles'])

    def test_edit_html_is_sanitized(self):
        rich, text = studio.safe_html('<p onclick="x()">正文<script>alert(1)</script><a href="javascript:x()">链接</a></p>')
        self.assertNotIn('script', rich)
        self.assertNotIn('onclick', rich)
        self.assertNotIn('alert', text)
        self.assertEqual(text, '正文链接')


if __name__ == '__main__':
    unittest.main()
