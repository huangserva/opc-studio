"""工作台启动装配；应用依赖由 server.main 显式传入。"""
import argparse
import os
from pathlib import Path
from http.server import ThreadingHTTPServer

def seed_intel_schedule(schedules, workspace):
    """登记「公众号订阅（每日抓最新）」。幂等：已存在就原样保留，不覆盖黄总的改动。

    初始 enabled=False —— 按合同，必须黄总在页面点「开启」才会每天自动跑。
    """
    try:
        existing = next((t for t in schedules.listing()['tasks'] if t['kind'] == 'intel_wechat_mp'), None)
        if existing:
            return existing
        saved = schedules.save({'name': '公众号订阅（每日抓最新）', 'kind': 'intel_wechat_mp',
            'body': '每天对已订阅的公众号各抓最新 1 页，新文章入库待确认。', 'cwd': str(workspace),
            'frequency': 'daily', 'at': '08:00', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
            'timeout': 1800, 'enabled': False, 'allow_commands': False, 'result_destination': 'runs'})
        return next((t for t in schedules.listing()['tasks'] if t['id'] == saved['id']), saved)
    except (OSError, ValueError, KeyError):
        return None  # 登记失败不阻断工作台启动；页面会如实提示「未登记」


def wire_intake_analysis(server, store):
    """收下即分析的接线：只复用「创意提炼」已配置的模型，不新增服务、不读密钥。

    优先用定时任务「创意提炼（Pi）」选的模型（即使该任务当前处于关闭状态）；
    没有则退回工作台助手当前模型。两者都没配置时返回 None，分析会如实记为可重试失败。
    """
    def model():
        profile_id = None
        try:
            tasks = server.schedules.listing()['tasks']
        except (OSError, ValueError, KeyError):
            tasks = []
        for task in tasks:
            if task['kind'] == 'intake_cards' and task.get('profile_id'):
                profile_id = task['profile_id']
                break
        if profile_id:
            try:
                config = server.schedules.model_config(profile_id)
            except ValueError:
                config = None
            if config:
                config['profile_id'] = profile_id
                return config
        if not server.assistant:
            return None
        try:
            config = server.assistant.config()
        except ValueError:
            return None
        config['profile_id'] = server.assistant.model_document().get('active_profile_id')
        return config

    def busy(config):
        profile_id = (config or {}).get('profile_id')
        if not profile_id:
            return False
        return server.schedules.model_in_use(profile_id)

    cards = store.intake_cards
    cards.model_provider = model
    cards.busy_probe = busy
    return cards


def wire_intake_media(store):
    """图片识别接工作台当前模型配置：复用即时分析那条模型与繁忙探测。

    不新建服务、不新建模型配置、不读密钥：config 只在进程内递给 PiRuntime，
    和聊天看图、定时提炼走同一条 chat/completions 通道。
    """
    from intake_media_recognition import MediaRecognizer, ProfileVision
    cards, intake = store.intake_cards, store.creative_intake
    if intake.media_recognizer is None:
        intake.media_recognizer = MediaRecognizer(
            ProfileVision(cards.analysis_model, cards.model_busy, cards.pi_folder))
    return intake.media_recognizer


class _IntakeLedger:
    """删除回收项时的接线：同步清掉情报侧留下的链接痕迹。

    清三样：link_index 里只剩这一条来源的链接、推荐订阅里的 intake 来源、
    情报文章上的「已进回收站」标记。没跑过情报采集（intel.db 不存在）就不新建库。

    单条 delete：__call__(links, drop_id) 里现算 live（与以前一致）。
    批量 delete_many：先 prepare() 拿一份 live 快照逐条透传（省掉逐条全表扫描）；
    但删除前快照会把「本批才删掉的链接」也算成存在，留下 knowledge 来源与推荐计数残留，
    所以批末再 finalize() 用新算的 live 对本批涉及的 URL 统一对账一次（本批第 2 次扫描）。
    """

    def __init__(self, store):
        self.store = store
        self._live = None
        self._touched = set()

    def _ready(self):
        return (self.store.workspace / 'intel' / 'intel.db').is_file()

    def prepare(self):
        self._live = None
        self._touched = set()
        if not self._ready():
            return None
        self._live = self.store.intel_recommend.live_links()
        return self._live

    def __call__(self, links, drop_id, live=None):
        if not self._ready():
            return
        if live is None:
            live = self._live if self._live is not None else self.store.intel_recommend.live_links()
        for link in (links or []):
            if link:
                self._touched.add(link)
        self.store.intel.forget_intake_drop(drop_id)
        self.store.intel_recommend.forget_intake_source(links, drop_id, live=live)

    def finalize(self):
        if not self._ready() or not self._touched:
            return
        touched, self._touched = self._touched, set()
        self.store.intel_recommend.reconcile_links(touched)


