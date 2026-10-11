"""xradar_source 离线测试：不联网。

两套 fixture：
- 内联合成的 `FIXTURE`，用来钉住解析规则（小而确定）；
- `tests/fixtures/xradar/` 下站方真实页面的原样快照（2026-10-09 历史页、
  2026-10-10 当天页），用来钉住**真实结构**——手写 fixture 简化得太多，
  站方一改版测试还照样绿，等于没测（M8）。
"""
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xradar_source import (  # noqa: E402
    BASE, Radar, format_views, health, parse, plain, robots_allows, views)

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'xradar'
REAL_HISTORY = FIXTURE_DIR / 'words-2026-10-09.html'          # 历史页：北京时间全天口径
REAL_TODAY = FIXTURE_DIR / 'words-today-2026-10-10.html'      # 当天页：最近 24 小时口径


def real_page(path):
    return path.read_text(encoding='utf-8')


FIXTURE = '''<html><body>
<div class="hwe-brief"><span>今日速览</span></div>
<details class="hwe-row hwe-top" data-hwi data-c="ai" data-s="up"><summary>
<span class="hwe-th"><span class="hwe-wm">AI</span><span class="hwe-bd">1</span></span>
<span class="hwe-b"><span class="hwe-t">懂车帝测尊界刹车踏板引全网热议</span>
<span class="hwe-s">懂车帝在测试尊界汽车时踩断制动踏板，零件供应商发文质疑。</span>
<span class="hwe-m">AI 科技 · 连续 3 天上榜 · 497 帖 · 294 人在说 · 12:42 首发</span></span>
<span class="hwe-r"><b>636.5万</b><i>曝光</i><span class="hwe-sg up">↑ 上升中</span></span></summary></details>
<details class="hwe-row hwe-top" data-hwi data-c="society" data-s="down"><summary>
<span class="hwe-b"><span class="hwe-t">俄罗斯鼠疫传闻致香港出现抢口罩潮</span>
<span class="hwe-s">传言引发市民恐慌，药房销量暴涨。</span>
<span class="hwe-m">社会民生 · 连续 5 天上榜 · 71 帖 · 65 人在说 · 12:46 首发</span></span>
<span class="hwe-r"><b>135.4万</b><i>曝光</i><span class="hwe-sg down">↓ 退烧</span></span></summary></details>
<section class="hwe-pn"><div class="hwe-ph"><span class="hwe-pt"><b>低粉爆款</b><i>粉丝不到 1 万</i></span></div>
<div class="hwe-pb"><ul class="hwe-ll">
<li><a href="https://x.com/i/status/2108418895325606328"><img src="/avatars/a.jpg"><span>
<b>@Yuan8028</b><small>6955 粉 → <strong>106.0万</strong> 曝光 · 俄罗斯鼠疫传闻致香港出现抢口罩潮</small>
</span></a></li>
<li><a href="https://x.com/i/status/2108484651765690619"><img src="/avatars/b.jpg"><span>
<b>@interr_x</b><small>2503 粉 → <strong>22.6万</strong> 曝光 · 邵艾伦专访孙哥长播客</small>
</span></a></li>
</ul></div></section>
<section class="hwe-pn"><div class="hwe-ph"><span class="hwe-pt"><b>海外在火</b></span></div>
<div class="hwe-pb"><ul class="hwe-gl">
<li><a href="/global/?ev=9">人工智能巨头推演灾难性事件</a><small>海外 492.3万 曝光</small></li>
<li><a href="/global/?ev=2">美国拟网络直播处决死刑犯</a><small>海外 379.4万 曝光</small></li>
</ul></div></section>
</body></html>'''


def _bars(count=24):
    """站方每条话题都带 24 根发帖柱（最近 6 根标 class="r"）。"""
    return '<svg class="hw-sp" viewBox="0 0 72 20">' + ''.join(
        '<rect class="%s" x="%d" y="10.0" width="2" height="8.0"/>' % ('r' if i >= 18 else '', i * 3)
        for i in range(count)) + '</svg>'


def _topic(title, state, cat, views_text, summary, meta):
    return ('<details class="hwe-row hwe-top" data-hwi data-c="%s" data-s="%s"><summary>'
            '<span class="hwe-b"><span class="hwe-t">%s</span>'
            '<span class="hwe-s">%s</span>'
            '<span class="hwe-m">%s</span></span>'
            '<span class="hwe-r"><b>%s</b><i>曝光</i>%s</span></summary></details>'
            % (cat, state, title, summary, meta, views_text, _bars()))


def _lowfan(url, handle, fans, exposure, topic):
    return ('<li><a href="https://x.com/i/status/%s"><img src="/avatars/%s.jpg"><span>'
            '<b>@%s</b><small>%s 粉 → <strong>%s</strong> 曝光 · %s</small>'
            '</span></a></li>' % (url, handle, handle, fans, exposure, topic))


def _overseas(ev, title, exposure):
    return '<li><a href="/global/?ev=%d">%s</a><small>海外 %s 曝光</small></li>' % (ev, title, exposure)


def healthy_markup(topics=14):
    """一页**结构上站得住**的合成页面：行数落在实测区间、每行 24 根柱、
    每行都有摘要和状态标签、两个侧栏都在。用来跑 Radar 的正常路径。

    合成 fixture（`FIXTURE`）只有 2 行、也没有发帖柱，那是钉解析规则的最小样本，
    拿它跑 Radar 会理所当然地触发结构告警 —— 那不是 bug，是它在正确工作。
    """
    rows = [
        # 前两行沿用 FIXTURE 里的真实条目，钉住既有断言（曝光 636.5万 / 152.4 倍）。
        _topic('懂车帝测尊界刹车踏板引全网热议', 'up', 'ai', '636.5万',
               '懂车帝在测试尊界汽车时踩断制动踏板，零件供应商发文质疑。',
               'AI 科技 · 连续 3 天上榜 · 497 帖 · 294 人在说 · 12:42 首发'),
        _topic('俄罗斯鼠疫传闻致香港出现抢口罩潮', 'down', 'society', '135.4万',
               '传言引发市民恐慌，药房销量暴涨。',
               '社会民生 · 连续 5 天上榜 · 71 帖 · 65 人在说 · 12:46 首发'),
    ]
    states = ('up', 'peak', 'down', '')
    for i in range(3, topics + 1):
        state = states[(i - 3) % len(states)]
        rows.append(_topic(
            '合成话题 %d 引全网热议' % i, state, 'ai', '%d.0万' % (100 + i),
            '这是第 %d 条合成摘要，用来把行数撑到实测区间内。' % i,
            'AI 科技 · 连续 %d 天上榜 · %d 帖 · %d 人在说 · 1%d:00 首发'
            % (i % 4 + 1, 10 * i, 5 * i, i % 10)))
    return ('<html><body><div class="hwe-brief"><span>今日速览</span></div>'
            '<p class="hw-sub">最近 24 小时的热门话题，按曝光排，每 2 小时更新</p>'
            + ''.join(rows)
            + '<section class="hwe-pn"><div class="hwe-pb"><ul class="hwe-ll">'
            + _lowfan('2108418895325606328', 'Yuan8028', '6955', '106.0万',
                      '俄罗斯鼠疫传闻致香港出现抢口罩潮')
            + _lowfan('2108484651765690619', 'interr_x', '2503', '22.6万',
                      '邵艾伦专访孙哥长播客')
            + '</ul></div></section>'
            + '<section class="hwe-pn"><div class="hwe-pb"><ul class="hwe-gl">'
            + _overseas(9, '人工智能巨头推演灾难性事件', '492.3万')
            + _overseas(2, '美国拟网络直播处决死刑犯', '379.4万')
            + '</ul></div></section></body></html>')


