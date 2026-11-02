from pathlib import Path
import tempfile,unittest,sys
from datetime import datetime
from source_catchup import CatchupLedger
class AIsaTimelinessTests(unittest.TestCase):
 def test_darkwake_ontime_output_is_not_proof_of_catchup(self):
  with tempfile.TemporaryDirectory(dir='/tmp',prefix='catchup-darkwake-') as folder:
   due=datetime.fromisoformat('2026-10-31T10:00:00+08:00').timestamp()
   row=CatchupLedger(folder).refresh([dict(source_id='twitter-radar',due=due,grace_seconds=1800,owner_managed=True,output_at=due+39)],due+4320,dict(slept_at=due-2835,woke_at=due+4331-60))['sources'][0]
   self.assertEqual(row['status'],'idle')
 def test_late_file_without_start_record_not_claimed_as_successful_rerun(self):
  with tempfile.TemporaryDirectory(dir='/tmp',prefix='catchup-aisa-late-') as folder:
   due=10000
   row=CatchupLedger(folder).refresh([dict(source_id='twitter-radar',due=due,grace_seconds=1800,owner_managed=True,output_at=due+4000)],due+5000,dict(slept_at=due-100,woke_at=due+3500))['sources'][0]
   self.assertEqual(row['status'],'idle')
if __name__=='__main__':unittest.main()
