import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
import publish_review as P

class Fix1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='publish-fix1-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();(self.root/'weekly').mkdir();(self.root/'posts').mkdir()
        self.reader=P.GrokReports(self.root,datetime(2026,11,4,12,tzinfo=P.BEIJING));P._REPORT_CACHE.clear()
    def weekly(self,md=None,summary=None,**extra):
        (self.root/'weekly/2026-W44.json').write_text(json.dumps({'summary':summary,**extra}))
        if md is not None:(self.root/'weekly/2026-W44.md').write_text(md)
        return self.reader.listing()['weekly']
    def test_section_preserves_exact_text_and_subheadings(self):
        section='## 5. 本周产出复盘\n\n原话 **重点**。\n### 子节\n- 一\n- 二\n\n'
        self.assertEqual(self.weekly('# 周报\n\n'+section+'## 6. 其他\n不要显示')['markdown'],section)
    def test_headings_in_fence_not_section_or_boundary(self):
        section='## 本周产出复盘\n```md\n## 假边界\n```\n留下\n'
        self.assertEqual(P.weekly_section('```\n## 本周产出复盘\n```\n'+section+'## 下节\n'),section)
    def test_no_section_falls_back_readable_summary_not_json_keys(self):
        w=self.weekly('## 其他\n不存在\n',{'posts_count':27,'id':'2117636143936426115','conclusion':['Grok原句。','Keep this sentence.'],'handle':'sudoingX'})
        self.assertEqual(w['markdown'],'');self.assertEqual(w['summary_text'],['Grok原句。','Keep this sentence.'])
    def test_missing_markdown_uses_summary(self):
        self.assertEqual(self.weekly(summary='本周可读结论。')['summary_text'],['本周可读结论。'])
    def test_missing_section_and_readable_summary_empty(self):
        w=self.weekly('## 其他\n',{'posts_count':27,'id':'2117636143936426115'},output_review={'posts_count':27})
        self.assertEqual(w['markdown'],'');self.assertEqual(w['summary_text'],[])
    def test_html_and_javascript_are_preserved_not_silently_rewritten(self):
        text='## 本周产出复盘\n<script>window.bad=1</script>\n[危险](javascript:alert)\n'
        self.assertEqual(self.weekly(text)['markdown'],text)
    def test_malformed_table_kept_for_safe_render(self):
        text='## 本周产出复盘\n| 一 | 二 |\n| -- | broken |\n| 三 |\n'
        self.assertEqual(self.weekly(text)['markdown'],text)
    def test_markdown_readonly_cached_and_mtime_invalidates(self):
        self.weekly('## 本周产出复盘\n原句\n');P._REPORT_CACHE.clear();original=io.open;opened=[]
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        def guard(path,mode='r',*a,**kw):
            self.assertEqual(mode,'rb');self.assertTrue(Path(path).resolve().is_relative_to(self.root));opened.append(str(path));return original(path,mode,*a,**kw)
        with patch('io.open',side_effect=guard):self.reader.listing();self.reader.listing()
        self.assertEqual(opened.count(str(self.root/'weekly/2026-W44.md')),1)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        (self.root/'weekly/2026-W44.md').write_text('## 本周产出复盘\n新原句。\n')
        self.assertIn('新原句。',self.reader.listing()['weekly']['markdown'])
    def test_markdown_oversize_not_opened(self):
        self.weekly();md=self.root/'weekly/2026-W44.md'
        with md.open('wb') as f:f.truncate(P.MAX_REPORT_BYTES+1)
        original=io.open
        def guard(p,*a,**kw):
            self.assertNotEqual(Path(p),md);return original(p,*a,**kw)
        with patch('io.open',side_effect=guard):w=self.reader.listing()['weekly']
        self.assertTrue(any('文件过大' in s for s in w['warnings']))
    def test_markdown_symlink_and_traversal_rejected(self):
        target=self.root/'else.md';target.write_text('secret');self.weekly(summary='回退原话');(self.root/'weekly/2026-W44.md').symlink_to(target)
        self.assertEqual(self.reader._read('weekly/2026-W44.md')[1],'拒绝符号链接')
        for name in ('../else.md','weekly/../../else.md','posts/2026-11-04.md','weekly/2026-W44.md/../else.md'):
            self.assertEqual(self.reader._read(name)[1],'文件名无效')
    def test_bad_utf8_markdown_isolated(self):
        self.weekly(summary=['仍可读']);(self.root/'weekly/2026-W44.md').write_bytes(b'\xff');w=self.reader.listing()['weekly'];self.assertEqual(w['summary_text'],['仍可读']);self.assertTrue(w['warnings'])
    def test_latest_type_and_history_chronological(self):
        def post(kind,capture):return {'id':'2117636143936426115','type':kind,'captured_at':capture,'metrics':{},'created_at':'2026-11-03T15:12:55Z'}
        (self.root/'posts/2026-11-04.json').write_text(json.dumps({'snapshots':[{'posts':[post('主帖','2026-11-04 09:19 Asia/Shanghai'),post('长文','2026-11-04T08:00:00+08:00'),post('long','2026-11-04T08:00:00+08:00')]}]}))
        row=self.reader.listing()['posts'][0];self.assertEqual(row['type'],'主帖');self.assertEqual(row['type_history'],[{'type':'长文','captured_at':'2026-11-04T00:00:00+00:00'}])
    def test_unchanged_type_no_history(self):
        (self.root/'posts/2026-11-04.json').write_text(json.dumps({'snapshots':[{'posts':[{'id':'1','type':'主帖'}]}]}));self.assertEqual(self.reader.listing()['posts'][0]['type_history'],[])

if __name__=='__main__':unittest.main()
