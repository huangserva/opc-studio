import tempfile
import unittest
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from server import Store, Handler
from knowledge_store import Knowledge
from assistant_knowledge import KnowledgeTools


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.k = Knowledge(self.store, sources=[{'id':'articles','kind':'articles','name':'合成作品'}])
        self.store._knowledge = self.k

    def tearDown(self):
        self.k.capture.close()
        self.temp.cleanup()

    def ref(self, **kw):
        return self.k.add_reference(dict(title='合成外部文章',url='https://x.com/test_author/status/123',**kw))

    def test_link_only_separates_readers_comment_from_original_and_not_a_work(self):
        d=self.ref(reader_note='这是我的评论，不能当原文',discovered_via='wechat_share',discovery_detail='合成群中的转发')
        self.assertEqual(d['body'],'')
        self.assertEqual(d['kind'],'external')
        self.assertEqual(d['provenance']['ownership'],'third_party')
        self.assertEqual(d['provenance']['author_handle'],'test_author')
        self.assertEqual(d['provenance']['published_on'],'')
        self.assertEqual(d['provenance']['discovered_via'],'微信转发')
        self.assertEqual(self.k.search('合成',kind='articles')['total'],0)
        self.assertEqual(self.k.search('合成',kind='external')['total'],1)
        self.assertEqual(list(self.store.library.iterdir()),[])

    def test_duplicate_tracking_and_twitter_links_keep_original_snapshot_and_comment(self):
        d=self.ref(reader_note='首次收藏理由',body='合成原文片段')
        again=self.k.add_reference({'title':'不能覆盖原文','url':'https://twitter.com/TEST_AUTHOR/status/123/photo/1?s=20','body':'新片段','reader_note':'新评论'})
        self.assertEqual(again['id'],d['id']);self.assertTrue(again['duplicate'])
        self.assertEqual(again['body'],'合成原文片段');self.assertEqual(again['provenance']['reader_note'],'首次收藏理由')
        self.k.sync();self.assertEqual(self.k.document(d['id'])['body'],'合成原文片段')

    def test_excerpt_and_pinned_dossier_draft_keep_attribution_and_not_readers_comment(self):
        d=self.ref(author='合成作者',body='😀这是合成原文片段。',reader_note='私人的点评',published_on='2026-07-02')
        dossier=self.k.save_dossier({'title':'合成资料夹','items':[{'id':d['id'],'hash':d['hash'],'start':1,'end':7}]})
        self.assertEqual(dossier['items'][0]['provenance']['author'],'合成作者')
        with patch.object(self.k,'start_sync'):
            result=self.k.draft({'id':dossier['id']})
        article=self.store.detail(result['slug'])
        rendered=str(article)
        self.assertIn('他人作品',rendered);self.assertIn('合成作者',rendered);self.assertIn('@test_author',rendered)
        self.assertNotIn('私人的点评',rendered)

    def test_pi_retrieval_preserves_external_author_and_coverage(self):
        d=self.ref(author='合成作者',body='合成引用正文',reader_note='评论'*2000)
        tools=KnowledgeTools(self.store)
        r=tools.execute('read_knowledge',{'id':d['id']},{})
        self.assertEqual(r['provenance']['ownership'],'third_party');self.assertEqual(r['provenance']['author'],'合成作者')
        self.assertIn('未验证为全文',r['provenance']['content_scope'])
        self.assertEqual(r['provenance']['sourceUrl'],d['url'])

    def test_invalid_input_is_not_written(self):
        bad=[{'url':'javascript:bad()'},{'url':'https://user:pass@example.test/a'}, {'url':'https://example.test/a\n'},
             {'published_on':'2026-02-31'}, {'discovered_via':'auto_fetched'}, {'ownership':'mine'}, {'body':['wrong']}]
        for delta in bad:
            with self.subTest(delta=delta),self.assertRaises(ValueError):
                self.k.add_reference({'title':'合成','url':'https://example.test/a',**delta})
        self.assertEqual(self.k.search(kind='external')['total'],0)

    def test_http_origin_guard_and_repeat_submit_do_not_overwrite_original(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def post(data,origin=base):
            req=Request(base+'/api/knowledge/reference',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Article-Studio':'1','Origin':origin})
            with urlopen(req,timeout=5) as r:return json.load(r)
        try:
            with self.assertRaises(HTTPError) as caught:post({'title':'合成','url':'https://example.test/a'},'https://example.test')
            self.assertEqual(caught.exception.code,403);caught.exception.close()
            d=post({'title':'合成外部文章','url':'https://example.test/a','reader_note':'原评论'})
            again=post({'title':'重试','url':'https://example.test/a','reader_note':'不得覆盖'})
            self.assertEqual(d['id'],again['id']);self.assertEqual(again['provenance']['reader_note'],'原评论')
            with self.assertRaises(HTTPError) as bad:post({'title':'未知','url':'javascript:bad()'})
            self.assertEqual(bad.exception.code,400);bad.exception.close()
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
