"""卡片页的持久化测试数据：只写启动器新建的临时 workspace。"""
import hashlib
import json
import uuid
from pathlib import Path


def seed(store):
    intake, cards = store.creative_intake, store.intake_cards
    manifest = []
    for n, channel in enumerate(('link', 'text', 'image', 'video', 'intel')):
        body = 'E2E卡片来源 %s：独立正文与四块摘要，供筛选、单张删除和清理验收。' % channel
        drop = intake.receive({'kind': 'text', 'text': body,
                               'reason': '情报采集 E2E' if channel == 'intel' else 'E2E卡片夹具'})
        drop = intake.resolve(drop['id'])
        with intake.db() as db:
            db.execute('UPDATE drops SET kind=?,url=?,received_at=? WHERE id=?',
                       ('text' if channel == 'intel' else channel,
                        'https://example.com/e2e-cards-link' if channel == 'link' else '',
                        '2026-01-%02dT00:00:00+00:00' % (n + 1), drop['id']))
        # image/video 是来源类型夹具，无上传文件或识别任务；不替换产品列表/删除方法。
        drop = intake.detail(drop['id'])
        document, refs = cards.evidence(drop)
        ids = []
        for j, status in enumerate(('candidate', 'confirmed', 'rejected')):
            card_id = uuid.uuid4().hex
            prompt = 'E2E %s %s：海湾倒影构图' % (channel, status)
            at = '2026-02-%02dT00:00:00+00:00' % (j * 5 + n + 1)
            with cards.db() as db:
                db.execute('''INSERT INTO cards(id,created_at,updated_at,drop_id,document_id,
                    prompt,model,scene,status,refs,prompt_sha) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                           (card_id, at, at, drop['id'], document['id'], prompt, 'qi21',
                            'E2E卡片页', status, json.dumps(list(refs.values()), ensure_ascii=False),
                            hashlib.sha256(prompt.encode()).hexdigest()))
            ids.append({'id': card_id, 'status': status, 'created_at': at})
        cards.mark_seen(drop, cards.fingerprint(drop))
        with cards.db() as db:
            db.execute('INSERT INTO analysis(drop_id,document_id,status,data) VALUES(?,?,?,?)',
                       (drop['id'], document['id'], 'done', json.dumps({
                           'summary': 'E2E %s 四块分析摘要' % channel, 'why': '具体构图',
                           'angles': [{'title': '海湾构图', 'how': '倒影'}]}, ensure_ascii=False)))
        manifest.append({'channel': channel, 'drop_id': drop['id'],
                         'document_id': document['id'], 'body': document['body'], 'cards': ids})
    Path(store.workspace, 'cards-fixture.json').write_text(json.dumps(manifest, ensure_ascii=False))
