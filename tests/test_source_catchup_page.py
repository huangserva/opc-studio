import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import today_view as today
from source_catchup import CatchupLedger


class CatchupPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='catchup-page-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ws=self.root/'workspace';self.ws.mkdir()

    def test_empty_receipts_never_create_state_or_scan_sources(self):
        with patch.object(today,'snapshot',side_effect=AssertionError('must not scan')), patch.object(today.linuxdo,'refresh_background',side_effect=AssertionError('must not refresh')):
            result=today.page_view(self.root,{'source_catchup':['1']})
        self.assertEqual(result['source_catchup']['status'],'waiting')
        self.assertFalse((self.ws/'catchup').exists())
        self.assertFalse(result['source_catchup']['action_endpoint'])

    def test_exact_mode_only_invalid_duplicate_injection_and_extra_filters_rejected(self):
        for query in ({'source_catchup':['0']},{'source_catchup':['1','1']},{'source_catchup':[]},{'source_catchup':'1'},{'source_catchup':['1'],'source':['G']},{'source_catchup':['1 OR 1=1']},{'source_catchup':['1'],'channel':['interest']}):
            with self.subTest(query=query), self.assertRaises(ValueError):today.page_view(self.root,query)
        self.assertFalse((self.ws/'catchup').exists())

    def test_unavailable_reason_and_zero_attempts_match_ledger_without_writes(self):
        ledger=CatchupLedger(self.ws)
        ledger.refresh([dict(source_id='grok-interest',due=1000,grace_seconds=60,output_at=None)],2000,dict(slept_at=900,woke_at=1500))
        path=self.ws/'catchup/state.json';before=hashlib.sha256(path.read_bytes()).hexdigest()
        for _ in range(3):result=today.page_view(self.ws,{'source_catchup':['1']})
        row=result['source_catchup']['sources'][0]
        self.assertEqual((row['status'],row['status_label'],row['cause'],row['reason'],row['attempts']),('unavailable','不可补','sleep','no_authorized_trigger',0))
        self.assertEqual(row['detail'],'没有授权的触发入口')
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_unknown_private_fields_not_returned_and_corrupt_state_fail_closed(self):
        ledger=CatchupLedger(self.ws);ledger.refresh([],2000)
        path=self.ws/'catchup/state.json';data=json.loads(path.read_text());data['provider_message']='private-fixture-text';path.write_text(json.dumps(data))
        self.assertNotIn('private-fixture-text',json.dumps(today.page_view(self.ws,{'source_catchup':['1']})))
        path.write_text('not-json')
        value=today.page_view(self.ws,{'source_catchup':['1']})['source_catchup']
        self.assertEqual((value['status'],value['sources'],value['action_endpoint']),('unavailable',[],False))
        self.assertEqual(path.read_text(),'not-json')

if __name__=='__main__':unittest.main()
