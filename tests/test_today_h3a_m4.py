"""M4: general camel-case acronym plural segmentation, preserving ordinary uppercase words."""
import json
from pathlib import Path
import unittest
from scripts import radar_common as common

ROWS = json.loads((Path(__file__).parent/'fixtures/today_h3a_m4.json').read_text())

class M4Examples(unittest.TestCase):
    pass

def case(row):
    def test(self):
        item = dict(title=row['text'], full_text=row['text'], sources=['A'], topic='')
        self.assertEqual(common.today_relevance_gate(item)['visible'],row['expected'],row['reason'])
    return test

for row in ROWS:
    setattr(M4Examples,'test_'+row['name'],case(row))
