from tests.test_assistant_backend import wait_for_background
"""assistant-edit-insert: the assistant can insert real images and restructure a draft."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parents[1] / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)

from assistant_policy import policy_for
from publishing.platforms.x.post import prepare as prepare_x_post


def png(seed, size=(240, 160)):
    image = Image.new('RGB', size, (seed * 40 % 256, 90, 200 - seed * 15 % 200))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class FakeGeneration:
    """Stand-in for the 4090 service: only job metadata and a local result file."""

    def __init__(self, jobs):
        self.jobs = jobs

    def public(self, job_id):
        if job_id not in self.jobs:
            raise ValueError('生成任务不存在')
        return dict(self.jobs[job_id])

    def asset(self, job_id):
        return self.jobs[job_id]['path']


class EditInsertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = studio.Store(self.root / 'workspace')
        article = self.store.create({'title': 'Sidrune 串文', 'type': 'post'})
        self.slug = article['slug']
        self.store.save(self.slug, {'base_version': 'v0001', 'title': 'Sidrune 串文', 'blocks': [
            {'kind': 'text', 'html': '<p>第一条推文</p>'},
            {'kind': 'text', 'html': '<p>第二条推文</p>'},
            {'kind': 'text', 'html': '<p>第三条推文</p>'}]})
        config = self.root / 'model.json'
        config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                      'api_key': 'secret-test-value', 'model': 'test-model'}))
        self.agent = studio.assistant_module.Assistant(self.store, studio.content, config)
        self.job = {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False, 'attachments': [],
                    'policy': {'allowed_tools': ['read_work', 'edit_work', 'create_card'], 'allow_insert': True,
                               'edit_grants': ['insert_image', 'insert_text', 'delete_block', 'move_block', 'set_text']}}

    def tearDown(self):
        self.temp.cleanup()

    def grant(self, message):
        policy = policy_for(message)
        self.job['policy'] = policy
        return policy

    def turn(self, message):
        """A new user turn: fresh read state and this turn's own grants."""
        self.job = {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False,
                    'attachments': self.job['attachments'], 'policy': policy_for(message)}
        return self.job['policy']

    def attachment(self, name='邮件截图.png', seed=1):
        meta = self.agent.upload_chat_image({'data': base64.b64encode(png(seed)).decode(), 'name': name})
        self.job['attachments'].append(meta)
        return meta

    def blocks(self, version=None):
        return [block['kind'] for block in self.store.detail(self.slug, version)['blocks']]

    def x_posts(self):
        manifest, _, _ = self.store.info(self.slug)
        snapshot = {'studio': {'type': 'post'}, 'manifest': manifest, 'target': {'caption': '', 'topics': ''}, 'text': ''}
        # only the shape of each tweet matters here; the URLs are format-checked, never fetched
        urls = {item['file']: 'https://cdn.example.com/' + item['file'] for item in manifest['body_media']}
        return prepare_x_post(snapshot, {'channel_id': 'channel-test', 'media_urls': urls})['posts']

    def test_insert_chat_attachment_after_first_tweet(self):
        meta = self.attachment()
        self.grant('把邮件截图插到第一条后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '配邮件截图',
                                                 'operations': [{'op': 'insert_image', 'after': 0,
                                                                 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertIn('第 1 条推文后', result['summary'])
        self.assertIn('v0003', result['summary'])
        self.assertIn(self.slug, self.job['changed'])
        posts = self.x_posts()
        self.assertEqual([len(post['assets']) for post in posts], [1, 0, 0])

    def test_insert_after_third_tweet_and_image_belongs_to_third_tweet(self):
        meta = self.attachment('价格卡.png', seed=2)
        self.grant('把价格卡插到第三条推文后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '第三条配图',
                                                 'operations': [{'op': 'insert_image', 'after': 2,
                                                                 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        self.assertIn('第 3 条推文后', result['summary'])
        self.assertEqual(self.blocks(), ['text', 'text', 'text', 'image'])
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [0, 0, 1])

    def test_generation_job_and_saved_assistant_image_are_real_sources(self):
        job_folder = self.store.workspace / 'generation' / 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        job_folder.mkdir(parents=True)
        (job_folder / 'result.png').write_bytes(png(3))
        self.agent.generation = FakeGeneration({
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa': {'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'kind': 'image', 'status': 'done', 'model_name': 'Qwen Image 2.1',
                       'slug': self.slug, 'conversation_id': self.agent.conversation_id, 'path': job_folder / 'result.png'},
            'b'*32: {'id': 'b'*32, 'kind': 'image', 'status': 'running', 'model_name': 'Qwen Image 2.1',
                        'slug': self.slug, 'conversation_id': self.agent.conversation_id, 'path': job_folder / 'result.png'}})
        card = self.agent.execute('create_card', {'title': 'Sidrune 价格', 'subtitle': '每月 9.9', 'theme': 'blue'}, self.job)
        card_id = card['image_url'].rsplit('/', 1)[1][:-4]
        self.grant('把生成的图和助手图片插到第一条后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        # an unfinished generation job is refused and nothing is written
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '再插一张',
                                             'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'generation_job', 'id': 'b'*32}}]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '两条来源配图',
                                                 'operations': [
                                                     {'op': 'insert_image', 'after': 0, 'source': {'kind': 'generation_job', 'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}},
                                                     {'op': 'insert_image', 'after': 0, 'source': {'kind': 'assistant_image', 'id': card_id}}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'image', 'image', 'text', 'text'])
        self.assertEqual(len(result['placements']), 2)
        self.assertTrue(all('第 1 条推文后' in note for note in result['placements']))
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [2, 0, 0])

    def test_text_card_inserts_at_the_requested_position(self):
        self.grant('生成一张文字封面，然后插入当前作品')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('create_card', {'title': 'Sidrune', 'subtitle': 'AI 邮件助手', 'theme': 'blue',
                                                    'slug': self.slug, 'base_version': 'v0002', 'insert': True, 'after': 0}, self.job)
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertEqual(result['version'], 'v0003')
        self.assertIn('第 1 条推文后', result['summary'])
        self.assertIn('v0003', result['summary'])

    def test_insert_text_in_the_middle_delete_and_move(self):
        meta = self.attachment('截图.png', seed=4)
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        inserted = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '加图和文字',
                                                    'operations': [{'op': 'insert_image', 'after': 2, 'source': {'kind': 'chat_attachment', 'id': meta['id']}},
                                                                   {'op': 'insert_text', 'after': 0, 'html': '<p>补充说明</p>'}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'text', 'text', 'text', 'image'])
        self.assertEqual(self.store.detail(self.slug)['blocks'][1]['text'], '补充说明')
        self.assertIn('新增文字→第 1 条推文后（挂在第 1 条推文）', inserted['placements'])
        # the new sentence became tweet 2, so the picture now hangs on the last tweet
        self.assertTrue(any('图片附件' in note and '第 4 条推文后（挂在第 4 条推文）' in note for note in inserted['placements']))
        self.turn('删除第二段，再把图片移到第一条后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        moved = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0003', 'summary': '调整结构',
                                                 'operations': [{'op': 'delete_block', 'index': 1},
                                                                {'op': 'move_block', 'index': 4, 'after': 0},
                                                                {'op': 'set_text', 'index': 0, 'html': '<p>第一条改过</p>'}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertEqual(self.store.detail(self.slug)['blocks'][0]['text'], '第一条改过')
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [1, 0, 0])
        self.assertTrue(any('已把原第 5 个块移到第 1 条推文后' in note for note in moved['placements']))
        self.assertIn('已删除原第 2 个块（text）', moved['placements'])
        self.assertEqual(moved['version'], 'v0004')
        self.assertEqual(inserted['version'], 'v0003')

    def test_refused_without_this_turns_imperative(self):
        meta = self.attachment()
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        for message in ['这张截图是什么内容？', '我之前让你插的图片成功了吗', '只预览，不要插入图片', '你好']:
            policy = self.grant(message)
            self.assertNotIn('edit_work', policy['allowed_tools'], message)
            with self.assertRaises(ValueError):
                self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '插图',
                                                 'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        # an imperative grants one operation only: it never grants delete or move
        policy = self.grant('把邮件截图插到第一条后面')
        self.assertEqual(policy['edit_grants'], ['insert_image'])
        for operation in ({'op': 'delete_block', 'index': 0}, {'op': 'move_block', 'index': 0, 'after': 1}):
            with self.assertRaisesRegex(ValueError, '未授权'):
                self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '越权',
                                                 'operations': [operation]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        self.assertEqual(self.blocks(), ['text', 'text', 'text'])

    def test_stale_version_and_fabricated_source_are_refused(self):
        meta = self.attachment()
        self.grant('把邮件截图插到第一条后面')
        with self.assertRaises(ValueError):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '插图',
                                             'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        for source in ({'kind': 'chat_attachment', 'id': 'f' * 32}, {'kind': 'generation_job', 'id': 'a' * 32},
                       {'kind': 'assistant_image', 'id': 'b' * 32}, {'kind': 'local_file', 'id': meta['id']}):
            with self.assertRaises(ValueError):
                self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '插图',
                                                 'operations': [{'op': 'insert_image', 'after': 0, 'source': source}]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_four_images_per_tweet_is_refused_before_saving(self):
        metas = [self.attachment(f'截图{i}.png', seed=i + 1) for i in range(5)]
        self.grant('把截图插到第一条后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '插 4 张',
                                                 'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}} for meta in metas[:4]]}, self.job)
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [4, 0, 0])
        self.turn('把邮件截图插到第一条后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        with self.assertRaisesRegex(ValueError, '最多 4 张'):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0003', 'summary': '第 5 张',
                                             'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': metas[4]['id']}}]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')

    def test_video_and_gif_mixing_is_refused_by_the_same_rule(self):
        thread = {'type': 'post', 'video': {'file': 'media/clip.mp4'}}
        problems = self.agent.thread_problems(thread, [('text', False), ('image', False)])
        self.assertTrue(any('视频' in problem for problem in problems))
        problems = self.agent.thread_problems({'type': 'post'}, [('text', False), ('image', True), ('image', False)])
        self.assertTrue(any('GIF' in problem for problem in problems))
        self.assertEqual(self.agent.thread_problems({'type': 'post'}, [('text', False), ('image', True)]), [])

    def test_several_edits_in_one_turn_save_one_version_and_the_old_one_is_restorable(self):
        meta = self.attachment()
        self.grant('把邮件截图插到第一条后面，再删掉第二段，并加一段文字')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '一次改齐',
                                                 'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}},
                                                                {'op': 'delete_block', 'index': 2},
                                                                {'op': 'insert_text', 'after': 1, 'html': '<p>新增第二条</p>'}]}, self.job)
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertEqual(len(self.store.detail(self.slug)['versions']), 3)
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertEqual(self.store.detail(self.slug)['blocks'][3]['text'], '新增第二条')
        # a second write in the same turn is refused instead of stacking versions
        with self.assertRaisesRegex(ValueError, '本轮已保存新版本'):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0003', 'summary': '再改',
                                             'operations': [{'op': 'insert_text', 'after': 0, 'html': '<p>不该保存</p>'}]}, self.job)
        self.job['policy']['allowed_tools'].append('update_text')
        with self.assertRaisesRegex(ValueError, '本轮已保存新版本'):
            self.agent.execute('update_text', {'slug': self.slug, 'base_version': 'v0003', 'summary': '越权',
                                               'edits': [{'index': 0, 'html': '<p>不该保存</p>'}]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')
        self.assertEqual(len(self.store.detail(self.slug)['versions']), 3)
        # the pre-edit version is still readable and restorable
        self.assertEqual(self.blocks('v0002'), ['text', 'text', 'text'])
        self.assertEqual(self.store.detail(self.slug, 'v0002')['blocks'][2]['text'], '第三条推文')
        restored = self.store.restore(self.slug, {'base_version': 'v0003', 'version': 'v0002'})
        self.assertEqual(self.blocks(restored['version']), ['text', 'text', 'text'])

    def picture_on_the_second_tweet(self):
        """Acceptance layout: three texts with test picture A hanging on tweet 2."""
        meta = self.attachment('测试图 A.png', seed=6)
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        setup = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '图 A 挂第二条',
                                                 'operations': [{'op': 'insert_image', 'after': 1,
                                                                 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'text', 'image', 'text'])
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [0, 1, 0])
        return setup['version']

    def test_move_back_words_grant_only_the_move(self):
        acceptance = '把它移回第一条后面。这里的它是上一轮从第一条移到第二条的测试图 A，其他内容不变。'
        for message in [acceptance, '把它移回第一条后面。', '把图挪回第一条', '把截图放回第二条后面', '把卡片调回第一条', '把图片换回第一条后面']:
            policy = policy_for(message)
            self.assertEqual(policy['edit_grants'], ['move_block'], message)
            self.assertIn('edit_work', policy['allowed_tools'], message)
            self.assertFalse(policy['allow_insert'], message)
        # “放到” stays ungranted on purpose: it can mean insert or move
        self.assertEqual(policy_for('把图放到第一条后面')['edit_grants'], [])
        # a collection request is never a block move
        self.assertNotIn('edit_work', policy_for('把这篇放回合集')['allowed_tools'])

    def test_the_acceptance_sentence_moves_the_picture_back_and_saves_a_version(self):
        version = self.picture_on_the_second_tweet()
        policy = self.turn('把它移回第一条后面。这里的它是上一轮从第一条移到第二条的测试图 A，其他内容不变。')
        self.assertEqual(policy['edit_grants'], ['move_block'])
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': version, 'summary': '图 A 移回第一条',
                                                 'operations': [{'op': 'move_block', 'index': 2, 'after': 0}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [1, 0, 0])
        self.assertEqual(result['version'], 'v0004')   # v0003 was the setup insert
        self.assertTrue(any('已把原第 3 个块移到第 1 条推文后' in note for note in result['placements']))
        self.assertEqual([block['text'] for block in self.store.detail(self.slug)['blocks'] if block['kind'] == 'text'],
                         ['第一条推文', '第二条推文', '第三条推文'])

    def test_question_negation_and_preview_still_refuse_the_move_back(self):
        version = self.picture_on_the_second_tweet()
        for message in ['能不能把图移回第一条？', '先别移回', '不要移回第一条', '只预览，把图移回第一条后面',
                        '他说“把图移回第一条”', '把图移回第一条后面好吗？', '上次移回成功了吗']:
            policy = self.turn(message)
            self.assertEqual(policy['edit_grants'], [], message)
            self.assertNotIn('edit_work', policy['allowed_tools'], message)
            self.agent.execute('read_work', {'slug': self.slug}, self.job)
            with self.assertRaises(ValueError):
                self.agent.execute('edit_work', {'slug': self.slug, 'base_version': version, 'summary': '移回',
                                                 'operations': [{'op': 'move_block', 'index': 2, 'after': 0}]}, self.job)
            self.assertEqual(self.store.meta(self.slug)['head'], version, message)
            self.assertEqual([len(post['assets']) for post in self.x_posts()], [0, 1, 0], message)

    def test_rewriting_and_moving_the_same_block_applies_the_new_text(self):
        self.turn('把第三条移到第一条后面，把第三条改成新的文字')
        self.assertEqual(set(self.job['policy']['edit_grants']), {'move_block', 'set_text'})
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '改写并移动',
                                                 'operations': [{'op': 'set_text', 'index': 2, 'html': '<p>改过的第三条</p>'},
                                                                {'op': 'move_block', 'index': 2, 'after': 0}]}, self.job)
        self.assertEqual([block['text'] for block in self.store.detail(self.slug)['blocks']],
                         ['第一条推文', '改过的第三条', '第二条推文'])
        self.assertEqual(result['version'], 'v0003')

    def test_full_turn_through_the_agent_loop_inserts_and_reports_the_version(self):
        meta = self.attachment()
        calls = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'read', 'type': 'function', 'function': {'name': 'read_work', 'arguments': json.dumps({'slug': self.slug})}}]},
                 {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'edit', 'type': 'function', 'function': {'name': 'edit_work', 'arguments': json.dumps(
                     {'slug': self.slug, 'base_version': 'v0002', 'summary': '配邮件截图',
                      'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]})}}]},
                 {'role': 'assistant', 'content': '已把邮件截图插到第 1 条推文后，保存为 v0003。'}]
        self.agent.client = lambda messages, **kwargs: calls.pop(0)
        started = self.agent.start({'message': '把邮件截图插到第一条后面', 'request_id': 'c' * 32, 'attachments': [meta['id']],
                                    'context': {'slug': self.slug, 'version': 'v0002'}})
        wait_for_background(self, self.agent, started['id'])
        job = self.agent.public_job(started['id'])
        self.assertEqual(job['status'], 'done', job.get('answer'))
        self.assertEqual([action['name'] for action in job['actions']], ['read_work', 'edit_work'])
        action = job['actions'][1]
        self.assertEqual(action['status'], 'done')
        self.assertEqual(action['version'], 'v0003')
        self.assertIn('第 1 条推文后', action['result'])
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.assertEqual([len(post['assets']) for post in self.x_posts()], [1, 0, 0])

    def test_existing_tools_keep_working(self):
        self.grant('帮我润色第一段')
        self.job['policy'] = {'allowed_tools': ['read_work', 'update_text', 'create_card'], 'allow_insert': True}
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': 'v0002', 'summary': '润色',
                                                    'edits': [{'index': 0, 'html': '<p>第一条润色过</p>'}], 'append_text': ['<p>追加段落</p>']}, self.job)
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual(self.blocks(), ['text', 'text', 'text', 'text'])
        self.assertEqual(self.store.detail(self.slug)['blocks'][0]['text'], '第一条润色过')
        self.turn('生成一张文字封面，然后插入当前作品')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        card = self.agent.execute('create_card', {'title': '末尾卡片', 'slug': self.slug, 'base_version': 'v0003', 'insert': True}, self.job)
        self.assertEqual(self.blocks(), ['text', 'text', 'text', 'text', 'image'])
        self.assertIn('保存 v0004', card['summary'])

    def test_remove_image_paste_and_drop_are_separate_grants(self):
        for message in ['去图', '帮我去图', '去掉图片', '把图片去掉', '删掉这张图', '去掉封面']:
            policy = policy_for(message)
            self.assertEqual(policy['edit_grants'], ['delete_block'], message)
            self.assertEqual(policy['edit_targets']['delete_block'], ['image'], message)
            self.assertNotIn('text_source', policy, message)
        for message in ['删除第二段', '删掉这段文字']:
            policy = policy_for(message)
            self.assertIn('delete_block', policy['edit_grants'], message)
            self.assertEqual(policy['edit_targets']['delete_block'], ['text'], message)
        self.assertEqual(policy_for('删除这个块')['edit_targets']['delete_block'], ['image', 'text'])
        pasted = policy_for('把这段贴文贴到第一条后面')
        self.assertEqual(pasted['edit_grants'], ['insert_text'])
        self.assertEqual(pasted['text_source'], 'user_paste')
        self.assertEqual(policy_for('贴文')['text_source'], 'user_paste')
        self.assertNotIn('composed_insert', pasted)
        self.assertNotIn('text_source', policy_for('贴一段文字'))
        self.assertTrue(policy_for('贴一段文字')['composed_insert'])
        self.assertEqual(policy_for('贴一段文字')['edit_grants'], ['insert_text'])
        mixed = policy_for('把下面贴文贴上，再补一句过渡')
        self.assertEqual(mixed['text_source'], 'user_paste')
        self.assertTrue(mixed['composed_insert'])
        self.assertEqual(mixed['edit_grants'], ['insert_text'])
        added = policy_for('加一句过渡')
        self.assertNotIn('text_source', added)
        self.assertTrue(added['composed_insert'])
        for message in ['丢图', '丢一张图到第一条后面', '贴一张图', '把图丢到第一条后面']:
            policy = policy_for(message)
            self.assertEqual(policy['edit_grants'], ['insert_image'], message)
            self.assertNotIn('text_source', policy, message)
        writes = {'update_text', 'organize_collection', 'create_card', 'generate_media', 'edit_work',
                  'remio_create_note', 'remio_update_note', 'memos_write'}
        for message in ['给仓库', '把这篇放进知识库', '存到资料库', '不要去图', '去图吗', '去图是什么意思',
                        '不要贴文', '他说“去图”', '只预览，去掉图片']:
            self.assertFalse(set(policy_for(message)['allowed_tools']) & writes, message)

    def test_remove_image_refuses_a_text_block_and_deletes_the_picture(self):
        meta = self.attachment()
        self.grant('把邮件截图插到第一条后面')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '先配图',
                                         'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text'])
        self.turn('去图')
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        with self.assertRaisesRegex(ValueError, '只授权删除图片'):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0003', 'summary': '误删文字',
                                             'operations': [{'op': 'delete_block', 'index': 0}]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0003', 'summary': '去掉图片',
                                                 'operations': [{'op': 'delete_block', 'index': 1}]}, self.job)
        self.assertEqual(self.blocks(), ['text', 'text', 'text'])
        self.assertEqual(result['version'], 'v0004')
        self.assertEqual(self.job['policy']['edit_grants'], ['delete_block'])

    def test_pasted_post_must_quote_this_message_and_a_dropped_image_keeps_its_id(self):
        sentence = '贴文原文：Sidrune 这周只发这一条。'
        self.turn('把这段贴文贴到第一条后面。' + sentence)
        self.job['prompt'] = '把这段贴文贴到第一条后面。' + sentence
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        with self.assertRaisesRegex(ValueError, '不能改写或编造'):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '贴文',
                                             'operations': [{'op': 'insert_text', 'after': 0, 'html': '<p>模型另写的一句</p>'}]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '贴文',
                                                 'operations': [{'op': 'insert_text', 'after': 0, 'html': '<p>' + sentence + '</p>'}]}, self.job)
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual(self.store.detail(self.slug)['blocks'][1]['text'], sentence)
        meta = self.attachment('丢进来的图.png', seed=7)
        self.turn('丢一张图到第一条后面')
        self.assertEqual(self.job['policy']['edit_grants'], ['insert_image'])
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        dropped = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0003', 'summary': '丢图',
                                                  'operations': [{'op': 'insert_image', 'after': 0, 'source': {'kind': 'chat_attachment', 'id': meta['id']}}]}, self.job)
        self.assertEqual(dropped['version'], 'v0004')
        self.assertEqual(self.blocks(), ['text', 'image', 'text', 'text', 'text'])
        self.assertNotIn('delete_block', self.job['policy']['edit_grants'])

    def test_mixed_paste_and_rewrite_keeps_the_verbatim_insert(self):
        sentence = '贴文原文：Sidrune 这周只发这一条。'
        message = '把下面贴文贴上，再补一句过渡。' + sentence
        self.turn(message)
        self.job['prompt'] = message
        self.assertEqual(self.job['policy']['text_source'], 'user_paste')
        self.assertTrue(self.job['policy']['composed_insert'])
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        with self.assertRaisesRegex(ValueError, '不能改写或编造'):
            self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '只改写',
                                             'operations': [
                                                 {'op': 'insert_text', 'text_source': 'compose', 'after': 0, 'html': '<p>模型另写的过渡</p>'},
                                                 {'op': 'insert_text', 'text_source': 'compose', 'after': 0, 'html': '<p>另一句也不是原文</p>'},
                                             ]}, self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002', 'summary': '贴文并补一句',
                                                 'operations': [
                                                     {'op': 'insert_text', 'text_source': 'compose', 'after': 0, 'html': '<p>模型另写的过渡</p>'},
                                                     {'op': 'insert_text', 'text_source': 'paste', 'after': 0, 'html': '<p>' + sentence + '</p>'},
                                                 ]}, self.job)
        self.assertEqual(result['version'], 'v0003')
        texts = [block['text'] for block in self.store.detail(self.slug)['blocks']]
        self.assertIn(sentence, texts)
        self.assertIn('模型另写的过渡', texts)


if __name__ == '__main__':
    unittest.main()