HEALTHY = healthy_markup()

# 站方真实 robots.txt（research-xradar-history-report.md §1.1 原文）。
ROBOTS_REAL = '''User-agent: *
Allow: /$
Allow: /index.html
Allow: /daily/
Disallow: /api/
Disallow: /admin/
Disallow: /data.json
Disallow: /ai/data.json
Disallow: /preview-

Sitemap: https://xbangdan.com/sitemap.xml

User-agent: ShapBot
Disallow: /
'''


class _Resp:
    """最小 urlopen 替身：支持 with 协议（read_robots 用的是 `with urlopen(...)`）。"""

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(routes):
    """按 URL 尾缀分派的假 urlopen。routes 命中则返回值，值为异常实例则抛出。"""
    def _open(request, timeout=None):
        url = getattr(request, 'full_url', str(request))
        for suffix, value in routes.items():
            if url.endswith(suffix):
                if isinstance(value, BaseException):
                    raise value
                return _Resp(value)
        raise URLError('未预期的请求：' + url)
    return _open


class FakeStore:
    def __init__(self, workspace):
        self.workspace = workspace
        self.saved = []
        # producer_key -> thread_id，模拟 clues_store._save 的命中即返回语义
        self.by_key = {}

        class _Clues:
            def __init__(self, outer):
                self.outer = outer

            def _save(self, payload, snapshots, producer_key=None, observations=None):
                self.outer.saved.append((payload, snapshots, producer_key))
                if producer_key and producer_key in self.outer.by_key:
                    # 与 clues_store 一致：命中 producer_key 直接返回，不写新 entry。
                    return {'id': self.outer.by_key[producer_key], 'duplicate': True}
                thread_id = payload.get('id') or f'clue-{len(self.outer.by_key) + 1}'
                if producer_key:
                    self.outer.by_key[producer_key] = thread_id
                return {'id': thread_id, 'duplicate': False}
            def detail(self, thread_id):
                return {'id':thread_id,'revision':1,'status':'candidate','tracking':False}
        self.clues = _Clues(self)

    def clues_saved(self, key_part=None):
        return [row for row in self.saved
                if key_part is None or (row[2] or '').startswith(key_part)]


def radar(tmp, markup=HEALTHY, store=None):
    """默认喂一页结构站得住的合成页面 —— Radar 的正常路径。

    想测解析规则的最小样本请直接用 `FIXTURE`，别用它跑 Radar。
    """
    return Radar(store or FakeStore(tmp), transport=lambda path: markup)


def unfreeze(r):
    """把配额时间戳清零，让同一个 Radar 能连续抓多次（绕过 60 秒间隔）。"""
    with r.db() as db:
        db.execute('UPDATE quota SET last_at=0')


class ParseTest(unittest.TestCase):
    def test_three_boards_from_one_page(self):
        boards = parse(FIXTURE)
        self.assertEqual(len(boards['topics']), 2)
        self.assertEqual(len(boards['lowfan']), 2)
        self.assertEqual(len(boards['global']), 2)

    def test_lifecycle_state_comes_from_attribute(self):
        row = parse(FIXTURE)['topics'][0]
        self.assertEqual(row['heat'], 'rising')
        self.assertEqual(parse(FIXTURE)['topics'][1]['heat'], 'fading')

    def test_topic_fields(self):
        row = parse(FIXTURE)['topics'][0]
        self.assertEqual(row['title'], '懂车帝测尊界刹车踏板引全网热议')
        self.assertEqual(row['days'], 3)
        self.assertEqual(row['posts'], 497)
        self.assertEqual(row['talkers'], 294)
        self.assertEqual(row['views'], 6365000)
        self.assertEqual(row['category'], 'ai')

    def test_lowfan_multiple(self):
        row = parse(FIXTURE)['lowfan'][0]
        self.assertEqual(row['handle'], 'Yuan8028')
        self.assertEqual(row['fans'], 6955)
        self.assertEqual(row['views'], 1060000)
        # 1,060,000 ÷ 6,955 ≈ 152.4 倍
        self.assertEqual(row['multiple'], 152.4)
        self.assertTrue(row['url'].startswith('https://x.com/i/status/'))

    def test_lowfan_without_fans_is_skipped_not_guessed(self):
        markup = FIXTURE.replace('<b>@Yuan8028</b><small>6955 粉', '<b>@Yuan8028</b><small>— 粉')
        rows = parse(markup)['lowfan']
        self.assertEqual([r['handle'] for r in rows], ['interr_x'])

    def test_overseas(self):
        row = parse(FIXTURE)['global'][0]
        self.assertEqual(row['title'], '人工智能巨头推演灾难性事件')
        self.assertEqual(row['views'], 4923000)
        self.assertEqual(row['url'], BASE + '/global/?ev=9')

    def test_missing_blocks_do_not_break_others(self):
        boards = parse('<html><body><ul class="hwe-ll"><li>x</li></ul></body></html>')
        self.assertEqual(boards['topics'], [])
        self.assertEqual(boards['global'], [])
        self.assertEqual(boards['lowfan'], [])

    def test_empty_markup(self):
        for rows in parse('').values():
            self.assertEqual(rows, [])

    def test_unparsable_exposure_keeps_the_raw_string(self):
        """M5：站方改文案时原文要留下来，不能凭空少一块依据。"""
        markup = FIXTURE.replace('<b>636.5万</b>', '<b>数据偏少</b>')
        row = parse(markup)['topics'][0]
        self.assertIsNone(row['views'])
        self.assertEqual(row['raw_views'], '数据偏少')
        self.assertIn('数据偏少', Radar._fact(row))


class RealPageTest(unittest.TestCase):
    """真实页面快照：钉住真实结构（M8）。2026-10-10 15:38 抓自站方公开页。"""

    def test_history_page_parses_to_the_baseline_shape(self):
        markup = real_page(REAL_HISTORY)
        boards = parse(markup)
        # structure.json 实测：10-09 = 话题 22 / 低粉 4 / 海外 0
        self.assertEqual(len(boards['topics']), 22)
        self.assertEqual(len(boards['lowfan']), 4)
        # 历史页没有海外信息差侧栏，12/12 天都是 0 条
        self.assertEqual(boards['global'], [])
        row = boards['topics'][0]
        self.assertEqual(row['days'], 2)
        self.assertGreater(row['posts'], 0)
        self.assertGreater(row['views'], 0)
        self.assertTrue(row['summary'])

    def test_today_page_has_the_overseas_board(self):
        boards = parse(real_page(REAL_TODAY))
        self.assertEqual(len(boards['topics']), 17)
        self.assertEqual(len(boards['lowfan']), 4)
        self.assertEqual(len(boards['global']), 4)

    def test_real_pages_pass_the_health_check(self):
        for path in (REAL_HISTORY, REAL_TODAY):
            markup = real_page(path)
            report = health(markup, parse(markup))
            self.assertTrue(report['ok'], (path.name, report['problems']))

    def test_page_scope_separates_rolling_and_full_day(self):
        self.assertEqual(health(real_page(REAL_TODAY), parse(real_page(REAL_TODAY)))['scope'],
                         'rolling_24h')
        self.assertEqual(health(real_page(REAL_HISTORY), parse(real_page(REAL_HISTORY)))['scope'],
                         'full_day')

    def test_real_page_shares_nothing_with_the_public_network(self):
        """L4：把 `urlopen` 打桩成「一调用就炸」，再跑真实页面用例，证明真不联网。

        原来这条只断言「fixture 存在且含 xbangdan.com」，名字比断言响 ——
        哪怕代码真的去联网它也照样绿。这里改成真拦：任何一次真实网络调用
        都会让用例失败。
        """
        self.assertTrue(REAL_HISTORY.exists() and REAL_TODAY.exists())
        for path in (REAL_HISTORY, REAL_TODAY):
            with patch('xradar_source.urlopen',
                       side_effect=AssertionError('真实页面用例不许联网')) as mocked:
                markup = real_page(path)
                boards = parse(markup)
                report = health(markup, boards)
                # 连 Radar 的 robots/页面两条路都不许碰网络
                radar(tempfile.mkdtemp(), markup=markup).latest()
            self.assertFalse(mocked.called, path.name)
            self.assertTrue(boards['topics'], path.name)
            self.assertIsNotNone(report, path.name)


