import json
import os
import stat
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import grok_bot_radar as grok
import interest_channel as interest
import today_view as today
from items_store import ItemsStore

NOW = datetime.now(timezone.utc)
REAL_TOPICS_PATH = Path.home() / 'grok-bot-reports/interest-topics.md'
REAL_TOPICS = REAL_TOPICS_PATH.read_bytes() if REAL_TOPICS_PATH.exists() else interest.DEFAULT_DOCUMENT.encode()


def row(identifier='101', **values):
    return dict(id=identifier, url='https://x.com/fixture/status/' + identifier, author_handle='fixture',
                author_followers=120, published_at=(NOW-timedelta(hours=1)).isoformat(), collected_at=NOW.isoformat(),
                topic='Agent', what_new='可直接使用的新工具', relevance=3, why='有可用代码',
                full_text='AI Agent 完整正文 <script>window.PWNED=1</script><img src=x onerror=alert(1)>',
                content_complete=True, usable=[dict(type='repo', url='https://example.test/repo', note='代码')], views=12, **values)


class InterestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='interest-unit-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workspace'
        self.root.mkdir()
        self.reports = Path(self.tmp.name) / 'reports'
        (self.reports / 'interest').mkdir(parents=True)
        env = patch.dict(os.environ, {'GROK_BOT_REPORTS_DIR': str(self.reports)})
        env.start()
        self.addCleanup(env.stop)
        self.file = NOW.astimezone(grok.BEIJING).strftime('%Y-%m-%d-%H%M') + '.json'

    def write(self, rows, filename=None):
        path = self.reports / 'interest' / (filename or self.file)
        path.write_text(json.dumps(rows, ensure_ascii=False))
        return path

    def listing(self):
        return today.page_view(self.root, {'channel':['interest'], 'range':['7d']}, NOW)

    def detail(self, identifier='101'):
        return today.item_view(self.root, {'id':['x:'+identifier], 'channel':['interest']})

    def act(self, action, identifier='101'):
        return today.save_feedback(self.root, dict(id='x:'+identifier, channel='interest', action=action))

    def test_content_notice_does_not_count_as_source_failure(self):
        for reason in ('正文被截断，API 未返回长文全文', '仓库链接在串推后续帖，未展开', '', None):
            with self.subTest(reason=reason):
                record=row();record.update(content_complete=False,incomplete_reason=reason)
                self.write([record]);items,states=interest.snapshot(self.root,NOW);state=states['G']
                self.assertEqual(state['bad_entries'],0);self.assertIsNone(state['error'])
                self.assertEqual(state['incomplete_entries'],1);self.assertEqual(len(items),1)
                self.assertEqual(state['incomplete_items'][0],dict(id='x:101',title=record['what_new'],url=record['url'],reasons=[reason or '来源未提供完整正文']))
                self.assertFalse(items[0]['content_complete'])
                self.assertEqual(self.listing()['total'],1)
                self.assertIn(reason or '来源未提供完整正文',self.detail()['incomplete_reasons'])

    def test_missing_body_is_notice_but_illegal_field_is_failure(self):
        record=row();record['full_text']='';self.write([record]);state=interest.snapshot(self.root,NOW)[1]['G']
        self.assertEqual(state['incomplete_entries'],1);self.assertEqual(state['bad_entries'],0)
        for field,value in [('content_complete','false'),('full_text',[]),('incomplete_reason',{})]:
            with self.subTest(field=field):
                record=row();record[field]=value;self.write([record]);state=interest.snapshot(self.root,NOW)[1]['G']
                self.assertEqual(state['bad_entries'],1);self.assertIsNotNone(state['error'])
                self.assertEqual(state['incomplete_entries'],0)

    def test_true_failures_and_content_notices_count_separately(self):
        incomplete=row('101');incomplete.update(content_complete=False,incomplete_reason='串推未展开')
        broken=row('102');broken['url']='javascript:alert(1)'
        self.write([incomplete,broken,None]);self.write('{bad',NOW.astimezone(grok.BEIJING).strftime('%Y-%m-%d-0000.json'))
        items,state=interest.snapshot(self.root,NOW);state=state['G']
        self.assertEqual(state['incomplete_entries'],1);self.assertEqual(state['bad_entries'],2);self.assertEqual(state['bad_files'],1)
        self.assertIn('1 个异常文件，2 处条目或链接异常',state['error']);self.assertEqual(len(items),2)
        # 同一条内容既有正文缺失也有坏链接，两个计数都不能丢。
        incomplete['usable']=[dict(type='repo',url='javascript:alert(1)')]
        self.write([incomplete]);state=interest.snapshot(self.root,NOW)[1]['G']
        self.assertEqual(state['incomplete_entries'],1);self.assertEqual(state['bad_entries'],1)

    def test_each_validation_reason_remains_source_failure(self):
        for field,value in [('author_handle',''),('topic',''),('what_new',''),('url','https://x.com/fixture/status/999'),('relevance',4),('published_at','invalid')]:
            with self.subTest(field=field):
                record=row();record[field]=value;self.write([record]);state=interest.snapshot(self.root,NOW)[1]['G']
                self.assertEqual(state['bad_entries'],1);self.assertEqual(state['incomplete_entries'],0);self.assertIsNotNone(state['error'])
        self.write([dict(id='invalid')]);state=interest.snapshot(self.root,NOW)[1]['G']
        self.assertEqual(state['bad_entries'],1);self.assertEqual(state['incomplete_entries'],0)

    def test_content_notice_deduplicates_using_latest_post(self):
        old=row();old.update(content_complete=False,incomplete_reason='旧正文截断',collected_at=(NOW-timedelta(hours=2)).isoformat())
        self.write([old],NOW.astimezone(grok.BEIJING).strftime('%Y-%m-%d-0000.json'))
        current=row();self.write([current]);items,state=interest.snapshot(self.root,NOW)
        self.assertEqual(state['G']['incomplete_entries'],0);self.assertEqual(state['G']['incomplete_items'],[])
        current.update(content_complete=False,incomplete_reason='新串推未展开');self.write([current]);items,state=interest.snapshot(self.root,NOW)
        self.assertEqual(state['G']['incomplete_entries'],1);self.assertEqual(state['G']['incomplete_items'][0]['reasons'],['新串推未展开'])

    def test_G1_urls(self):
        cases = [('https://x.com/fixture/status/101', True),
                 ('https://x.com/fixture/status/202', False),
                 ('https://x.com.evil.com/fixture/status/101', False),
                 ('https://twitter.com/fixture/status/101', False),
                 ('http://x.com/fixture/status/101', False),
                 ('https://x.com/fixture/status/101?s=20', False),
                 ('https://x.com/123/status/101', True)]
        for url, valid in cases:
            with self.subTest(url=url):
                record=row();record['url']=url;record['full_text']='正文先出现数字 9999'
                self.write([record]);item=self.detail()
                self.assertEqual(item['id'],'x:101')
                self.assertEqual(bool(item['url']),valid)
                self.assertEqual(item['content_complete'],valid)

    def test_G2_real_reports_unchanged(self):
        real = Path.home() / 'grok-bot-reports'
        def signature():
            return sorted((str(p.relative_to(real)),p.stat().st_mtime_ns) for p in real.rglob('*') if p.is_file())
        before=signature()
        self.write([row()]);self.listing();self.act('accurate');self.act('keep')
        self.assertEqual(signature(),before)

    def test_G3_allowed_report_writes_only(self):
        self.write([row()]);before={str(p.relative_to(self.reports)) for p in self.reports.rglob('*') if p.is_file()}
        state=interest.topics();interest.save_topics(dict(topics=state['topics'],revision=state['revision']))
        self.act('keep');self.act('inaccurate')
        after={str(p.relative_to(self.reports)) for p in self.reports.rglob('*') if p.is_file()}
        self.assertEqual(after-before,{'interest-topics.md','feedback/studio-feedback.json'})
        self.assertFalse(list(self.reports.glob('.interest-*')))

    def test_G3_atomic_failure_cleans_temp(self):
        path=self.reports/'interest-topics.md';path.write_bytes(REAL_TOPICS)
        state=interest.topics()
        with patch.object(interest.os,'replace',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):interest.save_topics(dict(topics=['替换方向'],revision=state['revision']))
        self.assertEqual(path.read_bytes(),REAL_TOPICS)
        self.assertFalse(list(self.reports.glob('.interest-*')))

    def test_G3_save_preserves_existing_permissions(self):
        path = self.reports / 'interest-topics.md'
        for mode in (0o644, 0o640):
            with self.subTest(mode=oct(mode)):
                path.write_text(interest.DEFAULT_DOCUMENT)
                path.chmod(mode)
                state = interest.topics()
                saved = interest.save_topics(dict(topics=['权限保留'], revision=state['revision']))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                self.assertEqual(saved['topics'], ['权限保留'])
                self.assertFalse(list(self.reports.glob('.interest-*')))

    def test_G3_new_topics_keeps_private_permissions(self):
        state = interest.topics()
        self.assertEqual(state['revision'], 'missing')
        saved = interest.save_topics(dict(topics=['新建方向'], revision=state['revision']))
        path = self.reports / 'interest-topics.md'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(saved['topics'], ['新建方向'])

    def test_G4_missing_fields_marked(self):
        self.write([dict(id='101'),dict(full_text='无ID'),None])
        result=self.listing();self.assertEqual(result['total'],1)
        item=self.detail();self.assertFalse(item['content_complete']);self.assertEqual(item['url'],'')
        self.assertIn('发布时间',item['incomplete_reason']);self.assertGreater(result['sources']['G']['bad_entries'],0)

    def test_G4_bad_link(self):
        data=row();data['url']='not-a-url';self.write([data]);self.assertEqual(self.detail()['url'],'')

    def test_G4_missing_metadata_marked(self):
        for field in ('author_handle', 'topic', 'what_new'):
            with self.subTest(field=field):
                data=row();data.pop(field);self.write([data])
                self.assertFalse(self.detail()['content_complete'])
                self.assertEqual(self.listing()['sources']['G']['bad_entries'],1)

    def test_G4_non_x_url(self):
        data=row();data['url']='https://example.test/fixture/status/101';self.write([data]);self.assertFalse(self.detail()['content_complete'])

    def test_G4_duplicates_latest_collected_and_union(self):
        old=row();old['collected_at']=(NOW-timedelta(hours=2)).isoformat();old['full_text']='旧正文';old['also_posted_by']=['a']
        self.write([old],NOW.astimezone(grok.BEIJING).strftime('%Y-%m-%d-0000.json'))
        new=row();new['full_text']='新正文';new['also_posted_by']=['b','a'];self.write([new],NOW.astimezone(grok.BEIJING).strftime('%Y-%m-%d-2359.json'))
        result=self.listing();self.assertEqual(result['total'],1);self.assertEqual(result['sources']['G']['duplicates'],1)
        item=self.detail();self.assertEqual(item['full_text'],'新正文');self.assertEqual(set(item['also_posted_by']),{'a','b'})

    def test_G4_empty_file_is_anomaly(self):
        path=self.write([]);path.write_text('');result=self.listing()
        self.assertEqual(result['total'],0);self.assertEqual(result['sources']['G']['bad_files'],1)

    def test_G4_empty_report_variants(self):
        for value in ([], '本次无', {'posts':[]}, {'message':'本次无'}):
            with self.subTest(value=value):
                self.write(value);result=self.listing();self.assertEqual(result['total'],0);self.assertIsNone(result['sources']['G']['error'])

    def test_G4_half_written_file(self):
        path=self.write([]);path.write_text('{"posts":[')
        (path.parent/(path.name+'.tmp')).write_text('{')
        result=self.listing();self.assertEqual(result['total'],0);self.assertGreater(result['sources']['G']['bad_files'],0)

    def test_G4_unsafe_usable(self):
        data=row();data['usable']=[dict(type='repo',url=u,note='链接') for u in ('javascript:alert(1)','data:text/html,x','https://example.test/repo','http://example.test/api','https://u:p@example.test/')]
        self.write([data]);urls=[u['url'] for u in self.detail()['usable']]
        self.assertEqual(urls,[None,None,'https://example.test/repo','http://example.test/api',None])

    def test_G4_rich_html_sanitized_and_plaintext_preserved(self):
        data=row();data['article']={'html':'<p>可读正文</p><script>window.PWNED=1</script><img src=x onerror=alert(1)>'}
        self.write([data]);item=self.detail();self.assertEqual(item['full_text'],data['full_text'])
        self.assertEqual(item['rich_sources'][0]['article']['full_text'],'可读正文')

    def test_G6_seven_days_performance_and_size_limit(self):
        for day in range(7):
            for hour in (10,22):
                stamp=(NOW-timedelta(days=day)).astimezone(grok.BEIJING).replace(hour=hour,minute=30)
                rows=[]
                for i in range(15):
                    record=row(str(10000+day*1000+hour*20+i));record['collected_at']=stamp.isoformat();record['full_text']='全文'*500;rows.append(record)
                self.write({'posts':rows},stamp.strftime('%Y-%m-%d-%H%M.json'))
        bad=self.reports/'interest'/NOW.astimezone(grok.BEIJING).strftime('%Y-%m-%d-2358.json');bad.write_bytes(b'x'*(interest.MAX_BYTES+1))
        started=time.perf_counter();result=self.listing();elapsed=time.perf_counter()-started
        self.assertEqual(result['total'],210);self.assertEqual(result['sources']['G']['bad_files'],1);self.assertLess(elapsed,1)
        print('G6 210条 + >2MiB坏文件:',round(elapsed*1000,2),'ms')

    def test_recent_filename_boundary_and_symlink(self):
        self.write([row()],(NOW-timedelta(days=8)).astimezone(grok.BEIJING).strftime('%Y-%m-%d-%H%M.json'))
        target=Path(self.tmp.name)/'outside.json';target.write_text(json.dumps([row('999')]))
        (self.reports/'interest'/self.file).symlink_to(target)
        result=self.listing();self.assertEqual(result['total'],0);self.assertEqual(result['sources']['G']['bad_files'],1)

    def test_G7_new_fields_daily_alert_types(self):
        bundle=Path(self.tmp.name)/'bundle.json';bundle.write_text(json.dumps({'tweets':[],'vip_tweets':[]}))
        for kind in ('daily','alerts'):
            (self.reports/kind).mkdir(exist_ok=True)
            for i,crowding in enumerate(({'count':12,'links':['https://x.com/a/status/1']},'未知',[],None)):
                record=dict(id=str(500+i),url=f'https://x.com/fixture/status/{500+i}',handle='fixture',author='作者',created_at=NOW.isoformat(),full_text='AI Agent 新工具完整正文',label='爆款',views='100000',bookmarks='12' if i%2 else None,is_retweet=None,original_author='original',original_author_followers='2000',collected_at=NOW.isoformat(),crowding=crowding)
                (self.reports/kind/f'{i}.json').write_text(json.dumps({'collected_at':NOW.isoformat(),'posts':[record]}))
        with patch.object(grok,'BUNDLE_PATH',bundle),patch.object(today.linuxdo,'refresh_background'):
            reports=grok.load_reports();result=today.page_view(self.root,{'range':['7d']},NOW)
        self.assertEqual(len(reports['posts']),4);self.assertEqual(result['total'],4)
        self.assertEqual({p['metrics']['bookmarks'] for p in result['items']},{12,None})

    def test_G8_format_preserved_add_remove_save(self):
        path=self.reports/'interest-topics.md';path.write_bytes(REAL_TOPICS)
        state=interest.topics();original=state['topics']
        added=interest.save_topics(dict(topics=original+['临时验收方向'],revision=state['revision']))
        expected=REAL_TOPICS.replace(b'\n## \xe8\xaf\xb4\xe6\x98\x8e', '- 临时验收方向\n\n## 说明'.encode())
        self.assertEqual(path.read_bytes(),expected)
        interest.save_topics(dict(topics=original,revision=added['revision']))
        self.assertEqual(path.read_bytes(),REAL_TOPICS)

    def test_G8_other_sections_and_blank_lines_preserved(self):
        document='# 前言\r\n\r\n逐字保留\r\n## 当前方向\r\n\r\n- 一\r\n\r\n- 二\r\n\r\n## 说明\r\n保留说明\r\n\r\n## 其他\r\n不能动\r\n'
        path=self.reports/'interest-topics.md';path.write_bytes(document.encode())
        state=interest.topics();interest.save_topics(dict(topics=['三','四','五'],revision=state['revision']))
        self.assertEqual(path.read_bytes(),document.replace('- 一\r\n','- 三\r\n').replace('- 二\r\n','- 四\r\n- 五\r\n').encode())

    def test_G8_mtime_conflict_no_overwrite(self):
        path=self.reports/'interest-topics.md';path.write_bytes(REAL_TOPICS)
        state=interest.topics();os.utime(path,ns=(path.stat().st_atime_ns,path.stat().st_mtime_ns+1))
        with self.assertRaisesRegex(interest.Conflict,'文件已被修改，请刷新'):
            interest.save_topics(dict(topics=['不能覆盖'],revision=state['revision']))
        self.assertEqual(path.read_bytes(),REAL_TOPICS)

    def test_G8_change_during_atomic_write(self):
        path=self.reports/'interest-topics.md';path.write_bytes(REAL_TOPICS)
        state=interest.topics();original_fsync=interest.os.fsync
        def changed(fd):
            original_fsync(fd);path.write_bytes(REAL_TOPICS+'\n外部修改\n'.encode())
        with patch.object(interest.os,'fsync',side_effect=changed):
            with self.assertRaises(interest.Conflict):interest.save_topics(dict(topics=['不能覆盖'],revision=state['revision']))
        self.assertTrue(path.read_bytes().endswith('外部修改\n'.encode()));self.assertFalse(list(self.reports.glob('.interest-*')))

    def test_topic_validation_and_dedup(self):
        state=interest.topics();saved=interest.save_topics(dict(topics=['  一  ','一','二'],revision=state['revision']))
        self.assertEqual(saved['topics'],['一','二'])
        for topics in ([''],['# 标题'],['一\n二'],['一\r二'],['x'*81],['a']*31,[None]):
            with self.subTest(topics=topics),self.assertRaises(ValueError):interest.save_topics(dict(topics=topics,revision=saved['revision']))

    def test_feedback_does_not_import_keep_is_idempotent(self):
        self.write([row()]);self.listing();self.detail()
        db=self.root/'items/items.db'
        self.act('accurate');self.act('inaccurate');self.assertFalse(db.exists())
        self.act('keep');self.act('keep')
        library=ItemsStore(self.root);result=library.listing({'q':['可直接使用']})
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['id'],'x:101');self.assertEqual(result['items'][0]['status'],'kept')
        feedback=json.loads((self.reports/'feedback/studio-feedback.json').read_text())
        self.assertEqual(feedback['101']['channel'],'interest')
        self.act('clear');self.assertEqual(self.detail()['status'],'kept');self.assertIsNone(self.detail()['feedback'])

    def test_feedback_preserves_existing_viral_feedback(self):
        self.write([row()]);path=grok.feedback_path(self.root);path.parent.mkdir(parents=True)
        old={'999':dict(tweet_id='999',verdict='worth',post={'id':'999'})};path.write_text(json.dumps(old))
        self.act('accurate');entries=json.loads(path.read_text());self.assertEqual(entries['999'],old['999'])

    def test_interest_does_not_mix_into_hot_or_start_sources(self):
        self.write([row()])
        with patch.object(today,'snapshot',side_effect=AssertionError('不应读取爆款')),patch.object(today.linuxdo,'refresh_background',side_effect=AssertionError('不应发起刷新')):
            self.assertEqual(self.listing()['total'],1)

    def test_interest_summary_has_resources_not_fulltext(self):
        self.write([row()]);result=self.listing()
        self.assertEqual(result['items'][0]['relevance'],3);self.assertIn('usable',result['items'][0]);self.assertNotIn('full_text',result['items'][0])


if __name__=='__main__':unittest.main()
