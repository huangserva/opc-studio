from isolation_patches import module_thread
import tempfile,unittest
from unittest.mock import patch
from server import Store
from generation.service import Generation
class ManagementTests(unittest.TestCase):
 def test_reversible_persistent_image_video_and_atomic_active_guard(self):
  with tempfile.TemporaryDirectory() as d:
   g=Generation(Store(d))
   with module_thread('generation.service'):
    a=g.start({'model':'qi21','prompt':'lake'});g.update(a['id'],status='done')
    b=g.start({'model':'h3','prompt':'lake'})
   with self.assertRaises(ValueError):g.manage([a['id'],b['id']],'delete')
   self.assertFalse(g.public(a['id']).get('deleted_at'))
   g.update(b['id'],status='error');g.manage([a['id'],b['id']],'delete')
   self.assertEqual(len(g.listing()['jobs']),0)
   self.assertEqual(len(g.listing(trash=True)['jobs']),2)
   loaded=Generation(Store(d));self.assertEqual(len(loaded.listing(trash=True)['jobs']),2)
   loaded.manage([a['id'],b['id']],'restore');self.assertEqual(len(loaded.listing()['jobs']),2)
   with self.assertRaises(ValueError):loaded.manage([a['id'],'unknown'],'delete')
   self.assertFalse(loaded.public(a['id']).get('deleted_at'))
