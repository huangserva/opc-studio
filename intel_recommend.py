"""情报采集 · 二期 A：回收站与知识库里的公众号推荐订阅。

只读扫描 intake.db 的回收项链接与 knowledge.db 的文章链接，对每条公众号链接
判定它属于哪个号，汇总成「推荐订阅」列表。已订阅的号不再推荐；黄总点「订阅」
才真正加入，点「忽略」则记住不再推荐。

三个实测事实决定了这里的做法（都不是猜的）：
1. 旧式链接 https://mp.weixin.qq.com/s?__biz=… 的 __biz 就在 URL 里，**不需要请求**；
   但这类链接现在会被微信跳到 wappoc_appmsgcaptcha 验证页，抓不到号名。
2. 新式链接 https://mp.weixin.qq.com/s/<id> 普通 HTTP 可解析，同时给出 biz 与号名。
3. /mp/waerrpage 与 /mp/homepage 不是文章页，**不请求、不推荐**。

所以解析按「未解析的号」而不是「每条链接」限流：同一号的多条链接只请求一次，
已解析的按链接缓存，重复扫描不会重复请求。全部走普通 HTTP，不碰公众号后台。
"""
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from intel_wechat_mp import FAKEID, RateLimited, CredentialsMissing

# 已知形态的公众号链接。__biz 是免费的真值来源，优先用它。
OLD_STYLE = re.compile(r'^https?://mp\.weixin\.qq\.com/s\?')
NEW_STYLE = re.compile(r'^https?://mp\.weixin\.qq\.com/s/([A-Za-z0-9_-]{6,64})$')
# 不是文章页：删除提示页、账号主页。它们没有可推荐的号。
SKIP_PATH = re.compile(r'^/mp/(waerrpage|homepage|appmsg|profile)')
ARTICLE_PATH = re.compile(r'^/s/?$')
SCAN_LIMIT = 4000


def now():
    return datetime.now(timezone.utc).isoformat()


def read_only(path):
    """只读打开，绝不写入 intake.db / knowledge.db。"""
    return sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True, timeout=20)


def classify(url):
    """返回 (kind, fakeid)；kind 为 skip / known / needs_fetch。

    known    —— fakeid 已经确定（URL 里的 __biz），不需要任何请求
    needs_fetch —— 需要一次普通 HTTP 才能拿到 fakeid 与号名
    """
    if not isinstance(url, str) or 'mp.weixin.qq.com' not in url:
        return ('skip', '')
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return ('skip', '')
    if SKIP_PATH.match(parts.path):
        return ('skip', '')
    query = urllib.parse.parse_qs(parts.query)
    business = (query.get('__biz') or [''])[0].strip()
    if business and FAKEID.fullmatch(business):
        # 旧式链接：号已经在 URL 里，免费拿到。
        return ('known', business)
    if NEW_STYLE.match(url.strip()):
        return ('needs_fetch', '')
    if ARTICLE_PATH.match(parts.path):
        return ('needs_fetch', '')
    return ('skip', '')