def wire_intake_links(store):
    """把情报侧的删除收尾接到回收项与知识库上，并返回接线对象。

    两处：
    - `creative_intake.link_ledger`：回收项删了要清链接痕迹；
    - `knowledge.document_removed_hook`：知识库正文删了要按**文档**精确清推荐来源行。
      推荐来源的主键是 `(source_kind, source_id, link)`，`source_id` 就是文档 id，
      而清理链接痕迹只按 URL 判 —— 同一 URL 有多份文档时，删掉一份会留下一条孤儿来源，
      推荐次数多算 1（reverify-perf-knowledge-report.md §6）。
    """
    ledger = _IntakeLedger(store)
    store.creative_intake.link_ledger = ledger

    def forget_document(document_id):
        # 情报侧没跑过（intel.db 不存在）就不动：删个回收项不该凭空建一个情报库。
        # 与 _IntakeLedger._ready 同一口径。
        if not (Path(store.workspace) / 'intel' / 'intel.db').is_file():
            return 0, 0
        # 用完整的收尾（清来源行 + 对账该文档的链接），不只是清来源行：
        # 直接删除入口没有批末 finalize 来做 URL 级对账，只清来源行会让 link_index
        # 留下已无真实引用的孤儿行（review-recommend-doc-granularity-report.md §3 L1）。
        return store.intel_recommend.forget_document_cleanup([document_id])

    store.knowledge.document_removed_hook = forget_document
    return ledger


def main(ROOT, Store, Handler, assistant_module, content, wire_outsourced_clues):
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default=str(ROOT / 'workspace'))
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--seed', help='Existing article package to show on first startup')
    args = parser.parse_args()
    store = Store(args.workspace)
    if args.seed and not (store.article('v100-qwen') / 'article.json').exists():
        store.commit('v100-qwen', Path(args.seed), '导入完整图文原稿', '原始文章包')
    store.scan(force=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.store = store
    store.knowledge.start_watch()
    __import__('library_search').start_catalog(store.workspace)
    from publishing.local_jobs import LocalDeliveries
    server.local_deliveries = LocalDeliveries(store)
    from generation.service import Generation
    server.generation = Generation(store)
    config_path = os.environ.get('ARTICLE_STUDIO_MODEL_CONFIG', str(ROOT / 'workspace/private/article-studio-model.json'))
    server.assistant = assistant_module.Assistant(store, content, config_path)
    server.assistant.generation = server.generation
    from scheduled_tasks import ScheduledTasks
    server.schedules = ScheduledTasks(store.workspace, server.assistant, autostart=False)
    store.schedules = server.schedules
    from clues_heat import recompute_heat
    server.schedules.heat_recomputer = lambda cancel, deadline: recompute_heat(store.clues,cancel=cancel,deadline=deadline)
    server.schedules.seed_heat_schedule(store.workspace)
    import gmail_view
    server.schedules.gmail_syncer = lambda cancel, deadline: gmail_view.scheduled_sync(gmail_view.service(server), cancel, deadline)
    try:
        server.schedules.seed_gmail_schedule(store.workspace)
    except (OSError, ValueError, KeyError):
        pass  # 登记失败不阻断工作台启动
    server.schedules.publisher = store.clues.from_run
    wire_outsourced_clues(server, store)
    server.schedules.source_analyzer = store.wechat_source.analyze_events
    server.schedules.source_receipt_reader = store.wechat_source.event_receipt
    server.schedules.wechat_analyzer = store.wechat_source.analyze_clues
    server.schedules.radar_analyzer = store.wechat_source.analyze_radar
    server.schedules.github_analyzer = store.github.analyze
    server.schedules.deals_analyzer = store.deals_review.analyze
    server.schedules.intake_analyzer = store.intake_cards.analyze
    wire_intake_analysis(server, store)
    wire_intake_media(store)
    wire_intake_links(store)
    server.schedules.intel_analyzer = store.intel.analyze_scheduled
    seed_intel_schedule(server.schedules, store.workspace)
    server.schedules.start()
    print(f'图文工作台：http://127.0.0.1:{server.server_port}', flush=True)
    print(f'投递目录：{store.inbox}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.schedules.close()
        store.knowledge.stop_event.set()
        store.knowledge.capture.close()
        server.generation.close()
        server.server_close()


