"""Persist manual media submissions in the same conversation as typed messages."""
import copy
import uuid
import re
from generation.models import MODELS, validate_size
from generation.loras import lora_parameters

class AssistantMediaMixin:
    def init_media_conversation(self):
        self.chat_state_path=self.folder/'chat-state.json'
        if self.chat_state_path.exists():
            self.conversation_id=self.content.read(self.chat_state_path)['id']
        else:
            self.rotate_media_conversation()

    def rotate_media_conversation(self):
        identity=uuid.uuid4().hex
        self.content.write(self.chat_state_path,{'id':identity})
        self.conversation_id=identity

    def record_media_turn(self, media_job, recovered=False):
        with self.lock:
            if any(a.get('media_job')==media_job['id'] for t in self.turns for a in t.get('actions',[])):
                return
            turn={'id':'media-'+media_job['id'],'prompt':(media_job.get('chat_request') or {}).get('message') or media_job['prompt'],
                  'answer':'已找回这条生成任务，实际状态见上方。' if recovered else '生成任务已记录。进度和结果会直接显示在这里，关闭设置窗口不会丢失。',
                  'model':media_job['model_name'],'time':media_job['created_at'],'status':'done',
                  'actions':[{'name':'generate_media','label':'生成图片' if media_job['kind']=='image' else '生成视频',
                              'status':'done','media_job':media_job['id']}],
                  'policy':{'allowed_tools':[],'allow_insert':False},'source':'media-composer',
                  'conversation_id':self.conversation_id}
            updated=self.turns+[turn]
            self.content.write(self.history_path,updated)
            self.turns=updated

    def recover_media_turns(self):
        generation=getattr(self,'generation',None)
        if not generation:return
        with generation.lock:
            jobs=copy.deepcopy(list(generation.jobs.values()))
        for job in sorted(jobs,key=lambda j:j['created_at']):
            if job.get('origin')=='composer' and job.get('conversation_id')==self.conversation_id:
                self.record_media_turn(job)

    def start_media(self, payload):
        with self.lock:
            payload=copy.deepcopy(payload)
            if MODELS.get(payload.get('model'),{}).get('kind')=='image':
                prompt,skill=self.image_skill.compose(payload.get('prompt',''),payload.get('image_style'))
                payload.update(prompt=prompt,image_skill=skill)
            result=self.generation.start(payload,origin='composer',conversation_id=self.conversation_id)
            self.record_media_turn(result)
            return result

    def start_lab_media(self,payload):
        payload=copy.deepcopy(payload)
        if MODELS.get(payload.get('model'),{}).get('kind')=='image':
            prompt,skill=self.image_skill.compose(payload.get('prompt',''),payload.get('image_style'))
            payload.update(prompt=prompt,image_skill=skill)
        # Separate source; never record a chat turn or accept caller-supplied origin.
        return self.generation.start(payload,origin='lab')

    def media_preferences(self):
        defaults={'image':{'model':'qi21','size':'1024x1024'},'video':{'model':'h3','size':'768x432','frames':56}}
        path=self.folder/'media-preferences.json'
        if path.exists():defaults.update(self.content.read(path))
        return defaults

    def save_media_preferences(self, payload):
        kind=payload.get('kind');model=MODELS.get(payload.get('model'))
        if kind not in ('image','video') or not model or model['kind']!=kind:
            raise ValueError('请选择对应类型的模型')
        size=validate_size(model,payload.get('size',model['sizes'][0]))
        value={'model':model['id'],'size':size}
        if model['id']=='krea2':value.update(lora_parameters(payload))
        elif payload.get('lora'):lora_parameters(payload)
        if kind=='image' and payload.get('image_style'):
            if payload['image_style'] not in self.image_skill.styles:raise ValueError('未知生图风格')
            value['image_style']=payload['image_style']
        if kind=='video':
            frames=payload.get('frames',56)
            if frames not in (56,124):raise ValueError('不支持该时长')
            value['frames']=frames
        with self.lock:
            prefs=self.media_preferences();prefs[kind]=value
            self.content.write(self.folder/'media-preferences.json',prefs)
        return prefs

    def direct_media(self, payload):
        """Explicit tool selections bypass the LLM; continuations reference actual jobs."""
        text=str(payload.get('message','')).strip()
        selected=payload.get('tool')
        if selected and selected not in ('image','video'):raise ValueError('未知工具')
        mention=re.match(r'^@(生图|视频)(?:\s+|[：:]|$)',text)
        if mention:
            selected={'生图':'image','视频':'video'}[mention[1]]
            text=text[mention.end():].strip()
        again=bool(re.fullmatch(r'(?:请)?(?:再生成(?:一张|一次)?|再次生成(?:一张|一次)?|重新生成(?:一张|一次)?|重试)[！!。\s]*',text))
        switch=re.fullmatch(r'(?:请)?(?:换成|换用|用)\s*(Krea\s*2|Qwen\s*Image\s*2\.1|H3)(?:\s*(?:再生成(?:一张|一次)?|重新生成))?[！!。\s]*',text,re.I)
        reference=payload.get('reference_job')
        if not (selected or again or switch or reference):return None
        if payload.get('attachments'):
            raise ValueError('当前生成工具尚未接入参考图；请取消生图/视频标签，先把图片发给聊天模型查看')
        with self.lock:
            # A retried HTTP request must reuse the original resolved reference and seed.
            request_id=payload.get('request_id')
            with self.generation.lock:
                existing=next((j for j in self.generation.jobs.values() if request_id and j.get('request_id')==request_id and j.get('conversation_id')==self.conversation_id),None)
                if existing:
                    if existing.get('chat_request')!=payload:raise ValueError('请求编号已使用，请重新发送')
                    self.record_media_turn(existing)
                    return {'media_job':existing['id']}
            options=payload.get('media') or {}
            if not isinstance(options,dict):raise ValueError('无效生成参数')
            source=None
            if again or switch or reference:
                references=[a['media_job'] for t in self.turns for a in t.get('actions',[]) if a.get('media_job')]
                if reference:
                    if reference not in references:raise ValueError('请从当前对话的生成卡片选择任务')
                else:
                    # Only the immediately preceding turn grants a referent, never a stale task.
                    recent=[a['media_job'] for a in (self.turns[-1].get('actions',[]) if self.turns else []) if a.get('media_job')]
                    if len(recent)!=1:raise ValueError('请点击要重试的生成卡片上的“再生成一张”，明确选择任务')
                    reference=recent[0]
                source=self.generation.public(reference)
                if selected and selected!=source['kind']:raise ValueError('所选工具与原任务类型不一致')
                selected=source['kind']
                if again or switch or not text:
                    text=(source.get('image_skill') or {}).get('user_prompt') or source.get('input_prompt') or source['prompt']
            if not text:raise ValueError('请填写希望生成的画面描述')
            settings=self.media_preferences()[selected].copy()
            if source:
                fields=('size','frames','lora','lora_strength','lora_trigger')
                fields+=('workflow','video_checkpoint','video_accelerator','video_loras','references') if selected=='video' else ('steps','cfg')
                settings.update({k:copy.deepcopy(source['settings'][k]) for k in fields if k in source['settings']})
                if selected=='video':
                    settings['video_loras']=[{'id':row['id'],'strength':row.get('strength',1.0)} for row in settings.get('video_loras',[])]
                if not source['settings'].get('lora'):settings.update(lora='',lora_trigger='')
                settings['model']=source['model']
                if selected=='image':settings['image_style']=(source.get('image_skill') or {}).get('style','')
            fields=('model','size','frames','seed','image_style','lora','lora_strength','lora_trigger')
            if selected=='video':fields+=('workflow','video_checkpoint','video_accelerator','video_loras','references')
            settings.update({k:copy.deepcopy(options[k]) for k in fields if k in options})
            if switch:
                name=re.sub(r'\s+','',switch[1]).lower()
                settings['model']={'krea2':'krea2','qwenimage2.1':'qi21','h3':'h3'}[name]
            model=MODELS.get(settings['model'])
            if not model or model['kind']!=selected:raise ValueError('所选模型与生成类型不匹配')
            if 'size' not in options:
                try:validate_size(model,settings.get('size'))
                except ValueError:settings['size']=model['sizes'][0]
            if model['id']!='krea2':
                for key in ('lora','lora_strength','lora_trigger'):settings.pop(key,None)
            context=payload.get('context') or {}
            if source:context={'slug':source.get('slug'),'version':source.get('base_version')}
            result=self.start_media({**settings,'prompt':text,'slug':context.get('slug'),'version':context.get('version'),
                                     'request_id':request_id,'chat_request':payload,'reference_job':reference})
            return {'media_job':result['id']}
