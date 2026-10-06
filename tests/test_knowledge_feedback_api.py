"""HTTP evidence chains with synthetic MIME, works and publication metrics only.

Gmail input follows the connected batch_read_email MCP schema (snake_case),
not Gmail REST's distinct camelCase resource. All stores are temporary and all
HTTP requests are to the fixture's loopback server; no connector/model is run.
"""
import base64
import copy
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from tests import test_events_api as event_fixture


class KnowledgeFeedbackApiTests(unittest.TestCase):
    # Reuse the existing server/temporary Store fixture, including its synthetic
    # seed note, instead of constructing any live Assistant or mail connection.
    setUp = event_fixture.EventApiTests.setUp
    tearDown = event_fixture.EventApiTests.tearDown

    def request(self, path, payload=None, origin=True, expected_status=None):
        headers={'Content-Type':'application/json','X-Article-Studio':'1'}
        if origin is not False:
            headers['Origin']=self.base if origin is True else origin
        req=Request(self.base+path,
                    data=json.dumps(payload,ensure_ascii=False).encode('utf-8') if payload is not None else None,
                    headers=headers)
        with urlopen(req,timeout=5) as response:
            if expected_status is not None:self.assertEqual(response.status,expected_status)
            return json.load(response)

    def reject(self, path, payload, code, message, origin=True):
        with self.assertRaises(HTTPError) as raised:
            self.request(path,payload,origin=origin)
        self.assertEqual(raised.exception.code,code)
        with raised.exception as response:error=json.load(response)['error']
        self.assertIn(message,error)

    @staticmethod
    def gmail_payload():
        """Parse a genuine multipart RFC 5322 message, then map MCP fields.

        Text bytes retain their MIME charset and transport CRLF. Only the field
        spelling is the connector contract; decoder behavior is exercised over
        HTTP rather than replacing the MIME cache with a mock.
        """
        plain='合成项目会议：请确认周三十点。\n\n> 原邮件：周二十点；这是合成测试。\n'
        message=EmailMessage(policy=policy.SMTP)
        message['Subject']='合成项目会议安排'
        message['From']='Synthetic Sender <sender@example.test>'
        message['To']='synthetic.user@example.test'
        message['Date']='Sat, 3 Oct 2026 16:00:00 +0800'
        message['Message-ID']='<synthetic-aa11@example.test>'
        message.set_content(plain,charset='gb18030')
        message.add_alternative('<p>重复 HTML 版本，不应代替纯文本</p>',subtype='html')
        message.add_attachment('合成附件内容，不可作为邮件正文',filename='synthetic.txt')
        raw=message.as_bytes()
        parsed=BytesParser(policy=policy.default).parsebytes(raw)

        def part(node, identity=''):
            value={'part_id':identity,'mime_type':node.get_content_type(),
                   'filename':node.get_filename() or '',
                   'headers':[{'name':name,'value':content} for name,content in node.raw_items()],
                   'read_attachment_supported':None}
            if node.is_multipart():
                value['body']={'size':0}
                value['parts']=[part(child,str(index) if not identity else identity+'.'+str(index))
                                for index,child in enumerate(node.iter_parts())]
            else:
                decoded=node.get_payload(decode=True)
                value['body']={'size':len(decoded),
                    'base64_url_content':base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=')}
                if node.get_filename():value['body']['attachment_id']='synthetic-attachment'
                value['parts']=None
            return value

        payload={'account':'synthetic.user@example.test','query':'subject:合成 newer_than:7d',
                 'has_more':True,'captured_at':'2026-10-04T12:00:00+08:00',
                 'messages':[{'id':'aa11','thread_id':'bb22','label_ids':['INBOX','Label_3'],
                     'history_id':'1234','internal_date':'1791014400000','size_estimate':len(raw),
                     'snippet':'合成预览不能当作完整正文','classification_label_values':None,
                     'payload':part(parsed)}]}
        return payload,plain

    def published_work(self):
        created=self.request('/api/create',{'title':'合成发布第一版','type':'post'},expected_status=201)
        slug=created['slug'];version=created['head']
        delivery=self.request('/api/record/'+slug,{'version':version,'platform':'x',
            'url':'https://x.com/synthetic/status/123','note':'合成发布登记，没有执行实际发布'},expected_status=200)
        self.assertEqual(delivery['kind'],'published_manual')
        newer=self.request('/api/save/'+slug,{'base_version':version,'title':'合成编辑第二版'},expected_status=200)
        self.assertNotEqual(newer['version'],version)
        observed=datetime.now(timezone.utc)-timedelta(hours=1)
        payload={'slug':slug,'delivery_id':delivery['id'],'platform':'x','version':version,
            'collected_at':observed.isoformat(),
            'range':{'kind':'interval','start':(observed-timedelta(days=1)).isoformat(),
                     'end':observed.isoformat(),'label':'合成平台原始曝光、互动次数，区间口径'},
            'metrics':{'impressions':1200,'likes':0,'engagement_rate':'1.25%'},
            'provenance':{'type':'manual','url':'https://analytics.x.com/content',
                          'note':'仅为合成 HTTP 测试，不是真实账号统计'}}
        return payload,newer['version']

    def test_standard_connector_mime_import_search_event_keeps_exact_body_and_provenance(self):
        payload,plain=self.gmail_payload();original=copy.deepcopy(payload)
        imported=self.request('/api/knowledge/gmail/import',payload,expected_status=201)
        repeated=self.request('/api/knowledge/gmail/import',payload,expected_status=201)
        self.assertEqual(imported['source'],repeated['source'])
        self.assertEqual(repeated['cache']['count'],1)
        source=next(item for item in repeated['sources'] if item['id']==repeated['source'])
        self.assertEqual(source['status'],'partial')
        self.assertFalse(repeated['syncing'])
        found=self.request('/api/knowledge/search?'+urlencode({'q':'合成项目会议','kind':'mail'}))
        self.assertEqual(found['total'],1)
        doc=self.request('/api/knowledge/document/'+found['results'][0]['id'])
        self.assertEqual(doc['title'],'合成项目会议安排')
        self.assertEqual(doc['body'],plain)
        self.assertNotIn('重复 HTML',doc['body'])
        self.assertNotIn('合成附件内容',doc['body'])
        self.assertNotIn('合成预览',doc['body'])
        provenance=doc['provenance']
        self.assertEqual(provenance['message_id'],'aa11')
        self.assertEqual(provenance['rfc_message_id'],'<synthetic-aa11@example.test>')
        self.assertEqual(provenance['account'],payload['account'])
        self.assertEqual(provenance['content_scope']['query'],payload['query'])
        self.assertEqual(provenance['content_scope']['body_source'],'text/plain')
        self.assertTrue(provenance['content_scope']['attachments_not_read'])
        self.assertFalse(provenance['content_scope']['missing'])
        self.assertFalse(provenance['content_scope']['decoding_replaced'])
        parsed=urlsplit(doc['url'])
        self.assertEqual(parsed.hostname,'mail.google.com')
        self.assertEqual(parsed.fragment,'all/aa11')
        self.assertEqual(parse_qs(parsed.query)['authuser'],[payload['account']])
        event=self.request('/api/events',{'title':'合成项目会议确认','priority':'important',
            'needs_confirmation':True,'items':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]},expected_status=201)
        evidence=event['evidence'][0]
        self.assertEqual(evidence['excerpt'],plain)
        self.assertEqual(evidence['hash'],doc['hash'])
        self.assertEqual(evidence['provenance'],provenance)
        self.assertEqual(evidence['state'],'fresh')
        linked=self.request('/api/events/'+event['id']+'/actions',{'summary':'确认合成会议时间',
            'owner':'','owner_is_self':True,'due_at':'2026-10-07T10:00:00+08:00',
            'status':'open','evidence_ids':[evidence['id']]},expected_status=201)
        detail=self.request('/api/events/'+event['id'])
        self.assertEqual(detail['actions'][0]['id'],linked['actions'][0]['id'])
        self.assertEqual(detail['actions'][0]['evidence_ids'],[evidence['id']])
        self.assertEqual(detail['actions'][0]['owner'],'')
        self.assertEqual(detail['actions'][0]['due_at'],'2026-10-07T10:00:00+08:00')
        self.assertEqual(self.request('/api/events?view=mine')['counts']['mine'],1)
        self.assertEqual(self.request('/api/events?view=calendar')['counts']['calendar'],1)
        self.assertEqual(payload,original)

    def test_wrong_origin_cannot_import_gmail_or_create_a_mail_event(self):
        payload,_=self.gmail_payload()
        before=self.request('/api/knowledge')['total']
        for origin in (False,'https://untrusted.example.test'):
            with self.subTest(origin=origin):
                self.reject('/api/knowledge/gmail/import',payload,403,'请求来源',origin=origin)
                self.reject('/api/events',{'title':'合成伪造事项'},403,'请求来源',origin=origin)
        self.assertEqual(self.request('/api/knowledge')['total'],before)
        self.assertEqual(self.request('/api/knowledge/search?kind=mail')['total'],0)
        self.assertEqual(self.request('/api/events')['counts']['total'],0)
        self.assertFalse((self.store.workspace/'mail'/'gmail.db').exists())

    def test_manual_feedback_http_binds_registered_original_version_and_explicitly_returns_to_knowledge(self):
        payload,newer=self.published_work()
        before=self.request('/api/knowledge')['total']
        binding=self.request('/api/feedback')['bindings'][0]
        self.assertEqual(binding['version'],payload['version'])
        self.assertEqual(binding['title'],'合成发布第一版')
        self.assertEqual(binding['delivery_id'],payload['delivery_id'])
        snapshot=self.request('/api/feedback/import',payload,expected_status=201)
        repeated=self.request('/api/feedback/import',payload,expected_status=201)
        self.assertEqual(repeated['id'],snapshot['id'])
        self.assertEqual(snapshot['binding'],binding)
        self.assertNotEqual(snapshot['binding']['version'],newer)
        self.assertEqual(snapshot['metrics'],payload['metrics'])
        self.assertEqual(snapshot['provenance']['note'],payload['provenance']['note'])
        self.assertFalse(snapshot['platform_verified'])
        self.assertNotIn('published_at',snapshot)
        self.assertIn('登记时间不是实际发布时间',snapshot['notice'])
        self.assertIsNone(snapshot['knowledge_id'])
        self.assertEqual(self.request('/api/knowledge')['total'],before)
        self.assertEqual(self.request('/api/feedback/snapshots?'+urlencode({'slug':payload['slug']}))['total'],1)
        self.assertEqual(self.request('/api/feedback/snapshot/'+snapshot['id'])['hash'],snapshot['hash'])
        returned=self.request('/api/feedback/snapshot/'+snapshot['id']+'/knowledge',{},expected_status=201)
        self.assertEqual(self.request('/api/feedback/snapshot/'+snapshot['id']+'/knowledge',{},expected_status=201),returned)
        self.assertEqual(self.request('/api/knowledge')['total'],before+1)
        indexed=self.request('/api/knowledge/search?'+urlencode({'q':'发布表现原始快照','source':'publication_feedback'}))
        self.assertEqual(indexed['total'],1)
        self.assertEqual(indexed['results'][0]['id'],returned['document_id'])
        doc=self.request('/api/knowledge/document/'+returned['document_id'])
        self.assertEqual(doc['hash'],returned['hash'])
        self.assertEqual(doc['source'],'publication_feedback')
        self.assertEqual(doc['provenance']['slug'],payload['slug'])
        self.assertEqual(doc['provenance']['version'],payload['version'])
        self.assertEqual(doc['provenance']['delivery_id'],payload['delivery_id'])
        self.assertEqual(doc['provenance']['publication_snapshot'],snapshot['id'])
        self.assertEqual(doc['provenance']['publication_snapshot_hash'],snapshot['hash'])
        self.assertFalse(doc['provenance']['platform_verified'])
        for original in ('impressions：1200','likes：0','engagement_rate：1.25%',snapshot['hash'],snapshot['url'],binding['work_url']):
            self.assertIn(original,doc['body'])
        self.assertNotIn('comments：0',doc['body'])
        citation=parse_qs(urlsplit(returned['url']).query)
        self.assertEqual(citation,{'document':[doc['id']],'start':['0'],'end':[str(len(doc['body']))],'hash':[doc['hash']]})
        self.assertEqual(self.request('/api/feedback/snapshot/'+snapshot['id'])['knowledge_id'],doc['id'])

    def test_feedback_origin_and_version_mismatch_rejected_without_import_or_knowledge_mutation(self):
        payload,newer=self.published_work()
        before=self.request('/api/knowledge')['total']
        for origin in (False,'https://untrusted.example.test'):
            with self.subTest(origin=origin):
                self.reject('/api/feedback/import',payload,403,'请求来源',origin=origin)
        self.reject('/api/feedback/import',{**payload,'version':newer},400,'作品版本与发布项不一致')
        exported=self.request('/api/export/'+payload['slug'],{'platform':'x','version':payload['version']},expected_status=200)
        export_id=exported['url'].rsplit('/',1)[-1].removesuffix('.zip')
        self.reject('/api/feedback/import',{**payload,'delivery_id':export_id},400,'导出记录')
        self.assertEqual(len(self.request('/api/feedback')['bindings']),1)
        self.assertEqual(self.request('/api/feedback/snapshots')['total'],0)
        self.assertEqual(self.request('/api/knowledge')['total'],before)
        snapshot=self.request('/api/feedback/import',payload,expected_status=201)
        for origin in (False,'https://untrusted.example.test'):
            with self.subTest(return_origin=origin):
                self.reject('/api/feedback/snapshot/'+snapshot['id']+'/knowledge',{},403,'请求来源',origin=origin)
        self.assertIsNone(self.request('/api/feedback/snapshot/'+snapshot['id'])['knowledge_id'])
        self.assertEqual(self.request('/api/knowledge')['total'],before)


if __name__=='__main__':
    unittest.main()
