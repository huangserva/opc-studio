"""视频榜候选保存简介前 200 字（黄总 2026-10-18 23:10 口径）。

只做留档：不改分类、排序、过滤、调用次数。断言重点在
①截断边界与逐字可回读（emoji/ZWJ、换行、制表、全角、<script>、引号、反斜杠）
②字段来源正确（小红书 desc / 抖音 multi desc / 热点宝与指数库 ""）
③旧 state.json 没有该字段也不报错不丢条目
④接口仍 ok、条目数一致
"""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # video_radar_page_api 在仓库根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from test_video_radar import AT, CONFIG, FixtureClient, item, load
import video_radar as v

LIMIT = v.DESC_EXCERPT_LIMIT


def xhs_raw(desc=None, drop_desc=False):
    """小红书 raw：夹具里在 items[0].note，且必须带 timestamp 才有发布时间。"""
    raw = copy.deepcopy(load('xhs_search')['data']['data']['items'][0]['note'])
    raw['timestamp'] = (AT - dt.timedelta(hours=3)).timestamp()
    if drop_desc:
        raw.pop('desc', None)
    else:
        raw['desc'] = desc
    return raw


def multi_raw(desc=None, drop_desc=False):
    """抖音 multi raw：走 envelope 取出的行，并把图集参数改成视频，否则 normalize 直接判 None。

    desc 里必须留 AI 关键词——分类判定不在本单范围，但条目被判不相关就会整条被丢掉，
    那样就量不到 desc_excerpt 了。
    """
    raw = copy.deepcopy(v.envelope(load('dy_multi'), 'multi')[0])
    raw['aweme_type'], raw['media_type'] = 0, 4
    raw['create_time'] = int((AT - dt.timedelta(hours=2)).timestamp())
    if drop_desc:
        raw.pop('desc', None)
        raw['desc'] = None
    else:
        raw['desc'] = desc if desc is not None else raw['desc']
    return raw


def index_raw(desc=None):
    raw = copy.deepcopy(load('dy_index')['data']['data'][0])
    if desc is not None:
        raw['desc'] = desc
    return raw


FIXTURES = Path(__file__).resolve().parent / 'fixtures/video_radar'


def load_frozen(name):
    """读本仓库 tests/ 下的冻结夹具。

    fix1 起这两条测试不再读正式目录源码、也不读正式 workspace：改前基线的预期输出
    与旧格式 state 样本都冻结成夹具文件，测试只依赖本仓库内稳定的输入与预期。
    """
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def frozen_baseline_expected():
    return load_frozen('desc_excerpt_baseline_expected.json')


def frozen_legacy_state():
    return load_frozen('legacy_state_sample.json')


def board_raw(fixture='dy_board_video_1h_p1', desc=None, title=None):
    """热点宝 raw：text 只取 item_title，所以给个能通过分类的标题；desc 键只用于「不该被取用」的对照。"""
    raw = copy.deepcopy(load(fixture)['data']['data']['objs'][0])
    if title is not None:
        raw['item_title'] = title
    if desc is not None:
        raw['desc'] = desc
    return raw

# 覆盖各种容易在 JSON / 编码上出问题的字符
NASTY = ('<script>window.__pwned=1</script>\n\t"引号" \'单引号\' \\反斜杠\\ '
         '全角ＡＢＣ　空格👨‍👩‍👧‍👦ZWJ组合🇨🇳区域😀表情 结束')


