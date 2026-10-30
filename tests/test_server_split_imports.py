"""拆分后公开入口、字面量策略及启动依赖的兼容性。"""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

import html_sanitize
import server
import server_boot


class ServerSplitImportsTests(unittest.TestCase):
    def test_reexports_keep_provider_identity(self):
        for provider, names in (
            (html_sanitize, ('CODE_TAGS', 'LINE_BREAKING_TAGS', 'WHOLE_LINE_TAGS',
                             'INTERNAL_REL', '_FORMAT_TAG', 'FORMAT_RECEIPT_LIMIT',
                             'html_formats', 'format_receipt', 'merge_dropped')),
            (server_boot, ('seed_intel_schedule', 'wire_intake_analysis',
                           'wire_intake_media', '_IntakeLedger', 'wire_intake_links')),
        ):
            for name in names:
                with self.subTest(name=name):
                    self.assertIs(getattr(server, name), getattr(provider, name))
        self.assertIs(server.SafeHTML.__bases__[0], html_sanitize.SafeHTML)
        self.assertEqual(set(server.SafeHTML.__dict__) - {'__module__', '__doc__',
                                                        '__firstlineno__', '__static_attributes__'},
                         {'_escape_data'})

    def test_public_cleaning_entries_observe_patch_and_restore(self):
        # Both normal imports and the existing tests' unregistered studio modules must work.
        spec = importlib.util.spec_from_file_location('split_studio', server.__file__)
        studio = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(studio)
        def parse(module, value):
            parser = module.SafeHTML()
            parser.feed(value)
            return ''.join(parser.parts)
        entrances = ('clean_html_full', 'clean_html', 'safe_html',
                     'markdown_aware_html', 'markdown_aware_html_full')
        for module in (server, studio):
            for name in (*entrances, 'SafeHTML'):
                fn = (lambda value: parse(module, value)) if name == 'SafeHTML' else getattr(module, name)
                for key, value, source, before, after in (
                    ('MARKUP_LITERALS', '', '<p>*literal*</p>', '&#42;literal&#42;', '*literal*'),
                    ('MARKUP_LINE_RULES', (), '<p>- literal</p>', '&#45; literal', '- literal'),
                ):
                    with self.subTest(module=module.__name__, entrance=name, target=key):
                        original = fn(source)
                        original_html = original if isinstance(original, str) else original[0]
                        self.assertIn(before, original_html)
                        with mock.patch.object(module, key, value):
                            patched = fn(source)
                            patched_html = patched if isinstance(patched, str) else patched[0]
                            self.assertIn(after, patched_html)
                            self.assertNotIn(before, patched_html)
                        self.assertEqual(fn(source), original)
            # These entries expose only style rejection notes; inspect the parser output
            # as well, because changing literal escapes must not change that notes channel.
            for name in ('SafeHTML_collect_rich', 'rich_dropped_of'):
                for key, value, source, escaped in (
                    ('MARKUP_LITERALS', '', '<p>*literal*</p>', '&#42;'),
                    ('MARKUP_LINE_RULES', (), '<p>- literal</p>', '&#45;'),
                ):
                    with self.subTest(module=module.__name__, entrance=name, target=key):
                        seen = []
                        escape_data = module.SafeHTML._escape_data
                        def trace(parser, *args, **kwargs):
                            result = escape_data(parser, *args, **kwargs)
                            seen.append(result)
                            return result
                        with mock.patch.object(module.SafeHTML, '_escape_data', trace):
                            original = getattr(module, name)(source)
                            self.assertIn(escaped, ''.join(seen)); seen.clear()
                            with mock.patch.object(module, key, value):
                                self.assertEqual(getattr(module, name)(source), original)
                                self.assertNotIn(escaped, ''.join(seen)); seen.clear()
                            self.assertEqual(getattr(module, name)(source), original)
                            self.assertIn(escaped, ''.join(seen))

    def test_markdown_entry_patch_still_reaches_full_wrapper(self):
        expected = ('<p>fixture</p>', 'fixture', [])
        with mock.patch.object(server, 'markdown_aware_html', return_value=expected) as patched:
            self.assertEqual(server.markdown_aware_html_full('**fixture**')[:3], expected)
            patched.assert_called_once_with('**fixture**', from_editor=False)
        self.assertIn('<strong>', server.markdown_aware_html_full('**fixture**')[0])

    def test_main_reads_current_server_dependencies(self):
        root = Path('/tmp/split-root-must-be-used')
        with mock.patch.object(server, 'ROOT', root), mock.patch.object(server_boot, 'main') as main:
            server.main()
            main.assert_called_once()
            self.assertEqual(main.call_args.args[:5], (root, server.Store, server.Handler,
                                                      server.assistant_module, server.content))
            from types import SimpleNamespace
            store = SimpleNamespace(clues=object())
            http = SimpleNamespace(schedules=SimpleNamespace())
            main.call_args.args[5](http, store)
            self.assertIs(http.schedules.outsourced_clues, store.clues)
        # The real startup body must use the injected ROOT for its default workspace.
        with mock.patch('sys.argv', ['server.py']), \
                mock.patch.object(server_boot, 'ThreadingHTTPServer') as http:
            store_factory = mock.Mock(side_effect=RuntimeError('stop before any background work'))
            with self.assertRaisesRegex(RuntimeError, 'stop before any background work'):
                server_boot.main(root, store_factory, server.Handler,
                                 server.assistant_module, server.content, mock.Mock())
            store_factory.assert_called_once_with(str(root / 'workspace'))
            http.assert_not_called()


if __name__ == '__main__':
    unittest.main()
