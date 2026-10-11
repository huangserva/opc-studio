"""贴文改为服务端照原文插入。临时工作区、假模型，不调用真实模型，不发外网。"""
import json
import time
import unittest
from pathlib import Path

from assistant_policy import policy_for
from tests.test_assistant import AssistantTests


ROOT = Path(__file__).resolve().parents[1]
# hygiene-4：原来这里是模块级 read_text()，而 coordination/round6-assets/ 不进公开仓库，
# 干净克隆里会在模块加载时就 FileNotFoundError 崩掉。改成用到时才读；
# 文件不存在就只跳过依赖它的用例，其余纯离线用例照跑。
PASTE_FIXTURE = ROOT / 'coordination/round6-assets/round6-assistant-text.txt'
PASTE_FIXTURE_AVAILABLE = PASTE_FIXTURE.is_file()


def paste_source():
    return PASTE_FIXTURE.read_text()


def paste_lines():
    source = paste_source()
    return [line.strip() for line in source.replace('\r\n', '\n').split('\n') if line.strip()]


PROMPT_PREFIX = '把下面贴文贴上，再补一句过渡。原文：'


class PasteOriginalTests(unittest.TestCase):
    setUp = AssistantTests.setUp
    tearDown = AssistantTests.tearDown
    def prepare(self, message, slug=None):
        slug = slug or self.slug
        self.job = {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False,
                    'attachments': [], 'prompt': message, 'policy': policy_for(message)}
        self.agent.execute('read_work', {'slug': slug}, self.job)
        return self.store.meta(slug)['head']

    def edit(self, operations, slug, version):
        return self.agent.execute('edit_work', {'slug': slug, 'base_version': version,
                                  'summary': '贴文v2', 'operations': operations}, self.job)

    @unittest.skipUnless(PASTE_FIXTURE_AVAILABLE,
                         '需要本机 fixture coordination/round6-assets/round6-assistant-text.txt；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_round6_paste_original_keeps_four_lines_and_compose_last(self):
        self.assertEqual(len(paste_lines()), 4)
        self.assertIn('第一段：', paste_lines()[1])
        created = self.store.create({'title': '贴文v2'})
        slug = created['slug']
        version = self.prepare(PROMPT_PREFIX + paste_source(), slug)
        saved = self.edit([
            {'op': 'insert_text', 'text_source': 'paste_original'},
            {'op': 'insert_text', 'text_source': 'compose', 'html': '<p>接下来继续观察。</p>'},
        ], slug, version)
        self.assertNotEqual(saved['version'], version)
        texts = [block['text'].strip() for block in self.store.detail(slug)['blocks']]
        self.assertEqual(texts, paste_lines() + ['接下来继续观察。'])
        self.assertIn('第一段：', texts[1])
        self.assertEqual(self.store.detail(slug, version)['blocks'], [])

    @unittest.skipUnless(PASTE_FIXTURE_AVAILABLE,
                         '需要本机 fixture coordination/round6-assets/round6-assistant-text.txt；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_unclear_original_is_refused_without_saving(self):
        version = self.prepare('把这段贴文贴上，再补一句过渡。')
        with self.assertRaisesRegex(ValueError, '原文范围不清楚'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste_original'}], self.slug, version)
        self.assertEqual(self.store.meta(self.slug)['head'], version)

    @unittest.skipUnless(PASTE_FIXTURE_AVAILABLE,
                         '需要本机 fixture coordination/round6-assets/round6-assistant-text.txt；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_model_html_is_not_accepted_on_paste_original(self):
        version = self.prepare(PROMPT_PREFIX + paste_source())
        with self.assertRaisesRegex(ValueError, '不要写 html'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste_original',
                        'html': '<p>round6-20261010 助手贴文测试。</p>'}], self.slug, version)
        self.assertEqual(self.store.meta(self.slug)['head'], version)

    @unittest.skipUnless(PASTE_FIXTURE_AVAILABLE,
                         '需要本机 fixture coordination/round6-assets/round6-assistant-text.txt；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_old_paste_mismatch_names_the_gap_and_suggests_paste_original(self):
        version = self.prepare(PROMPT_PREFIX + paste_source())
        missing = '<p>' + '</p><p>'.join(paste_lines()[1:]) + '</p>'
        with self.assertRaisesRegex(ValueError, '缺少首行') as missing_head:
            self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': missing}], self.slug, version)
        self.assertIn('完整复制', str(missing_head.exception))
        self.assertIn('paste_original', str(missing_head.exception))
        self.assertIn('应插入原文 4 段', str(missing_head.exception))
        dropped = paste_lines()[1].replace('第一段：', '', 1)
        partial = '<p>' + '</p><p>'.join([paste_lines()[0], dropped, paste_lines()[2], paste_lines()[3]]) + '</p>'
        with self.assertRaisesRegex(ValueError, '段落前缀被删'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': partial}], self.slug, version)
        self.assertEqual(self.store.meta(self.slug)['head'], version)

    def wait_job(self, result):
        deadline = time.time() + 45
        while self.agent.active and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.agent.active)
        return self.agent.public_job(result['id'])

    def test_same_rejection_twice_stops_before_the_quota_with_a_chinese_reason(self):
        message = '把下面贴文贴上\n今天发布甲方案。'
        bad = {'slug': self.slug, 'base_version': 'v0002', 'summary': '抄写', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>贴文</p>'}]}
        calls = []

        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 3:
                raise AssertionError('同一原因已拒绝两次，不应再请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [{'id': 'read', 'type': 'function', 'function': {
                    'name': 'read_work', 'arguments': json.dumps({'slug': self.slug})}}]}
            return {'role': 'assistant', 'tool_calls': [{'id': 'edit' + str(len(calls)), 'type': 'function', 'function': {
                'name': 'edit_work', 'arguments': json.dumps(bad)}}]}

        self.agent.client = client
        job = self.wait_job(self.agent.start({'message': message, 'request_id': 'c' * 32}))
        self.assertEqual(len(calls), 3, job.get('answer'))
        self.assertIn('连续被拒', job['answer'])
        self.assertIn('paste_original', job['answer'])
        self.assertNotIn('模型没有返回回复正文', job['answer'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        edits = [action for action in job['actions'] if action['name'] == 'edit_work']
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(action['status'] == 'error' for action in edits))


if __name__ == '__main__':
    unittest.main()
