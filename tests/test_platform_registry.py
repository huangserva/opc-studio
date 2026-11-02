import unittest
from pathlib import Path

from platform_registry import PLATFORMS, derive_platform


class PlatformRegistryTests(unittest.TestCase):
    def test_registered_prefixes_and_case(self):
        for key in ('x', 'douyin', 'xhs', 'wechat', 'github', 'linuxdo', 'web'):
            for prefix in (key, key.upper()):
                with self.subTest(prefix=prefix):
                    self.assertEqual(derive_platform(prefix + ':example'), key)

    def test_unknown_missing_colon_empty_and_invalid_ids(self):
        for value in ('', 'x', 'x:', 'x:  ', ' x:1', 'twitter:1', ':1', None, 123, [], {}):
            with self.subTest(value=value):
                self.assertEqual(derive_platform(value), 'web')

    def test_registry_contains_display_names_and_reserved_visual_fields(self):
        self.assertEqual(set(PLATFORMS), {'x', 'douyin', 'xhs', 'wechat', 'github', 'linuxdo', 'web'})
        for key, entry in PLATFORMS.items():
            self.assertEqual(entry['key'], key)
            self.assertTrue(entry['display_name'])
            self.assertRegex(entry['icon'], r'^/icons/platforms/[a-z0-9-]+\.svg$')
            self.assertTrue((Path(__file__).resolve().parents[1] / 'public' / entry['icon'].lstrip('/')).is_file())
            self.assertRegex(entry['color'], r'^#[0-9A-Fa-f]{6}$')


if __name__ == '__main__':
    unittest.main()
