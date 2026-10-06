"""Persistent local browser deliveries. No platform credentials or private APIs."""
import base64
import re
import uuid
import time
import copy
import tempfile
import shutil
import hashlib
from pathlib import Path
from .core.common import read_json, write_json, now, under
from .platforms.x.article_workflow import account_name, draft_id

SAVED_CHECKS = ('text_checked', 'order_checked', 'positions_checked', 'cover_checked', 'paragraphs_checked')

class LocalDeliveries:
    def __init__(self, store):
        self.store = store
        self.root = store.workspace / 'publishing' / 'jobs'
        self.connections = {}

    def report_connection(self, payload):
        client=payload.get('client','')
        if not re.fullmatch(r'[a-f0-9-]{36}',str(client)) or type(payload.get('connected')) is not bool:
            raise ValueError('无效浏览器连接状态')
        with self.store.lock:
            self.connections[client]={'connected':payload['connected'],'seen':time.monotonic(),'at':now()}
            return self.connection_status()

    def connection_status(self):
        with self.store.lock:
            current=time.monotonic()
            self.connections={key:value for key,value in self.connections.items() if current-value['seen']<35}
            live=[value for value in self.connections.values() if value['connected']]
            return {'connected':bool(live),'last_seen':max((x['at'] for x in live),default=None),
                    'account_verified':False,'message':'本机发布扩展已与工作台连接；账号将在上传时核验' if live else '等待 Chrome 工作台页面刷新并连接扩展'}

    def path(self, ident):
        if not re.fullmatch(r'[a-f0-9]{32}', str(ident)):
            raise ValueError('无效发布任务')
        return self.root / (ident + '.json')

    def get(self, ident):
        return read_json(self.path(ident))

    def listing(self, slug, version):
        with self.store.lock:
            jobs = [read_json(p) for p in self.root.glob('*.json')]
            return sorted([j for j in jobs if j['slug']==slug and j['version']==version], key=lambda j:j['created_at'], reverse=True)

    def preflight(self, slug, payload):
        account=account_name(str(payload.get('account','')).strip())
        version=payload.get('version')
        if not version: raise ValueError('请选择已保存的作品版本')
        with self.store.lock:
            manifest,_,_=self.store.info(slug,version)
            observed=[];reference=[]
            for path in self.root.glob('*.json'):
                job=read_json(path)
                limit=job.get('checkpoint',{}).get('native_limits',{})
                count=limit.get('body_images')
                if limit.get('account')==job['account'] and type(count) is int and 0<count<=500 and 'exceeds the media limit per Article' in limit.get('message',''):
                    reference.append(limit)
                    if job['account']==account: observed.append(limit)
            limit=min(observed,key=lambda x:x['body_images']) if observed else None
            count=len(manifest['body_media'])
            ident=hashlib.sha256(f"{account}:{manifest['package_id']}:{limit['body_images'] if limit else ''}".encode()).hexdigest()[:24]
            path=self.store.workspace/'publishing'/'adaptations'/(ident+'.json')
            over_limit=bool(limit and count>limit['body_images'])
            return dict(account=account,source_images=count,limit=limit,over_limit=over_limit,
                        excess_images=max(0,count-limit['body_images']) if limit else None,
                        reference_limit=min(reference,key=lambda x:x['body_images']) if reference and not limit else None,
                        needs_split=over_limit,adaptation=read_json(path) if path.exists() else None,adaptation_id=ident)

    def require_media_capacity(self, slug, payload):
        preflight=self.preflight(slug,payload)
        if preflight['over_limit']:
            raise ValueError(f"图片数量超限：正文 {preflight['source_images']} 张，@{preflight['account']} 已实测单篇上限 {preflight['limit']['body_images']} 张，超出 {preflight['excess_images']} 张。已停止上传；分批上传不能增加单篇文章容量。")
        return preflight

    def adapt(self, slug, payload):
        """Produce immutable local parts; source content and X drafts stay intact."""
        from .core import article_package as importer
        import content_store as content
        with self.store.lock:
            preflight=self.preflight(slug,payload)
            if preflight['adaptation']: return preflight['adaptation']
            if not preflight['needs_split']: raise ValueError('当前版本没有已检测到的媒体超限，无需分篇')
            manifest,_,package=self.store.info(slug,payload['version'])
            if content.studio_at(package)['type']!='article': raise ValueError('分篇适配仅支持长文章')
            maximum=preflight['limit']['body_images']
            blocks=manifest['blocks'];chunks=[];start=0
            while True:
                media=[i for i in range(start,len(blocks)) if blocks[i]['kind']!='text']
                if len(media)<=maximum:
                    chunks.append((start,len(blocks)));break
                overflow=media[maximum]
                headings=[i for i in range(start+1,overflow+1) if blocks[i]['kind']=='text' and re.match(r'\s*<h[1-6]\b',blocks[i]['html']) and sum(j<i for j in media)>=max(1,maximum//2)]
                cut=headings[-1] if headings else overflow
                chunks.append((start,cut));start=cut
            parts=[]
            for number,(begin,end) in enumerate(chunks,1):
                part_slug=f"x-part-{preflight['adaptation_id']}-{number}"
                selected=copy.deepcopy(blocks[begin:end]);cover=copy.deepcopy(manifest['cover']) if number==1 else None
                title=manifest['title']+f'（{number}/{len(chunks)}）'
                with tempfile.TemporaryDirectory(prefix='studio-x-adapt-') as tmp:
                    out=Path(tmp);(out/'images').mkdir()
                    for item in [b for b in selected if b['kind']!='text']+([cover] if cover else []):
                        destination=under(out,item['file']);destination.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copy2(under(package,item['file']),destination)
                    importer.finalize_package(out,title,cover,selected,manifest['source_sha256'],list(manifest.get('warnings',[])))
                    content.seal(out,content.empty_studio('article'),importer)
                    revision=self.store.commit(part_slug,out,f'X 分篇适配 {number}/{len(chunks)}','原稿 '+slug+' '+payload['version'])
                part_manifest,_,_=self.store.info(part_slug,revision)
                parts.append(dict(slug=part_slug,version=revision,title=title,body_images=len(part_manifest['body_media']),source_block_start=begin,source_block_end=end,package_id=part_manifest['package_id']))
            record=dict(id=preflight['adaptation_id'],source_slug=slug,source_version=payload['version'],source_package_id=manifest['package_id'],account=preflight['account'],limit=maximum,parts=parts,created_at=now())
            write_json(self.store.workspace/'publishing'/'adaptations'/(record['id']+'.json'),record)
            return record

    def start(self, slug, payload):
        from content_store import studio_at
        version = payload.get('version')
        if not version: raise ValueError('请选择已保存的作品版本')
        account = account_name(str(payload.get('account','')).strip())
        url = str(payload.get('draft_url') or '').strip()
        if url: draft_id(url)
        with self.store.lock:
            manifest, _, package = self.store.info(slug, version)
            if studio_at(package)['type'] != 'article':
                raise ValueError('本机浏览器入口当前支持 X 长文章；普通推文入口尚未接通')
            self.require_media_capacity(slug,payload)
            # Repeated clicks resume the same account + immutable version.
            for job in self.listing(slug, version):
                if job['account']!=account or job['status'] in ('published','superseded'): continue
                if url:
                    if job['draft_url'] and draft_id(job['draft_url'])!=draft_id(url):
                        raise ValueError('当前任务已经绑定另一份草稿，不能替换原绑定')
                    if not job['draft_url']:
                        if job['publish_attempted'] or job['status'] not in ('queued','blocked'):
                            raise ValueError('请等当前操作停止后再接入手动创建的草稿')
                        job.update(draft_url=url,status='queued',error_code=None,
                                   message='已接入手动创建的草稿，等待核验账号与编辑器',updated_at=now())
                        write_json(self.path(job['id']),job)
                return job
            # Preserve the previously bound real draft; do not create duplicates.
            legacy = Path(__file__).resolve().parents[2] / 'x-article-resume/job/job.json'
            if not url and legacy.exists():
                old = read_json(legacy)
                if old.get('package_id')==manifest['package_id'] and old.get('account')==account:
                    url = old.get('draft_url','')
            job = dict(id=uuid.uuid4().hex, slug=slug, version=version, account=account,
                       package_id=manifest['package_id'], title=manifest['title'],
                       status='queued', message='等待连接本机 Chrome', draft_url=url,
                       created_at=now(), updated_at=now(), checkpoint={}, evidence=None,
                       published_url=None, publish_attempted=False)
            write_json(self.path(job['id']), job)
            return job

    def bundle(self, ident):
        with self.store.lock:
            j=self.get(ident)
            self.require_media_capacity(j['slug'],{'version':j['version'],'account':j['account']})
            m,_,p=self.store.info(j['slug'],j['version'])
            if m['package_id']!=j['package_id']: raise ValueError('版本校验失败')
            def media(item):
                file=under(p,item['file'])
                return dict(id=item.get('id','cover'), marker=item.get('marker'), name=file.name,
                            sha256=item['sha256'], data=base64.b64encode(file.read_bytes()).decode())
            return dict(job=j, title=m['title'], html=(p/'paste-body.html').read_text(),
                        text=(p/'body-text.txt').read_text(), images=[media(x) for x in m['body_media']],
                        cover=media(m['cover']) if m['cover'] else None)

    def update(self, ident, payload):
        with self.store.lock:
            j=self.get(ident)
            if j['status']=='published': raise ValueError('已发布任务不能再次执行')
            action=payload.get('action')
            if action=='new-draft':
                self.require_media_capacity(j['slug'],{'version':j['version'],'account':j['account']})
                if j['status']!='blocked' or j['publish_attempted'] or j.get('error_code')!='DRAFT_UNAVAILABLE':
                    raise ValueError('只有原草稿无法访问且未尝试发布的任务可以另建草稿')
                if payload.get('confirmed') is not True:
                    raise ValueError('请明确确认保留原草稿记录并另建草稿')
                new=dict(j,id=uuid.uuid4().hex,status='queued',message='已选择另建草稿，原草稿和记录均保留',
                         draft_url='',checkpoint={},evidence=None,error_code=None,
                         created_at=now(),updated_at=now(),supersedes=j['id'])
                # Write the replacement before retiring this record; a repeated
                # action can never silently delete or replace a bound X draft.
                write_json(self.path(new['id']),new)
                j.update(status='superseded',message='已另建任务；原草稿记录保留',replacement_id=new['id'],updated_at=now())
                write_json(self.path(ident),j)
                return new
            if j['status']=='superseded':
                raise ValueError('此任务已保留为历史记录，请使用新任务')
            if action=='confirm':
                self.require_media_capacity(j['slug'],{'version':j['version'],'account':j['account']})
                if j['status']!='review_ready' or not j['draft_url'] or not j.get('evidence') or any(j['evidence'].get(key) is not True for key in SAVED_CHECKS):
                    raise ValueError('请先完成草稿上传与检查')
                if payload.get('account')!=j['account'] or payload.get('version')!=j['version'] or payload.get('reviewed') is not True:
                    raise ValueError('请核对账号、版本、正文和全部图片后确认')
                if j['publish_attempted']: raise ValueError('发布结果待核验，不能重复点击发布')
                j.update(status='publish_requested', message='已确认，等待本机浏览器发布')
            elif action=='cover-reviewed':
                if j['publish_attempted']: raise ValueError('发布结果待核验')
                j['checkpoint']['cover']={'user_reviewed':True,'at':now()}
            elif action=='attempt':
                if j['status']!='publish_requested' or j['publish_attempted']:
                    raise ValueError('任务未授权发布或已有未核验的发布尝试')
                j.update(status='publishing',publish_attempted=True,message='正在发布；结果未知时不会自动重试')
            else:
                status=payload.get('status')
                if status not in ('running','blocked','review_ready','unknown','published'):
                    raise ValueError('无效任务状态')
                if j['publish_attempted'] and status not in ('unknown','published'):
                    raise ValueError('发布尝试后必须先核验结果')
                observed=payload.get('observed_account')
                if observed and account_name(observed)!=j['account']: raise ValueError('浏览器登录账号与目标不符')
                url=payload.get('draft_url')
                if url:
                    draft_id(url)
                    if j['draft_url'] and draft_id(j['draft_url'])!=draft_id(url): raise ValueError('不能更换已绑定草稿')
                    j['draft_url']=url
                if status=='review_ready':
                    evidence=payload.get('evidence') or {}
                    manifest,_,_=self.store.info(j['slug'],j['version'])
                    if not isinstance(evidence,dict) or any(evidence.get(key) is not True for key in SAVED_CHECKS) or evidence.get('title')!=j['title'] or evidence.get('image_count')!=len(manifest['body_media']):
                        raise ValueError('正文、标题、段落、封面或图片位置检查证据不完整')
                    if observed!=j['account'] or not j['draft_url'] or not evidence:
                        raise ValueError('缺少账号、草稿或检查证据')
                if status=='published':
                    if j['status']!='publishing' or not j['publish_attempted']: raise ValueError('未发起发布')
                    url=payload.get('published_url','')
                    if not re.fullmatch(r'https://x\.com/'+re.escape(j['account'])+r'/(?:status|article)/[0-9]+',url):
                        raise ValueError('缺少目标账号的真实发布链接')
                    record=self.store.record_delivery(j['slug'],dict(version=j['version'],platform='x',url=url,note='本机浏览器发布任务 '+ident))
                    records=self.store.delivery_records(j['slug'])
                    for entry in records:
                        if entry['id']==record['id']: entry.update(account=j['account'],kind='published_browser')
                    write_json(self.store.article(j['slug'])/'deliveries.json',records)
                    j['published_url']=url
                j.update(status=status,message=str(payload.get('message',''))[:1000])
                j['error_code']=str(payload.get('error_code') or '')[:80] or None
                if 'diagnostics' in payload:
                    diagnostics=payload['diagnostics']
                    if diagnostics is not None and (not isinstance(diagnostics,dict) or len(str(diagnostics))>12000):
                        raise ValueError('无效上传诊断数据')
                    j['diagnostics']=diagnostics
                if 'checkpoint' in payload: j['checkpoint']=payload['checkpoint']
                if 'evidence' in payload: j['evidence']=payload['evidence']
            j['updated_at']=now();write_json(self.path(ident),j);return j
