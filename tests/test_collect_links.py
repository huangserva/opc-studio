"""collect_links：三家平台链接识别（纯函数，零网络）。

编号一律用公开格式的合成示例，不使用真实账号或真实作品。
"""
import socket
import unittest
from urllib.parse import parse_qs, urlsplit

import collect_links

XHS_NOTE = '64f0a1b20000000012034567'
DOUYIN_VIDEO = '7412345678901234567'
DOUYIN_USER = 'MS4wLjABAAAA' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6'


class IdentifyTests(unittest.TestCase):
    def xhs(self, url):
        found = collect_links.identify(url)
        self.assertIsNotNone(found, url)
        self.assertEqual(found['platform'], 'xiaohongshu')
        self.assertEqual(found['label'], '小红书')
        self.assertTrue(found['needs_login'])
        return found

    def test_explore_note_keeps_xsec_token_in_canonical(self):
        found = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567'
                         '?xsec_token=ABc123xyz=&xsec_source=pc_share&utm_medium=openinapp')
        self.assertEqual(found['item_kind'], 'note')
        self.assertEqual(found['item_id'], '64f0a1b20000000012034567')
        self.assertEqual(found['canonical_url'], 'https://www.xiaohongshu.com/explore/64f0a1b20000000012034567'
                                                '?xsec_token=ABc123xyz%3D&xsec_source=pc_share')
        self.assertFalse(found['short_link'])
        self.assertIn('登录浏览器', found['body_note'])

    def test_discovery_item_and_search_result_are_the_same_note(self):
        explore = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567')
        discovery = self.xhs('https://www.xiaohongshu.com/discovery/item/64f0a1b20000000012034567'
                             '?source=webshare&xhsshare=pc_web')
        search = self.xhs('https://www.xiaohongshu.com/search_result/64f0a1b20000000012034567'
                          '?xsec_token=AB999')
        self.assertEqual(discovery['item_kind'], 'note')
        self.assertEqual(search['item_kind'], 'note')
        self.assertEqual(explore['dedupe_key'], discovery['dedupe_key'])
        self.assertEqual(explore['dedupe_key'], search['dedupe_key'])
        self.assertEqual(explore['dedupe_key'], 'xiaohongshu:note:64f0a1b20000000012034567')

    def test_a_different_token_is_the_same_dedupe_key(self):
        first = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567?xsec_token=AAA111')
        again = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567?xsec_token=BBB222')
        self.assertNotEqual(first['canonical_url'], again['canonical_url'])
        self.assertEqual(first['dedupe_key'], again['dedupe_key'])
        self.assertEqual(first['item_id'], again['item_id'])

    def test_no_token_link_still_matches_the_token_link(self):
        bare = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567')
        tokened = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567?xsec_token=AAA111')
        self.assertEqual(bare['dedupe_key'], tokened['dedupe_key'])
        self.assertEqual(bare['canonical_url'], 'https://www.xiaohongshu.com/explore/64f0a1b20000000012034567')

    def test_profile_link_is_a_user_not_a_note(self):
        found = self.xhs('https://www.xiaohongshu.com/user/profile/5f1234567890abcdef123456')
        self.assertEqual(found['item_kind'], 'user')
        self.assertEqual(found['item_id'], '5f1234567890abcdef123456')
        self.assertEqual(found['dedupe_key'], 'xiaohongshu:user:5f1234567890abcdef123456')
        self.assertEqual(found['canonical_url'], 'https://www.xiaohongshu.com/user/profile/5f1234567890abcdef123456')

    def test_xhslink_is_marked_short_and_never_guesses_an_id(self):
        found = self.xhs('http://xhslink.com/a/AbCdEf123')
        self.assertTrue(found['short_link'])
        self.assertEqual(found['item_id'], '')
        self.assertEqual(found['canonical_url'], '')
        self.assertEqual(found['dedupe_key'], '')
        self.assertIn('不联网解析', found['short_note'])

    def test_douyin_video_drops_tracking_parameters(self):
        found = collect_links.identify('https://www.douyin.com/video/7412345678901234567'
                                       '?previous_page=webapp.com&utm_source=share&tt_from=copy_link#player')
        self.assertIsNotNone(found)
        self.assertEqual(found['platform'], 'douyin')
        self.assertEqual(found['label'], '抖音')
        self.assertEqual(found['item_kind'], 'video')
        self.assertEqual(found['item_id'], '7412345678901234567')
        self.assertEqual(found['canonical_url'], 'https://www.douyin.com/video/7412345678901234567')
        self.assertEqual(found['dedupe_key'], 'douyin:video:7412345678901234567')
        self.assertFalse(found['needs_login'])
        self.assertIn('JS 渲染页', found['body_note'])

    def test_douyin_bare_host_and_user_link(self):
        bare = collect_links.identify('https://douyin.com/video/7412345678901234567')
        self.assertIsNotNone(bare)
        self.assertEqual(bare['canonical_url'], 'https://www.douyin.com/video/7412345678901234567')
        user = collect_links.identify('https://www.douyin.com/user/MS4wLjABAAAA' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6')
        self.assertIsNotNone(user)
        self.assertEqual(user['item_kind'], 'user')
        self.assertEqual(user['dedupe_key'], 'douyin:user:MS4wLjABAAAA' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6')

    def test_douyin_video_trailing_slash_is_the_same_address(self):
        """末尾斜杠不能把同一条视频拆成两个去重地址（知识库拿 dedupe_url 当主键）。"""
        plain = collect_links.identify('https://www.douyin.com/video/' + DOUYIN_VIDEO)
        slashed = collect_links.identify('https://www.douyin.com/video/%s/' % DOUYIN_VIDEO)
        tracked = collect_links.identify('https://www.douyin.com/video/%s/?utm_source=share' % DOUYIN_VIDEO)
        self.assertIsNotNone(slashed)
        self.assertIsNotNone(tracked)
        self.assertEqual(slashed['item_id'], DOUYIN_VIDEO)
        self.assertEqual(slashed['dedupe_url'], 'https://www.douyin.com/video/' + DOUYIN_VIDEO)
        self.assertEqual(plain['dedupe_url'], slashed['dedupe_url'])
        self.assertEqual(plain['dedupe_url'], tracked['dedupe_url'])
        self.assertEqual(plain['canonical_url'], slashed['canonical_url'])

    def test_douyin_account_trailing_slash_is_the_same_address(self):
        plain = collect_links.identify('https://www.douyin.com/user/' + DOUYIN_USER)
        slashed = collect_links.identify('https://www.douyin.com/user/%s/' % DOUYIN_USER)
        self.assertIsNotNone(slashed)
        self.assertEqual(slashed['item_kind'], 'user')
        self.assertEqual(slashed['dedupe_url'], 'https://www.douyin.com/user/' + DOUYIN_USER)
        self.assertEqual(plain['dedupe_url'], slashed['dedupe_url'])
        self.assertEqual(plain['canonical_url'], slashed['canonical_url'])

    def test_v_douyin_is_marked_short(self):
        found = collect_links.identify('https://v.douyin.com/iRb7Xqk0/')
        self.assertIsNotNone(found)
        self.assertEqual(found['platform'], 'douyin')
        self.assertTrue(found['short_link'])
        self.assertEqual(found['item_id'], '')

    def test_sph_share_link(self):
        found = collect_links.identify('https://weixin.qq.com/sph/Aa1Bb2Cc3Dd')
        self.assertIsNotNone(found)
        self.assertEqual(found['platform'], 'channels')
        self.assertEqual(found['label'], '视频号')
        self.assertEqual(found['item_kind'], 'sph')
        self.assertEqual(found['item_id'], 'Aa1Bb2Cc3Dd')
        self.assertEqual(found['canonical_url'], 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd')
        self.assertEqual(found['dedupe_key'], 'channels:sph:Aa1Bb2Cc3Dd')
        self.assertTrue(found['needs_login'])

    def test_finder_preview_and_share_link_are_the_same_work(self):
        preview = collect_links.identify('https://channels.weixin.qq.com/finder-preview/pages/sph'
                                         '?id=Aa1Bb2Cc3Dd&from=share')
        share = collect_links.identify('https://weixin.qq.com/sph/Aa1Bb2Cc3Dd')
        self.assertIsNotNone(preview)
        self.assertEqual(preview['item_id'], 'Aa1Bb2Cc3Dd')
        self.assertEqual(preview['dedupe_key'], share['dedupe_key'])
        self.assertEqual(preview['canonical_url'], share['canonical_url'])

    def test_dedupe_url_is_the_token_free_address(self):
        tokened = self.xhs('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567?xsec_token=AAA111')
        self.assertEqual(tokened['dedupe_url'], 'https://www.xiaohongshu.com/explore/64f0a1b20000000012034567')
        self.assertIn('xsec_token=AAA111', tokened['canonical_url'])

    def test_short_links_have_no_dedupe_address(self):
        for url in ('http://xhslink.com/a/AbCdEf123', 'https://v.douyin.com/iRb7Xqk0/'):
            found = collect_links.identify(url)
            self.assertIsNotNone(found)
            self.assertEqual(found['dedupe_url'], '')
            self.assertEqual(found['dedupe_key'], '')

    def test_kept_query_re_encodes_the_token(self):
        """保留下来的参数要重新编码：解码后的 + & = % 和中文不能直接拼回去。"""
        raw = ('https://www.xiaohongshu.com/explore/%s'
               '?xsec_token=A%%2BB%%26C%%3DD%%25E%%E4%%B8%%AD%%E6%%96%%87&xsec_source=pc_share'
               '&utm_source=share' % XHS_NOTE)
        found = self.xhs(raw)
        kept = parse_qs(urlsplit(found['canonical_url']).query)
        original = parse_qs(urlsplit(raw).query)
        self.assertEqual(kept['xsec_token'], ['A+B&C=D%E中文'])
        self.assertEqual(kept['xsec_token'], original['xsec_token'])
        # token 里的 & 没有被拼成参数分隔符：xsec_source 仍然完整，跟踪参数仍然被丢掉。
        self.assertEqual(kept['xsec_source'], ['pc_share'])
        self.assertNotIn('utm_source', kept)
        self.assertNotIn('A+B&C', found['canonical_url'])

    def test_plain_token_stays_readable_after_encoding(self):
        """重新编码不能弄坏常见 token：尾巴上的 = 百分号化，解回来还是原值。"""
        found = self.xhs('https://www.xiaohongshu.com/explore/' + XHS_NOTE + '?xsec_token=ABc123xyz=&xsec_source=pc_share')
        self.assertEqual(found['canonical_url'], 'https://www.xiaohongshu.com/explore/' + XHS_NOTE
                                                + '?xsec_token=ABc123xyz%3D&xsec_source=pc_share')
        self.assertEqual(parse_qs(urlsplit(found['canonical_url']).query)['xsec_token'], ['ABc123xyz='])

    def test_douyin_and_sph_dedupe_url_matches_their_canonical(self):
        douyin = collect_links.identify('https://www.douyin.com/video/7412345678901234567?utm_source=share')
        self.assertEqual(douyin['dedupe_url'], 'https://www.douyin.com/video/7412345678901234567')
        sph = collect_links.identify('https://channels.weixin.qq.com/finder-preview/pages/sph?id=Aa1Bb2Cc3Dd')
        self.assertEqual(sph['dedupe_url'], 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd')

    def test_other_platforms_are_not_misread(self):
        for url in ('https://x.com/some_author/status/1450000000000000000',
                    'https://mp.weixin.qq.com/s/AbCdEfGhIjKlMnOpQrStUv',
                    'https://mp.weixin.qq.com/sph-like/not-a-sph-link',
                    'https://www.example.com/blog/64f0a1b20000000012034567',
                    'https://www.bilibili.com/video/BV1xx411c7mD',
                    'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'https://github.com/torvalds/linux',
                    'https://weixin.qq.com/',
                    'https://channels.weixin.qq.com/',
                    'https://channels.weixin.qq.com/platform',
                    'https://xiaohongshu.com/',
                    'https://www.xiaohongshu.com/explore',
                    'https://www.xiaohongshu.com/explore/64f0a1b20000000012034567/comments',
                    'https://www.douyin.com/discover?modal_id=7412345678901234567',
                    'https://v.douyin.com/',
                    'https://xhslink.com/'):
            self.assertIsNone(collect_links.identify(url), url)

    def test_invalid_input_is_refused_without_raising(self):
        for url in ('', '   ', None, 123, 'xiaohongshu.com/explore/64f0a1b20000000012034567',
                    'javascript:alert(1)', 'file:///tmp/x', 'ftp://xiaohongshu.com/explore/64f0a1b20000000012034567',
                    'https://user:pass@www.xiaohongshu.com/explore/64f0a1b20000000012034567',
                    'https://www.xiaohongshu.com:8443/explore/64f0a1b20000000012034567',
                    'https://www.xiaohongshu.com/explore/64f0a1b20000000012034567\x00',
                    'https://' + 'a' * 2100, 'http://127.0.0.1/explore/64f0a1b20000000012034567'):
            self.assertIsNone(collect_links.identify(url), repr(url))

    def test_every_control_character_is_refused(self):
        """控制字符一律不认：TAB、CR、LF、DEL 也算，不只 NUL。"""
        base = 'https://www.xiaohongshu.com/explore/' + XHS_NOTE
        for code in list(range(0, 32)) + [127]:
            mark = chr(code)
            for url in (base + mark, base + '?xsec_token=AA' + mark + 'BB',
                        base.replace('/', mark, 1), 'https://v.douyin.com/iRb7Xqk0/' + mark):
                self.assertIsNone(collect_links.identify(url), repr(url))

    def test_identify_opens_no_connection(self):
        """识别必须是纯字符串判断：把网络入口全部堵死，结果不变。"""
        def blocked(*args, **kwargs):
            raise AssertionError('识别链接不应该联网')
        real_socket, real_addrinfo = socket.socket, socket.getaddrinfo
        socket.socket, socket.getaddrinfo = blocked, blocked
        try:
            self.assertIsNotNone(collect_links.identify('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567'))
            self.assertIsNotNone(collect_links.identify('https://v.douyin.com/iRb7Xqk0/'))
            self.assertIsNone(collect_links.identify('https://example.com/post'))
        finally:
            socket.socket, socket.getaddrinfo = real_socket, real_addrinfo


class BodyGuardTests(unittest.TestCase):
    def test_login_wall_and_shell_are_not_the_work_body(self):
        info = collect_links.identify('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567')
        self.assertIn('空壳', collect_links.body_not_read(info, '    '))
        self.assertIn('空壳', collect_links.body_not_read(info, '登录后查看完整内容' * 3))
        self.assertIn('登录墙', collect_links.body_not_read(info, '当前页面需要扫码登录，' + '正文占位' * 200))
        self.assertIn('登录墙', collect_links.body_not_read(info, 'Unusual Login Activity detected. ' + 'x' * 400))

    def test_a_long_body_without_login_marks_counts_as_read(self):
        info = collect_links.identify('https://www.douyin.com/video/7412345678901234567')
        self.assertEqual(collect_links.body_not_read(info, '正文' * 300), '')

    def test_other_links_are_not_judged_by_the_platform_guard(self):
        self.assertEqual(collect_links.body_not_read(None, ''), '')
        self.assertEqual(collect_links.body_not_read(None, '很短'), '')


class ReceiptTests(unittest.TestCase):
    def test_receipt_names_the_platform_reason_and_the_backend_reason(self):
        info = collect_links.identify('https://www.xiaohongshu.com/explore/64f0a1b20000000012034567')
        text = collect_links.receipt(info, '联网服务未就绪（remio 未运行）')
        self.assertEqual(text, '小红书正文需要登录浏览器，本机不代跑；联网服务未就绪（remio 未运行）')

    def test_short_link_receipt_says_the_id_is_still_unknown(self):
        info = collect_links.identify('https://v.douyin.com/iRb7Xqk0/')
        text = collect_links.receipt(info)
        self.assertIn('抖音是 JS 渲染页', text)
        self.assertIn('短链', text)

    def test_receipt_without_information_says_nothing_invented(self):
        self.assertEqual(collect_links.receipt(None, '网页没有返回可读正文'), '网页没有返回可读正文')
        self.assertEqual(collect_links.receipt(None), '')


if __name__ == '__main__':
    unittest.main()
