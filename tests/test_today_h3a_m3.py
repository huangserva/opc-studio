"""M3: all-uppercase false positives, evidence-based products, ambiguous everyday nouns."""
import json
from pathlib import Path
import unittest
from scripts import radar_common as common
ROWS = json.loads((Path(__file__).parent / 'fixtures/today_h3a_m3.json').read_text())

class M3Examples(unittest.TestCase):
    pass

def case(row):
    def test(self):
        self.assertEqual(common.today_relevance_gate(row)['visible'], row['expected'], row['full_text'])
    return test
for row in ROWS['cases']:
    setattr(M3Examples, 'test_'+row['name'], case(row))

class RealProductExamples(unittest.TestCase):
    pass

def positive(row):
    def test(self):
        self.assertTrue(common.today_relevance_gate(row)['visible'], row['id'])
    return test
for row in ROWS['real']:
    setattr(RealProductExamples, 'test_restore_'+row['id'].replace(':','_'), positive(row))

class ProductSentenceBoundaries(unittest.TestCase):
    pass

def context_case(row):
    def test(self):
        self.assertEqual(common._today_product_related(common._today_normalize(row['text'])),row['expected'],row['text'])
    return test
for row in ROWS['context']:
    setattr(ProductSentenceBoundaries,'test_'+row['name'],context_case(row))
