import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assistant_memos import MemosTools, MAX_OUTPUT_BYTES


class MemosBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.workspace=Path(self.temp.name)
        self.tools=MemosTools(self.workspace,sensitive_values=('private-model-key',))

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_database_is_unavailable_not_an_empty_search(self):
        self.tools.db_path=self.workspace/'missing.db'
        self.assertFalse(self.tools.status()['available'])
        with self.assertRaisesRegex(ValueError,'未就绪'):
            self.tools.search('MemOS')

    def test_empty_success_is_distinct_from_provider_failure(self):
        with patch.object(self.tools,'_call',return_value={'results':[]}):
            result=self.tools.search('不存在的主题')
        self.assertEqual(result['count'],0)
        self.assertTrue(result['local_only'])
        self.assertFalse(result['semantic'])
        with patch.object(self.tools,'_call',side_effect=ValueError('MemOS Local 操作失败')):
            with self.assertRaisesRegex(ValueError,'失败'):self.tools.search('不存在的主题')

    def test_query_limits_secret_input_and_owner_scope_fail_before_disclosure(self):
        with patch.object(self.tools,'_call') as call:
            for query in ('','x'*501,'secret=abcdefghijk','private-model-key','\x00secret'):
                with self.subTest(query=query),self.assertRaises(ValueError):self.tools.search(query)
            for limit in (True,0,9,3.5):
                with self.subTest(limit=limit),self.assertRaises(ValueError):self.tools.search('MemOS',limit)
            call.assert_not_called()
        with patch.object(self.tools,'_call',return_value={'results':[{'chunk_id':'bad','text':'private','owner':'agent:other'}]}):
            with self.assertRaisesRegex(ValueError,'范围外'):self.tools.search('MemOS')

    def test_only_bounded_selected_fields_and_redacted_memory_reach_model(self):
        data={'results':[{'chunk_id':'chunk-1','text':'MemOS '+('x'*500)+' api_key=abcdefghijk private-model-key',
            'summary':'Bearer abcdefghijklmnop','owner':'agent:main','role':'user','score':.4,
            'created_at':10,'extra_secret':'do-not-return'}]}
        with patch.object(self.tools,'_call',return_value=data):result=self.tools.search('MemOS')
        serialized=json.dumps(result,ensure_ascii=False)
        for secret in ('abcdefghijk','private-model-key','abcdefghijklmnop','do-not-return','extra_secret'):
            self.assertNotIn(secret,serialized)
        self.assertIn('已隐藏凭据',serialized)
        self.assertLessEqual(len(result['results'][0]['text']),1200)

    def fake_node(self, body):
        script=self.workspace/'fake-node'
        script.write_text('#!/usr/bin/env python3\n'+body)
        script.chmod(0o700)
        self.tools.node=str(script)
        self.tools.db_path=self.workspace/'fake.db';self.tools.db_path.touch()
        self.tools.plugin_dir=self.workspace/'plugin'
        (self.tools.plugin_dir/'src/storage').mkdir(parents=True)
        (self.tools.plugin_dir/'src/storage/sqlite.ts').touch()

    def test_provider_exception_details_never_reach_status_or_model(self):
        self.fake_node('import json,sys\nprint(json.dumps({"ok":False,"error":"secret-token /private/path"}))\nprint("secret-token",file=sys.stderr)\n')
        with self.assertRaises(ValueError) as error:self.tools.search('MemOS')
        self.assertNotIn('secret-token',str(error.exception))
        self.assertNotIn('/private/path',json.dumps(self.tools.status()))

    def test_timeout_and_output_size_are_bounded(self):
        self.fake_node('import time\ntime.sleep(10)\n')
        self.tools.timeout=.08
        with self.assertRaisesRegex(ValueError,'超时'):self.tools.search('MemOS')
        self.tools.node=None
        # Reuse the existing fake plugin and database for an oversized response.
        script=self.workspace/'large-node'
        script.write_text('#!/usr/bin/env python3\nprint("x"*'+str(MAX_OUTPUT_BYTES+1)+')\n');script.chmod(0o700)
        self.tools.node=str(script);self.tools.timeout=2
        with self.assertRaisesRegex(ValueError,'超过限制'):self.tools.search('MemOS')

    def test_writes_need_verified_success_and_durable_request_binding(self):
        with patch.object(self.tools,'_call',return_value={'saved':False}):
            with self.assertRaisesRegex(ValueError,'未确认'):self.tools.write('本机记忆合成内容','request-0001')
        with patch.object(self.tools,'_call',return_value={'saved':True,'duplicate':False,'chunk_id':'safe-id'}) as call:
            result=self.tools.write('本机记忆合成内容','request-0001')
            self.assertTrue(result['saved'])
            self.assertTrue(result['local_only'])
            with self.assertRaisesRegex(ValueError,'不能更改'):self.tools.write('不同的记忆合成内容','request-0001')
            self.assertEqual(call.call_count,1)
        ledger=self.workspace/'.memos-write-requests.sqlite'
        self.assertNotIn('本机记忆合成内容',ledger.read_bytes().decode('utf8',errors='ignore'))