class HealthTest(unittest.TestCase):
    def test_intact_page_is_healthy(self):
        report = health(HEALTHY, parse(HEALTHY))
        self.assertTrue(report['ok'], report['problems'])
        self.assertEqual(report['rows'], 14)
        self.assertEqual(report['bars_per_row'], [24])
        self.assertEqual(report['summary_ratio'], 1.0)
        self.assertTrue(report['lowfan_block'])
        self.assertTrue(report['overseas_block'])
        self.assertEqual(report['scope'], 'rolling_24h')

    def test_summary_field_disappearing_is_flagged(self):
        """09-27/09-28 整天没有 hwe-s，这是真实发生过的整字段消失。"""
        markup = HEALTHY.replace('<span class="hwe-s">', '<span class="hwe-x">')
        report = health(markup, parse(markup))
        self.assertFalse(report['ok'])
        self.assertEqual(report['summary_ratio'], 0.0)
        self.assertTrue(any('摘要' in p for p in report['problems']))

    def test_row_count_outside_the_measured_band_is_flagged(self):
        report = health(FIXTURE, parse(FIXTURE))     # 合成 fixture 只有 2 行，低于实测下限 12
        self.assertFalse(report['ok'])
        self.assertTrue(any('行数' in p for p in report['problems']))

    def test_bars_not_twenty_four_is_flagged(self):
        import re as _re
        # 只把第一行的发帖柱砍掉一根：那一行变 23 根，其余仍是 24 根
        markup = _re.sub(r'<rect class="r" x="63"[^/]*/>', '', HEALTHY, count=1)
        report = health(markup, parse(markup))
        self.assertEqual(report['bars_per_row'], [23, 24])
        self.assertFalse(report['ok'])
        self.assertTrue(any('发帖柱' in p for p in report['problems']))

    def test_new_data_s_value_is_flagged(self):
        markup = HEALTHY.replace('data-s="up"', 'data-s="spiking"', 1)
        report = health(markup, parse(markup))
        self.assertFalse(report['ok'])
        self.assertIn('spiking', report['unknown_states'])

    def test_state_labels_vanishing_is_flagged(self):
        """10-03 实测状态标签占比最低 0.52；掉到 0.5 以下就说明状态列没了。"""
        import re as _re
        markup = _re.sub(r' data-s="[^"]*"', ' data-s=""', HEALTHY)
        report = health(markup, parse(markup))
        self.assertFalse(report['ok'])
        self.assertTrue(any('状态标签' in p for p in report['problems']))

    def test_container_rename_makes_everything_empty(self):
        markup = (HEALTHY.replace('hwe-row', 'hwe-zzz')
                  .replace('hwe-ll', 'hwe-zz').replace('hwe-gl', 'hwe-gz'))
        report = health(markup, parse(markup))
        self.assertFalse(report['ok'])
        self.assertEqual(sum(report['counts'].values()), 0)
        self.assertFalse(report['lowfan_block'])
        self.assertFalse(report['overseas_block'])


class BoardBlindSpotTest(unittest.TestCase):
    """M1：`health()` 原来只盯 ROW 级信号，三张榜各自空掉完全不报。

    四种破坏形态逐一定案，全部拿站方真实页面（当天页，`scope=rolling_24h`，
    原始 counts=[17,4,4]）改，而不是合成页 —— 合成页恰好也有三个容器，
    但真实页才是站方现在的样子。
    """

    def setUp(self):
        self.page = real_page(REAL_TODAY)

    def _health(self, markup):
        return health(markup, parse(markup))

    def test_baseline_real_page_is_healthy(self):
        report = self._health(self.page)
        self.assertTrue(report['ok'], report['problems'])
        self.assertEqual(list(report['counts'].values()), [17, 4, 4])
        self.assertEqual(report['scope'], 'rolling_24h')

    def test_overseas_board_disappearing_is_flagged(self):
        """hwe-gl 改名 → 海外榜整块解析不到，另外两榜还好。"""
        markup = self.page.replace('hwe-gl', 'hwe-glz')
        report = self._health(markup)
        self.assertEqual(list(report['counts'].values()), [17, 4, 0])
        self.assertTrue(report['rows'])                 # ROW 容器还在
        self.assertFalse(report['overseas_block'])
        self.assertFalse(report['ok'], '只坏一块也必须报')
        self.assertTrue(any('海外' in p for p in report['problems']), report['problems'])

    def test_lowfan_board_disappearing_is_flagged(self):
        markup = self.page.replace('hwe-ll', 'hwe-llz')
        report = self._health(markup)
        self.assertEqual(list(report['counts'].values()), [17, 0, 4])
        self.assertTrue(report['rows'])
        self.assertFalse(report['lowfan_block'])
        self.assertFalse(report['ok'])
        self.assertTrue(any('低粉' in p for p in report['problems']), report['problems'])

    def test_topics_going_empty_is_flagged(self):
        """hwe-t 改名 → topics 全部解析不出，但 lowfan/global 都还在。"""
        markup = self.page.replace('hwe-t"', 'hwe-tz"')
        report = self._health(markup)
        self.assertEqual(list(report['counts'].values()), [0, 4, 4])
        self.assertTrue(report['rows'])
        self.assertTrue(report['lowfan_block'] and report['overseas_block'])
        self.assertFalse(report['ok'])
        self.assertTrue(any('话题' in p for p in report['problems']), report['problems'])

    def test_all_three_boards_empty_while_row_container_survives(self):
        """容器全改名但 hwe-row 还在：原来 `not row_count and not total` 完全不成立。"""
        markup = (self.page.replace('hwe-gl', 'hwe-glz').replace('hwe-ll', 'hwe-llz')
                  .replace('hwe-t"', 'hwe-tz"'))
        report = self._health(markup)
        self.assertEqual(sum(report['counts'].values()), 0)
        self.assertTrue(report['rows'], 'ROW 容器仍在，正是原来漏掉的形态')
        self.assertFalse(report['ok'])
        self.assertTrue(any('全空' in p for p in report['problems']), report['problems'])

    def test_missing_ratio_reflects_rows_that_failed_to_parse(self):
        """missing_ratio 不该恒为 0：ROW 命中 17 行、topics 只出 0 行 → 1.0。"""
        report = self._health(self.page.replace('hwe-t"', 'hwe-tz"'))
        self.assertEqual(report['rows'], 17)
        self.assertEqual(report['counts']['topics'], 0)
        self.assertEqual(report['missing_ratio'], 1.0)

    def test_intact_page_has_zero_missing_ratio(self):
        self.assertEqual(self._health(self.page)['missing_ratio'], 0.0)

    def test_full_day_page_without_overseas_block_is_not_flagged(self):
        """防误报：历史页本来就没有海外侧栏（13 天实测 12/12 天都没有），不能报。"""
        markup = real_page(REAL_HISTORY)
        report = self._health(markup)
        self.assertEqual(report['scope'], 'full_day')
        self.assertFalse(report['overseas_block'])
        self.assertEqual(report['counts']['global'], 0)
        self.assertTrue(report['ok'], report['problems'])

    def test_full_day_page_missing_lowfan_block_is_flagged(self):
        """历史页没有海外侧栏是常态，但低粉侧栏 14/14 天都在，缺了就要报。"""
        report = self._health(real_page(REAL_HISTORY).replace('hwe-ll', 'hwe-llz'))
        self.assertFalse(report['ok'])
        self.assertTrue(any('低粉' in p for p in report['problems']), report['problems'])


class ViewsTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(views('636.5万'), 6365000)
        self.assertEqual(views('492.3万'), 4923000)
        self.assertEqual(views('9824'), 9824)
        self.assertEqual(views('1.2亿'), 120000000)
        self.assertEqual(views('3.7万'), 37000)

    def test_unparsable_is_none_not_guess(self):
        for bad in ('', '未知', '很多', None, '曝光'):
            self.assertIsNone(views(bad))

    def test_roundtrip(self):
        self.assertEqual(format_views(6365000), '636.5万')
        self.assertEqual(format_views(9824), '9824')
        # 没有数字就返回空串，不写 0，避免把「未知」说成「零」。
        self.assertEqual(format_views(None), '')

    def test_plain_strips_tags(self):
        self.assertEqual(plain('<b>a</b>&amp;b <i>c</i>'), 'a&b c')


class RobotsTest(unittest.TestCase):
    """M2/M3/M9：robots 判定必须能被直接测到，不能只靠 transport 路径。"""

    def test_words_path_is_allowed_by_the_real_robots(self):
        self.assertTrue(robots_allows('/words/', body=ROBOTS_REAL))
        self.assertTrue(robots_allows('/words/2026-10-09/', body=ROBOTS_REAL))

    def test_explicitly_disallowed_paths_are_refused(self):
        for path in ('/api/', '/data.json', '/ai/data.json', '/preview-x', '/admin/'):
            self.assertFalse(robots_allows(path, body=ROBOTS_REAL), path)

    def test_longest_match_wins(self):
        body = 'User-agent: *\nDisallow: /words/\nAllow: /words/2026-10-09/\n'
        self.assertTrue(robots_allows('/words/2026-10-09/', body=body))
        self.assertFalse(robots_allows('/words/other/', body=body))

    def test_equal_length_allow_beats_disallow(self):
        body = 'User-agent: *\nDisallow: /api/\nAllow: /api/x\n'
        self.assertTrue(robots_allows('/api/x', body=body))

    def test_other_user_agent_groups_are_ignored(self):
        """M3：只认 `*` 组。ShapBot 的全站禁止与我们无关。"""
        self.assertTrue(robots_allows('/words/', body=ROBOTS_REAL))

    def test_read_failure_propagates_instead_of_being_swallowed(self):
        """读不到 ≠ 允许。异常抛给调用方，由它决定走缓存还是 fail-open 并记账。"""
        def boom():
            raise URLError('network down')
        with self.assertRaises(URLError):
            robots_allows('/words/', opener=boom)

    def test_no_rules_at_all_means_allowed(self):
        self.assertTrue(robots_allows('/words/', body='# 只有注释\n'))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_collect_records_all_boards(self):
        result = radar(self.tmp).latest()
        self.assertEqual(result['counts'], {'topics': 14, 'lowfan': 2, 'global': 2})
        self.assertIn('fetched_at', result)

    def test_collect_saves_a_gzipped_page_snapshot(self):
        result = radar(self.tmp).latest()
        saved = Path(self.tmp) / 'xradar' / result['snapshot']
        self.assertTrue(saved.exists(), result['snapshot'])
        self.assertEqual(gzip.decompress(saved.read_bytes()).decode('utf-8'), HEALTHY)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        # runs 里记着哈希与字节数，页面变了才查得到
        with radar(self.tmp).db() as db:
            row = db.execute('SELECT page_hash,page_bytes,page_path FROM runs'
                             ' ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(row['page_hash'], result['snapshot_hash'])
        self.assertEqual(row['page_bytes'], len(HEALTHY.encode('utf-8')))
        self.assertEqual(row['page_path'], result['snapshot'])

    def test_snapshot_dir_follows_the_day(self):
        r = radar(self.tmp)
        unfreeze(r)
        result = r.history('2026-10-09')
        self.assertTrue(result['snapshot'].startswith('pages/2026-10-09/'))

    def test_existing_database_is_migrated_in_place(self):
        """既有 xradar.db 缺 4 个新列，要能就地补上且不丢老数据。"""
        path = Path(self.tmp) / 'xradar' / 'xradar.db'
        store = FakeStore(self.tmp)
        Radar(store, transport=lambda p: HEALTHY).latest()
        with sqlite3.connect(path) as db:          # 模拟「旧版建的库」
            for column in ('health', 'page_hash', 'page_bytes', 'page_path'):
                db.execute(f'ALTER TABLE runs DROP COLUMN {column}')
            columns = [row[1] for row in db.execute('PRAGMA table_info(runs)')]
            before = db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        self.assertNotIn('health', columns)

        store2 = FakeStore(self.tmp)
        second = Radar(store2, transport=lambda p: HEALTHY)   # 再实例化一次即触发迁移
        unfreeze(second)
        second.latest()
        with sqlite3.connect(path) as db:
            columns = [row[1] for row in db.execute('PRAGMA table_info(runs)')]
            after = db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        for column in ('health', 'page_hash', 'page_bytes', 'page_path'):
            self.assertIn(column, columns)
        self.assertEqual(after, before + 1)

    def test_old_snapshots_are_pruned(self):
        root = Path(self.tmp) / 'xradar' / 'pages'
        for name in ('2020-01-01', '2020-06-01'):
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / 'words-old.html.gz').write_bytes(b'x')
        fresh = root / '2999-01-01'
        fresh.mkdir(parents=True, exist_ok=True)
        (fresh / 'words-new.html.gz').write_bytes(b'x')
        radar(self.tmp).latest()
        self.assertFalse((root / '2020-01-01').exists())
        self.assertFalse((root / '2020-06-01').exists())
        self.assertTrue(fresh.exists())

    def test_refetching_same_day_keeps_the_clue_link(self):
        """M7：重抓同一天不能把 items.clue_id 冲成 NULL。"""
        store = FakeStore(self.tmp)
        r = radar(self.tmp, store=store)
        r.latest()
        out = r.publish()
        self.assertEqual(out['total'], 18)
        with r.db() as db:
            before = db.execute('SELECT COUNT(*) n FROM items WHERE clue_id IS NOT NULL').fetchone()[0]
        self.assertEqual(before, 18)
        unfreeze(r)
        r.latest()
        with r.db() as db:
            after = db.execute('SELECT COUNT(*) n FROM items WHERE clue_id IS NOT NULL').fetchone()[0]
            ids = {row[0] for row in db.execute('SELECT clue_id FROM items WHERE clue_id IS NOT NULL')}
        self.assertEqual(after, 18)
        self.assertEqual(len(ids), 18)

    def test_history_rejects_bad_date(self):
        with self.assertRaises(ValueError):
            radar(self.tmp).history('10/09')
        with self.assertRaises(ValueError):
            radar(self.tmp).history('')

    def test_history_rejects_a_date_that_does_not_exist(self):
        """L5：`2026-13-40` 过了正则但不是真日期，不能白烧一次请求。"""
        with self.assertRaises(ValueError) as ctx:
            radar(self.tmp).history('2026-13-40')
        self.assertIn('不存在', str(ctx.exception))
        with self.assertRaises(ValueError):
            radar(self.tmp).history('2026-02-30')

    def test_history_uses_date_path(self):
        seen = []
        r = Radar(FakeStore(self.tmp), transport=lambda p: (seen.append(p), HEALTHY)[1])
        r.history('2026-10-09')
        self.assertEqual(seen, ['/words/2026-10-09/'])

    def test_spends_quota_per_fetch(self):
        r = radar(self.tmp)
        with r.db() as db:
            db.execute('UPDATE quota SET last_at=0')
        r.latest()
        with r.db() as db:
            row = db.execute('SELECT count FROM quota WHERE id=1').fetchone()
        self.assertEqual(row['count'], 1)

    def test_min_interval_blocks_second_fetch(self):
        r = radar(self.tmp)
        r.latest()
        with self.assertRaises(ValueError) as ctx:
            r.latest()
        self.assertIn('秒', str(ctx.exception))

    def test_daily_limit(self):
        r = radar(self.tmp)
        with r.db() as db:
            db.execute('INSERT OR REPLACE INTO quota VALUES(1,?,?,?,?)', (
                datetime.now(timezone.utc).date().isoformat(), 99, 0, 0))
        with self.assertRaises(ValueError) as ctx:
            r.latest()
        self.assertIn('上限', str(ctx.exception))

    def test_cooldown_blocks(self):
        r = radar(self.tmp)
        with r.db() as db:
            db.execute('INSERT OR REPLACE INTO quota VALUES(1,?,?,?,?)', (
                datetime.now(timezone.utc).date().isoformat(), 0, 0, time.time() + 600))
        with self.assertRaises(ValueError) as ctx:
            r.latest()
        self.assertIn('冷却', str(ctx.exception))

    def test_guard_is_checked_before_transport_is_called(self):
        """被限流挡住时不该发出任何请求。"""
        hits = []
        r = Radar(FakeStore(self.tmp), transport=lambda p: hits.append(p) or HEALTHY)
        with r.db() as db:
            db.execute('INSERT OR REPLACE INTO quota VALUES(1,?,?,?,?)', (
                datetime.now(timezone.utc).date().isoformat(), 0, 0, time.time() + 600))
        with self.assertRaises(ValueError):
            r.latest()
        self.assertEqual(hits, [])