class DescExcerptTests(unittest.TestCase):
    # ---------- ① 截断边界与逐字可回读 ----------

    def test_limit_is_200(self):
        self.assertEqual(LIMIT, 200)

    def test_exact_boundary_lengths(self):
        for size in (0, 1, 199, 200, 201, 400):
            with self.subTest(size=size):
                got = v.normalize(xhs_raw('字' * size), 'xhs_latest', AT, CONFIG)
                self.assertIsNotNone(got)
                self.assertEqual(got['desc_excerpt'], '字' * min(size, LIMIT))
                self.assertEqual(len(got['desc_excerpt']), min(size, LIMIT))

    def test_unicode_and_escapes_round_trip_through_json(self):
        """含 emoji（含 ZWJ）、换行、制表、全角、<script>、引号、反斜杠：写入后逐字相等。"""
        got = v.normalize(xhs_raw(NASTY * 40), 'xhs_latest', AT, CONFIG)   # 远超 200 字，触发截断
        self.assertEqual(len(got['desc_excerpt']), LIMIT)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            storage = root / 'workspace/video-radar'
            storage.mkdir(parents=True)
            target = storage / 'page.json'
            target.write_text(json.dumps({'items': [got]}, ensure_ascii=False), encoding='utf-8')
            back = json.loads(target.read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(back['desc_excerpt'], (NASTY * 40)[:LIMIT])
        self.assertEqual(list(back['desc_excerpt']), list((NASTY * 40)[:LIMIT]))
        self.assertIn('\n', back['desc_excerpt'])
        self.assertIn('\t', back['desc_excerpt'])
        self.assertIn('\\', back['desc_excerpt'])
        self.assertIn('"', back['desc_excerpt'])
        self.assertIn('<script>', back['desc_excerpt'])
        self.assertIn('👨‍👩‍👧‍👦', back['desc_excerpt'])

    def test_zwj_sequence_may_be_cut_but_stays_readable(self):
        """按合同「前 200 个字符」切在 ZWJ 中间也是定义内行为，且必须逐字可回读。"""
        got = v.normalize(xhs_raw('👨‍👩‍👧‍👦' * 100), 'xhs_latest', AT, CONFIG)
        self.assertEqual(len(got['desc_excerpt']), LIMIT)
        self.assertEqual(got['desc_excerpt'], ('👨‍👩‍👧‍👦' * 100)[:LIMIT])
        self.assertEqual(json.loads(json.dumps(got, ensure_ascii=False))['desc_excerpt'], got['desc_excerpt'])

    def test_non_string_desc_is_treated_as_missing(self):
        for value in (None, 0, 1, 123456, [], {}, True, False, 3.14):
            with self.subTest(value=repr(value)):
                got = v.normalize(xhs_raw(value), 'xhs_latest', AT, CONFIG)
                self.assertEqual(got['desc_excerpt'], '')

    # ---------- ② 字段来源正确 ----------

    def test_xiaohongshu_takes_desc(self):
        got = v.normalize(xhs_raw('小红书简介原文ABC'), 'xhs_latest', AT, CONFIG)
        self.assertEqual(got['desc_excerpt'], '小红书简介原文ABC')

    def test_xiaohongshu_missing_desc_is_empty_not_title(self):
        got = v.normalize(xhs_raw(drop_desc=True), 'xhs_latest', AT, CONFIG)
        self.assertEqual(got['desc_excerpt'], '')
        self.assertNotEqual(got['title'], '这是标题不该顶替简介')

    def test_douyin_multi_takes_desc(self):
        got = v.normalize(multi_raw('抖音multi简介：别害怕AI，学会驾驭它。XYZ'), 'dy_multi', AT, CONFIG)
        self.assertEqual(got['desc_excerpt'], '抖音multi简介：别害怕AI，学会驾驭它。XYZ')

    def test_douyin_multi_missing_desc_falls_back_to_item_title_but_excerpt_stays_empty(self):
        raw = multi_raw(drop_desc=True)
        raw['desc'] = None
        raw['item_title'] = 'item_title顶替 但含AI所以留得住'
        got = v.normalize(raw, 'dy_multi', AT, CONFIG)
        self.assertIsNotNone(got, '含 AI 关键词的条目应当留得住')
        self.assertEqual(got['title'], 'item_title顶替 但含AI所以留得住')
        self.assertEqual(got['desc_excerpt'], '', '平台没给 desc 就是空串，不拿 item_title 顶替')

    def test_hot_board_and_index_have_no_desc_field(self):
        """热点宝与指数库没有单独简介字段，一律 ''（即便夹具里存在同名键也不取）。"""
        keep = 'AI教程：用大模型做视频'
        self.assertEqual(v.normalize(board_raw(desc='热点宝的desc不该被取用', title=keep), 'dy_board_video_1h', AT, CONFIG)['desc_excerpt'], '')
        self.assertEqual(v.normalize(board_raw('dy_board_low_fan_1h_p1', desc='低粉榜的desc也不该取', title=keep), 'dy_board_low_fan_1h', AT, CONFIG)['desc_excerpt'], '')
        self.assertEqual(v.normalize(board_raw(title=keep), 'dy_board_video_1h', AT, CONFIG)['desc_excerpt'], '')
        self.assertEqual(v.normalize(index_raw(desc='指数库的desc不该被取用'), 'dy_index', AT, CONFIG)['desc_excerpt'], '')
        self.assertEqual(v.normalize(index_raw(), 'dy_index', AT, CONFIG)['desc_excerpt'], '')

    # ---------- ③ 旧数据兼容 ----------

    def test_old_state_without_field_is_preserved_and_backfilled(self):
        """旧条目没有 desc_excerpt 键：读取不报错、不丢条目，新一轮采到时补上。"""
        fresh = v.normalize(xhs_raw('新一轮补上的简介'), 'xhs_latest', AT, CONFIG)
        legacy = copy.deepcopy(fresh)
        legacy.pop('desc_excerpt')                       # 模拟旧 state.json 的条目
        legacy['likes'] = 5
        previous = copy.deepcopy(legacy)
        legacy['sources'] = ['xhs_latest', 'dy_board_video_1h']
        previous = copy.deepcopy(legacy)
        merged = v.observe(copy.deepcopy(fresh), previous)
        self.assertEqual(merged['desc_excerpt'], '新一轮补上的简介', '新一轮采到时应补上简介')
        self.assertEqual(merged['first_seen_at'], previous['first_seen_at'], '首次观察时间沿用旧条目')
        self.assertEqual(merged['sources'], ['dy_board_video_1h', 'xhs_latest'], '来源合并不变')
        self.assertEqual(merged['key'], fresh['key'])
        # 旧条目原样读出来也不报错
        self.assertNotIn('desc_excerpt', legacy)
        self.assertEqual(v.reclassify_cached(legacy, CONFIG)['key'], legacy['key'])

    def test_excerpt_survives_a_desc_less_reobservation(self):
        """同一条被没有简介字段的来源再次观测到时，已采到的简介不能被抹掉。"""
        with_desc = copy.deepcopy(item())
        with_desc['desc_excerpt'] = '第一次采到的简介'
        without = copy.deepcopy(with_desc)
        without['desc_excerpt'] = ''
        without['likes'] = with_desc['likes'] + 10
        merged = v.observe(without, with_desc)
        self.assertEqual(merged['desc_excerpt'], '第一次采到的简介')

    def test_run_against_real_state_copy_keeps_item_count(self):
        """旧格式 state 跑一轮离线 run（FixtureClient，真实调用 0）：不报错、条目数不减。

        fix1 起输入换成冻结夹具 `tests/fixtures/video_radar/legacy_state_sample.json`
        （旧格式、不含 desc_excerpt，从生产 state 的只读副本裁剪而来），不再读正式
        workspace——采集开始写新字段后，生产 state 里本来就会有 desc_excerpt，拿它当
        「旧数据」会把合法升级当成错误。

        时间基准取夹具自身的时间：run 里有 24 小时观察窗口，用夹具时间跑会把条目全判过期。
        """
        sample = frozen_legacy_state()
        before_items = dict(sample['items'])
        before_daily = sorted(sample['daily'])
        self.assertGreater(len(before_items), 0, '旧 state 夹具没有候选，测试无意义')
        # 夹具必须是「旧格式」：本单之前采集的条目里不应有这个键（证明兼容性测的是旧数据）
        self.assertEqual(sum(1 for e in before_items.values() if 'desc_excerpt' in e), 0,
                         '旧 state 夹具里不该有 desc_excerpt——真出现说明夹具被污染成新格式')
        for rows in sample['daily'].values():
            self.assertEqual(sum(1 for e in rows if 'desc_excerpt' in e), 0,
                             '旧 state 夹具的 daily 里不该有 desc_excerpt')
        latest = max(v.published(e['last_seen_at']) for e in before_items.values())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            storage = root / 'workspace/video-radar'
            storage.mkdir(parents=True)
            (storage / 'state.json').write_text(json.dumps(
                {'items': before_items, 'daily': sample['daily']}, ensure_ascii=False), encoding='utf-8')
            FixtureClient.fail, FixtureClient.requests, FixtureClient.at = set(), [], latest
            with mock.patch.object(v, 'now', return_value=latest):
                v.run(root, 'hourly', FixtureClient)
            after = json.loads((storage / 'state.json').read_text(encoding='utf-8'))
            calls = len(FixtureClient.requests)
        after_items = after.get('items', {})
        # 24 小时窗口内的原有条目一个都不能少（被裁掉的只有本来就超过 24 小时的，与本单无关）
        window = {k: e for k, e in before_items.items()
                  if 0 <= (latest - v.published(e['published_at'])).total_seconds() < 86400}
        self.assertGreater(len(window), 0, '旧 state 夹具里没有 24 小时内的条目，测试无意义')
        missing = sorted(set(window) - set(after_items))
        self.assertEqual(missing, [], '有条目被丢掉：' + ','.join(missing[:5]))
        self.assertGreaterEqual(len(after_items), len(window), '条目数不能减少')
        self.assertEqual(sorted(after.get('daily', {})), before_daily, 'daily 来源集合不变')
        # 旧条目没这个键也照常读出来，不报错、不补空串、不改任何既有字段
        for key, entry in after_items.items():
            self.assertIn(key, before_items, '不该凭空多出条目：' + key)
            for field in ('key', 'platform', 'id', 'title', 'url', 'published_at', 'first_seen_at'):
                self.assertEqual(entry[field], before_items[key][field], key + '.' + field)
            if 'desc_excerpt' in entry:
                self.assertIsInstance(entry['desc_excerpt'], str, key)
        self.assertGreater(calls, 0, '这一轮应真的发过夹具请求')

    def test_matches_baseline_field_by_field(self):
        """零变化证明：与「改前版本对同一组夹具的冻结输出」逐字段比对，只多出 desc_excerpt。

        fix1 起基线不再是运行时加载的外部源码，而是 `tests/fixtures/video_radar/
        desc_excerpt_baseline_expected.json`——改前版本（SHA256 前 16 位 e3f5e191bb2365e3）
        对同一组夹具跑一轮 hourly 的完整结果。补丁一旦合入正式目录，正式目录就是新版本，
        再去加载它当「基线」必然自相矛盾；冻结夹具没有这个时序依赖。
        """
        frozen = frozen_baseline_expected()
        # 冻结的改前输出里不能有这个键（逐条目检查，不能对整个 JSON 做子串搜索——
        # 夹具的 note 说明文字里就写着 desc_excerpt，子串搜索会误伤）
        self.assertEqual(sum(1 for e in frozen['state_items'].values() if 'desc_excerpt' in e), 0,
                         '冻结的改前条目里不该有 desc_excerpt——真出现说明夹具不是改前版本产的')
        for source, rows in frozen['page_lists'].items():
            self.assertEqual(sum(1 for e in rows if 'desc_excerpt' in e), 0, source)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            FixtureClient.fail, FixtureClient.requests, FixtureClient.at = set(), [], AT
            with mock.patch.object(v, 'now', return_value=AT):
                v.run(root, 'hourly', FixtureClient)
            storage = root / 'workspace/video-radar'
            page = json.loads((storage / 'page.json').read_text(encoding='utf-8'))
            state = json.loads((storage / 'state.json').read_text(encoding='utf-8'))
            calls = sorted((m, p, json.dumps(q, sort_keys=True, ensure_ascii=False), qq)
                           for m, p, q, qq in FixtureClient.requests)
        # 请求次数与参数不能变（改前版本的请求序列已冻结）
        expected_calls = sorted((c['method'], c['path'],
                                 json.dumps(c['params'], sort_keys=True, ensure_ascii=False), c['query'])
                                for c in frozen['requests'])
        self.assertEqual(calls, expected_calls, '请求次数与参数不能变')
        base_items = frozen['state_items']
        desc_items = state['items']
        self.assertEqual(sorted(base_items), sorted(desc_items), 'state 条目集合不能变')
        for key in base_items:
            for field in base_items[key]:
                self.assertEqual(base_items[key][field], desc_items[key][field], key + '.' + field)
            self.assertNotIn('desc_excerpt', base_items[key], '改前的冻结输出里不该有这个键')
            self.assertIn('desc_excerpt', desc_items[key], key)
            self.assertIsInstance(desc_items[key]['desc_excerpt'], str, key)
        for source in ('xiaohongshu', 'douyin_realtime', 'douyin_daily'):
            base_list = frozen['page_lists'][source]
            desc_list = page['lists'][source]
            self.assertEqual([i['key'] for i in base_list], [i['key'] for i in desc_list], source)
            for a, b in zip(base_list, desc_list):
                for field in a:
                    self.assertEqual(a[field], b[field], source + '/' + a['key'] + '.' + field)
                self.assertIn('desc_excerpt', b, source + '/' + a['key'])
        self.assertEqual(page['today'], frozen['today'], 'today 汇总不能变')
        self.assertEqual({k: v['status'] for k, v in page['sources'].items()},
                         frozen['source_status'], '来源状态不能变')

    def test_offline_run_writes_excerpts_into_state_and_page(self):
        """空 state 跑一轮夹具：state.json / page.json / 快照的每条都带 desc_excerpt。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            FixtureClient.fail, FixtureClient.requests, FixtureClient.at = set(), [], AT
            with mock.patch.object(v, 'now', return_value=AT):
                v.run(root, 'hourly', FixtureClient)
            storage = root / 'workspace/video-radar'
            state = json.loads((storage / 'state.json').read_text(encoding='utf-8'))
            page = json.loads((storage / 'page.json').read_text(encoding='utf-8'))
            snapshot = sorted((storage / 'snapshots').rglob('*.json'))
            snap = json.loads(snapshot[-1].read_text(encoding='utf-8'))
        self.assertTrue(snapshot, '应有快照文件')
        for source in ('xiaohongshu', 'douyin_realtime', 'douyin_daily'):
            for entry in page['lists'][source]:
                self.assertIn('desc_excerpt', entry, source)
                self.assertIsInstance(entry['desc_excerpt'], str, source)
            for entry in snap['lists'][source]:
                self.assertIn('desc_excerpt', entry, '快照 ' + source)
        for entry in state['items'].values():
            self.assertIsInstance(entry['desc_excerpt'], str)
        xhs = [e for e in page['lists']['xiaohongshu'] if 'xhs_latest' in e['sources'] or 'xhs_hot' in e['sources']]
        self.assertTrue(xhs, '夹具应采到小红书条目')
        self.assertTrue(any(e['desc_excerpt'] for e in xhs), '小红书条目应带简介原文')
        # 抖音热点宝条目没有简介字段，一律空串
        board = [e for e in page['lists']['douyin_realtime'] if any(s.startswith('dy_board_') for s in e['sources'])]
        self.assertTrue(board, '夹具应采到热点宝条目')
        for entry in board:
            self.assertEqual(entry['desc_excerpt'], '', entry['key'])

    # ---------- ④ 接口与页面不受影响 ----------

    def test_page_api_still_ok_and_item_counts_match(self):
        """离线 run 的 page.json 经 video_radar_page_api.py 读仍 status=ok、条目数一致。"""
        import video_radar_page_api as api
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            FixtureClient.fail, FixtureClient.requests, FixtureClient.at = set(), [], AT
            page = v.run(root, 'hourly', FixtureClient)
            stored = json.loads((root / 'workspace/video-radar/page.json').read_text(encoding='utf-8'))
            view = api.page_view(root, now=v.now())
        self.assertEqual(view['status'], 'ok')
        for name in ('xiaohongshu', 'douyin_realtime', 'douyin_daily'):
            self.assertEqual(len(view['lists'][name]), len(stored['lists'][name]), name)
        # 新字段不会传给浏览器（页面 API 逐字段构造条目）
        self.assertNotIn('desc_excerpt', json.dumps(view['lists'], ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()