class Recommender:
    """推荐订阅的读、解析与决策。只往 intel.db 写。"""

    def __init__(self, intel, fetch_limit=20, opener=None):
        self.intel = intel
        self.fetch_limit = fetch_limit
        self.opener = opener

    # ── 读取（只读） ──────────────────────────────────────────────────────
    @staticmethod
    def _drop_links(intake_path):
        """回收站链接。intake.db 不存在时如实返回空，不报错。"""
        path = Path(intake_path)
        if not path.is_file():
            return []
        connection = read_only(path)
        try:
            return connection.execute("SELECT id, url, title FROM drops WHERE url LIKE '%mp.weixin.qq.com%' LIMIT ?",
                                     (SCAN_LIMIT,)).fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()

    @staticmethod
    def _document_links(knowledge_path):
        path = Path(knowledge_path)
        if not path.is_file():
            return []
        connection = read_only(path)
        try:
            if Recommender._has_link_host(connection):
                # 迁移后：documents.link_host 是生成列且有索引，等值查找不再全表扫。
                return connection.execute("SELECT id, url, title FROM documents "
                                          "WHERE link_host=? LIMIT ?", ('mp.weixin.qq.com', SCAN_LIMIT)).fetchall()
            # 迁移前：库上还没有 link_host 列，退回原来的 LIKE 全表扫描，保证前后兼容。
            return connection.execute("SELECT id, url, title FROM documents "
                                      "WHERE url LIKE '%mp.weixin.qq.com%' LIMIT ?", (SCAN_LIMIT,)).fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()

    @staticmethod
    def _has_link_host(connection):
        """documents 表有没有迁移后的 link_host 生成列；没有就走 LIKE 兜底。

        必须用 table_xinfo：link_host 是 VIRTUAL 生成列，PRAGMA table_info 不列它。
        """
        try:
            return 'link_host' in {row[1] for row in connection.execute('PRAGMA table_xinfo(documents)')}
        except sqlite3.Error:
            return False

    def collect(self):
        """扫描两个库，把链接归入 link_index / recommendation_sources。"""
        workspace = self.intel.store.workspace
        rows = ([('intake', r[0], r[1], r[2]) for r in self._drop_links(workspace / 'inspiration' / 'intake.db')] +
                [('knowledge', r[0], r[1], r[2]) for r in self._document_links(workspace / 'knowledge' / 'knowledge.db')])
        counted = {'links': 0, 'known': 0, 'needs_fetch': 0, 'skipped': 0, 'accounts': 0}
        seen_links = set()
        with self.intel.db() as connection:
            for kind, source_id, url, title in rows:
                if not url:
                    continue
                # 同一链接出现在多处是正常的：只算一次链接，但每个来源都要记，
                # 否则「回收站和知识库都见过」这个信号会丢。
                fresh = url not in seen_links
                seen_links.add(url)
                if fresh:
                    counted['links'] += 1
                shape, business = classify(url)
                if shape == 'skip':
                    if fresh:
                        counted['skipped'] += 1
                    continue
                connection.execute('''INSERT INTO link_index(link,fakeid,status,source_kind) VALUES(?,?,'pending',?)
                    ON CONFLICT(link) DO UPDATE SET
                      fakeid=CASE WHEN link_index.fakeid!='' THEN link_index.fakeid ELSE excluded.fakeid END,
                      source_kind=CASE WHEN link_index.source_kind!='' THEN link_index.source_kind ELSE excluded.source_kind END''',
                    (url, business, kind))
                if business:
                    counted['known'] += int(fresh)
                    self._touch(connection, business, kind, source_id, url, title)
                elif fresh:
                    counted['needs_fetch'] += 1
            counted['accounts'] = connection.execute(
                'SELECT COUNT(DISTINCT fakeid) FROM recommendation_sources').fetchone()[0]
        return counted

    @staticmethod
    def _touch(connection, business, kind, source_id, url, title):
        moment = now()
        connection.execute('''INSERT INTO recommendation_sources(fakeid,source_kind,source_id,link,title,seen_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(source_kind,source_id,link) DO NOTHING''',
            (business, kind, source_id, url, (title or '')[:200], moment))
        # 次数 = 不同来源条数，不是扫描轮数；否则每扫一次就虚增。
        connection.execute('''INSERT INTO recommendations(fakeid,count,first_seen,last_seen)
            VALUES(?,(SELECT COUNT(*) FROM recommendation_sources WHERE fakeid=?),?,?)
            ON CONFLICT(fakeid) DO UPDATE SET
              count=(SELECT COUNT(*) FROM recommendation_sources WHERE fakeid=excluded.fakeid),
              last_seen=excluded.last_seen''',
            (business, business, moment, moment))

    # ── 解析（普通 HTTP，不碰后台） ────────────────────────────────────────
    def live_links(self, workspace=None):
        """两个库里现存的公众号链接（去重后的 URL 集合）。

        批量删除时由上层只算一次再交给 forget_intake_source，避免逐条重扫知识库；
        单条删除不预扫，forget_intake_source 里现算一次，单条路径照旧可用。
        """
        workspace = workspace or self.intel.store.workspace
        return ({row[1] for row in self._drop_links(workspace / 'inspiration' / 'intake.db')} |
                {row[1] for row in self._document_links(workspace / 'knowledge' / 'knowledge.db')})

    @staticmethod
    def _drop_dead_links(connection, dead):
        """删掉这些「两库都不再有」的链接的全部来源与 link_index。

        返回 (清掉的来源条数, 清掉的 link_index 条数, 受影响的号集合)。
        调用方必须先用**当下**的 live 集合确认这些链接确实没了，才能保护另一个现存来源。
        """
        business = set()
        removed = 0
        for link in dead:
            business |= {row['fakeid'] for row in connection.execute(
                'SELECT fakeid FROM recommendation_sources WHERE link=?', (link,)) if row['fakeid']}
            removed += connection.execute('DELETE FROM recommendation_sources WHERE link=?', (link,)).rowcount
        links_removed = 0
        for link in dead:
            links_removed += connection.execute('DELETE FROM link_index WHERE link=?', (link,)).rowcount
        return removed, links_removed, business

    @staticmethod
    def _refresh_counts(connection, fakeids):
        """按剩下的来源条数重算这些号的推荐次数；没来源且未处理过就删掉推荐行。"""
        for fake in fakeids:
            left = connection.execute('SELECT COUNT(*) FROM recommendation_sources WHERE fakeid=?',
                                      (fake,)).fetchone()[0]
            moment = now()
            connection.execute('''INSERT INTO recommendations(fakeid,count,first_seen,last_seen) VALUES(?,?,?,?)
                ON CONFLICT(fakeid) DO UPDATE SET count=excluded.count,last_seen=excluded.last_seen''',
                               (fake, left, moment, moment))
            connection.execute("DELETE FROM recommendations WHERE fakeid=? AND count=0 AND status='new'", (fake,))

    def _links_still_referenced(self, links, workspace):
        """这些链接里，哪些**还有**真实引用（知识库文档或回收站回收项）。

        与 `live_links()` 同一个口径 ——「两个库里现存的公众号链接」—— 但只对传入的
        这几个链接**定点**判断，不做全库扫描。

        **调用前必须确认知识库有 `link_host` 索引**（`forget_document_cleanup` 会先查，
        没有就直接跳过对账、不调本方法）。理由：定点查询只有在 `link_host` 生成列 +
        索引上才是真的「点」查；没有索引时它会退化成全表扫 —— 而「删掉最后一份引用」
        必然未命中，必须把 17.6 万行扫完（实测中位 8.5 秒，/tmp 的 .backup 正式库副本）。
        宁可不做对账（留下孤儿由 `check_link_index_orphans.py` 发现），也不让删除被
        拖到秒级。

        口径一致性：`link_index` 只可能因为 `collect()` 见过某个链接而插入，而
        `collect()` 只收录 `classify()` 不判 skip 的链接，所以只查公众号链接足够。
        """
        wanted = [str(link) for link in (links or []) if link]
        if not wanted:
            return set()
        alive = set()
        knowledge = Path(workspace) / 'knowledge' / 'knowledge.db'
        if knowledge.is_file():
            connection = read_only(knowledge)
            try:
                # 一篇文档可能带多个链接，逐个问；走 documents_link_host 索引，毫秒级。
                for link in wanted:
                    if connection.execute(
                            "SELECT 1 FROM documents WHERE link_host=? AND url=? LIMIT 1",
                            ('mp.weixin.qq.com', link)).fetchone():
                        alive.add(link)
            finally:
                connection.close()
        intake = Path(workspace) / 'inspiration' / 'intake.db'
        if intake.is_file():
            connection = read_only(intake)
            try:
                for link in wanted:
                    if link in alive:
                        continue
                    if connection.execute(
                            "SELECT 1 FROM drops WHERE url=? LIMIT 1", (link,)).fetchone():
                        alive.add(link)
            finally:
                connection.close()
        return alive

    def forget_document_cleanup(self, document_ids):
        """删掉知识库文档后的收尾：精确清来源行，有索引时再对账它的链接。

        `forget_document_sources` 只做前者，所以「删掉最后一个引用某 URL 的文档」时
        `link_index` 里那行没人管 —— URL 已经没有任何真实引用却还留着（孤儿行）。
        回收项批删路径批末有 `reconcile_links` 兜底，直接删除入口没有。

        有 `link_host` 索引时，复用 `_links_still_referenced` 的定点口径，只问被删文档
        自己那几个 URL：仍被别的文档/回收项引用的**不动**，彻底没人引用的才清到真实
        状态。全程不调 `live_links()`，所以不做全库扫描（合同第 1 条）。

        **没有索引就不做对账**（23f-2 保险）：定点查询只有在索引上才便宜。没有
        `link_host` 时它会退化成全表扫，而「删掉最后一份引用」必然未命中 —— 实测
        单次删除中位 4.4 秒、批删 50 篇约 218 秒（/tmp 的 .backup 正式库副本）。
        迁移若失败或被跳过，不能让删除被拖到这个量级。此时退回 23e 行为：只按文档
        清来源行 + 重算推荐（这部分不碰知识库，永远便宜），留下的 `link_index` 孤儿
        由 `coordination/tools/check_link_index_orphans.py` 只读发现，不在删除路径上
        偷偷修。返回值里 `reconciled=False` 并写明原因。

        钩子已提交删除，这一步失败不该推翻删除结果 —— 抛出去由 knowledge_store 收进
        `hook_error`。

        返回 {'sources': n, 'links': n, 'accounts': n, 'reconciled': bool, 'note': str}。
        """
        ids = [str(one) for one in (document_ids or []) if one]
        result = {'sources': 0, 'links': 0, 'accounts': 0, 'reconciled': False, 'note': ''}
        if not ids:
            result['note'] = '没有要清理的文档 id'
            return result
        # 先记下这些文档带过哪些链接：来源行马上要删，删完就查不到归属了。
        workspace = self.intel.store.workspace
        links = set()
        with self.intel.db() as connection:
            for one in ids:
                for row in connection.execute(
                        "SELECT DISTINCT link FROM recommendation_sources"
                        " WHERE source_kind='knowledge' AND source_id=?", (one,)):
                    if row['link']:
                        links.add(row['link'])
        removed, _ = self.forget_document_sources(ids)
        result['sources'] = removed
        if not links:
            result['note'] = '这些文档没有公众号链接，无需 URL 级对账'
            return result
        # 保险闸门：没有 link_host 索引就不做 URL 级对账（见上面 docstring 的理由）。
        knowledge = Path(workspace) / 'knowledge' / 'knowledge.db'
        indexed = False
        skip_reason = "缺 link_host 列"
        if knowledge.is_file():
            connection = read_only(knowledge)
            try:
                if self._has_link_host(connection):
                    skip_reason = '缺有效 link_host 索引'
                    # 非局部索引的首个键须为 link_host，且匹配等值查询的 BINARY 排序规则。
                    # 列存在不代表索引已装载；不改变共用 _has_link_host 的列判定语义。
                    for index in connection.execute("SELECT name, partial FROM pragma_index_list('documents')"):
                        if index[1]:
                            continue
                        keys = connection.execute(
                            'SELECT name, coll FROM pragma_index_xinfo(?) WHERE key=1 ORDER BY seqno',
                            (index[0],)).fetchall()
                        if keys and keys[0][0] == 'link_host' and keys[0][1] == 'BINARY':
                            indexed = True
                            break
            finally:
                connection.close()
        if not indexed:
            result['note'] = ('URL 级对账已跳过：' + skip_reason + '，'
                              'link_index 孤儿行由 check_link_index_orphans.py 发现')
            return result
        alive = self._links_still_referenced(links, workspace)
        dead = sorted(links - alive)
        if not dead:
            result['reconciled'] = True
            result['note'] = '这些链接仍被别处引用，link_index 保留'
            return result
        with self.intel.db() as connection:
            dead_removed, links_removed, business = self._drop_dead_links(connection, dead)
            self._refresh_counts(connection, business)
        result['sources'] = removed + dead_removed
        result['links'] = links_removed
        result['accounts'] = len(business)
        result['reconciled'] = True
        result['note'] = '已按 link_host 索引对账 %d 个链接，清掉 %d 行 link_index' % (
            len(links), links_removed)
        return result

    def forget_document_sources(self, document_ids):
        """删掉知识库文档时，精确清掉**这些文档自己**的来源行（粒度 = 文档，不是 URL）。

        为什么需要它：`recommendation_sources` 的主键是 `(source_kind, source_id, link)`，
        knowledge 来源的 `source_id` 就是文档 id。而 URL 级对账（`forget_intake_source` /
        `reconcile_links`）只在「两库都不再有这个 URL」时才清 —— 同一 URL 在知识库有
        多份文档时，删掉其中一份，那份文档的来源行会一直留着，推荐次数多算 1。

        这里按 `(source_kind='knowledge', source_id=<文档 id>)` 精确删，**不碰 link_index
        与推荐行**：URL 还被别的文档引用着时，那两样必须保留，交给 URL 级对账决定。
        只动 recommendation_sources 一张表，**不做知识库扫描**，所以不影响 23d 定下的
        「每批最多 2 次扫描」上限。

        返回 (清掉的来源条数, 受影响的号数)。
        """
        ids = [str(one) for one in (document_ids or []) if one]
        if not ids:
            return 0, 0
        business = set()
        removed = 0
        with self.intel.db() as connection:
            for one in ids:
                rows = connection.execute(
                    "SELECT fakeid FROM recommendation_sources"
                    " WHERE source_kind='knowledge' AND source_id=?", (one,)).fetchall()
                business |= {row['fakeid'] for row in rows if row['fakeid']}
                removed += connection.execute(
                    "DELETE FROM recommendation_sources"
                    " WHERE source_kind='knowledge' AND source_id=?", (one,)).rowcount
            # 只重算受影响的号：按剩下的来源条数算；没来源且未处理过就删掉推荐行。
            self._refresh_counts(connection, business)
        return removed, len(business)

    def forget_intake_source(self, links, drop_id, live=None):
        """回收项删了，把它带进推荐订阅的痕迹一并清掉。

        只清 intake 来源；同一链接如果知识库或别的回收项里还在，link_index 行保留。
        次数按剩下的来源条数重算；没来源了、且黄总还没处理过这个号，才删掉推荐行。
        live 是「两个库里现存的公众号链接」快照：批量删除时上层只算一次传进来，
        为 None 时这里现算（单条删除路径）。
        """
        if not isinstance(drop_id, str) or not re.fullmatch(r'[a-f0-9]{32}', drop_id):
            raise ValueError('回收项编号格式无效')
        wanted = [str(link) for link in (links or []) if link]
        workspace = self.intel.store.workspace
        if live is None:
            live = self.live_links(workspace)
        dead = [link for link in wanted if link not in live]
        with self.intel.db() as connection:
            business = {row['fakeid'] for row in connection.execute(
                "SELECT fakeid FROM recommendation_sources WHERE source_kind='intake' AND source_id=?",
                (drop_id,)) if row['fakeid']}
            removed = connection.execute(
                "DELETE FROM recommendation_sources WHERE source_kind='intake' AND source_id=?",
                (drop_id,)).rowcount
            dead_removed, links_removed, dead_business = self._drop_dead_links(connection, dead)
            business |= dead_business
            self._refresh_counts(connection, business)
            return {'sources': removed + dead_removed, 'links': links_removed, 'accounts': len(business)}

    def reconcile_links(self, links):
        """批末对账：这些链接如果在两个库里都不再存在，就把痕迹清到真实状态。

        批量删除用的是「删除前」的 live 快照，本批才删掉的链接在快照里仍算存在，
        于是 knowledge 来源与推荐计数会残留。删除全部做完后调用本方法，用**新算**的
        live 集合（这是本批的第 2 次知识库扫描）对这批涉及的 URL 统一清一次：
        清掉 recommendation_sources（所有来源）、link_index，并重算受影响号的次数。
        仍被别的来源引用的链接在 live 里，不会被误清。
        """
        wanted = sorted({str(link) for link in (links or []) if link})
        if not wanted:
            return {'sources': 0, 'links': 0, 'accounts': 0}
        live = self.live_links()
        dead = [link for link in wanted if link not in live]
        if not dead:
            return {'sources': 0, 'links': 0, 'accounts': 0}
        with self.intel.db() as connection:
            removed, links_removed, business = self._drop_dead_links(connection, dead)
            self._refresh_counts(connection, business)
            return {'sources': removed, 'links': links_removed, 'accounts': len(business)}

    def pending(self):
        """待解析链接：只挑还没有 fakeid 的，同一链接不会重复解析。"""
        with self.intel.db() as connection:
            return [row['link'] for row in connection.execute(
                "SELECT link FROM link_index WHERE fakeid='' AND status!='failed' ORDER BY resolved_at, link LIMIT ?",
                (self.fetch_limit,))]

    def resolve(self):
        """按间隔解析最多 fetch_limit 条。命中限流立即停，不重试。"""
        budget = self.pending()[:self.fetch_limit]
        client = self.intel.client()
        result = {'attempted': 0, 'resolved': 0, 'failed': 0, 'stopped': ''}
        for link in budget:
            if result['stopped']:
                break
            try:
                parsed = client.fetch_article(link)
            except RateLimited as exc:
                result['stopped'] = f'{exc}；本轮停止，不再重试'
                break
            except CredentialsMissing as exc:
                result['stopped'] = str(exc)
                break
            except (ValueError, OSError) as exc:
                result['attempted'] += 1
                result['failed'] += 1
                self._mark(link, '', '', 'failed', str(exc)[:200])
                continue
            result['attempted'] += 1
            result['resolved'] += 1
            self._mark(link, parsed['biz'], parsed['nickname'], 'resolved', '')
            with self.intel.db() as connection:
                # 归到这条链接原本的来源，不新造一个来源，否则界面会出现两条「知识库」。
                origin = connection.execute('SELECT source_kind FROM link_index WHERE link=?', (link,)).fetchone()
                kind = (origin['source_kind'] if origin and origin['source_kind'] else 'knowledge')
                source_id = connection.execute(
                    'SELECT source_id FROM recommendation_sources WHERE link=? LIMIT 1', (link,)).fetchone()
                self._touch(connection, parsed['biz'], kind,
                             source_id['source_id'] if source_id else link[:32], link, parsed.get('title', ''))
        return result

    def _mark(self, link, business, nickname, status, detail):
        with self.intel.db() as connection:
            connection.execute('UPDATE link_index SET fakeid=?,nickname=?,status=?,detail=?,resolved_at=? WHERE link=?',
                               (business, nickname, status, detail, now(), link))

    # ── 决策 ──────────────────────────────────────────────────────────────
    def listing(self):
        """推荐订阅列表：号名、fakeid、来源、出现次数。已订阅与已忽略的不再出现。"""
        with self.intel.db() as connection:
            subscribed = {row['fakeid'] for row in connection.execute('SELECT fakeid FROM subscriptions')}
            ignored = {row['fakeid'] for row in connection.execute(
                "SELECT fakeid FROM recommendations WHERE status='ignored'")}
            rows = connection.execute('''SELECT r.fakeid, r.nickname, r.count, r.status,
                    (SELECT nickname FROM link_index l WHERE l.fakeid=r.fakeid AND l.nickname!='' LIMIT 1) AS from_link
                FROM recommendations r ORDER BY r.count DESC, r.last_seen DESC LIMIT 200''').fetchall()
            items, unresolved = [], 0
            for row in rows:
                if row['fakeid'] in subscribed or row['fakeid'] in ignored:
                    continue
                sources = connection.execute(
                    'SELECT source_kind, COUNT(*) AS n FROM recommendation_sources WHERE fakeid=? '
                    'GROUP BY source_kind ORDER BY n DESC', (row['fakeid'],)).fetchall()
                examples = connection.execute(
                    'SELECT source_kind, source_id, title FROM recommendation_sources WHERE fakeid=? '
                    'GROUP BY source_kind LIMIT 4', (row['fakeid'],)).fetchall()
                items.append({'fakeid': row['fakeid'], 'nickname': row['nickname'] or row['from_link'] or '',
                              'count': row['count'],
                              'sources': [{'kind': s['source_kind'], 'count': s['n']} for s in sources],
                              'examples': [{'kind': s['source_kind'], 'id': s['source_id'], 'title': s['title']}
                                           for s in examples],
                              'name_known': bool(row['nickname'] or row['from_link'])})
            pending = connection.execute(
                "SELECT COUNT(*) FROM link_index WHERE fakeid='' AND status!='failed'").fetchone()[0]
            failed = connection.execute("SELECT COUNT(*) FROM link_index WHERE status='failed'").fetchone()[0]
        return {'items': items, 'pending_links': pending, 'failed_links': failed,
                'ignored': len(ignored), 'subscribed': len(subscribed)}

    def summary(self):
        with self.intel.db() as connection:
            return {'accounts': connection.execute('SELECT COUNT(*) FROM recommendations').fetchone()[0],
                    'links': connection.execute('SELECT COUNT(*) FROM link_index').fetchone()[0],
                    'pending': connection.execute(
                        "SELECT COUNT(*) FROM link_index WHERE fakeid='' AND status!='failed'").fetchone()[0]}

    def ignore(self, business):
        """忽略：记住不再推荐，但不删除任何来源记录。"""
        from intel_wechat_mp import fakeid as normalise
        business = normalise(business)
        with self.intel.db() as connection:
            connection.execute("UPDATE recommendations SET status='ignored' WHERE fakeid=?", (business,))
        return {'fakeid': business, 'ignored': True}

    def unignore(self, business):
        from intel_wechat_mp import fakeid as normalise
        business = normalise(business)
        with self.intel.db() as connection:
            connection.execute("UPDATE recommendations SET status='new' WHERE fakeid=?", (business,))
        return {'fakeid': business, 'ignored': False}