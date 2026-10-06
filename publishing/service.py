"""Unified delivery records and immutable export service."""
import json, uuid, zipfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .core.common import read_json as read, write_json as write
from .registry import PLATFORMS, build_plan

class PublishingMixin:
    def publication_plan(self, slug, payload):
        from content_store import studio_at
        with self.lock:
            platform, version = payload.get('platform'), payload.get('version')
            if platform not in PLATFORMS or not version:
                raise ValueError('请选择平台与已保存的版本')
            manifest, _, package = self.info(slug, version)
            studio = studio_at(package)
            snapshot = {'slug':slug,'version':version,'platform':platform,
                        'manifest':manifest,'studio':studio,'package':package,
                        'target':studio['platforms'].get(platform,{}),
                        'text':(package/'body-text.txt').read_text(encoding='utf-8')}
            return build_plan(snapshot, payload.get('options'))

    def wechat_preview(self, slug, version, embedded=False):
        from content_store import studio_at
        from .platforms.wechat import render
        with self.lock:
            if not version:
                raise ValueError('请选择已保存的版本')
            manifest, _, package = self.info(slug, version)
            studio = studio_at(package)
            return render({'slug':slug, 'version':version, 'manifest':manifest,
                           'studio':studio, 'package':package,
                           'target':studio['platforms'].get('wechat',{})}, embedded)

    def delivery_records(self, slug):
        path = self.article(slug) / 'deliveries.json'
        return read(path) if path.exists() else []

    def record_delivery(self, slug, payload):
        from content_store import studio_at
        with self.lock:
            revision, platform = payload.get('version'), payload.get('platform')
            if platform not in PLATFORMS or not revision:
                raise ValueError('请选择平台与保存过的版本')
            manifest, _, package = self.info(slug, revision)
            studio = studio_at(package)
            url = str(payload.get('url', '')).strip()
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('请填写已发布作品的 HTTPS 链接')
            domains = {'x': ('x.com', 'twitter.com'), 'douyin': ('douyin.com',), 'xiaohongshu': ('xiaohongshu.com', 'xhslink.com'), 'channels': ('weixin.qq.com', 'weixinbridge.com'), 'wechat': ('mp.weixin.qq.com',)}
            if not any(parsed.hostname == d or parsed.hostname.endswith('.'+d) for d in domains[platform]):
                raise ValueError('链接与所选平台不符')
            records = self.delivery_records(slug)
            target = studio['platforms'].get(platform, {})
            account=payload.get('account', target.get('account', ''))
            if not isinstance(account,str) or len(account)>200 or '\x00' in account:
                raise ValueError('发布账号格式无效')
            record = {'id': uuid.uuid4().hex, 'kind': 'published_manual', 'platform': platform,
                      'version': revision, 'url': url, 'account': account.strip(),
                      'created_at': datetime.now(timezone.utc).isoformat(), 'note': str(payload.get('note', ''))[:1000]}
            records.append(record)
            write(self.article(slug) / 'deliveries.json', records)
            return record

    def export_delivery(self, slug, payload):
        from content_store import studio_at, safe_path
        with self.lock:
            platform, revision = payload.get('platform'), payload.get('version')
            if platform not in PLATFORMS or not revision:
                raise ValueError('请选择平台与版本')
            manifest, _, package = self.info(slug, revision)
            studio = studio_at(package)
            target = studio['platforms'].get(platform, {})
            video, poster = target.get('video') or studio.get('video'), target.get('poster') or studio.get('poster')
            if studio['type'] == 'video' and not video:
                raise ValueError('请先上传视频成片并保存版本')
            text = (package / 'body-text.txt').read_text(encoding='utf-8')
            title = target.get('title') or manifest['title']
            caption = target.get('caption', '') if platform == 'wechat' else target.get('caption') or text
            files = set()
            for asset in manifest['body_media'] + ([manifest['cover']] if manifest['cover'] else []) + [a for a in (video, poster) if a]:
                files.add(asset['file'])
            directory = self.workspace / 'exports'
            directory.mkdir(exist_ok=True)
            identity = uuid.uuid4().hex
            destination = directory / (identity + '.zip')
            wechat_html = self.wechat_preview(slug, revision, embedded=True) if platform == 'wechat' else None
            description = {'schema_version': 1, 'platform': platform, 'content_type': studio['type'], 'source_version': revision,
                           'title': title, 'caption': caption, 'topics': target.get('topics', ''), 'account': target.get('account', ''),
                           'video': video, 'poster': poster, 'blocks': manifest['blocks'], 'cover': manifest['cover'],
                           'status': 'exported_not_published',
                           'publication_plan': self.publication_plan(slug, payload)}
            try:
                with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as archive:
                    if wechat_html is not None:
                        archive.writestr('wechat.html', wechat_html)
                        archive.writestr('公众号使用说明.txt', 'wechat.html 为内联样式、内嵌配图的本地预览。\n正文保留作品图文顺序；平台发布文案作为摘要，不替换正文。\n在浏览器打开，复制排版到公众号编辑器后，上传并核验所有图片。\n封面位于素材目录，需在公众号后台单独设置。\n本包未上传、未创建在线草稿、未公开发布。')
                    archive.writestr('publish.json', json.dumps(description, ensure_ascii=False, indent=2))
                    archive.writestr('发布文案.txt', title + '\n\n' + caption + '\n\n' + target.get('topics', ''))
                    archive.writestr('脚本.txt', studio['script'])
                    subtitles = target.get('subtitles') or studio['subtitles']
                    if subtitles:
                        archive.writestr('subtitles.vtt' if subtitles.lstrip().startswith('WEBVTT') else 'subtitles.srt', subtitles)
                    for name in ('article.html', 'body-text.txt'):
                        archive.write(package / name, name)
                    for name in sorted(files):
                        archive.write(safe_path(package, name), name)
                    archive.writestr('说明.txt', '本包包含指定版本的发布素材，尚未上传或公开发布。\n请核对平台标题、画幅、音乐版权和视频编码后发布。\n视频建议使用 H.264/AAC 编码的 MP4。')
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            records = self.delivery_records(slug)
            records.append({'id': identity, 'kind': 'exported', 'platform': platform, 'version': revision,
                            'created_at': datetime.now(timezone.utc).isoformat(), 'file': destination.name})
            write(self.article(slug) / 'deliveries.json', records)
            return {'url': '/exports/' + destination.name, 'path': str(destination), 'version': revision}
