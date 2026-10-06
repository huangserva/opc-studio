import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from knowledge_context import freeze_context


class KnowledgeContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.identity = 'a' * 32
        self.dossier_id = 'b' * 32
        self.body = '未选前文😀。已选的原始证据。清单外后文必须不读取。'
        self.digest = hashlib.sha256(self.body.encode()).hexdigest()
        self.start, self.end = 6, 15
        self.snapshot = {'id': self.identity, 'hash': self.digest, 'start': self.start,
            'end': self.end, 'excerpt': self.body[self.start:self.end], 'title': '合成项目记录',
            'source': 'project-demo', 'kind': 'projects', 'url': '', 'modified': '2026-10-04T12:00:00+08:00',
            'provenance': {'local_path': '/synthetic/project/plan.md', 'relative_path': 'plan.md'}}
        self.dossier = {'id': self.dossier_id, 'title': '合成资料夹', 'modified': self.snapshot['modified'],
                        'items': [copy.deepcopy(self.snapshot)]}
        self.knowledge = SimpleNamespace(dossier=Mock(return_value=self.dossier),
            document=Mock(return_value={'id': self.identity, 'body': self.body, 'hash': self.digest}))
        self.store = SimpleNamespace(workspace=self.workspace, knowledge=self.knowledge,
            article=lambda slug: self.workspace / 'library' / slug, meta=lambda slug: {'head': 'v0002'}, info=self.info)
        self.article = self.store.article('draft-demo')
        for version in ('v0001', 'v0002'):
            (self.article / 'versions' / version).mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def info(self, slug, version):
        package = self.store.article(slug) / 'versions' / version
        if not package.is_dir():
            raise FileNotFoundError(package)
        return {'title': '合成写作底稿'}, {'id': version}, package

    def freeze(self):
        return freeze_context(self.store, {'kind': 'dossier', 'id': self.dossier_id})

    def write_manifest(self, **fields):
        data = {'dossier': self.dossier_id, 'items': [self.snapshot], **fields}
        (self.article / 'knowledge-sources.json').write_text(json.dumps(data), encoding='utf-8')

    def test_descriptor_has_only_routing_metadata_and_snapshots_are_detached(self):
        frozen = self.freeze()
        self.assertEqual(frozen['items'][0]['excerpt'], self.snapshot['excerpt'])
        self.assertEqual(frozen['items'][0]['provenance'], self.snapshot['provenance'])
        self.assertEqual(frozen['items'][0]['state'], 'fresh')
        self.assertNotIn('excerpt', frozen['descriptor']['items'][0])
        self.assertNotIn('provenance', frozen['descriptor']['items'][0])
        self.assertNotIn(self.snapshot['excerpt'], json.dumps(frozen['descriptor']))
        self.assertIn('hash=' + self.digest, frozen['descriptor']['items'][0]['citation_url'])
        self.dossier['items'][0]['excerpt'] = 'later mutation'
        self.dossier['items'][0]['provenance']['local_path'] = '/changed'
        self.assertEqual(frozen['items'][0]['excerpt'], self.snapshot['excerpt'])
        self.assertEqual(frozen['items'][0]['provenance'], self.snapshot['provenance'])
        self.knowledge.document.assert_called_once_with(self.identity)

    def test_stale_or_deleted_original_keeps_saved_excerpt_and_hash(self):
        self.knowledge.document.return_value = {'body': '已修改当前资料', 'hash': 'c' * 64}
        frozen = self.freeze()
        self.assertEqual(frozen['items'][0]['state'], 'stale')
        self.assertEqual(frozen['items'][0]['excerpt'], self.snapshot['excerpt'])
        self.assertEqual(frozen['items'][0]['hash'], self.digest)
        self.knowledge.document.side_effect = ValueError('资料不存在，请重新检索')
        self.assertEqual(self.freeze()['items'][0]['state'], 'missing')

    def test_current_hash_requires_excerpt_content_match(self):
        self.dossier['items'][0]['excerpt'] = '伪' * (self.end - self.start)
        with self.assertRaisesRegex(ValueError, '同 hash'):
            self.freeze()

    def test_shape_integer_hash_and_total_char_limits_are_enforced(self):
        variants = [{'id': '../secret'}, {'hash': 'forged'}, {'start': True},
                    {'end': self.start}, {'excerpt': 'wrong-length'}, {'provenance': []},
                    {'url': 'file:///etc/passwd'}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.dossier['items'] = [{**self.snapshot, **changes}]
                with self.assertRaises(ValueError):
                    self.freeze()
        self.dossier['items'] = [copy.deepcopy(self.snapshot)] * 21
        with self.assertRaisesRegex(ValueError, '1–20'):
            self.freeze()
        self.knowledge.document.return_value = {'hash': 'c' * 64}
        large = {**self.snapshot, 'start': 0, 'end': 6001, 'excerpt': '字' * 6001}
        self.dossier['items'] = [large, copy.deepcopy(large)]
        with self.assertRaisesRegex(ValueError, '12,000'):
            self.freeze()
        self.dossier['items'] = []
        with self.assertRaises(ValueError):
            self.freeze()

    def test_draft_without_manifest_does_not_collect_any_knowledge(self):
        self.assertIsNone(freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0002'}))
        self.knowledge.document.assert_not_called()
        self.knowledge.dossier.assert_not_called()

    def test_unversioned_manifest_only_binds_current_head_and_is_marked(self):
        self.write_manifest()
        frozen = freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0002'})
        self.assertTrue(frozen['descriptor']['legacy_binding_unverified'])
        self.assertEqual(frozen['descriptor']['version'], 'v0002')
        with self.assertRaisesRegex(ValueError, '未绑定'):
            freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0001'})

    def test_versioned_manifest_stays_bound_to_actual_version_after_head_changes(self):
        self.write_manifest(version='v0001')
        frozen = freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0001'})
        self.assertFalse(frozen['descriptor']['legacy_binding_unverified'])
        with self.assertRaisesRegex(ValueError, '未绑定'):
            freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0002'})
        with self.assertRaisesRegex(ValueError, '不存在'):
            freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0999'})

    def test_explicit_invalid_requests_and_manifest_do_not_widen_source_reads(self):
        for request in [None, {}, {'kind': 'all'}, {'kind': 'dossier', 'id': self.dossier_id, 'items': []},
                        {'kind': 'draft', 'slug': '../../private', 'version': 'v0002'},
                        {'kind': 'draft', 'slug': 'draft-demo', 'version': '../v0002'}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                freeze_context(self.store, request)
        path = self.article / 'knowledge-sources.json'
        path.write_text('{bad-json', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'JSON'):
            freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0002'})
        self.knowledge.document.assert_not_called()

    def test_symlink_sidecar_and_versions_are_rejected_before_contents_are_read(self):
        external = self.workspace / 'private-not-read.json'
        external.write_text('this is not JSON', encoding='utf-8')
        sidecar = self.article / 'knowledge-sources.json'
        sidecar.symlink_to(external)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0002'})
        sidecar.unlink()
        (self.article / 'versions' / 'v0002' / 'manifest.json').symlink_to(external)
        with self.assertRaisesRegex(ValueError, '版本路径'):
            freeze_context(self.store, {'kind': 'draft', 'slug': 'draft-demo', 'version': 'v0002'})
        self.knowledge.document.assert_not_called()


if __name__ == '__main__':
    unittest.main()
