"""收下即分析：入库后自动分析一条回收项（四块内容 + 相关条目），含手动重跑与模型繁忙。

全部用假模型与假抓取：不联网、不调真实模型、不读任何密钥。
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import intake_store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from intake_cards import IntakeCards
from intake_store import Intake
from knowledge_store import Knowledge
from server import Handler, Store, ThreadingHTTPServer

SOURCE = ('黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'
          '拍照的人蹲下来，让水面把天空再复制一遍，人物只留一个背影，不露脸。')
READING = {'summary': '旧码头黄昏的蹲拍方法：用水面倒影复制天空，人物只留背影。',
           'points': ['水面倒影是主手法', '人物不露脸', '渡船只留灯线'],
           'why': '开头就能直接借的取景方式，适合写一条拍摄笔记。',
           'angles': [{'title': '写一条蹲拍笔记', 'how': '从水面倒影切入，交代蹲位与光线'},
                      {'title': '写成取景清单', 'how': '把倒影、背影、灯线三条要点排成清单'}]}
CARD = {'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影，暖色低对比',
        'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}


def payload(value):
    return json.dumps(value, ensure_ascii=False)


class FakeWeb:
    def __call__(self):
        return self

    def fetch(self, args, job):
        return {'url': args['url'], 'title': '旧码头的蹲拍', 'markdown': SOURCE, 'start': 0,
                'total_chars': len(SOURCE), 'truncated': False, 'next_start': None}


class InstantAnalysisTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.store._creative_intake = self.intake
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.store._intake_cards = self.cards
        self.intake.analysis_provider = self.cards.analysis_states
        self.cards.model_provider = lambda: {'model': 'synthetic', 'timeout_seconds': 100}
        self.cards.analysis_retry_wait = 0
        self.calls = []
        self.answers = {'read': payload(READING), 'cards': payload({'cards': [CARD]})}
        patch_intake_dependency(self, "WebTools", FakeWeb)
        self.seed_model()
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.settle()
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        for worker in list(self.intake.recognition_workers.values()):
            worker.join(timeout=5)
        self.tmp.cleanup()

    def seed_model(self):
        def fake(identity, prompt, system, cancel, config, deadline):
            self.calls.append({'identity': identity, 'prompt': prompt, 'system': system})
            if identity.endswith('-read'):
                key = 'read'
            elif identity.endswith('-repair'):
                key = 'repair'
            else:
                key = 'cards'
            answer = self.answers.get(key, self.answers['read'])
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.cards.call_model = fake

    def settle(self, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            workers = list(self.cards.analysis_workers.values())
            if not workers:
                break
            for worker in workers:
                worker.join(timeout=1)
        time.sleep(0.05)

    def api(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data,
                                         method='POST' if data is not None else 'GET',
                                         headers={'Origin': self.base, 'X-Article-Studio': '1',
                                                  'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def drop_link(self, url='https://example.com/pier', reason='这个开头方式可以借'):
        self.counter = getattr(self, 'counter', 0) + 1
        code, saved = self.api('/api/intake/drop', {'id': '%032x' % self.counter, 'kind': 'link',
                                                    'url': url, 'reason': reason})
        self.assertEqual(code, 201)
        return saved['id']

    def resolve(self, drop_id):
        code, result = self.api('/api/intake/%s/resolve' % drop_id, {})
        self.assertEqual(code, 200)
        return result

    def state(self, drop_id):
        self.settle()
        return self.intake.detail(drop_id)['analysis']

    # ── 自动触发 ───────────────────────────────────────────────────────────────

    def test_resolve_files_it_and_starts_the_analysis_without_waiting(self):
        drop_id = self.drop_link()
        result = self.resolve(drop_id)
        self.assertEqual(result['status'], 'resolved')
        self.assertIn(result['analysis']['status'], ('queued', 'running', 'done'))
        # The HTTP answer came back before any model turn finished.
        self.assertLessEqual(len(self.calls), 2)
        state = self.state(drop_id)
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['summary'], READING['summary'])
        self.assertEqual(state['points'], READING['points'])
        self.assertEqual(state['why'], READING['why'])
        self.assertEqual([angle['title'] for angle in state['angles']],
                         [angle['title'] for angle in READING['angles']])
        self.assertEqual(state['model'], 'synthetic')
        self.assertEqual(state['error'], '')

    def test_one_analysis_uses_at_most_two_model_turns(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.settle()
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'cards'])
        # The reading prompt never asks for a prompt card, and the card turn is the existing one.
        self.assertIn('不要输出提示词卡', [c['system'] for c in self.calls if c['identity'].endswith('-read')][0])
        self.assertIn('"cards"', [c['system'] for c in self.calls if c['identity'].endswith('-cards')][0])

    def test_instant_cards_stay_candidates_and_are_linked_from_the_state(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        state = self.state(drop_id)
        self.assertEqual(state['cards'], 1)
        listing = self.cards.listing()
        self.assertEqual(listing['counts'], {'candidate': 1, 'confirmed': 0, 'rejected': 0})
        card = listing['items'][0]
        self.assertEqual(card['drop_id'], drop_id)
        self.assertEqual(card['source_state'], 'verified')

    def test_the_listing_carries_the_state_for_every_row(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.settle()
        code, listing = self.api('/api/intake')
        self.assertEqual(code, 200)
        self.assertEqual(len(listing['items']), 1)
        item = listing['items'][0]
        self.assertEqual(item['analysis']['status'], 'done')
        self.assertEqual(item['analysis']['drop_id'], drop_id)
        self.assertEqual(item['state'], '已存入知识库，已抓取正文，共 %d 字' % len(SOURCE))

    # ── 相关条目 ───────────────────────────────────────────────────────────────

    def test_related_entries_come_from_the_local_index_with_links(self):
        for index in range(6):
            self.store.knowledge.add_note({'title': '码头摄影笔记 %d' % index,
                                           'body': '黄昏码头，水面倒影与背影的拍法，第 %d 条。' % index})
        drop_id = self.drop_link()
        self.resolve(drop_id)
        state = self.state(drop_id)
        self.assertTrue(state['related'])
        self.assertLessEqual(len(state['related']), 5)
        documents = {entry['document_id'] for entry in state['related']}
        self.assertNotIn(self.intake.detail(drop_id)['document_id'], documents)
        for entry in state['related']:
            self.assertEqual(entry['url'], '/knowledge.html?document=' + entry['document_id'])
            self.assertIn('码头摄影笔记', entry['title'])
            self.assertTrue(entry['excerpt'])

    def test_related_entries_stay_empty_when_the_index_has_nothing(self):
        self.answers['read'] = payload({'summary': '无关内容。', 'points': [], 'why': '没有可写的。',
                                        'angles': []})
        record = self.intake.receive({'kind': 'text', 'text': 'zzzz qqqq jjjj', 'reason': ''})
        self.intake.resolve(record['id'])
        self.cards.analyze_drop(record['id'])
        state = self.state(record['id'])
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['related'], [])

    # ── 手动重跑 ───────────────────────────────────────────────────────────────

    def test_manual_rerun_replaces_the_previous_result(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.assertEqual(self.state(drop_id)['status'], 'done')
        self.answers['read'] = payload({**READING, 'summary': '重写后的摘要。'})
        code, state = self.api('/api/intake/%s/analyze' % drop_id, {})
        self.assertEqual(code, 200)
        self.assertIn(state['status'], ('queued', 'running', 'done'))
        state = self.state(drop_id)
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['summary'], '重写后的摘要。')
        self.assertEqual(len(self.calls), 4)

    def test_manual_rerun_after_a_failure_recovers(self):
        self.answers['read'] = ValueError('模型未返回有效正文，本条未推进')
        drop_id = self.drop_link()
        result = self.resolve(drop_id)
        self.assertEqual(result['status'], 'resolved')
        state = self.state(drop_id)
        self.assertEqual((state['status'], state['retryable']), ('failed', True))
        self.assertIn('模型未返回有效正文', state['error'])
        self.answers['read'] = payload(READING)
        self.seed_model()
        code, state = self.api('/api/intake/%s/analyze' % drop_id, {})
        self.assertEqual(code, 200)
        self.assertEqual(self.state(drop_id)['status'], 'done')

    def test_analyze_endpoint_refuses_extra_fields_and_an_unfiled_drop(self):
        code, body = self.api('/api/intake/%s/analyze' % ('a' * 32), {'force': True})
        self.assertEqual((code, body['error']), (400, '重新分析不接受额外字段'))
        record = self.intake.receive({'kind': 'text', 'text': '还没解析的一条', 'reason': ''})
        code, body = self.api('/api/intake/%s/analyze' % record['id'], {})
        self.assertEqual(code, 400)
        self.assertIn('还没有存入知识库', body['error'])
        self.assertEqual(self.calls, [])

    # ── 重复链接 ───────────────────────────────────────────────────────────────

    def test_a_duplicate_link_shows_the_analysis_it_already_has(self):
        first = self.drop_link('https://example.com/pier')
        self.resolve(first)
        self.assertEqual(self.state(first)['status'], 'done')
        self.assertEqual(len(self.calls), 2)
        second = self.drop_link('https://example.com/pier')
        result = self.resolve(second)
        self.assertEqual(result['outcome'], 'duplicate')
        self.assertEqual(result['state'], '已存在，未重复导入')
        state = self.state(second)
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['summary'], READING['summary'])
        self.assertEqual(state['inherited_from'], first)
        # 没有为同一条资料再花一次模型调用。
        self.assertEqual(len(self.calls), 2)

    def test_a_finished_analysis_is_not_run_again_without_a_force(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.settle()
        self.cards.request_analysis(drop_id)
        self.settle()
        self.assertEqual(len(self.calls), 2)

    # ── 页面可以先问一句：这个后端支持分析吗 ──────────────────────────

    def test_the_page_can_ask_whether_this_backend_supports_analysis(self):
        code, data = self.api('/api/intake/analysis-status')
        self.assertEqual(code, 200)
        self.assertEqual(data['schema'], 'intake-analysis-status/v1')
        self.assertTrue(data['supported'])
        self.assertTrue(data['model_configured'])
        # 新接口不能挡住回收项详情路由（两者都是 /api/intake/xxx）。
        drop_id = self.drop_link()
        detail_code, detail = self.api('/api/intake/' + drop_id)
        self.assertEqual(detail_code, 200)
        self.assertEqual(detail['id'], drop_id)

    def test_an_unconfigured_model_is_reported_honestly_by_the_status_endpoint(self):
        self.cards.model_provider = lambda: None
        code, data = self.api('/api/intake/analysis-status')
        self.assertEqual(code, 200)
        self.assertTrue(data['supported'])
        self.assertFalse(data['model_configured'])

    def test_the_workbench_wires_recognition_to_the_analysis_queue(self):
        # 接线写在 Store.intake_cards 属性里，不能只靠 main() 手动接；否则识别完成不会自动排队分析。
        store = Store(self.tmp.name)
        store._knowledge = Knowledge(store, sources=[])
        self.assertIsNone(store.creative_intake.analysis_after_recognition)
        store.intake_cards
        self.assertTrue(callable(store.creative_intake.analysis_after_recognition))
        self.assertTrue(callable(store.creative_intake.analysis_provider))

    # ── 媒体识别完成后自动分析（round5）─────────────────────────────

    def set_recognition(self, drop_id, status, text=''):
        with self.intake.db() as connection:
            connection.execute('UPDATE drops SET recognition=? WHERE id=?',
                               (json.dumps({'status': status, 'text': text, 'label': 'AI 描述，未核实',
                                            'model': 'synthetic vision', 'scope': '本张图片的视觉描述，未核实'},
                                           ensure_ascii=False), drop_id))

    def test_recognition_completion_re_analyses_with_the_new_text(self):
        self.intake.analysis_after_recognition = self.cards.request_analysis_after_recognition
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.assertEqual(self.state(drop_id)['status'], 'done')
        turns = len(self.calls)
        self.set_recognition(drop_id, 'succeeded', '红色方块上压着一行白色字幕')
        queued = self.cards.request_analysis_after_recognition(drop_id)
        self.assertIn(queued['status'], ('queued', 'running'))
        state = self.state(drop_id)
        self.assertEqual(state['status'], 'done')
        # 自动补一次分析同样最多两次模型调用：一次四块阅读、一次候选卡。
        self.assertLessEqual(len(self.calls) - turns, 2)
        reading = [call['prompt'] for call in self.calls if call['identity'].endswith('-read')][-1]
        self.assertIn('红色方块上压着一行白色字幕', reading)

    def test_recognition_completion_reuses_an_unchanged_result_without_a_model_call(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.state(drop_id)
        turns = len(self.calls)
        state = self.cards.request_analysis_after_recognition(drop_id)
        self.assertEqual(state['status'], 'done')
        self.settle()
        self.assertEqual(len(self.calls), turns)

    def test_recognition_completion_does_not_disturb_an_analysis_already_running(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.settle()
        self.set_recognition(drop_id, 'succeeded', '新的识别正文')
        self.cards.request_analysis(drop_id, force=True)
        second = self.cards.request_analysis_after_recognition(drop_id)
        self.assertIn(second['status'], ('queued', 'running'))
        self.assertEqual(len(self.cards.analysis_workers), 1)
        self.settle()
        self.assertEqual(len(self.calls), 4)

    def test_recognition_completion_on_an_unfiled_drop_is_refused(self):
        drop_id = self.drop_link()   # 只收下，未解析
        with self.assertRaisesRegex(ValueError, '还没有存入知识库'):
            self.cards.request_analysis_after_recognition(drop_id)

    # ── 模型繁忙 ───────────────────────────────────────────────────────────────

    def test_model_busy_is_queued_then_a_retryable_failure_never_silent(self):
        statuses = []
        original = self.cards._write_analysis

        def spy(drop, status, **fields):
            statuses.append(status)
            return original(drop, status, **fields)
        self.cards._write_analysis = spy
        self.cards.busy_probe = lambda config: True
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': '这个开头方式可以借'})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.cards._write_analysis = original
        self.assertEqual((state['status'], state['retryable']), ('failed', True))
        self.assertIn('queued', statuses)
        self.assertIn('模型', state['error'])
        self.assertEqual(state['attempts'], 2)
        # 一次模型调用都没发生，也没有假装分析完成。
        self.assertEqual(self.calls, [])
        self.assertEqual(state['summary'], '')

    def test_model_busy_frees_up_and_the_retry_finishes(self):
        busy = {'now': True}
        self.cards.busy_probe = lambda config: busy['now']
        self.cards.analysis_retry_wait = 0.3
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': '这个开头方式可以借'})
        self.intake.resolve(drop['id'])
        release = threading.Timer(0.2, lambda: busy.update(now=False))
        release.start()
        state = self.cards.analyze_drop(drop['id'])
        release.join(timeout=2)
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['attempts'], 2)
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'cards'])

    def test_the_scheduler_slot_is_the_one_being_checked(self):
        # busy_probe 收到的是要用的模型配置，接线才能判断该模型是否已被定时任务占用。
        seen = []
        self.cards.busy_probe = lambda config: seen.append(config) or False
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        self.cards.analyze_drop(drop['id'])
        self.assertEqual([entry['model'] for entry in seen], ['synthetic', 'synthetic'])

    def test_no_configured_model_is_an_honest_retryable_failure(self):
        self.cards.model_provider = lambda: None
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.assertEqual((state['status'], state['retryable']), ('failed', True))
        self.assertIn('未配置提炼模型', state['error'])
        self.assertEqual(self.calls, [])

    # ── 定时兜底与状态持久 ─────────────────────────────────────────────────────

    def test_pending_skips_drops_the_instant_analysis_already_finished(self):
        drop_id = self.drop_link()
        self.resolve(drop_id)
        self.settle()
        self.assertEqual(self.cards.pending()[1], 0)

    def test_pending_skips_a_drop_currently_being_analysed(self):
        gate = threading.Event()
        def slow(identity, prompt, system, cancel, config, deadline):
            self.calls.append({'identity': identity})
            gate.wait(timeout=5)
            return self.answers['read'] if identity.endswith('-read') else self.answers['cards']
        self.cards.call_model = slow
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        self.cards.request_analysis(drop['id'])
        deadline = time.time() + 5
        while time.time() < deadline and self.cards.analysis_states(
                [{'id': drop['id'], 'document_id': drop['document_id']}])[drop['id']]['status'] == 'queued':
            time.sleep(0.02)
        self.assertEqual(self.cards.pending()[1], 0)
        gate.set()
        self.settle()
        self.assertEqual(self.cards.pending()[1], 0)

    def test_pending_still_offers_a_drop_whose_analysis_failed(self):
        self.answers['read'] = ValueError('模型服务中断')
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        self.cards.analyze_drop(drop['id'])
        self.assertEqual(self.cards.pending()[1], 1)

    def test_a_restart_marks_an_unfinished_analysis_as_retryable(self):
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        self.cards._write_analysis(drop, 'running')
        reopened = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        state = reopened.analysis_states([{'id': drop['id'], 'document_id': drop['document_id']}])[drop['id']]
        self.assertEqual((state['status'], state['retryable']), ('failed', True))
        self.assertIn('重启', state['error'])

    def test_a_broken_hook_never_hides_the_recycle_list(self):
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        self.intake.analysis_provider = lambda rows: (_ for _ in ()).throw(ValueError('卡库读不到'))
        listing = self.intake.listing()
        self.assertEqual(len(listing['items']), 1)
        self.assertEqual(listing['items'][0]['analysis'], {})

    def test_a_placeholder_body_is_not_analysed_with_a_wasted_model_call(self):
        record = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        filed = self.intake.resolve(record['id'])
        with self.store.knowledge.db() as connection:
            connection.execute('UPDATE documents SET body=? WHERE id=?',
                               ('本机图片：shot.png，1024×768，PNG，2.0 KB。图片内容未识别。', filed['document_id']))
        state = self.cards.analyze_drop(record['id'])
        self.assertEqual((state['status'], state['retryable']), ('failed', True))
        self.assertIn('没有可分析的内容', state['error'])
        self.assertEqual(self.calls, [])

    def image_drop(self, name='pier-dusk.png', reason='这个取景可以借'):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (24, 16), 'blue').save(buffer, format='PNG')
        patch_intake_dependency(self, "ffprobe", lambda: None)
        saved = self.intake.receive_file('image', name, reason, io.BytesIO(buffer.getvalue()),
                                         len(buffer.getvalue()))
        self.intake.resolve(saved['id'])
        deadline = time.time() + 5
        while time.time() < deadline:
            row = self.intake.detail(saved['id'])
            if row['recognition'].get('status') not in ('queued', 'running'):
                return row
            time.sleep(0.02)
        self.fail('识别后台任务没结束')

    def test_a_failed_image_recognition_still_analyses_from_reason_and_file_name(self):
        # 合同③：图片识别不成（没接上模型）时，分析照常用收藏理由 + 文件名进行，不再卡在「识别完再分析」。
        row = self.image_drop()
        self.assertEqual(row['recognition']['status'], 'unavailable')
        self.assertIn('图片内容未识别', row['state'])
        state = self.cards.analyze_drop(row['id'])
        self.assertEqual(state['status'], 'done')
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'cards'])
        material = self.calls[0]['prompt']
        self.assertIn('这个取景可以借', material)
        self.assertIn('pier-dusk.png', material)
        self.assertIn('图片内容未识别', material)

    def test_a_file_name_alone_is_still_material_for_the_analysis(self):
        # 没写收藏理由也不永远卡住：文件名 + 「图片内容未识别」这句事实就是可分析的资料。
        row = self.image_drop(reason='')
        state = self.cards.analyze_drop(row['id'])
        self.assertEqual(state['status'], 'done')
        self.assertIn('pier-dusk.png', self.calls[0]['prompt'])

    def test_unusable_model_output_gets_one_repair_and_stays_a_failed_run(self):
        # 第一次回答不可用：同一次分析里只补一次格式重写，不超两次模型调用上限。
        self.answers['read'] = '这不是 JSON'
        self.answers['repair'] = '这还是不是 JSON'
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.assertEqual((state['status'], state['retryable']), ('failed', True))
        self.assertIn('按格式重写后仍不可用', state['error'])
        self.assertEqual(self.cards.listing()['counts'], {'candidate': 0, 'confirmed': 0, 'rejected': 0})
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'repair'])

    def test_a_repair_turn_saves_a_run_that_failed_parsing(self):
        self.answers['read'] = '这不是 JSON'
        self.answers['repair'] = payload(READING)
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.assertEqual(state['status'], 'done')
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'repair'])
        self.assertEqual(state['summary'], READING['summary'])
        # 两次调用已用完：提示词卡留给定时提炼，不谎报成没内容。
        self.assertEqual(state['cards'], 0)
        self.assertIn('两次模型调用', state['cards_error'])
        self.assertEqual(self.cards.pending()[1], 1)

    def test_a_repair_turn_only_asks_for_the_format_and_keeps_the_answer(self):
        self.answers['read'] = payload({'summary': '只有一句摘要。'})
        self.answers['repair'] = payload(READING)
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.assertEqual(state['status'], 'done')
        # 缺 why/angles 不判失败，所以不会花第二次调用。
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'cards'])
        self.assertEqual(state['summary'], '只有一句摘要。')

    def test_tolerant_parsing_keeps_what_the_model_gave_and_only_needs_a_summary(self):
        # round5 的 NFT_Chen：角度写成字符串、多给几个、缺 how、多加字段 —— 都不能整条失败。
        cases = (
            # 角度写成字符串
            (payload({'summary': '摘要。', 'why': '理由。', 'angles': ['写成一条拍摄笔记']}),
             [{'title': '写成一条拍摄笔记', 'how': ''}]),
            # 五个角度：只留前四个
            (payload({'summary': '摘要。', 'angles': [{'title': '角度%d' % n} for n in range(5)]}),
             [{'title': '角度%d' % n, 'how': ''} for n in range(4)]),
            # 缺 how、多余字段
            (payload({'summary': '摘要。', 'angles': [{'title': '只有标题', 'note': '多余的字段'}]}),
             [{'title': '只有标题', 'how': ''}]),
            # 多余顶层字段、points 不是列表
            (payload({'summary': '摘要。', 'points': '不是列表', 'cards': [], 'extra': 1,
                      'angles': [{'title': '角度', 'how': '写法'}]}),
             [{'title': '角度', 'how': '写法'}]),
        )
        for answer, angles in cases:
            with self.subTest(answer=answer[:40]):
                reading = self.cards.parse_analysis(answer)
                self.assertEqual(reading['angles'], angles)
                self.assertEqual(reading['summary'], '摘要。')

    def test_tolerant_parsing_reports_what_it_dropped_instead_of_hiding_it(self):
        reading = self.cards.parse_analysis(payload({'summary': '摘要。', 'points': ['一条要点', '', 7],
                                                     'angles': ['一条角度', 5, {'title': '', 'how': ''}]}))
        self.assertEqual(reading['points'], ['一条要点'])
        self.assertEqual(reading['angles'], [{'title': '一条角度', 'how': ''}])
        self.assertIn('要点 2 条不合要求已忽略', reading['parse_notes'])
        self.assertIn('可写角度 2 条不合要求已忽略', reading['parse_notes'])

    def test_only_a_missing_summary_makes_the_reading_fail(self):
        for answer, marker in ((payload({'why': '有理由没摘要'}), '摘要'),
                               (payload({'summary': '   ', 'why': '理由'}), '摘要'),
                               ('这不是 JSON', 'JSON')):
            with self.subTest(marker=marker):
                with self.assertRaises(ValueError) as caught:
                    self.cards.parse_analysis(answer)
                self.assertIn(marker, str(caught.exception))

    def test_a_busy_card_turn_stops_at_one_call_and_leaves_the_reading(self):
        # 卡片那一段撞上模型忙：不重新跑整条，保证一次分折最多两次模型调用。
        def selective(identity, prompt, system, cancel, config, deadline):
            self.calls.append({'identity': identity})
            if identity.endswith('-cards'):
                raise self.cards.model_busy_error()
            return self.answers['read']
        self.cards.call_model = selective
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.assertEqual(state['status'], 'done')
        self.assertEqual([call['identity'].rsplit('-', 1)[1] for call in self.calls], ['read', 'cards'])
        self.assertIn('模型', state['cards_error'])
        self.assertEqual(state['summary'], READING['summary'])
        self.assertEqual(self.cards.pending()[1], 1)

    def test_a_card_turn_failure_keeps_the_reading_and_leaves_cards_to_the_schedule(self):
        self.answers['cards'] = ValueError('模型未返回有效正文，本条未推进')
        drop = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': ''})
        self.intake.resolve(drop['id'])
        state = self.cards.analyze_drop(drop['id'])
        self.assertEqual(state['status'], 'done')
        self.assertIn('模型未返回有效正文', state['cards_error'])
        self.assertEqual(state['cards'], 0)
        # 卡片这一段没跑完，所以定时兜底还会看到这条。
        self.assertEqual(self.cards.pending()[1], 1)

    def test_a_drop_deleted_under_the_analysis_call_answers_with_a_reason(self):
        # 低-1：分析状态读不到要说「回收项已删除」，不能让接口抛未捕获异常。
        key = self.drop_link()
        self.resolve(key)
        self.settle()
        self.cards._analysis_row = lambda drop_id: None      # 模拟写入提交后行被并发删掉
        code, body = self.api('/api/intake/%s/analyze' % key, {})
        self.assertEqual(code, 400)
        self.assertIn('回收项已删除', body['error'])


class WiringTests(unittest.TestCase):
    def test_the_model_comes_from_the_creative_extraction_task(self):
        from server import wire_intake_analysis

        class Schedules:
            def listing(self):
                return {'tasks': [{'kind': 'intake_cards', 'profile_id': 'p1'},
                                  {'kind': 'wechat_radar', 'profile_id': 'p2'}]}

            def model_config(self, identity):
                return {'model': 'chosen', 'endpoint': 'https://x/v1/chat/completions'}

            def model_in_use(self, identity):
                return identity == 'p1'

        class Server:
            schedules = Schedules()
            assistant = None

        class Holder:
            intake_cards = IntakeCards(None, None, tempfile.mkdtemp())

        cards = wire_intake_analysis(Server(), Holder())
        self.assertEqual(cards.model_provider()['model'], 'chosen')
        self.assertEqual(cards.model_provider()['profile_id'], 'p1')
        self.assertTrue(cards.busy_probe({'profile_id': 'p1'}))
        self.assertFalse(cards.busy_probe({'profile_id': 'p2'}))
        self.assertFalse(cards.busy_probe(None))

    def test_without_any_configured_model_the_state_says_so(self):
        from server import wire_intake_analysis

        class Schedules:
            def listing(self):
                raise ValueError('任务库读不到')

        class Server:
            schedules = Schedules()
            assistant = None

        class Holder:
            intake_cards = IntakeCards(None, None, tempfile.mkdtemp())

        self.assertIsNone(wire_intake_analysis(Server(), Holder()).model_provider())


if __name__ == '__main__':
    unittest.main()
