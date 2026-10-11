"""Unified delivery records and immutable export service."""
import hashlib, json, os, re, uuid, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from .core.common import read_json as read, write_json as write, now, under
from .registry import PLATFORMS, build_plan
from .platforms.x import post as x_post
from .transports import buffer_cli, r2_media

# Only these two Buffer modes exist; the caller must name one explicitly.
BUFFER_MODES = {'draft': 'buffer_draft', 'publish_now': 'buffer_submitted'}
# Any Buffer record that has not been proven a clean failure blocks a new submit.
BUFFER_HELD_KINDS = ('buffer_in_progress', 'buffer_unknown', 'buffer_draft',
                     'buffer_submitted', 'buffer_published')
BUFFER_TRACKED_KINDS = BUFFER_HELD_KINDS + ('buffer_failed',)
BUFFER_STATUS_KINDS = ('buffer_in_progress', 'buffer_unknown', 'buffer_draft',
                       'buffer_submitted', 'buffer_published')
BUFFER_HELD_LABELS = {'buffer_in_progress': '提交中、结果未确认', 'buffer_unknown': '结果待核实',
                      'buffer_draft': '已在 Buffer 草稿箱', 'buffer_submitted': '已提交 Buffer 发布',
                      'buffer_published': '已发布'}

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

    # --- X 推文/串文 → 官方 Buffer CLI --------------------------------------
    def buffer_command(self):
        """Operator-level CLI override. Never taken from a request payload."""
        override = getattr(self, 'buffer_cli_command', None)
        if override:
            return list(override)
        configured = str(os.environ.get('ARTICLE_STUDIO_BUFFER_CLI', '')).strip()
        if not configured:
            return None
        path = Path(configured).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError('ARTICLE_STUDIO_BUFFER_CLI 指向的文件不存在或不可执行')
        return [str(path)]

    def buffer_timeout(self):
        try:
            value = int(getattr(self, 'buffer_cli_timeout', 60))
        except (TypeError, ValueError):
            value = 60
        return value if 0 < value <= 300 else 60

    def _buffer_context(self, slug, payload):
        from content_store import studio_at
        platform = payload.get('platform') or 'x'
        if platform != 'x':
            raise ValueError('Buffer 只承接 X 推文/串文；其他平台仍走导出或手动登记')
        # The target account is part of the saved version. A request may never name
        # or replace it, otherwise the Buffer channel check could be pointed at a
        # different account than the one the work shows.
        if 'account' in payload:
            raise ValueError('目标账号只能取该版本保存的 X 账号；请求体不能指定或覆盖 account，要换账号请先保存新版本')
        # Public image links are produced by the image host after an unauthenticated
        # reachability check; a caller must never inject them.
        if 'media_urls' in payload:
            raise ValueError('图片公开链接由图床上传与公开校验后生成，请求体不能传入 media_urls')
        version = str(payload.get('version') or '')
        if not re.fullmatch(r'v\d{4,}', version):
            raise ValueError('请选择已保存的版本')
        _, _, package = self.info(slug, version)
        studio = studio_at(package)
        if studio['type'] != 'post':
            raise ValueError('Buffer 提交只支持推文/串文作品；长文章仍走本机浏览器草稿流程')
        target = studio['platforms'].get('x') or {}
        account = str(target.get('account') or '').strip()
        if not account:
            raise ValueError('请先在 X 平台填写发布账号，再核对 Buffer 渠道')
        return {'version': version, 'studio': studio, 'target': target, 'account': account}

    @staticmethod
    def _buffer_held(records, version):
        return [item for item in records if item.get('platform') == 'x'
                and item.get('version') == version and item.get('kind') in BUFFER_HELD_KINDS]

    def _buffer_held_message(self, held):
        labels = '、'.join(sorted({BUFFER_HELD_LABELS.get(item.get('kind'), item.get('kind')) for item in held}))
        return ('该版本已有 Buffer 提交记录（' + labels + '），为避免重复发布已停止提交；'
                '请先在 Buffer 核实或处理该帖子。结果不明的提交不会自动重试')

    def _buffer_plan(self, slug, version, mode, channel_id, media_urls):
        plan = self.publication_plan(slug, {'platform': 'x', 'version': version,
                                            'options': {'mode': mode, 'channel_id': channel_id,
                                                        'media_urls': media_urls or {}}})
        if plan['status'] != 'payload_ready' or not plan.get('buffer_input'):
            raise ValueError('发布前检查未通过：' + '；'.join(plan.get('blocking') or ['无法生成 Buffer 请求']))
        return plan

    def _buffer_media(self, package, manifest):
        """Which images each tweet carries, mirroring the X adapter's block→tweet rule."""
        media = {item.get('id'): item for item in manifest.get('body_media') or []}
        posts = []
        problems = []
        for block in manifest.get('blocks') or []:
            kind = block.get('kind')
            if kind == 'text':
                posts.append([])
            elif kind == 'image':
                if not posts:
                    posts.append([])
                item = media.get(block.get('id'))
                if item is None:
                    problems.append('正文图片 ' + str(block.get('id')) + ' 不在素材清单里，请先保存新版本')
                    continue
                posts[-1].append(item)
        total = 0
        for index, images in enumerate(posts):
            total += len(images)
            if len(images) > r2_media.MAX_IMAGES_PER_POST:
                problems.append('第 ' + str(index + 1) + ' 条推文有 ' + str(len(images))
                                + ' 张图片：X 单条最多 ' + str(r2_media.MAX_IMAGES_PER_POST) + ' 张')
            extensions = [r2_media.extension_of(item.get('file')) for item in images]
            if 'gif' in extensions and len(images) > 1:
                problems.append('第 ' + str(index + 1) + ' 条推文把 GIF 与其他图片混用：'
                                'X 单条只能是 1 个 GIF 或最多 4 张图片')
            for item in images:
                name = str(item.get('file'))
                if r2_media.extension_of(name) not in r2_media.CONTENT_TYPES:
                    problems.append(name + '：不支持的图片格式（仅 jpg / png / webp / gif）')
                size = item.get('bytes')
                if isinstance(size, int) and size > r2_media.MAX_IMAGE_BYTES:
                    problems.append(name + '：' + format(size / 1024 / 1024, '.1f')
                                    + ' MB 超过单张 5 MB 上限')
                try:
                    path = under(package, name)
                except ValueError:
                    problems.append(name + '：素材路径不在作品目录内')
                    continue
                if not path.is_file():
                    problems.append(name + '：本机素材文件不存在')
        return {'posts': posts, 'problems': problems, 'total': total}

    def _media_view(self, slug, version, images):
        return [{'file': str(item.get('file')), 'bytes': item.get('bytes'),
                 'width': item.get('width'), 'height': item.get('height'),
                 'url': self.asset_url(slug, version, str(item.get('file')))} for item in images]

    def _buffer_image_host(self):
        """Return (config, problem). No config is fine for text-only works."""
        try:
            config = r2_media.load_config()
        except ValueError as exc:
            return None, '图床（R2）配置不可用：' + str(exc)
        if config is None:
            return None, ('尚未配置图床（R2）：带图作品需要 '
                          '~/Library/Application Support/ArticleStudio/r2-media.json'
                          '（或环境变量 ARTICLE_STUDIO_R2_CONFIG）；纯文字推文不受影响')
        return config, None

    def _buffer_media_blocking(self, media):
        """Image rules that can be decided before Buffer is contacted at all."""
        problems = list(media['problems'])
        if media['total']:
            _, missing_host = self._buffer_image_host()
            if missing_host:
                problems.append(missing_host)
        return problems

    def _buffer_adapter_blocking(self, plan, media):
        """Adapter rules, except the missing-link note the image host resolves itself."""
        if media['total']:
            return [note for note in plan.get('blocking') or [] if note != x_post.MISSING_MEDIA_NOTE]
        return list(plan.get('blocking') or [])

    def buffer_media_status(self):
        """Read-only image-host readiness for the workbench. Never returns credentials."""
        config, problem = self._buffer_image_host()
        return {'configured': config is not None,
                'host': urlsplit(config.public_base_url).netloc if config else None,
                'problem': problem}

    def _buffer_upload(self, slug, version, package, media):
        """Upload every image and prove its public link. Writes no delivery record."""
        config, missing_host = self._buffer_image_host()
        if config is None:
            raise ValueError(missing_host)
        timeout = self.buffer_timeout()
        urls, objects = {}, []
        for images in media['posts']:
            for item in images:
                name = str(item.get('file'))
                body = under(package, name).read_bytes()
                if len(body) > r2_media.MAX_IMAGE_BYTES:
                    raise ValueError('提交前检查未通过：' + name + ' 实际 '
                                     + format(len(body) / 1024 / 1024, '.1f')
                                     + ' MB 超过单张 5 MB 上限；未提交到 Buffer')
                digest = hashlib.sha256(body).hexdigest()
                key = r2_media.object_key(config.prefix, slug, version, digest, name)
                content_type = r2_media.content_type_for(name)
                url = r2_media.public_url(config, key)
                if not content_type:
                    raise ValueError('图片公开校验未通过：' + name + ' → 不支持的图片格式；未提交到 Buffer')
                existing = r2_media.head_object(key, config, timeout)
                reused = bool(existing and existing.get('status') == 200
                              and existing.get('size') == len(body))
                if not reused:
                    r2_media.put_object(key, body, content_type, config, timeout)
                reachable = r2_media.public_reachable(url, timeout, config)
                if not reachable['ok']:
                    raise ValueError('图片公开校验未通过：' + name + ' → ' + reachable['reason']
                                     + '；未提交到 Buffer，可重试')
                urls[name] = url
                objects.append({'file': name, 'key': key, 'url': url, 'bytes': len(body),
                                'content_type': content_type, 'uploaded': not reused})
        return {'urls': urls, 'objects': objects}

    def buffer_channels(self, slug, payload):
        """Read-only check: channel match, the exact tweets, and the images each carries."""
        mode = payload.get('mode') or 'draft'
        if mode not in BUFFER_MODES:
            raise ValueError('必须明确选择提交模式：draft（Buffer 草稿箱）或 publish_now（直接发布到 X）')
        with self.lock:
            context = self._buffer_context(slug, payload)
            manifest, _, package = self.info(slug, context['version'])
        command = self.buffer_command()
        channels = buffer_cli.channels_list(command, self.buffer_timeout())
        try:
            channel = buffer_cli.match_channel(channels, context['account'])
        except ValueError as exc:
            return {'account': context['account'], 'version': context['version'], 'resolved': False,
                    'channel': None, 'channels': channels, 'message': str(exc), 'posts': [], 'blocking': []}
        plan = self.publication_plan(slug, {'platform': 'x', 'version': context['version'],
                                            'options': {'mode': mode, 'channel_id': channel['id']}})
        media = self._buffer_media(package, manifest)
        aligned = len(media['posts']) == len(plan['posts'])
        blocking = self._buffer_media_blocking(media) + self._buffer_adapter_blocking(plan, media)
        if media['total'] and not aligned:
            blocking.append('图片与推文的对应关系无法核对（正文块数与发布计划条数不一致），已停止提交')
        posts = []
        for index, post in enumerate(plan['posts']):
            images = media['posts'][index] if aligned else []
            posts.append(dict(post, images=self._media_view(slug, context['version'], images),
                              image_count=len(images)))
        return {'account': context['account'], 'version': context['version'],
                'resolved': not blocking,
                'channel': channel, 'channels': channels,
                'message': ('Buffer 渠道 @' + str(channel['name']) + ' 与目标账号一致') if not blocking
                           else '；'.join(blocking),
                'posts': posts, 'post_count': len(posts), 'blocking': blocking,
                'missing_media': plan.get('missing_media') or [],
                'image_total': media['total'],
                'submission_mode': plan.get('submission_mode', mode)}

    def buffer_submit(self, slug, payload):
        mode = payload.get('mode')
        if mode not in BUFFER_MODES:
            raise ValueError('必须明确选择提交模式：draft（Buffer 草稿箱）或 publish_now（直接发布到 X）')
        if payload.get('confirmed') is not True:
            raise ValueError('请先在确认框核对目标账号、Buffer 渠道与每条推文全文')
        with self.lock:
            context = self._buffer_context(slug, payload)
            held = self._buffer_held(self.delivery_records(slug), context['version'])
            if held:
                raise ValueError(self._buffer_held_message(held))
            manifest, _, package = self.info(slug, context['version'])
        media = self._buffer_media(package, manifest)
        media_problems = self._buffer_media_blocking(media)
        if media_problems:
            raise ValueError('提交前检查未通过：' + '；'.join(media_problems))
        command = self.buffer_command()
        channel = buffer_cli.resolve_channel(context['account'], command, self.buffer_timeout())
        base = self.publication_plan(slug, {'platform': 'x', 'version': context['version'],
                                            'options': {'mode': mode, 'channel_id': channel['id']}})
        if media['total'] and len(media['posts']) != len(base['posts']):
            raise ValueError('发布前检查未通过：图片与推文的对应关系无法核对（正文块数与发布计划条数不一致）')
        problems = self._buffer_adapter_blocking(base, media)
        if problems:
            raise ValueError('发布前检查未通过：' + '；'.join(problems))
        # Upload only after the reviewer confirmed and every pre-flight check passed.
        # A failed upload or public check leaves no delivery record behind, so the same
        # version stays submittable.
        uploaded = self._buffer_upload(slug, context['version'], package, media) if media['total'] \
            else {'urls': {}, 'objects': []}
        plan = self._buffer_plan(slug, context['version'], mode, channel['id'], uploaded['urls'])
        with self.lock:
            records = self.delivery_records(slug)
            held = self._buffer_held(records, context['version'])
            if held:
                raise ValueError(self._buffer_held_message(held))
            # Written before the CLI call: a crash mid-submit leaves a held record,
            # so the same version can never be submitted twice by accident.
            record = {'id': uuid.uuid4().hex, 'kind': 'buffer_in_progress', 'platform': 'x',
                      'version': context['version'], 'account': context['account'],
                      'submission_mode': mode, 'channel_id': channel['id'],
                      'channel_name': channel['name'], 'post_id': None, 'buffer_status': None,
                      'url': None, 'post_count': len(plan['posts']),
                      'media': uploaded['objects'],
                      'created_at': now(), 'updated_at': now(),
                      'message': '正在提交到 Buffer，结果尚未确认'}
            records.append(record)
            write(self.article(slug) / 'deliveries.json', records)
        result = buffer_cli.create_post(plan['buffer_input'], command, self.buffer_timeout())
        return self._buffer_settle(slug, record['id'], result)

    def _buffer_settle(self, slug, ident, result):
        with self.lock:
            records = self.delivery_records(slug)
            record = next((item for item in records if item.get('id') == ident), None)
            if record is None:
                raise ValueError('发布记录已不存在，未改动 Buffer 结果')
            post = result.get('post') or {}
            record['message'] = result['message']
            record['updated_at'] = now()
            if post.get('status') is not None:
                record['buffer_status'] = post.get('status')
            if post.get('error_message'):
                record['error_message'] = post['error_message']
            if result['outcome'] == 'submitted':
                record['kind'] = BUFFER_MODES[record['submission_mode']]
                record['post_id'] = post.get('id')
                link = buffer_cli.tweet_link(post.get('externalLink'))
                if link and link['account'].lower() == buffer_cli.normalize_account(record.get('account')):
                    record['kind'] = 'buffer_published'
                    record['url'] = link['url']
            elif result['outcome'] == 'failed':
                record['kind'] = 'buffer_failed'
            else:
                record['kind'] = 'buffer_unknown'
            write(self.article(slug) / 'deliveries.json', records)
            return {'record': record, 'outcome': result['outcome'], 'retryable': bool(result.get('retryable'))}

    def buffer_refresh(self, slug, payload):
        with self.lock:
            context = self._buffer_context(slug, payload)
            candidates = [item for item in self.delivery_records(slug)
                          if item.get('platform') == 'x' and item.get('version') == context['version']
                          and item.get('post_id') and item.get('kind') in BUFFER_STATUS_KINDS]
            if not candidates:
                raise ValueError('该版本还没有可查询的 Buffer 帖子记录')
            ident, post_id = candidates[-1]['id'], candidates[-1]['post_id']
        result = buffer_cli.get_post(post_id, self.buffer_command(), self.buffer_timeout())
        with self.lock:
            records = self.delivery_records(slug)
            record = next((item for item in records if item.get('id') == ident), None)
            if record is None:
                raise ValueError('发布记录已不存在，未改动 Buffer 状态')
            post = result.get('post') or {}
            record['updated_at'] = now()
            record['observed_at'] = now()
            record['message'] = result['message']
            if post.get('status') is not None:
                record['buffer_status'] = post.get('status')
            if post.get('error_message'):
                record['error_message'] = post['error_message']
            if result['outcome'] == 'ok':
                if record['kind'] in ('buffer_in_progress', 'buffer_unknown'):
                    record['kind'] = BUFFER_MODES.get(record.get('submission_mode'), 'buffer_submitted')
                link = buffer_cli.tweet_link(post.get('externalLink'))
                if link:
                    if link['account'].lower() == buffer_cli.normalize_account(record.get('account')):
                        record['kind'] = 'buffer_published'
                        record['url'] = link['url']
                        record['message'] = 'Buffer 已发布，推文链接已记录'
                    else:
                        record['link_account'] = link['account']
                        record['message'] = ('Buffer 返回的推文链接属于 @' + link['account']
                                             + '，与目标账号 @' + str(record.get('account') or '')
                                             + ' 不一致，未记为已发布')
                elif post.get('externalLink'):
                    record['message'] = 'Buffer 返回的链接不是目标账号的推文链接，未记为已发布'
                elif post.get('status') == 'sent':
                    record['message'] = 'Buffer 显示已发布，但推文链接尚未返回，可稍后再刷新状态'
                else:
                    record['message'] = 'Buffer 当前状态：' + str(post.get('status') or '未知')
            write(self.article(slug) / 'deliveries.json', records)
            return {'record': record, 'outcome': result['outcome']}
