"""Lifecycle business dates, migration and explicit evidence decisions on throwaway DBs."""
import hashlib,json,shutil,tempfile,unittest,uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from deals_store import Deals,DealConflict,STAGES,MAIN_STAGES,canonical_stage,stage_state,beijing_today
from deals_evidence import lifecycle_candidates,evidence_date
from deals_review import DealsReview
from scripts.migrate_deals_lifecycle import backup,sha,reconciliation

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=SimpleNamespace(workspace=Path(self.tmp.name));self.deals=self.store.deals=Deals(self.store)
    def create(self,**kw):return self.deals.save(dict(dict(title='合成项目',customer='合成品牌',request_key=uuid.uuid4().hex),**kw))
    def edit(self,d,**kw):return self.deals.save(dict(id=d['id'],revision=d['revision'],request_key=uuid.uuid4().hex,**kw))
    def evidence(self,d,body='合成品牌合作，明天交稿',at='2026-10-01T23:30:00+00:00'):
        e=dict(id=uuid.uuid4().hex,state='candidate',text=body,title='合成品牌商务 · 客户',at=at,terms=['合成品牌'],provenance=dict(conversation_type='private',sender='客户'),url='/knowledge.html?document=fixture')
        with self.deals.db() as c:c.execute('INSERT INTO evidence VALUES(?,?,?)',(d['id'],e['id'],json.dumps(e,ensure_ascii=False)))
        return e
    def test_stage_aliases_readable_and_preparing_is_primary(self):
        self.assertEqual(MAIN_STAGES,('contact','negotiating','preparing','writing','draft','published','closed'))
        for stage in STAGES:
            d=self.create(stage=stage,settled=stage=='settled');self.assertEqual(self.deals.detail(d['id'])['stage'],stage)
        self.assertEqual(canonical_stage('in_progress'),'writing');self.assertEqual(canonical_stage('settled'),'closed')
    def test_changes_append_business_history_and_notes_do_not_reset_age(self):
        d=self.create(stage='contact');self.assertEqual(d['stage_history'][0]['entered_on'],beijing_today().isoformat())
        entered=(beijing_today()-timedelta(days=20)).isoformat()
        d=self.edit(d,stage='preparing',stage_entered_on=entered)
        self.assertEqual(d['lifecycle']['days'],20);self.assertEqual(len(d['stage_history']),2)
        d=self.edit(d,notes='只改备注');self.assertEqual(len(d['stage_history']),2);self.assertEqual(d['lifecycle']['days'],20)
        self.assertEqual(d['stage_history'][-1]['source'],'人工修改')
    def test_no_business_date_never_uses_import_or_update_clock(self):
        d=self.create(stage='writing',stage_entered_on='')
        with self.deals.db() as c:
            raw=json.loads(c.execute('SELECT data FROM deals WHERE id=?',(d['id'],)).fetchone()[0]);raw.update(created_at='2000-01-01',updated_at='2000-01-01');raw.pop('stage_history');c.execute('UPDATE deals SET data=? WHERE id=?',(json.dumps(raw),d['id']))
        result=self.deals.detail(d['id']);self.assertIsNone(result['lifecycle']['days']);self.assertEqual(result['lifecycle']['entered_on'],'')
        self.assertFalse(DealsReview(self.store).progress_check()['stalled'])
    def test_manual_past_node_does_not_change_current_stage_or_fake_current_date(self):
        d=self.create(stage='published',stage_entered_on='')
        d=self.deals.stage_date(d['id'],dict(revision=d['revision'],request_key=uuid.uuid4().hex,stage='contact',entered_on='2026-01-01'))
        self.assertEqual(d['stage'],'published');self.assertIsNone(d['lifecycle']['days']);self.assertEqual(d['contacted_on'],'2026-01-01')
        self.assertTrue(any('补充阶段日期' in a['action'] for a in d['audit']))
    def test_future_stage_dates_and_invalid_dates_are_rejected(self):
        d=self.create()
        for value in ['2026-02-30',(beijing_today()+timedelta(days=1)).isoformat()]:
            with self.assertRaises(ValueError):self.edit(d,stage='draft',stage_entered_on=value)
            with self.assertRaises(ValueError):self.deals.stage_date(d['id'],dict(revision=d['revision'],request_key=uuid.uuid4().hex,stage='contact',entered_on=value))
    def test_owner_paid_migration_is_atomic_idempotent_and_receipt_free(self):
        d=self.create(stage='unknown',stage_entered_on='')
        with self.deals.db() as c:
            raw=self.deals._read(c,d['id']);raw['original_group']='已付';self.deals._write(c,raw,'测试种子')
        first=self.deals.migrate_owner_paid(expected_count=1);digest=sha(self.deals.path)
        self.assertEqual(self.deals.migrate_owner_paid(expected_count=1),[]);self.assertEqual(digest,sha(self.deals.path))
        d=self.deals.detail(d['id']);self.assertEqual(d['stage'],'published');self.assertEqual(d['reported_payment'],'paid');self.assertEqual(d['cash']['status'],'reported_paid');self.assertFalse(d['receipts']);self.assertFalse(d['publications']);self.assertIsNone(d['lifecycle']['days']);self.assertEqual(len(first),1)
    def test_migration_refuses_changed_target_atomically(self):
        for stage in ('unknown','writing'):
            d=self.create(stage=stage)
            with self.deals.db() as c:
                raw=self.deals._read(c,d['id']);raw['original_group']='已付';self.deals._write(c,raw,'测试种子')
        digest=sha(self.deals.path)
        with self.assertRaises(DealConflict):self.deals.migrate_owner_paid(expected_count=2)
        self.assertEqual(digest,sha(self.deals.path))
    def test_rejected_evidence_cannot_be_confirmed_through_old_proposal(self):
        d=self.create(stage='unknown');e=self.evidence(d);proposal=self.deals.generate_lifecycle_proposals(d['id'])['candidates'][0]
        self.deals.review_evidence(d['id'],dict(revision=d['revision'],request_key=uuid.uuid4().hex,evidence_id=e['id'],state='rejected'))
        with self.assertRaises(DealConflict):self.deals.resolve_lifecycle(dict(proposal_id=proposal['id'],decision='confirmed'))
    def test_backup_rollback_preserves_exact_backup_sha(self):
        d=self.create();folder=Path(self.tmp.name)/'backup';hashes=backup(self.store.workspace,folder)
        self.edit(d,stage='draft');shutil.copy2(folder/'deals.db',self.deals.path)
        self.assertEqual(sha(self.deals.path),hashes['deals.db']);self.assertEqual(self.deals.detail(d['id'])['stage'],'contact')
    def test_cash_and_stage_remain_independent(self):
        published=self.create(stage='published');self.assertEqual(published['cash']['status'],'unpaid')
        d=self.create(stage='writing')
        with self.deals.db() as c:
            raw=self.deals._read(c,d['id']);raw['reported_payment']='paid';self.deals._write(c,raw,'测试手记')
        d=self.deals.detail(d['id']);self.assertEqual(d['stage'],'writing');self.assertEqual(d['cash']['status'],'reported_paid');self.assertEqual(d['cash']['totals']['CNY'],'0.00')
    def test_no_evidence_or_unrelated_news_produces_no_candidates(self):
        d=self.create(stage='unknown');self.assertEqual(self.deals.generate_lifecycle_proposals(d['id'])['created'],0)
        self.evidence(d,'AI日报 【模型发布】 合成品牌的新产品今天发布');self.assertEqual(self.deals.generate_lifecycle_proposals(d['id'])['created'],0)
        self.evidence(d,'合成品牌商单没找我');self.assertEqual(self.deals.generate_lifecycle_proposals(d['id'])['created'],0)
    def test_relative_dates_use_message_beijing_time_and_are_marked_inferred(self):
        value,basis=evidence_date('下周三交稿','2026-10-04T23:30:00+00:00')
        self.assertEqual(value,'2026-10-14');self.assertIn('推算',basis)
        value,basis=evidence_date('下周交稿','2026-10-04T23:30:00+00:00');self.assertEqual(value,'2026-10-12');self.assertIn('未指明',basis)
        d=self.create(stage='unknown');e=self.evidence(d)
        out=self.deals.generate_lifecycle_proposals(d['id']);p=next(p for p in out['candidates'] if 'deadline' in p['changes']);self.assertEqual(p['changes']['deadline'],'2026-10-03');self.assertEqual(p['evidence']['id'],e['id']);self.assertIn('推算',p['reason'])
    def test_versions_and_planned_publications_are_not_actual_dates(self):
        self.assertEqual(evidence_date('seed audio 1.5功能测评，10.28发布','2026-10-22T09:00:00Z')[0],'2026-10-28')
        self.assertEqual(evidence_date('版本1.5功能测评','2026-10-22T09:00:00Z')[0],'')
        self.assertEqual(evidence_date('下周的周报','2026-10-22T09:00:00Z')[0],'2026-10-26')
        self.assertEqual(evidence_date('周报','2026-10-22T09:00:00Z')[0],'')
        d=self.create(stage='unknown');self.evidence(d,'合成品牌老师，seed audio 1.5功能测评，10.28发布，老师有档期吗？今天要发布。还请今天发布了哈。')
        items=self.deals.generate_lifecycle_proposals(d['id'])['candidates'];self.assertFalse(any('published_on' in p['changes'] for p in items))
        d=self.create(stage='unknown');self.evidence(d,'合成品牌本周发布的，因为开白耽误，周四交初稿也行','2026-09-08T02:10:13+00:00')
        items=self.deals.generate_lifecycle_proposals(d['id'])['candidates'];self.assertEqual(next(p['changes']['deadline'] for p in items if 'deadline' in p['changes']),'2026-09-10');self.assertFalse(any('published_on' in p['changes'] for p in items))
    def test_confirm_modify_and_reject_are_field_scoped_idempotent_audited(self):
        d=self.create(stage='unknown');self.evidence(d);before=self.deals.detail(d['id']);out=self.deals.generate_lifecycle_proposals(d['id'])
        self.assertEqual(self.deals.detail(d['id'])['revision'],before['revision']);self.assertEqual(self.deals.detail(d['id'])['deadline'],'')
        deadline=next(p for p in out['candidates'] if 'deadline' in p['changes']);contact=next(p for p in out['candidates'] if 'contacted_on' in p['changes'])
        d=self.deals.resolve_lifecycle(dict(proposal_id=deadline['id'],decision='confirmed',value='2026-10-10'))
        self.assertEqual(d['deadline'],'2026-10-10');self.assertTrue(any('确认微信' in a['action'] for a in d['audit']))
        revision=d['revision'];self.assertEqual(self.deals.resolve_lifecycle(dict(proposal_id=deadline['id'],decision='confirmed'))['revision'],revision)
        d=self.deals.resolve_lifecycle(dict(proposal_id=contact['id'],decision='rejected'));self.assertEqual(d['contacted_on'],'')
        self.deals.generate_lifecycle_proposals(d['id']);self.assertNotIn(contact['id'],[p['id'] for p in self.deals.pending_proposals()])
        with self.assertRaises(DealConflict):self.deals.resolve_lifecycle(dict(proposal_id=contact['id'],decision='confirmed'))
    def test_stale_field_cannot_overwrite_new_manual_value(self):
        d=self.create(stage='unknown');self.evidence(d);p=next(p for p in self.deals.generate_lifecycle_proposals(d['id'])['candidates'] if 'deadline' in p['changes'])
        self.edit(d,deadline='2026-10-20')
        with self.assertRaises(DealConflict):self.deals.resolve_lifecycle(dict(proposal_id=p['id'],decision='confirmed'))
    def test_cash_date_confirmation_does_not_invent_money_or_settlement(self):
        d=self.create(stage='unknown');self.evidence(d,'合成品牌的7000刚到账了')
        p=next(p for p in self.deals.generate_lifecycle_proposals(d['id'])['candidates'] if 'paid_on' in p['changes'])
        d=self.deals.resolve_lifecycle(dict(proposal_id=p['id'],decision='confirmed'));self.assertFalse(d['receipts']);self.assertEqual(d['cash']['status'],'unpaid');self.assertEqual(d['date_events'][0]['kind'],'paid_on');self.assertTrue(d['business_dates'])
    def test_reconciliation_preserves_each_nonempty_owner_line_without_writes(self):
        d=self.create(title='合成项目',quote='100',currency='CNY');path=Path(self.tmp.name)/'owner.md';path.write_text('# 清单\n已付\n合成项目  200元\n缺失项目 300元\nhttps://example.test\n')
        digest=sha(self.deals.path);data=reconciliation(path,[d]);self.assertEqual(len(data['rows']),3);self.assertIn('金额不一致',data['rows'][0]['status']);self.assertIn('库里没有',data['rows'][1]['status']);self.assertEqual(digest,sha(self.deals.path))

    def contact_bundle(self):
        return json.loads((Path(__file__).resolve().parents[1]/'scripts/deals-lifecycle-contact-20261101.json').read_text())
    def test_confirmed_new_order_migration_keeps_dates_and_stage_unconfirmed(self):
        old=self.create(title='AI for Math',stage='unknown',stage_entered_on='')
        with self.deals.db() as c:before=self.deals._read(c,old['id'])
        r=self.deals.migrate_owner_contact_bundle(self.contact_bundle());self.assertTrue(r['created']);self.assertEqual(r['proposals_added'],7)
        new=self.deals.detail(r['new_deal_id']);self.assertEqual(new['stage'],'unknown');self.assertIsNone(new['lifecycle']['days'])
        self.assertEqual((new['quote'],new['currency'],new['quote_confirmed']),('4000.00','CNY',True));self.assertIn('黄总 2026-11-01 确认',new['owner_confirmation_source'])
        self.assertEqual(new['original_text'],'大模型评测 4000rmb 1单 AI for Math同一家');self.assertFalse(new['business_dates']);self.assertFalse(new['receipts']);self.assertFalse(new['publications'])
        self.assertEqual(self.deals.listing()['imports'],[])
        self.assertIn('黄总确认新建',new['audit'][0]['action'])
        with self.deals.db() as c:self.assertEqual(self.deals._read(c,old['id']),before)
        digest=sha(self.deals.path);again=self.deals.migrate_owner_contact_bundle(self.contact_bundle())
        self.assertFalse(again['created']);self.assertEqual(again['new_deal_id'],new['id']);self.assertEqual(again['proposals_added'],0);self.assertEqual(again['evidence_added'],0);self.assertEqual(sha(self.deals.path),digest)
    def test_contact_migration_does_not_overwrite_later_edits_or_reoffer_rejected_item(self):
        self.create(title='AI for Math',stage='unknown');bundle=self.contact_bundle();r=self.deals.migrate_owner_contact_bundle(bundle)
        new=self.deals.detail(r['new_deal_id']);new=self.edit(new,notes='黄总后续修改')
        q=next(q for q in r['proposals'] if q['deal_id']==new['id'] and 'stage' in q['changes']);self.deals.resolve_lifecycle(dict(proposal_id=q['id'],decision='rejected'))
        digest=sha(self.deals.path);again=self.deals.migrate_owner_contact_bundle(bundle);self.assertEqual(again['proposals_added'],0);self.assertEqual(sha(self.deals.path),digest)
        self.assertEqual(self.deals.detail(new['id'])['notes'],'黄总后续修改');self.assertNotIn(q['id'],[q['id'] for q in self.deals.pending_proposals()])
    def test_contact_migration_refuses_ambiguous_target_and_rolls_back_invalid_manifest(self):
        self.create(title='AI for Math');self.create(title='大模型评测');digest=sha(self.deals.path)
        with self.assertRaises(DealConflict):self.deals.migrate_owner_contact_bundle(self.contact_bundle())
        self.assertEqual(sha(self.deals.path),digest)
        with self.deals.db() as c:c.execute("DELETE FROM deals WHERE json_extract(data,'$.title')='大模型评测'")
        bundle=self.contact_bundle();bundle['proposals'][-1]['value']='not-a-stage';digest=sha(self.deals.path)
        with self.assertRaises(ValueError):self.deals.migrate_owner_contact_bundle(bundle)
        self.assertEqual(sha(self.deals.path),digest)
    def test_two_publication_date_proposals_can_both_be_confirmed_without_duplicate(self):
        old=self.create(title='AI for Math',stage='unknown');r=self.deals.migrate_owner_contact_bundle(self.contact_bundle())
        qs=[q for q in r['proposals'] if 'published_on' in q['changes']]
        self.assertEqual(len(qs),2)
        for q in qs:self.deals.resolve_lifecycle(dict(proposal_id=q['id'],decision='confirmed'))
        d=self.deals.detail(old['id']);self.assertEqual(sorted(x['date'] for x in d['date_events']),['2026-10-01','2026-10-08']);self.assertFalse(d['publications']);self.assertFalse(d['receipts'])
        self.assertEqual(self.deals.resolve_lifecycle(dict(proposal_id=qs[0]['id'],decision='confirmed'))['revision'],d['revision'])
    def test_same_date_already_manually_recorded_cannot_be_confirmed_again(self):
        old=self.create(title='AI for Math',stage='unknown');r=self.deals.migrate_owner_contact_bundle(self.contact_bundle());q=next(q for q in r['proposals'] if 'published_on' in q['changes'])
        with self.deals.db() as c:
            d=self.deals._read(c,old['id']);d['date_events']=[dict(kind='published_on',date=q['changes']['published_on'])];self.deals._write(c,d,'人工日期')
        with self.assertRaises(DealConflict):self.deals.resolve_lifecycle(dict(proposal_id=q['id'],decision='confirmed'))
