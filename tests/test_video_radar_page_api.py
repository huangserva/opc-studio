"""C2 视频榜页面后端单测：只按 coordination/video-radar-schema.md 造夹具。

不联网、不起服务、不碰正式 workspace；只用临时目录。
"""
import importlib.util
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('video_radar_page_api', ROOT / 'video_radar_page_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def iso(moment):
    return moment.isoformat()


class Fixture:
    """一个符合 schema v1 的 page.json 生成器。"""

    def __init__(self, root):
        self.dir = Path(root) / 'video-radar'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.page = self.dir / 'page.json'
        self.feedback = self.dir / 'feedback.json'

    def item(self, **over):
        base = {
            'key': 'xhs:1001', 'platform': 'xiaohongshu', 'id': '1001',
            'sources': ['xhs_latest'], 'title': '示例标题', 'url': 'https://www.xiaohongshu.com/explore/1001',
            'author': '作者甲', 'author_fans': 1200, 'published_at': iso(NOW - timedelta(hours=3)),
            'first_seen_at': iso(NOW - timedelta(hours=3)), 'last_seen_at': iso(NOW - timedelta(minutes=5)),
            'likes': 880, 'collects': 40, 'comments': 12, 'shares': 3, 'plays': None,
            'likes_per_hour': 293.3, 'likes_delta_per_hour': 120.0, 'score': 88.8,
            'relevance': 'relevant', 'relevance_reason': '关键词命中', 'flags': ['low_fan_hot'], 'cover': '',
        }
        base.update(over)
        return base

    def page_json(self, **over):
        data = {
            'schema': 'video-radar-page/v1',
            'generated_at': iso(NOW - timedelta(minutes=4)),
            'keyword': 'AI',
            'sources': {
                'xiaohongshu': {'last_attempt_at': iso(NOW - timedelta(minutes=4)),
                                'last_success_at': iso(NOW - timedelta(minutes=10)), 'status': 'ok', 'note': ''},
                'douyin_realtime': {'last_attempt_at': iso(NOW - timedelta(minutes=4)),
                                    'last_success_at': iso(NOW - timedelta(minutes=9)), 'status': 'ok', 'note': ''},
                'douyin_daily': {'last_attempt_at': iso(NOW - timedelta(hours=2)),
                                 'last_success_at': iso(NOW - timedelta(hours=2)), 'status': 'ok',
                                 'note': '数据约晚 1–2 天'},
            },
            'today': {'date': '2026-10-16', 'rounds': 7, 'xiaohongshu_ok': 7,
                      'douyin_realtime_ok': 6, 'douyin_daily_ok': 1, 'api_calls': 251},
            'lists': {
                'xiaohongshu': [self.item()],
                'douyin_realtime': [self.item(key='dy:2001', platform='douyin', id='2001',
                                              url='https://www.douyin.com/video/2001')],
                'douyin_daily': [self.item(key='dy:3001', platform='douyin', id='3001',
                                            url='https://www.douyin.com/video/3001')],
            },
        }
        data.update(over)
        return data

    def write(self, payload):
        if isinstance(payload, str):
            self.page.write_text(payload, encoding='utf-8')
        else:
            self.page.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


class PageViewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vrc2-view-', dir='/tmp'))
        self.fx = Fixture(self.root)

    def test_missing_file(self):
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'missing')
        self.assertEqual(view['lists'], {'xiaohongshu': [], 'douyin_realtime': [], 'douyin_daily': []})
        self.assertTrue(view['stale'])

    def test_corrupt_variants(self):
        cases = {
            '坏 JSON': '{ broken',
            'schema 不对': json.dumps({'schema': 'video-radar-page/v2', 'lists': {}}),
            '不是对象': '[]',
            '缺 lists': json.dumps({'schema': 'video-radar-page/v1'}),
            'lists 不是对象': json.dumps({'schema': 'video-radar-page/v1', 'lists': []}),
            '某个列表不是数组': json.dumps({'schema': 'video-radar-page/v1',
                                          'lists': {'xiaohongshu': 'nope'}}),
            '条目不是对象': json.dumps({'schema': 'video-radar-page/v1',
                                      'lists': {'xiaohongshu': ['oops']}}),
            '条目缺 key': json.dumps({'schema': 'video-radar-page/v1',
                                     'lists': {'xiaohongshu': [{'title': 'x'}]}}),
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                self.fx.write(raw)
                view = api.page_view(self.root, now=NOW)
                self.assertEqual(view['status'], 'corrupt', label)
                self.assertEqual(view['lists'], {'xiaohongshu': [], 'douyin_realtime': [], 'douyin_daily': []})

    def test_ok_shape(self):
        self.fx.write(self.fx.page_json())
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        self.assertEqual(view['keyword'], 'AI')
        self.assertEqual(view['today']['api_calls'], 251)
        self.assertEqual(len(view['lists']['xiaohongshu']), 1)
        item = view['lists']['xiaohongshu'][0]
        self.assertIsNone(item['feedback'])
        self.assertFalse(view['sources']['xiaohongshu']['stale'])
        self.assertFalse(view['stale'])

    def test_missing_lists_keys_become_empty(self):
        self.fx.write({'schema': 'video-radar-page/v1', 'generated_at': iso(NOW),
                       'sources': {}, 'today': {}, 'lists': {}})
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        self.assertEqual(view['lists'], {'xiaohongshu': [], 'douyin_realtime': [], 'douyin_daily': []})
        self.assertTrue(view['sources']['xiaohongshu']['stale'], '没有来源信息时一律算过期')

    def test_stale_boundaries(self):
        cases = [
            ('xiaohongshu', timedelta(minutes=69), False),
            ('xiaohongshu', timedelta(minutes=71), True),
            ('xiaohongshu', timedelta(minutes=70), False),      # 正好 70 分钟还不算过期
            ('douyin_realtime', timedelta(minutes=70), False),
            ('douyin_realtime', timedelta(minutes=70, seconds=1), True),
            ('douyin_daily', timedelta(hours=30), False),
            ('douyin_daily', timedelta(hours=30, minutes=1), True),
        ]
        for name, age, expect_stale in cases:
            with self.subTest(source=name, age=str(age)):
                data = self.fx.page_json()
                data['sources'] = {key: {'last_attempt_at': iso(NOW), 'last_success_at': iso(NOW),
                                         'status': 'ok', 'note': ''} for key in
                                   ('xiaohongshu', 'douyin_realtime', 'douyin_daily')}
                data['sources'][name]['last_success_at'] = iso(NOW - age)
                self.fx.write(data)
                view = api.page_view(self.root, now=NOW)
                self.assertEqual(view['sources'][name]['stale'], expect_stale)

    def test_empty_last_success_is_stale(self):
        data = self.fx.page_json()
        for key in ('xiaohongshu', 'douyin_realtime', 'douyin_daily'):
            data['sources'][key]['last_success_at'] = ''
        self.fx.write(data)
        view = api.page_view(self.root, now=NOW)
        self.assertTrue(all(view['sources'][key]['stale'] for key in view['sources']))
        self.assertTrue(view['stale'])

    def test_naive_timestamp_counts_as_stale(self):
        data = self.fx.page_json()
        data['sources']['xiaohongshu']['last_success_at'] = '2026-10-16T11:00:00'
        self.fx.write(data)
        view = api.page_view(self.root, now=NOW)
        self.assertTrue(view['sources']['xiaohongshu']['stale'], '无时区挂钟串不可信')

    def test_null_and_zero_are_distinct(self):
        data = self.fx.page_json()
        data['lists']['xiaohongshu'] = [
            self.fx.item(key='xhs:null', author_fans=None, plays=None, likes=0, comments=0, shares=None,
                      likes_per_hour=None, likes_delta_per_hour=0),
        ]
        self.fx.write(data)
        item = api.page_view(self.root, now=NOW)['lists']['xiaohongshu'][0]
        self.assertIsNone(item['author_fans'])
        self.assertIsNone(item['plays'])
        self.assertIsNone(item['shares'])
        self.assertIsNone(item['likes_per_hour'])
        self.assertEqual(item['likes'], 0)
        self.assertEqual(item['comments'], 0)
        self.assertEqual(item['likes_delta_per_hour'], 0)

    def test_non_numeric_strings_stay_unknown(self):
        """非数字字符串（含布尔）沿用原行为：收敛成 None，整包仍 ok。"""
        data = self.fx.page_json()
        data['lists']['xiaohongshu'] = [
            self.fx.item(key='xhs:weird', likes='abc', plays='', likes_per_hour='NaN-ish',
                      author_fans=True, score='12.5'),
        ]
        self.fx.write(data)
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        item = view['lists']['xiaohongshu'][0]
        self.assertIsNone(item['likes'])
        self.assertIsNone(item['plays'])
        self.assertIsNone(item['likes_per_hour'])
        self.assertIsNone(item['author_fans'], '布尔仍按未知处理')
        self.assertEqual(item['score'], 12.5, '数字字符串照常解析')

    def test_m1_non_finite_makes_whole_package_corrupt(self):
        """C2-fix1/M1：NaN / Infinity / -Infinity 出现在任何位置都整包判 corrupt。"""
        base = json.dumps(self.fx.page_json())
        substitutions = [
            ('"likes": 880', '"likes": NaN'),
            ('"score": 88.8', '"score": Infinity'),
            ('"api_calls": 251', '"api_calls": -Infinity'),
            ('"note": ""', '"note": NaN'),
            ('"rounds": 7', '"rounds": Infinity'),
        ]
        for old, new in substitutions:
            with self.subTest(replace=old + ' → ' + new):
                self.assertIn(old, base, '夹具结构变了')
                self.fx.write(base.replace(old, new, 1))
                view = api.page_view(self.root, now=NOW)
                self.assertEqual(view['status'], 'corrupt')
                self.assertEqual(view['lists'], {'xiaohongshu': [], 'douyin_realtime': [], 'douyin_daily': []})

    def test_m1_huge_exponent_becomes_inf_and_is_corrupt(self):
        """1e400 解析出来就是 inf，同样判 corrupt（不是合法数据）。"""
        self.fx.write(json.dumps(self.fx.page_json()).replace('"likes": 880', '"likes": 1e400'))
        self.assertEqual(api.page_view(self.root, now=NOW)['status'], 'corrupt')

    def test_m1_legal_numbers_unchanged(self):
        """合法数字与合法 null 行为不变。"""
        self.fx.write(self.fx.page_json())
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        item = view['lists']['xiaohongshu'][0]
        self.assertEqual(item['likes'], 880)
        self.assertEqual(item['score'], 88.8)
        self.assertIsNone(item['plays'])

    # ============ C2-fix2：整包有限数校验 + 异常类型可区分 ============

    def _inject(self, position, token):
        """把 token 精确注入到指定位置（用唯一占位符再替换，避免正则/缩进失配）。"""
        data = self.fx.page_json()
        mark = '__TOK__'
        setters = {
            'lists.xiaohongshu[0].likes': lambda d: d['lists']['xiaohongshu'][0].update(likes=mark),
            'lists.douyin_realtime[0].score': lambda d: d['lists']['douyin_realtime'][0].update(score=mark),
            'today.api_calls': lambda d: d['today'].update(api_calls=mark),
            'today.rounds': lambda d: d['today'].update(rounds=mark),
            'sources.xiaohongshu.note': lambda d: d['sources']['xiaohongshu'].update(note=mark),
            'sources.douyin_daily.last_success_at': lambda d: d['sources']['douyin_daily'].update(last_success_at=mark),
            'lists.douyin_daily[0].extra.a[0].b.c[0]': lambda d: d['lists']['douyin_daily'][0].update(extra={'a': [{'b': {'c': [mark]}}]}),
            'sources.xiaohongshu.extra.a[0].b.c[0]': lambda d: d['sources']['xiaohongshu'].update(extra={'a': [{'b': {'c': [mark]}}]}),
            'today.extra.a[0].b.c[0]': lambda d: d['today'].update(extra={'a': [{'b': {'c': [mark]}}]}),
            'lists.xiaohongshu[0].sources[0]': lambda d: d['lists']['xiaohongshu'][0].update(sources=[mark]),
        }
        setters[position](data)
        raw = json.dumps(data).replace('"' + mark + '"', token, 1)
        self.assertIn(token, raw, 'token 没注入进去：' + position)
        self.fx.write(raw)

    FIX2_POSITIONS = [
        'lists.xiaohongshu[0].likes', 'lists.douyin_realtime[0].score', 'today.api_calls', 'today.rounds',
        'sources.xiaohongshu.note', 'sources.douyin_daily.last_success_at',
        'lists.douyin_daily[0].extra.a[0].b.c[0]', 'sources.xiaohongshu.extra.a[0].b.c[0]',
        'today.extra.a[0].b.c[0]', 'lists.xiaohongshu[0].sources[0]',
    ]

    def test_fix2_every_token_at_every_position_is_corrupt(self):
        """C2-fix2/M1：5 个 token × 10 个位置（含未使用字段与深层嵌套）全部整包 corrupt。"""
        for token in ('NaN', 'Infinity', '-Infinity', '1e400', '-1e400'):
            for position in self.FIX2_POSITIONS:
                with self.subTest(token=token, position=position):
                    self._inject(position, token)
                    view = api.page_view(self.root, now=NOW)
                    self.assertEqual(view['status'], 'corrupt')
                    self.assertEqual(view['lists'],
                                     {'xiaohongshu': [], 'douyin_realtime': [], 'douyin_daily': []})

    def test_fix2_legal_values_not_rejected(self):
        """1e300 / -1e300 / 0 / 0.0 / null / 超长数字字符串照常展示。"""
        for label, literal in (('1e300', '1e300'), ('-1e300', '-1e300'), ('0', '0'),
                               ('0.0', '0.0'), ('null', 'null'),
                               ('超长数字字符串', '"999999999999999999999999999"')):
            with self.subTest(value=label):
                self._inject('lists.xiaohongshu[0].likes', literal)
                view = api.page_view(self.root, now=NOW)
                self.assertEqual(view['status'], 'ok', label)
        self._inject('lists.douyin_realtime[0].score', '1e300')
        self.assertEqual(api.page_view(self.root, now=NOW)['lists']['douyin_realtime'][0]['score'], 1e300)

    def test_fix2_error_text_has_no_nameerror(self):
        """L2：预期校验错误与意外异常可区分——文案里不得出现 NameError。"""
        for token in ('NaN', '1e400'):
            with self.subTest(token=token):
                self._inject('lists.xiaohongshu[0].likes', token)
                view = api.page_view(self.root, now=NOW)
                self.assertNotIn('NameError', (view.get('error') or ''))
                self.assertIn('非有限数', (view.get('error') or ''))

    def test_fix2_error_text_points_at_the_field(self):
        """有限性错误的文案要能直接定位到 page.json 里的字段（含深层与未使用字段）。"""
        for position in ('sources.xiaohongshu.note',
                         'lists.douyin_daily[0].extra.a[0].b.c[0]',
                         'today.extra.a[0].b.c[0]',
                         'lists.xiaohongshu[0].sources[0]',
                         'lists.xiaohongshu[0].likes'):
            with self.subTest(position=position):
                self._inject(position, '1e400')
                view = api.page_view(self.root, now=NOW)
                error = view.get('error') or ''
                self.assertEqual(view['status'], 'corrupt')
                self.assertIn(position, error, '文案没指出出错字段：' + error)
                self.assertIn('非有限数字面量', error)
                # 语法错与非有限是两回事，文案不能混
                self.assertNotIn('不是合法 JSON', error)

    def test_fix2_syntax_error_text_is_distinct(self):
        """真的语法错仍然说「不是合法 JSON」，两条错误路径不混淆。"""
        self.fx.write('{bad')
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'corrupt')
        self.assertIn('不是合法 JSON', view['error'])

    def test_fix2_defined_exception_is_used(self):
        """L2：抛的是已定义的 CorruptData，不是未定义类。"""
        self.assertTrue(hasattr(api, 'CorruptData'), 'CorruptData 必须存在')
        for call in (lambda: api._count(float('inf')),
                     lambda: api._count(float('nan')),
                     lambda: api._rate(float('inf')),
                     lambda: api._rate(float('-inf'))):
            with self.assertRaises(api.CorruptData):
                call()
        # 布尔仍是未知，不抛
        self.assertIsNone(api._count(True))
        self.assertIsNone(api._count(None))

    def test_fix2_deep_nesting_does_not_recurse(self):
        """任意深度：深层嵌套里的非有限数也要判坏（用迭代遍历，不触发 RecursionError）。"""
        data = self.fx.page_json()
        node = 'leaf'
        for _ in range(2000):
            node = [node]
        data['today']['deep'] = node + [float('inf')]
        self.fx.write(data)
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'corrupt')
        self.assertNotIn('RecursionError', (view.get('error') or ''))

    def test_m2_long_external_text_is_not_truncated(self):
        """C2-fix1/M2：作者、理由、来源名、来源注释、标题一律保留原文（复核用 2110 字）。"""
        poison = ('<script>window.__pwned=1</script><img src=x onerror="window.__pwned=1">'
                  '\"><svg onload="window.__pwned=1">&amp;\n')
        long_text = poison + '题' * 2000
        self.assertEqual(len(long_text), 2110)
        data = self.fx.page_json()
        data['lists']['xiaohongshu'] = [self.fx.item(key='xhs:long', title=long_text, author=long_text,
                                                      relevance_reason=long_text, sources=[long_text],
                                                      cover=long_text)]
        data['sources']['xiaohongshu']['note'] = long_text
        self.fx.write(data)
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        item = view['lists']['xiaohongshu'][0]
        self.assertEqual(item['author'], long_text)
        self.assertEqual(item['relevance_reason'], long_text)
        self.assertEqual(item['sources'], [long_text])
        self.assertEqual(item['title'], long_text)
        self.assertEqual(item['cover'], long_text)
        self.assertEqual(view['sources']['xiaohongshu']['note'], long_text)
        # 反馈快照同样不再截断
        api.save_feedback(self.root, {'key': 'xhs:long', 'verdict': 'worth'}, now=NOW)
        stored = json.loads(self.fx.feedback.read_text(encoding='utf-8'))['xhs:long']
        self.assertEqual(stored['title'], long_text)

    def test_hostile_title_kept_verbatim(self):
        nasty = '</td></tr><script>window.__pwned=1</script><img src=x onerror="window.__pwned=1">'
        data = self.fx.page_json()
        data['lists']['xiaohongshu'] = [self.fx.item(key='xhs:evil', title=nasty)]
        self.fx.write(data)
        item = api.page_view(self.root, now=NOW)['lists']['xiaohongshu'][0]
        self.assertEqual(item['title'], nasty, '原文必须原样透出，转义交给页面')

    def test_hotboard_plays_and_fans(self):
        """2026-10-16 13:00 修订：抖音每小时主来源改为热点宝近 1 小时榜，播放/粉丝有真实值。"""
        data = self.fx.page_json()
        data['lists']['douyin_realtime'] = [
            self.fx.item(key='dy:board', platform='douyin', id='7300000000000000001',
                         url='https://www.douyin.com/video/7300000000000000001',
                         sources=['dy_board_video_1h'], plays=1280000, author_fans=45000),
            self.fx.item(key='dy:multi', platform='douyin', id='7300000000000000003',
                         sources=['dy_multi'], plays=None, author_fans=None),
        ]
        self.fx.write(data)
        rows = api.page_view(self.root, now=NOW)['lists']['douyin_realtime']
        self.assertEqual(rows[0]['plays'], 1280000)
        self.assertEqual(rows[0]['author_fans'], 45000)
        self.assertEqual(rows[0]['sources'], ['dy_board_video_1h'])
        self.assertIsNone(rows[1]['plays'], 'dy_multi 补充来源没有播放量，写 null 而不是 0')
        self.assertIsNone(rows[1]['author_fans'])

    def test_hotboard_url_shape_is_preserved_but_not_fixed_by_backend(self):
        """item_url 是带时效签名的文件地址：后端只原样透传，是否成链由页面白名单决定。"""
        signed = 'https://v3-web.douyinvod.com/video/tos/cn/tos-cn-ve-15c001/o8FzJf.mp4?a=6383&sign=deadbeef'
        data = self.fx.page_json()
        data['lists']['douyin_realtime'] = [self.fx.item(key='dy:signed', platform='douyin',
                                                          id='9', url=signed, sources=['dy_board_video_1h'])]
        self.fx.write(data)
        row = api.page_view(self.root, now=NOW)['lists']['douyin_realtime'][0]
        self.assertEqual(row['url'], signed, '后端不篡改 C1 给的 url，成链与否由页面判定')

    def test_unknown_fields_are_ignored(self):
        data = self.fx.page_json()
        data['lists']['xiaohongshu'] = [self.fx.item(key='xhs:extra', mystery_field='x', another=[1, 2])]
        self.fx.write(data)
        item = api.page_view(self.root, now=NOW)['lists']['xiaohongshu'][0]
        self.assertNotIn('mystery_field', item)
        self.assertNotIn('another', item)


    # C2-fix3：无引号整数在 Python 有限，在浏览器可能溢出。
    def _fix3_raw(self, position, literal):
        data = self.fx.page_json()
        data['extra'] = {'a': [{'b': {'c': ['__INT__']}}]}
        if position != 'extra.a[0].b.c[0]':
            self._inject(position, literal)
            return self.fx.page.read_text(encoding='utf-8')
        return json.dumps(data).replace('"__INT__"', literal)

    def test_fix3_int400_every_position_both_signs(self):
        positions = ('lists.xiaohongshu[0].likes', 'today.api_calls',
                     'lists.douyin_realtime[0].score', 'sources.xiaohongshu.note',
                     'extra.a[0].b.c[0]')
        for position in positions:
            for sign in ('', '-'):
                with self.subTest(position=position, sign=sign):
                    self.fx.write(self._fix3_raw(position, sign + '9' * 400))
                    view = api.page_view(self.root, now=NOW)
                    self.assertEqual(view['status'], 'corrupt')
                    self.assertEqual(view['lists'], {key: [] for key in api.LIST_KEYS})
                    self.assertIn(position, view['error'])
                    self.assertNotIn('OverflowError', view['error'])

    def test_fix3_browser_number_boundaries(self):
        for literal in ('9' * 309, '-' + '9' * 309):
            with self.subTest(literal=literal):
                self._inject('lists.xiaohongshu[0].likes', literal)
                self.assertEqual(api.page_view(self.root, now=NOW)['status'], 'corrupt')
        for literal in ('9' * 308, '-' + '9' * 308, '9' * 300, '1' + '0' * 308,
                        '1e300', '-1e300', '0', 'null', '-7', '9007199254740993'):
            with self.subTest(literal=literal):
                self._inject('lists.xiaohongshu[0].likes', literal)
                view = api.page_view(self.root, now=NOW)
                self.assertEqual(view['status'], 'ok')
                self.assertEqual(view['lists']['xiaohongshu'][0]['likes'], json.loads(literal))

    def test_fix3_direct_overflow_raises_corruptdata_with_path(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                with self.assertRaisesRegex(api.CorruptData, r'extra.a\[0\].b.c\[0\]'):
                    api._assert_all_finite({'extra': {'a': [{'b': {'c': [sign * int('9' * 400)]}}]}})
        for value in (int('9' * 308), -int('9' * 308), 9007199254740993, True, False, None):
            api._assert_all_finite({'extra': value})

    def test_fix3_deepest_leaf_checked_without_recursion(self):
        for literal, expected in (('0', 'ok'), ('9' * 400, 'corrupt')):
            with self.subTest(expected=expected):
                data = self.fx.page_json()
                data['extra'] = '__DEEP__'
                raw = json.dumps(data).replace('"__DEEP__"', '[' * 2000 + literal + ']' * 2000)
                self.fx.write(raw)
                view = api.page_view(self.root, now=NOW)
                self.assertEqual(view['status'], expected)
                self.assertNotIn('RecursionError', view.get('error', ''))


class SaveFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vrc2-fb-', dir='/tmp'))
        self.fx = Fixture(self.root)
        self.fx.write(self.fx.page_json())

    def stored(self):
        return json.loads(self.fx.feedback.read_text(encoding='utf-8'))

    def test_writes_snapshot_fields(self):
        record = api.save_feedback(self.root, {'key': 'xhs:1001', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(record['verdict'], 'worth')
        self.assertEqual(record['updated_at'], iso(NOW))
        self.assertEqual(record['title'], '示例标题')
        self.assertEqual(record['url'], 'https://www.xiaohongshu.com/explore/1001')
        self.assertEqual(record['platform'], 'xiaohongshu')

    def test_same_key_overwrites(self):
        api.save_feedback(self.root, {'key': 'xhs:1001', 'verdict': 'worth'}, now=NOW)
        api.save_feedback(self.root, {'key': 'xhs:1001', 'verdict': 'irrelevant'}, now=NOW)
        self.assertEqual(self.stored()['xhs:1001']['verdict'], 'irrelevant')
        self.assertEqual(len(self.stored()), 1)

    def test_feedback_appears_in_page_view(self):
        api.save_feedback(self.root, {'key': 'dy:2001', 'verdict': 'not_worth'}, now=NOW)
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['lists']['douyin_realtime'][0]['feedback'], 'not_worth')
        self.assertIsNone(view['lists']['xiaohongshu'][0]['feedback'])

    def test_rejects_bad_payloads(self):
        bad = [
            {'key': 'xhs:1001'},                                # 缺 verdict
            {'key': 'xhs:1001', 'verdict': 'worth', 'x': 1},     # 多余字段
            {'key': 'xhs:1001', 'verdict': 'WORTH'},
            {'key': 'xhs:1001', 'verdict': 'yes'},
            {'key': 'xhs:1001', 'verdict': None},
            {'key': 'xhs:1001', 'verdict': ['worth']},
            {'key': '', 'verdict': 'worth'},
            {'key': 1001, 'verdict': 'worth'},
            {'key': True, 'verdict': 'worth'},
            {'key': 'x' * 201, 'verdict': 'worth'},
            {'verdict': 'worth'},
            [],
            'xhs:1001',
            None,
        ]
        for payload in bad:
            with self.subTest(payload=repr(payload)[:40]):
                with self.assertRaises(ValueError):
                    api.save_feedback(self.root, payload, now=NOW)
        self.assertFalse(self.fx.feedback.exists(), '非法 payload 不能写出文件')

    def test_key_must_exist_in_current_page(self):
        with self.assertRaises(ValueError):
            api.save_feedback(self.root, {'key': 'xhs:9999', 'verdict': 'worth'}, now=NOW)
        self.assertFalse(self.fx.feedback.exists())

    def test_rejects_when_page_is_missing(self):
        self.fx.page.unlink()
        with self.assertRaises(ValueError):
            api.save_feedback(self.root, {'key': 'xhs:1001', 'verdict': 'worth'}, now=NOW)

    def test_corrupt_feedback_blocks_write(self):
        raw = '{"xhs:1001": {"verdict": "wo'
        self.fx.feedback.write_text(raw, encoding='utf-8')
        with self.assertRaises(api.FeedbackCorrupt):
            api.save_feedback(self.root, {'key': 'dy:2001', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(self.fx.feedback.read_text(encoding='utf-8'), raw, '原文件一字节不动')
        self.assertEqual([p.name for p in self.fx.dir.iterdir() if '.tmp-' in p.name], [])

    def test_corrupt_feedback_marks_status(self):
        self.fx.feedback.write_text('[]', encoding='utf-8')
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok', 'page.json 好就要正常返回')
        self.assertEqual(view['feedback_status'], 'invalid')
        self.assertEqual(view['feedback'], {})

    def test_duplicate_keys_are_corrupt(self):
        self.fx.feedback.write_text('{"xhs:1001": {"verdict": "worth"}, "xhs:1001": {"verdict": "worth"}}',
                                    encoding='utf-8')
        self.assertEqual(api.load_feedback(self.root)[1], 'invalid')

    def test_empty_object_is_writable(self):
        self.fx.feedback.write_text('{}', encoding='utf-8')
        api.save_feedback(self.root, {'key': 'xhs:1001', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(self.stored()['xhs:1001']['verdict'], 'worth')

    def test_concurrent_writes_all_land(self):
        errors = []

        def write(index):
            try:
                for _ in range(4):
                    api.save_feedback(self.root, {'key': 'dy:3001', 'verdict': 'worth'}, now=NOW)
                    api.save_feedback(self.root, {'key': 'dy:2001', 'verdict': 'not_worth'}, now=NOW)
            except Exception as exc:      # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.stored()), ['dy:2001', 'dy:3001'])
        self.assertEqual([p.name for p in self.fx.dir.iterdir() if '.tmp-' in p.name], [])

    def test_long_title_snapshot_is_verbatim(self):
        """C2-fix1/M2 之后：反馈快照也不再截断标题（整文件大小上限负责防超大）。"""
        long_title = 'x' * 6000
        data = self.fx.page_json()
        data['lists']['xiaohongshu'] = [self.fx.item(key='xhs:long', title=long_title)]
        self.fx.write(data)
        api.save_feedback(self.root, {'key': 'xhs:long', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(self.stored()['xhs:long']['title'], long_title)

    def test_page_file_over_size_limit_is_corrupt(self):
        """防超大文件靠整文件上限（不靠逐字段截断）。"""
        big = 'x' * (api.PAGE_MAX_BYTES + 1024)
        data = self.fx.page_json()
        data['keyword'] = big
        self.fx.write(data)
        view = api.page_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'corrupt')
        self.assertIn('大小上限', view['error'])


class WorkspaceResolutionTests(unittest.TestCase):
    def test_accepts_workspace_or_repo_root(self):
        base = Path(tempfile.mkdtemp(prefix='vrc2-res-', dir='/tmp'))
        (base / 'workspace' / 'video-radar').mkdir(parents=True)
        fx = Fixture(base / 'workspace')
        fx.write(fx.page_json())
        self.assertEqual(api.page_view(base / 'workspace', now=NOW)['status'], 'ok', '传 workspace 目录')
        self.assertEqual(api.page_view(base, now=NOW)['status'], 'ok', '传仓库根')


if __name__ == '__main__':
    unittest.main(verbosity=2)
