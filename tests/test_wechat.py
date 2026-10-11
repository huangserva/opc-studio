"""公众号输出必须保留固定版本、图文顺序，并明确区分本地导出与发布。"""
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image
from server import Store
import publishing.platforms.wechat as wechat
from publishing.platforms.wechat import InlineHTML

class WeChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def article(self):
        article = self.store.create({'title':'原题','type':'article'})
        image = io.BytesIO()
        Image.new('RGB',(12,12),'blue').save(image,'PNG')
        self.store.save(article['slug'], {'base_version':'v0001','title':'原题',
            'blocks':[{'kind':'text','html':'<h2>第一节</h2><p>之前<strong>重点</strong></p>'},
                      {'kind':'image','data':base64.b64encode(image.getvalue()).decode()},
                      {'kind':'text','html':'<blockquote>之后</blockquote>'}],
            'studio':{'type':'article','platforms':{'wechat':{'title':'公众号题','caption':'独立摘要','account':'测试号'}}}})
        return article['slug']
    def test_export_keeps_revision_order_title_and_inline_images(self):
        slug = self.article()
        self.store.save(slug, {'base_version':'v0002','title':'新题','blocks':[{'kind':'text','html':'<p>新正文</p>'}]})
        before = self.store.package(slug,'v0002').joinpath('manifest.json').read_bytes()
        result = self.store.export_delivery(slug, {'platform':'wechat','version':'v0002'})
        with zipfile.ZipFile(result['path']) as archive:
            output = archive.read('wechat.html').decode()
            self.assertIn('公众号题',output)
            self.assertLess(output.index('之前'),output.index('data:image/png;base64,'))
            self.assertLess(output.index('data:image/png;base64,'),output.index('之后'))
            self.assertIn('color:#d4237a',output)
            self.assertNotIn('新正文',output)
            self.assertNotIn('独立摘要',output)
            self.assertIn('独立摘要',archive.read('发布文案.txt').decode())
            self.assertIn('未创建在线草稿',archive.read('公众号使用说明.txt').decode())
            self.assertTrue(any(name.startswith('images/') for name in archive.namelist()))
        self.assertEqual(before,self.store.package(slug,'v0002').joinpath('manifest.json').read_bytes())
        plan = self.store.publication_plan(slug, {'platform':'wechat','version':'v0002'})
        self.assertEqual(plan['status'],'export_ready')
        self.assertFalse(plan['published'])
        self.assertIn('/api/wechat-preview/',plan['preview_url'])
    def test_preview_urls_and_delivery_domain(self):
        slug = self.article()
        output = self.store.wechat_preview(slug,'v0002')
        self.assertIn('/assets/'+slug+'/v0002/images/',output)
        self.assertNotIn('data:image',output)
        self.store.record_delivery(slug, {'platform':'wechat','version':'v0002','url':'https://mp.weixin.qq.com/s/test'})
        with self.assertRaises(ValueError):
            self.store.record_delivery(slug, {'platform':'wechat','version':'v0002','url':'https://x.com/test'})
        with self.assertRaises(ValueError):
            self.store.wechat_preview(slug,None)
    def test_video_does_not_silently_become_article(self):
        article = self.store.create({'title':'视频','type':'video'})
        plan = self.store.publication_plan(article['slug'], {'platform':'wechat','version':'v0001'})
        self.assertEqual(plan['stage'],'unsupported')
        self.assertEqual(plan['status'],'blocked')
        with self.assertRaises(ValueError):
            self.store.wechat_preview(article['slug'],'v0001')
    def test_formatter_strips_executable_html(self):
        parser = InlineHTML()
        parser.feed('<p onclick="bad()">正文<script>alert(1)</script><a href="javascript:bad()">链接</a></p>')
        output = ''.join(parser.output)
        for word in ('onclick','script','alert','javascript'):
            self.assertNotIn(word,output)
        self.assertIn('正文',output)

class WeChatSkillPathTests(unittest.TestCase):
    """排版 skill 路径必须可移植：环境覆盖 > 第一个存在的候选 > 第一个候选。"""

    def make_home(self, home, candidates):
        for candidate in candidates:
            path = home / candidate
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# formatter')

    def test_environment_variable_overrides_every_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.make_home(home, wechat.SKILL_CANDIDATES)
            with patch.dict(os.environ, {'ARTICLE_STUDIO_WECHAT_SKILL': str(home / 'custom' / 'SKILL.md')}):
                self.assertEqual(wechat.resolve_skill(os.environ, home), str(home / 'custom' / 'SKILL.md'))

    def test_only_downloads_candidate_selects_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            downloads = wechat.SKILL_CANDIDATES[1]
            self.make_home(home, [downloads])
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(wechat.resolve_skill(os.environ, home), str(home / downloads))

    def test_first_existing_candidate_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.make_home(home, wechat.SKILL_CANDIDATES)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(wechat.resolve_skill(os.environ, home), str(home / wechat.SKILL_CANDIDATES[0]))

    def test_missing_candidates_fall_back_to_first_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.dict(os.environ, {}, clear=True):
                resolved = wechat.resolve_skill(os.environ, home)
            self.assertEqual(resolved, str(home / wechat.SKILL_CANDIDATES[0]))
            self.assertFalse(Path(resolved).is_file())

    def test_module_skill_is_derived_from_the_resolver(self):
        self.assertEqual(wechat.SKILL, wechat.resolve_skill(os.environ, Path.home()))
        self.assertTrue(Path(wechat.SKILL).is_absolute())

    def test_plan_note_documents_the_environment_override(self):
        plan = wechat.prepare({'studio': {'type': 'article'}, 'slug': 'a', 'version': 'v0001',
                                    'package': Path('.'), 'manifest': {}, 'target': {}}, {})
        self.assertEqual(plan['skill'], wechat.SKILL)
        self.assertTrue(any('ARTICLE_STUDIO_WECHAT_SKILL' in note for note in plan['notes']))


if __name__ == '__main__':
    unittest.main()