class _RateLimited(HTTPError):
    """只当异常用的限流响应。

    HTTPError 的 MRO 里有 tempfile._TemporaryFileWrapper，它的 `__del__` 由 C 层
    按**基类**缓存，子类覆盖不掉：只要构造过就会在解释器收尾时刷一行
    ResourceWarning。这里主动关掉 fp，把警告消掉。
    """

    def __init__(self, url, code):
        super().__init__(url, code, 'rate limited', {}, None)

    @property
    def fp(self):
        return None

    @fp.setter
    def fp(self, value):
        pass

    def close(self):
        pass


class RateLimitTest(unittest.TestCase):
    """M10：429/403 真的会开冷却，_cool_down 返回的是新值（L1）。全程 mock，不联网。

    注意：`_RateLimited` 会被 CPython 的 `_TemporaryFileWrapper.__del__`（C 层按基类
    缓存，子类覆盖不掉）在解释器收尾时判为「隐式清理」，抛一条与本用例无关的
    ResourceWarning。子类的 close/fp 覆盖都拦不住它，这里显式关掉这一类噪音，
    免得真正的警告被淹没。
    """

    def setUp(self):
        warnings.filterwarnings('ignore', message='Implicitly cleaning up <HTTPError',
                                category=ResourceWarning)
        self.tmp = tempfile.mkdtemp()

    def _radar_with_http(self, code):
        """不注入 transport，让 _fetch 走真实分支，但把 urlopen 换成会抛 429 的桩。

        robots 那一次必须成功，否则测的就不是「页面请求被限流」而是「robots 读失败」。
        """
        calls = []

        def fake_urlopen(request, timeout=None):
            url = getattr(request, 'full_url', str(request))
            calls.append(url)
            if url.endswith('/robots.txt'):
                return _Resp(ROBOTS_REAL)
            raise _RateLimited(url, code)

        r = Radar(FakeStore(self.tmp))     # transport=None
        return r, fake_urlopen, calls

    def test_429_sets_cooldown_and_blocks_the_next_fetch(self):
        r, fake, calls = self._radar_with_http(429)
        with patch('xradar_source.urlopen', side_effect=fake):
            with self.assertRaises(ValueError) as ctx:
                r.latest()
        self.assertIn('429', str(ctx.exception))
        self.assertTrue(any('/words/' in url for url in calls), calls)
        # 冷却已写入，后续 latest() 必须被拦在 _guard
        self.assertTrue(r.status()['extra']['cooling'])
        with self.assertRaises(ValueError) as ctx2:
            r.latest()
        self.assertIn('冷却', str(ctx2.exception))

    def test_403_also_sets_cooldown(self):
        r, fake, calls = self._radar_with_http(403)
        with patch('xradar_source.urlopen', side_effect=fake):
            with self.assertRaises(ValueError) as ctx:
                r.latest()
        self.assertIn('403', str(ctx.exception))
        self.assertTrue(r.status()['extra']['cooling'])

    def test_cool_down_returns_the_value_it_just_wrote(self):
        """L1：原来返回的是更新前的旧值 0，调用方拿到的冷却截止时间完全没用。"""
        from xradar_source import COOLDOWN_SECONDS
        r = radar(self.tmp)
        with r.db() as db:
            db.execute('INSERT OR REPLACE INTO quota VALUES(1,?,?,?,?)', (
                datetime.now(timezone.utc).date().isoformat(), 0, 0, 0))
        before = time.time()
        returned = r._cool_down()
        after = time.time()
        # 返回值就是刚写进去的那个时间戳，不是旧值 0
        self.assertGreater(returned, before + COOLDOWN_SECONDS - 5)
        self.assertLessEqual(returned, after + COOLDOWN_SECONDS + 1)
        with r.db() as db:
            stored = db.execute('SELECT cooldown_until FROM quota WHERE id=1').fetchone()[0]
        self.assertEqual(int(stored), returned)
        self.assertTrue(r.status()['extra']['cooling'])


