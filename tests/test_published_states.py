"""R11：已发布状态集合只认一份，Buffer 与 X 官方接口的发布不能漏统计、漏商单复盘。

合成回执：直接在本机写 deliveries.json，不调 Buffer、X 或任何外部服务。
"""
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import publication_metrics
from knowledge_store import Knowledge
from publication_metrics import PUBLISHED, UNPUBLISHED, PublicationMetrics
from publishing.core.common import write_json
from server import Store


class PublishedStatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.deals = self.store.deals
        self.metrics = PublicationMetrics(self.store)
        self.work = self.store.create({'title': '合成发布', 'type': 'post'})
        self.slug = self.work['slug']
        self.when = datetime.now(timezone.utc) - timedelta(hours=1)

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return uuid.uuid4().hex

    # ---------- 合成回执：照 Buffer / X 官方接口真实写出来的 kind 与字段落一条记录 ----------

    def receipt(self, kind, ident=None, account='test', url='https://x.com/test/status/1000'):
        records = self.store.delivery_records(self.slug)
        record = {'id': ident or uuid.uuid4().hex, 'kind': kind, 'platform': 'x', 'version': 'v0001',
                  'url': url, 'account': account,
                  'created_at': datetime.now(timezone.utc).isoformat(), 'note': '合成回执，不是真实发布'}
        records.append(record)
        write_json(self.store.article(self.slug) / 'deliveries.json', records)
        return record

    def payload(self, delivery_id):
        return {'slug': self.slug, 'delivery_id': delivery_id, 'platform': 'x', 'version': 'v0001',
                'collected_at': self.when.isoformat(),
                'range': {'kind': 'cumulative', 'end': self.when.isoformat(), 'label': '合成累计数据'},
                'metrics': {'impressions': 100, 'likes': 3},
                'provenance': {'type': 'manual', 'url': 'https://analytics.x.com/content',
                               'note': '合成测试，不是真实账号统计'}}

    # ---------- 一份已发布清单：Buffer 与 X 官方接口都得算，Buffer 中间态一律不算 ----------

    def test_published_set_is_shared_and_excludes_buffer_intermediate_states(self):
        self.assertEqual(set(PUBLISHED), {'published_manual', 'published_browser', 'published_x_api', 'buffer_published'})
        self.assertEqual(set(UNPUBLISHED), {'buffer_in_progress', 'buffer_draft', 'buffer_submitted',
                                            'buffer_unknown', 'buffer_failed'})
        self.assertEqual(PUBLISHED & UNPUBLISHED, set())

    def test_deal_and_statistics_read_the_same_set_at_call_time(self):
        # 只改 publication_metrics.PUBLISHED 的成员，统计与商单复盘必须同时跟着变：
        # 说明两处读的是同一份清单，而不是各抄一份。
        def synth(status):
            work = self.store.create({'title': '合成共享清单 %d' % status, 'type': 'post'})
            url = 'https://x.com/test/status/%d' % status
            record = {'id': uuid.uuid4().hex, 'kind': 'published_x_api', 'platform': 'x', 'version': 'v0001',
                      'url': url, 'account': 'test',
                      'created_at': datetime.now(timezone.utc).isoformat(), 'note': '合成回执'}
            write_json(self.store.article(work['slug']) / 'deliveries.json', [record])
            deal = self.deal()
            deal = self.deals.child(deal['id'], 'deliverable',
                                    dict(title='第一篇', slug=work['slug'], version='v0001',
                                         revision=deal['revision'], request_key=self.key()))
            publish = dict(platform='x', url=url, account='test', slug=work['slug'], version='v0001',
                           deliverable_id=deal['deliverables'][0]['id'], published_on='2026-10-03',
                           revision=deal['revision'], request_key=self.key())
            return record, self.deals.child(deal['id'], 'publication', publish)

        with patch.object(publication_metrics, 'PUBLISHED', frozenset({'published_manual'})):
            hidden, manual = synth(5000)
            self.assertNotIn(hidden['id'], [b['delivery_id'] for b in self.metrics.bindings()['bindings']])
            self.assertNotEqual(manual['publications'][0]['delivery_id'], hidden['id'],
                                '清单里没有这一类时商单复盘不该复用它')
        shown, reused = synth(5001)
        self.assertIn(shown['id'], [b['delivery_id'] for b in self.metrics.bindings()['bindings']])
        self.assertEqual(reused['publications'][0]['delivery_id'], shown['id'], '清单放开后商单复盘就能复用同一条发布')
        self.assertEqual(len(self.store.delivery_records(reused['publications'][0]['slug'])), 1)

    def test_buffer_published_can_bind_statistics_and_shows_in_bindings(self):
        record = self.receipt('buffer_published')
        binding = self.metrics.bindings()['bindings']
        self.assertEqual([b['delivery_id'] for b in binding], [record['id']])
        self.assertEqual(binding[0]['delivery_kind'], 'buffer_published')
        snapshot = self.metrics.import_snapshot(self.payload(record['id']))
        self.assertEqual(snapshot['binding']['delivery_id'], record['id'])
        self.assertEqual(snapshot['binding']['url'], 'https://x.com/test/status/1000')
        self.assertEqual(self.metrics.listing()['total'], 1)

    def test_buffer_intermediate_states_cannot_be_treated_as_published(self):
        for kind in sorted(UNPUBLISHED):
            record = self.receipt(kind)
            with self.subTest(kind=kind):
                self.assertEqual(self.metrics.bindings()['bindings'], [], kind + ' 不该出现在可统计的发布项里')
                with self.assertRaisesRegex(ValueError, '这条 Buffer 记录还没发出去（' + kind + '）'):
                    self.metrics.import_snapshot(self.payload(record['id']))
                self.assertEqual(self.metrics.listing()['total'], 0)

    def test_x_api_and_browser_and_manual_kinds_all_bind_statistics(self):
        # 补进来的两类不能挤掉原来三类：manual/browser/x_api 依旧都能绑统计。
        for index, kind in enumerate(('published_manual', 'published_browser', 'published_x_api')):
            self.receipt(kind, url='https://x.com/test/status/%d' % (2000 + index))
        kinds = sorted(binding['delivery_kind'] for binding in self.metrics.bindings()['bindings'])
        self.assertEqual(kinds, ['published_browser', 'published_manual', 'published_x_api'])

    # ---------- 商单复盘：已发布记录要被认出来，不该同一条发布再登记一次 ----------

    def deal(self):
        return self.deals.save(dict(title='合成商单', customer='合成客户', request_key=self.key()))

    def test_deal_binds_existing_x_api_and_buffer_published_records(self):
        expected = 0
        for index, kind in enumerate(('published_x_api', 'buffer_published')):
            with self.subTest(kind=kind):
                work = self.store.create({'title': '合成作品 ' + kind, 'type': 'post'})
                url = 'https://x.com/test/status/%d' % (3000 + index)
                record = {'id': uuid.uuid4().hex, 'kind': kind, 'platform': 'x', 'version': 'v0001',
                          'url': url, 'account': 'test',
                          'created_at': datetime.now(timezone.utc).isoformat(), 'note': '合成回执'}
                write_json(self.store.article(work['slug']) / 'deliveries.json', [record])
                deal = self.deal()
                deal = self.deals.child(deal['id'], 'deliverable',
                                        dict(title='第一篇', slug=work['slug'], version='v0001',
                                             revision=deal['revision'], request_key=self.key()))
                deal = self.deals.child(deal['id'], 'publication',
                                        dict(platform='x', url=url, account='test', slug=work['slug'],
                                             version='v0001', deliverable_id=deal['deliverables'][0]['id'],
                                             published_on='2026-10-03', revision=deal['revision'],
                                             request_key=self.key()))
                publication = deal['publications'][0]
                self.assertEqual(publication['delivery_id'], record['id'], kind + ' 的发布记录应被直接复用')
                self.assertEqual(len(self.store.delivery_records(work['slug'])), 1, kind + ' 不该再多登记一条手动记录')
                expected += 1
                self.assertEqual(self.deals.listing(month='2026-10')['summary']['published'], expected)

    def test_deal_does_not_reuse_buffer_intermediate_records(self):
        work = self.store.create({'title': '合成作品', 'type': 'post'})
        url = 'https://x.com/test/status/4000'
        record = {'id': uuid.uuid4().hex, 'kind': 'buffer_submitted', 'platform': 'x', 'version': 'v0001',
                  'url': None, 'account': 'test', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'note': '已提交 Buffer，结果未确认'}
        write_json(self.store.article(work['slug']) / 'deliveries.json', [record])
        deal = self.deal()
        deal = self.deals.child(deal['id'], 'deliverable', dict(title='第一篇', slug=work['slug'], version='v0001',
                                                                 revision=deal['revision'], request_key=self.key()))
        deal = self.deals.child(deal['id'], 'publication',
                                dict(platform='x', url=url, account='test', slug=work['slug'], version='v0001',
                                     deliverable_id=deal['deliverables'][0]['id'], published_on='2026-10-03',
                                     revision=deal['revision'], request_key=self.key()))
        self.assertNotEqual(deal['publications'][0]['delivery_id'], record['id'], '提交中的 Buffer 不能当已发布项复用')
        self.assertEqual(len(self.store.delivery_records(work['slug'])), 2)
        self.assertEqual(self.store.delivery_records(work['slug'])[1]['kind'], 'published_manual')


if __name__ == '__main__':
    unittest.main()