SCHEMA = '''
CREATE TABLE chunks (
 id TEXT PRIMARY KEY,session_key TEXT NOT NULL,turn_id TEXT NOT NULL,seq INTEGER NOT NULL,
 role TEXT NOT NULL,content TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'paragraph',summary TEXT NOT NULL DEFAULT '',
 task_id TEXT,content_hash TEXT,owner TEXT NOT NULL DEFAULT 'agent:main',
 dedup_status TEXT NOT NULL DEFAULT 'active',dedup_target TEXT,dedup_reason TEXT,
 created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(summary,content,content='chunks',content_rowid='rowid',tokenize='trigram');
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
 INSERT INTO chunks_fts(rowid,summary,content) VALUES(new.rowid,new.summary,new.content);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
 INSERT INTO chunks_fts(chunks_fts,rowid,summary,content) VALUES('delete',old.rowid,old.summary,old.content);
END;
CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
 INSERT INTO chunks_fts(chunks_fts,rowid,summary,content) VALUES('delete',old.rowid,old.summary,old.content);
 INSERT INTO chunks_fts(rowid,summary,content) VALUES(new.rowid,new.summary,new.content);
END;
'''


class MemosInstalledPluginTests(unittest.TestCase):
    """The installed plugin runs against a synthetic test DB, never the user's DB."""
    def setUp(self):
        self.plugin=Path.home()/'.openclaw/extensions/memos-local-openclaw-plugin'
        if not shutil.which('node') or not (self.plugin/'src/storage/sqlite.ts').is_file():
            self.skipTest('MemOS Local plugin is not installed on this machine')
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name)
        self.db=self.workspace/'synthetic.db'
        with sqlite3.connect(self.db) as connection:connection.executescript(SCHEMA)
        self.add_chunk('main-1','agent:main','合成测试：MemOS 本机配置与合同记忆。')
        self.add_chunk('public-1','public','合成测试：MemOS 公共记忆。')
        self.add_chunk('other-1','agent:sales','合成测试：MemOS 其他Agent私有记忆。')
        self.add_chunk('inactive-1','agent:main','合成测试：MemOS inactive。',active=False)
        self.tools=MemosTools(self.workspace,db_path=self.db,plugin_dir=self.plugin,sensitive_values=('private-model-key',))

    def add_chunk(self, identity, owner, text, active=True):
        with sqlite3.connect(self.db) as connection:
            connection.execute('INSERT INTO chunks (id,session_key,turn_id,seq,role,content,summary,content_hash,owner,dedup_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,'synthetic','turn',0,'user',text,text[:400],hashlib.sha256(text.encode()).hexdigest()[:16],owner,'active' if active else 'duplicate',10,10))

    def test_actual_plugin_methods_search_readonly_and_respect_owner_dedup(self):
        before=self.db.read_bytes()
        status=self.tools.status()
        self.assertTrue(status['available'],status['summary'])
        self.assertEqual(status['total_memories'],4)
        self.assertEqual(status['visible_memories'],2)
        result=self.tools.search('MemOS',8)
        self.assertEqual({x['chunk_id'] for x in result['results']},{'main-1','public-1'})
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(self.tools.search('随机zzzzzzzz未见词')['count'],0)

    def test_short_chinese_terms_match_without_vector_or_cloud(self):
        result=self.tools.search('合同',3)
        self.assertEqual(result['results'][0]['chunk_id'],'main-1')
        self.assertFalse(result['semantic'])
        self.assertTrue(result['local_only'])

    def test_plugin_write_transaction_fts_and_repeated_request_do_not_duplicate(self):
        text='仅用于合成测试的知识：工作台记忆能够在本机搜索。'
        first=self.tools.write(text,'request-unique-001','conversation-001')
        replay=self.tools.write(text,'request-unique-001','conversation-001')
        duplicate=self.tools.write(text,'request-unique-002','conversation-002')
        self.assertFalse(first['duplicate']);self.assertTrue(replay['duplicate']);self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['chunk_id'],duplicate['chunk_id'])
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM chunks WHERE owner='agent:article-studio'").fetchone()[0],1)
            self.assertEqual(connection.execute('SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?',('"工作台记忆"',)).fetchone()[0],1)
        self.assertIn(first['chunk_id'],[x['chunk_id'] for x in self.tools.search('工作台记忆')['results']])
        with self.assertRaisesRegex(ValueError,'不能更改'):
            self.tools.write('另一个合成记忆内容','request-unique-002','conversation-002')

    def test_redaction_precedes_excerpt_slicing(self):
        text='MemOS '+('x'*1160)+' api_key='+('z'*2000)+' private-model-key'
        self.add_chunk('secret-1','agent:main',text)
        result=self.tools.search('MemOS',8)
        serialized=json.dumps(result,ensure_ascii=False)
        self.assertNotIn('z'*12,serialized)
        self.assertNotIn('private-model-key',serialized)
        self.assertIn('已隐藏凭据',serialized)


if __name__=='__main__':unittest.main()