class RobotsPolicyTest(unittest.TestCase):
    """M1/M2：robots 请求计入配额、结果缓存、读失败降级要留痕。全程 mock，不联网。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _radar(self, routes):
        return Radar(FakeStore(self.tmp)), opener_for(routes)

    def test_robots_request_counts_against_the_daily_quota(self):
        """M1：一次真实抓取实际发 2 个请求，配额不能只算 1 个。"""
        r, fake = self._radar({'/robots.txt': URLError('down'), '/words/': HEALTHY})
        with patch('xradar_source.urlopen', side_effect=fake):
            r.latest()
        with r.db() as db:
            self.assertEqual(db.execute('SELECT count FROM quota WHERE id=1').fetchone()[0], 2)

    def test_disallowed_path_reports_and_spends_quota(self):
        blocked = ROBOTS_REAL.replace('Allow: /daily/', 'Disallow: /words/')
        r, fake = self._radar({'/robots.txt': blocked, '/words/': HEALTHY})
        with patch('xradar_source.urlopen', side_effect=fake):
            with self.assertRaises(ValueError) as ctx:
                r.latest()
        self.assertIn('robots', str(ctx.exception))
        # 被 robots 挡住时只发了 robots 那一个请求，但配额照样计了
        with r.db() as db:
            self.assertEqual(db.execute('SELECT count FROM quota WHERE id=1').fetchone()[0], 1)

    def test_robots_body_is_cached_for_a_day(self):
        """M1：robots 一天最多读一次，不能每次抓取都白烧一个请求。"""
        reads = []

        def counting(request, timeout=None):
            url = getattr(request, 'full_url', str(request))
            if url.endswith('/robots.txt'):
                reads.append(1)
                return _Resp(ROBOTS_REAL)
            return _Resp(HEALTHY)

        r = Radar(FakeStore(self.tmp))
        with patch('xradar_source.urlopen', side_effect=counting):
            unfreeze(r)
            r.latest()
            self.assertEqual(len(reads), 1)
            unfreeze(r)
            r.latest()
        self.assertEqual(len(reads), 1, '第二次应当命中缓存，不再读 robots')
        # 两次抓取各 1 个页面请求 = 2，加上第一次那次 robots = 3（M1：robots 也计配额）
        with r.db() as db:
            self.assertEqual(db.execute('SELECT count FROM quota WHERE id=1').fetchone()[0], 3)

    def test_read_failure_without_cache_fails_open_but_leaves_a_trail(self):
        """M2：读不到 robots 时 fail-open，但必须写 runs.error 并在 status 暴露。"""
        r, fake = self._radar({'/robots.txt': URLError('down'), '/words/': HEALTHY})
        with patch('xradar_source.urlopen', side_effect=fake):
            result = r.latest()
        self.assertIn('robots.txt 读取失败', result['warning'])
        self.assertIn('fail-open', result['warning'])
        with r.db() as db:
            row = db.execute('SELECT error FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertIn('robots.txt 读取失败', row['error'])
        status = r.status()
        self.assertTrue(status['warning'])
        self.assertFalse(status['available'])

    def test_stale_cache_is_reused_when_robots_becomes_unreachable(self):
        """M2：有旧缓存就按旧规则走，而不是当没规则。"""
        state = {'n': 0}

        def flaky(request, timeout=None):
            url = getattr(request, 'full_url', str(request))
            if url.endswith('/robots.txt'):
                state['n'] += 1
                if state['n'] == 1:
                    return _Resp(ROBOTS_REAL)
                raise URLError('down')
            return _Resp(HEALTHY)

        r = Radar(FakeStore(self.tmp))
        with patch('xradar_source.urlopen', side_effect=flaky):
            r.latest()
            with r.db() as db:                   # 把落表缓存做成过期的
                db.execute('UPDATE robots SET fetched_at=0')
            r._robots = {'at': 0.0, 'body': '', 'ok': False}
            unfreeze(r)
            result = r.latest()
        self.assertIn('robots.txt 读取失败', result['warning'])
        self.assertIn('缓存', result['warning'])
        self.assertNotIn('fail-open', result['warning'])   # 有旧缓存，不是裸 fail-open

class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = FakeStore(self.tmp)
        self.r = Radar(self.store, transport=lambda p: HEALTHY)

    def test_publishes_every_row_with_provenance(self):
        self.r.latest()
        out = self.r.publish()
        self.assertEqual(out['total'], 18)
        self.assertEqual(len(out['published']), 18)
        self.assertTrue(all(p.get('ok') is not False for p in out['published']))
        for payload, snapshots, key in self.store.saved:
            self.assertTrue(key.startswith('xradar:'))
            self.assertEqual(payload['kind'], 'information')
            self.assertEqual(payload['categories'], ['topics'])
            self.assertTrue(payload['summary'])
            self.assertEqual(snapshots[0]['source'], 'xradar')

    def test_producer_key_is_stable_across_days(self):
        self.r.latest()
        self.r.publish()
        keys = [k for _, _, k in self.store.saved]
        self.r.publish()  # 同一天再发一次，键必须完全一样
        self.assertEqual(keys, [k for _, _, k in self.store.saved][:len(keys)])

    def test_cross_day_evidence_keys_do_not_confuse_title_identity(self):
        """22h approved fallback: no first-post gold => no speculative merge.

        Source-specific evidence keys change across buckets; store duplicate semantics
        remain untouched. Real identity and three-day evidence are tested on real Clues.
        """
        self.r.history('2026-10-09');self.r.publish(day='2026-10-09')
        first_keys={k for _,_,k in self.store.saved}
        unfreeze(self.r);self.r.history('2026-10-10')
        out=self.r.publish(day='2026-10-10')
        self.assertEqual(out['total'],18)
        keys=[k for _,_,k in self.store.saved]
        self.assertEqual(len(keys),36)
        self.assertEqual(len(set(keys)),36)
        self.assertTrue(first_keys.isdisjoint(set(keys)-first_keys))
        self.assertTrue(all(not p.get('duplicate') for p in out['published']))
        self.assertEqual(len(self.store.by_key),36)

    def test_topic_heat_travels_into_provenance(self):
        """22h：站方 heat 现在同时进入正式字段和依据。"""
        self.r.latest()
        self.r.publish(board='topics')
        for payload, snapshots, _ in self.store.saved:
            value=snapshots[0]['provenance']['heat']
            self.assertEqual(payload.get('heat',''),value)
            self.assertEqual(payload.get('heat_source',''),'site' if value else '')
        heats = {snapshots[0]['provenance']['heat'] for _, snapshots, _ in self.store.saved}
        self.assertEqual(heats, {'rising', 'peak', 'fading', ''})
        # 站方状态原样进了线索依据，不会因为删掉顶层键就丢掉
        self.assertTrue(any(snapshots[0]['provenance']['heat'] == 'rising'
                            for _, snapshots, _ in self.store.saved))

    def test_provenance_carries_enough_to_recheck_the_row(self):
        """M12：稳定 id / 原始曝光 / 逐日量 / 快照路径与哈希。"""
        self.r.latest()
        out = self.r.publish(board='topics')
        rows = self.store.saved
        self.assertEqual(len(rows), 14)
        self.assertEqual(len(out['published']), 14)
        for (_, snapshots, key) in rows:
            prov = snapshots[0]['provenance']
            self.assertTrue(prov['stable_id'])
            self.assertTrue(key.startswith(f"xradar:topics:{prov['stable_id']}:{prov['bucket']}:"))
            self.assertEqual(prov['board'], 'topics')
            self.assertTrue(prov['raw_views'])
            self.assertEqual(prov['marker'], f"collect:xradar:topics:{prov['stable_id']}")
            self.assertGreater(prov['posts'], 0)
            self.assertGreater(prov['talkers'], 0)
            self.assertGreater(prov['days'], 0)
            self.assertTrue(prov['page_snapshot']['path'])
            self.assertEqual(len(prov['page_snapshot']['hash']), 64)
            self.assertTrue(prov['day'])

    def test_lowfan_title_and_multiple(self):
        self.r.latest()
        self.r.publish(board='lowfan')
        titles = [p['title'] for p, _, _ in self.store.saved]
        self.assertTrue(all(t.startswith('【低粉爆款】') for t in titles))
        payload = self.store.saved[0][0]
        self.assertIn('152.4', payload['summary'])

    def test_lowfan_title_has_no_dangling_exposure_when_unparsed(self):
        """L4：views 为 None 时别留个「曝光 」空尾巴。"""
        markup = HEALTHY.replace('<strong>106.0万</strong>', '<strong>数据偏少</strong>')
        store = FakeStore(self.tmp)
        r = Radar(store, transport=lambda p: markup)
        r.latest()
        r.publish(board='lowfan')
        titles = [p['title'] for p, _, _ in store.saved]
        self.assertTrue(any(t.endswith('@Yuan8028') for t in titles), titles)
        self.assertFalse(any('曝光 ' in t and t.endswith('曝光 ') for t in titles), titles)

    def test_overseas_marks_unclaimed(self):
        self.r.latest()
        self.r.publish(board='global')
        for payload, _, _ in self.store.saved:
            self.assertTrue(payload['title'].startswith('【信息差】'))
            self.assertIn('中文区尚未有人讨论', payload['summary'])

    def test_unknown_board_rejected(self):
        with self.assertRaises(ValueError):
            self.r.publish(board='nope')

    def test_one_failure_does_not_stop_batch(self):
        original = self.store.clues._save
        calls = {'n': 0}

        def flaky(payload, snapshots, producer_key=None, observations=None):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('模拟单条失败')
            return original(payload, snapshots, producer_key, observations=observations)
        self.store.clues._save = flaky
        self.r.latest()
        out = self.r.publish()
        self.assertEqual(len(out['published']), 18)
        self.assertEqual(sum(1 for p in out['published'] if p.get('ok') is False), 1)

    def test_limit_caps_published(self):
        self.r.latest()
        out = self.r.publish(limit=2)
        self.assertEqual(len(out['published']), 2)

    def test_structure_change_raises_an_alert_and_writes_no_bad_rows(self):
        """H2：站方改容器类名 → 解析全空 → 告警线索 + publish 拒绝写坏数据。"""
        broken = (HEALTHY.replace('hwe-row', 'hwe-zzz')
                  .replace('hwe-ll', 'hwe-zz').replace('hwe-gl', 'hwe-gz'))
        store = FakeStore(self.tmp)
        r = Radar(store, transport=lambda p: broken)
        result = r.latest()
        self.assertFalse(result['health']['ok'])
        self.assertEqual(sum(result['counts'].values()), 0)
        alerts = store.clues_saved('xradar:health:')
        self.assertEqual(len(alerts), 1)
        payload, snapshots, _ = alerts[0]
        self.assertEqual(payload['priority'], 'important')
        self.assertIn('【xbangdan 结构变化告警】', payload['title'])
        self.assertIn(result['snapshot'], payload['summary'])
        self.assertTrue(snapshots[0]['excerpt'])
        out = r.publish()
        self.assertEqual(out['published'], [])
        self.assertIn('blocked', out)
        # 除了那条告警，没有任何榜单数据进了线索
        self.assertEqual(len(store.saved), 1)

    def test_alert_is_written_once_per_day(self):
        """同一页抓两次，告警线索只该有一条（producer_key 幂等，不刷屏）。"""
        broken = HEALTHY.replace('hwe-row', 'hwe-zzz')
        store = FakeStore(self.tmp)
        r = Radar(store, transport=lambda p: broken)
        r.latest()
        unfreeze(r)
        second = r.latest()
        self.assertEqual(len(store.clues_saved('xradar:health:')), 2)   # 两次都尝试写了
        alert_keys = {k for _, _, k in store.clues_saved('xradar:health:')}
        self.assertEqual(len(alert_keys), 1)
        self.assertEqual(alert_keys, {f"xradar:health:{second['day']}"})
        # 第二条命中 producer_key，没有新建
        self.assertEqual(store.saved[-1][2], f"xradar:health:{second['day']}")

    def test_alert_write_failure_is_reported_not_swallowed(self):
        """L5：真 clues_store 哪天拒收这条 payload，告警不能静默消失。

        `_save_clue` 吞掉所有异常只返回 `{'error': True}`，`_collect` 又不检查 ——
        结果结构异常既没告警、`runs.error` 里也看不出「告警没写成」。
        """
        broken = HEALTHY.replace('hwe-row', 'hwe-zzz')
        store = FakeStore(self.tmp)

        def refuse(payload, snapshots, producer_key=None):
            raise ValueError('模拟 clues_store 拒收：payload 漂移')
        store.clues._save = refuse
        r = Radar(store, transport=lambda p: broken)
        result = r.latest()
        self.assertFalse(result['health']['ok'])
        # 告警确实没写成，但这件事必须留下痕迹
        self.assertIn('告警', result['warning'])
        self.assertIn('失败', result['warning'])
        with r.db() as db:
            row = db.execute('SELECT error FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertIn('告警线索写入失败', row['error'])
        # 结构异常本身仍然成立，不能因为告警写失败就被当成正常
        self.assertFalse(r.status()['available'])
        self.assertTrue(r.status()['warning'])
        self.assertEqual(r.publish()['published'], [])

    def test_alert_success_leaves_no_failure_note(self):
        """反向：告警正常写入时，error 里不该出现「失败」字样。"""
        broken = HEALTHY.replace('hwe-row', 'hwe-zzz')
        store = FakeStore(self.tmp)
        r = Radar(store, transport=lambda p: broken)
        result = r.latest()
        self.assertNotIn('失败', result['warning'])
        with r.db() as db:
            row = db.execute('SELECT error FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertNotIn('告警线索写入失败', row['error'])


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_before_any_fetch(self):
        s = radar(self.tmp).status()
        self.assertFalse(s['available'])
        self.assertEqual(s['count'], 0)
        self.assertEqual(set(s['boards']), {'topics', 'lowfan', 'global'})

    def test_after_fetch(self):
        r = radar(self.tmp)
        r.latest()
        s = r.status()
        self.assertTrue(s['available'])
        self.assertEqual(s['count'], 18)
        self.assertEqual(s['extra']['last_counts'], {'topics': 14, 'lowfan': 2, 'global': 2})
        self.assertEqual(s['extra']['today_requests'], 1)
        self.assertEqual(s['warning'], '')
        self.assertTrue(s['extra']['health']['ok'])

    def test_all_zero_is_reported_as_unavailable(self):
        """M6：`counts={'topics':0,...}` 是非空 dict，truthy，原来会报「正常」。"""
        empty = '<html><body><div class="hwe-nothing">改版了</div></body></html>'
        r = radar(self.tmp, markup=empty)
        r.latest()
        s = r.status()
        self.assertFalse(s['available'])
        self.assertEqual(s['count'], 0)
        self.assertTrue(s['warning'])
        self.assertTrue(s['extra']['health']['counts'])

    def test_degraded_page_is_reported_as_unavailable(self):
        """行数掉到实测区间之外也要报异常，不能因为「有条数」就当正常。"""
        thin = healthy_markup(topics=5)          # 5 行，低于实测下限 12
        r = radar(self.tmp, markup=thin)
        r.latest()
        s = r.status()
        self.assertGreater(s['count'], 0)        # 条数还在
        self.assertFalse(s['available'])
        self.assertTrue(s['warning'])
        self.assertIn('行数', s['warning'])
        self.assertIn('结构', s['detail'])

    def test_state_loss_makes_status_unavailable(self):
        """状态标签占比掉到 0.5 以下：还有条数，但已经不可信。"""
        import re as _re
        r = radar(self.tmp, markup=_re.sub(r' data-s="[^"]*"', ' data-s=""', HEALTHY))
        r.latest()
        s = r.status()
        self.assertFalse(s['available'])
        self.assertEqual(s['count'], 18)         # 条数还在
        self.assertIn('状态标签', s['warning'])

    def test_real_page_status_is_available(self):
        store = FakeStore(self.tmp)
        r = Radar(store, transport=lambda p: real_page(REAL_HISTORY))
        r.latest()
        s = r.status()
        self.assertTrue(s['available'], s['warning'])
        self.assertEqual(s['count'], 26)
        self.assertEqual(s['detail'], 'xbangdan 选题雷达正常')
        self.assertTrue(s['extra']['page_snapshot'].startswith('pages/'))
        self.assertEqual(len(s['extra']['page_hash']), 64)

    def test_cooldown_reported(self):
        r = radar(self.tmp)
        r.latest()
        with r.db() as db:
            db.execute('UPDATE quota SET cooldown_until=?', (time.time() + 600,))
        s = r.status()
        self.assertFalse(s['available'])
        self.assertIn('冷却', s['detail'])
        self.assertTrue(s['extra']['cooling'])

    def test_sources_do_not_hit_network(self):
        rows = radar(self.tmp).sources()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['url'] == BASE + '/words/' for row in rows))


if __name__ == '__main__':
    unittest.main(verbosity=2)

class RealPublishHashTest(unittest.TestCase):
    def test_same_bucket_data_hash_append_overwrite_and_duplicate(self):
        from types import SimpleNamespace
        from pathlib import Path
        from clues_store import Clues
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            store = SimpleNamespace(workspace=Path(tmp))
            store.clues = Clues(store)
            radar = Radar(store, transport=lambda p: HEALTHY)
            radar.history('2026-10-10')
            captured = []
            original = store.clues._save
            def recording(*args, **kwargs):
                captured.append(args[2])
                return original(*args, **kwargs)
            with patch.object(store.clues, '_save', side_effect=recording):
                first = radar.publish(day='2026-10-10', board='topics', limit=1)['published'][0]
                self.assertNotIn('error', first)
                ident = first['clue_id']
                with radar.db() as db:
                    record = db.execute("SELECT key,data FROM items WHERE day=? AND board='topics' ORDER BY rowid LIMIT 1", ('2026-10-10',)).fetchone()
                    row = json.loads(record['data']); row['posts'] += 7
                    data = json.dumps(row, ensure_ascii=False)
                    db.execute("UPDATE items SET data=? WHERE day=? AND board='topics' AND key=?", (data, '2026-10-10', record['key']))
                second = radar.publish(day='2026-10-10', board='topics', limit=1)['published'][0]
                self.assertEqual(second['clue_id'], ident)
                self.assertFalse(second['duplicate'])
                self.assertEqual(captured[0].rsplit(':', 1)[0], captured[1].rsplit(':', 1)[0])
                self.assertNotEqual(captured[0].rsplit(':', 1)[1], captured[1].rsplit(':', 1)[1])
                self.assertEqual(captured[1].rsplit(':', 1)[1], hashlib.sha256(data.encode()).hexdigest())
                self.assertEqual(store.clues.detail(ident)['entries_total'], 2)
                observations = store.clues.observations(ident)
                self.assertEqual(len(observations), 1)
                self.assertEqual(observations[0]['mentions'], row['posts'])
                third = radar.publish(day='2026-10-10', board='topics', limit=1)['published'][0]
                self.assertTrue(third['duplicate'])
                self.assertEqual(captured[1], captured[2])
                self.assertEqual(store.clues.detail(ident)['entries_total'], 2)


class DaysLabelTest(unittest.TestCase):
    def row(self, key='a', days=0, first='https://x.com/i/status/111'):
        return dict(board='topics', key=key, category='ai', heat='rising',
                    title='合成火星探测器任务进展', summary='synthetic', meta='',
                    views=100, raw_views='100', days=days, posts=10, talkers=2,
                    url='https://xbangdan.com/words/', first_post_url=first)

    def test_days_zero_two_five_and_missing_readable_labels(self):
        for days in (0, 1, 2, 5, None):
            with self.subTest(days=days):
                row = self.row(days=days)
                if days is None:
                    row.pop('days')
                fact = Radar._fact(row)
                if days in (2, 5):
                    self.assertIn(f'站方标注：连续 {days} 天上榜', fact)
                else:
                    self.assertNotIn('连续 1 天上榜', fact)
                    self.assertNotIn('站方标注：连续', fact)

    def test_labels_do_not_change_gold_reuse_or_duplicates(self):
        from types import SimpleNamespace
        from pathlib import Path
        from clues_store import Clues
        with tempfile.TemporaryDirectory() as tmp:
            store = SimpleNamespace(workspace=Path(tmp)); store.clues = Clues(store)
            radar = Radar(store)
            def publish(day, row):
                with radar.db() as db:
                    db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)',
                               (day, 'topics', row['key'], json.dumps(row)))
                result = radar.publish(day=day)['published']
                self.assertTrue(all('error' not in item for item in result), result)
                return result
            first = publish('2026-10-01', self.row())
            reused = publish('2026-10-02', self.row('b', 2))
            self.assertEqual(first[0]['clue_id'], reused[0]['clue_id'])
            ident = reused[0]['clue_id']
            entry = store.clues.detail(ident)['entries'][0]
            self.assertIn('站方标注：连续 2 天上榜', entry['evidence'][0]['excerpt'])
            self.assertIn('站方标注：连续 2 天上榜', entry['note'])
            repeated = radar.publish(day='2026-10-02')['published'][0]
            self.assertTrue(repeated['duplicate'])
            self.assertEqual(store.clues.detail(ident)['entries_total'], 2)
            distinct = publish('2026-10-03', self.row('c', 5, 'https://x.com/i/status/222'))
            self.assertNotEqual(distinct[0]['clue_id'], ident)
            self.assertEqual(store.clues.listing()['total'], 2)
            self.assertIn('站方标注：连续 5 天上榜',
                          store.clues.detail(distinct[0]['clue_id'])['entries'][0]['evidence'][0]['excerpt'])

    def test_previous_key_stays_duplicate_after_label_change(self):
        from types import SimpleNamespace
        from pathlib import Path
        from clues_store import Clues
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            store = SimpleNamespace(workspace=Path(tmp)); store.clues = Clues(store)
            radar = Radar(store); row = self.row(days=2); data = json.dumps(row)
            key = 'xradar:topics:a:2026-10-02T00:' + hashlib.sha256(data.encode()).hexdigest()
            # Previously persisted evidence has no added label; exact producer key
            # remains the identity even when today's rendered fact would differ.
            saved = store.clues._save({'title': 'old'},
                     [{'kind': 'xradar', 'source': 'xradar', 'excerpt': 'old fact'}], key)
            with radar.db() as db:
                db.execute('INSERT INTO items VALUES(?,?,?,?,?)',
                           ('2026-10-02', 'topics', 'a', data, saved['id']))
            result = radar.publish(day='2026-10-02')['published'][0]
            self.assertTrue(result['duplicate'])
            self.assertEqual(store.clues.detail(saved['id'])['entries_total'], 1)
            self.assertEqual(store.clues.detail(saved['id'])['entries'][0]['evidence'][0]['excerpt'], 'old fact')
