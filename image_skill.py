"""Local illustration-generator knowledge; execution stays in Generation."""
import ast
import json
import os
from pathlib import Path


class ImageSkill:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('ARTICLE_STUDIO_IMAGE_SKILL',
                         str(Path.home()/'.codex/skills/illustration-generator'))).resolve()
        self.styles = {}
        self.error = None
        try:
            if not (self.root/'SKILL.md').is_file():raise ValueError('本地 illustration-generator 尚未安装')
            source = self.root/'modules/gpt-prompts/references/styles.py'
            tree = ast.parse(source.read_text(encoding='utf-8'))
            library = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                           and any(isinstance(t,ast.Name) and t.id=='STYLES' for t in n.targets))
            for category, group in library.items():
                for name, data in group['styles'].items():
                    self.styles['gpt:'+name] = {'id':'gpt:'+name,'name':name,'category':category,
                        'instruction':data.get('style_instruction',data.get('instruction','')),
                        'tips':data.get('tips',''),'example':data.get('example','')}
            article = self.root/'references/article-presets.json'
            if article.is_file():
                for name, data in json.loads(article.read_text())['styles'].items():
                    self.styles['article:'+name] = {'id':'article:'+name,'name':name,'category':'文章配图',
                        'instruction':data['prompt_template'],'tips':data.get('description',''),'example':''}
            info = self.root/'STUDIO-SOURCE.json'
            self.version = json.loads(info.read_text()).get('commit','local') if info.is_file() else 'local'
        except (OSError,ValueError,SyntaxError,StopIteration,KeyError,TypeError) as exc:
            self.styles={};self.version=None;self.error=str(exc)

    def status(self):
        return {'name':'illustration-generator','ready':bool(self.styles),'version':self.version,
                'features':{'prompt_design':bool(self.styles),'style_presets':bool(self.styles),
                            'reference_image_editing':False,'automatic_batch':False,'collection_write':False},
                'styles':[{k:s[k] for k in ('id','name','category')} for s in self.styles.values()],
                **({'error':self.error} if self.error else {})}

    def read(self, args):
        if not self.styles:raise ValueError(self.error or '生图技能未就绪')
        raw=args.get('path')
        if not isinstance(raw,str) or not raw:raise ValueError('请选择技能资料路径')
        path=(self.root/raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if not path.is_relative_to(self.root) or path.suffix not in ('.md','.json','.yaml','.py'):
            raise ValueError('仅能读取 illustration-generator 内的技能资料')
        if any(p.startswith('.') for p in path.relative_to(self.root).parts):raise ValueError('不能读取隐藏配置')
        if path.stat().st_size>1024*1024:raise ValueError('技能资料过大')
        lines=path.read_text(encoding='utf-8').splitlines()
        start=max(0,int(args.get('offset',1))-1);limit=min(300,max(1,int(args.get('limit',120))))
        return {'path':str(path),'content':'\n'.join(lines[start:start+limit]),'total_lines':len(lines),
                'next_offset':start+limit+1 if start+limit<len(lines) else None,
                'summary':'已读取生图技能资料：'+path.name}

    def compose(self, prompt, style):
        if not style:return prompt,None
        if style not in self.styles:raise ValueError('未知生图风格，请重新选择')
        data=self.styles[style]
        full=f'{prompt}\n\n视觉风格：{data["name"]}\n{data["instruction"]}\n{data["tips"]}\n{data["example"]}\n用户明确指定的主体、文字、构图和限制优先于以上通用风格建议。'
        if len(full)>8000:raise ValueError('描述加风格规范超过 8000 字，请缩短描述')
        return full,{'name':'illustration-generator','version':self.version,'style':style,
                     'style_name':data['name'],'category':data['category'],'user_prompt':prompt}

    def entry_paths(self):
        return [str(self.root/'SKILL.md')] if self.styles else []
