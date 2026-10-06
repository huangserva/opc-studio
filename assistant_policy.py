"""Conservative, per-message tool grants. History never grants tool access.

This is a bounded Chinese/English command grammar, not a general intent classifier.
Read-only queries are always available; mutation requests must be explicit.
"""
import re
from studio_profile import user_name

READ_TOOLS = {'search_works', 'read_work', 'get_capabilities', 'get_workspace_context', 'get_generation_status', 'get_backend_inventory', 'get_backend_health', 'web_search', 'web_fetch', 'read', 'get_image_styles', 'read_chat_image'}
READ_TOOLS.update({'search_knowledge', 'read_knowledge', 'search_events', 'read_event', 'propose_event'})
READ_TOOLS.update({'ssh_connection_status', 'ssh_run_readonly'})
READ_TOOLS.update({'remio_status', 'remio_search', 'remio_read', 'memos_status', 'memos_search'})
READ_TOOLS.update({'search_deals','read_deal','propose_deal_update'})
MEMORY_WRITE_TOOLS = {'remio_create_note', 'remio_update_note', 'memos_write'}

def policy_for(message):
    plain=message.strip()
    greeting=re.fullmatch(r'(?:你好|您好|嗨|哈喽|哈啰|早上好|晚上好|下午好|早|在吗|谢谢|谢了|好的|收到|hello|hi|hey|thanks|thank you)[呀啊哦哈\s！!。.?？~～]*',plain,re.I)
    if greeting:
        name=user_name()
        answer=f'{name}，不客气。' if re.match(r'谢谢|谢了|thanks|thank you',plain,re.I) else f'{name}，您好！'
        if re.match(r'好的|收到',plain):answer=f'好的，{name}。'
        return {'allowed_tools':[], 'allow_insert':False, 'reply':answer}
    # Quoted examples, code and embedded content are not executable requests.
    text=re.sub(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`',' ',plain)
    clauses=re.split(r'[，,。；;！？!?\n：:]|但是|然后',text)
    # Read-only business queries do not require a particular wording. Mutation grants
    # remain separate and are checked again by the Python executor.
    allowed=set(READ_TOOLS);insert=False;media_kinds=set();requested_media_model=None
    for clause in clauses:
        c=clause.strip()
        if not c:continue
        if re.search(r'不要|不用|不需要|暂不|先不|别|禁止|未授权|不许|do not|don.t',c,re.I):continue
        if re.search(r'怎么|如何|为什么|建议|方案|如果|假如|是否|能否|上次|之前|曾经|我说|他说|她说|我只是|能做什么|可以做什么|how to|can you explain',c,re.I):
            if re.search(r'建议|看看|查询|查找|搜索|分析|总结|统计',c):allowed.update(READ_TOOLS)
            continue
        command=re.sub(r'^(?:'+re.escape(user_name())+r'[\s，]*)?(?:(?:请你|请|帮我|帮忙|麻烦你|麻烦|给我|替我|能帮我|可以帮我|please)\s*)+','',c,flags=re.I)
        skill_prefix=re.match(r'^(?:用|使用)\s*illustration-generator\s*(?:来)?\s*',command,re.I)
        if skill_prefix:command=re.sub(r'^(?:请|帮我|给我)\s*','',command[skill_prefix.end():])
        model_prefix=re.match(r'^(?:用|使用)\s*(Krea\s*2|Qwen\s*Image\s*2\.1|H3)\s*(?:来)?\s*',command,re.I)
        if model_prefix:
            named=re.sub(r'\s+','',model_prefix[1]).lower()
            requested_media_model={'krea2':'krea2','qwenimage2.1':'qi21','h3':'h3'}[named]
            command=command[model_prefix.end():]
        # Memory writes require this turn's imperative. Mentions, examples and
        # history do not grant writes, and saving a note never grants draft edits.
        memory_target = bool(re.search(r'remio|lumio|mem(?:o|oo)s|记忆|偏好|笔记|note\b',command,re.I))
        remembering = bool(re.match(r'^(?:以后\s*|务必\s*)?(?:记住|记一下|记下来|remember\b)',command,re.I))
        storage_action = bool(re.match(r'^(?:保存|存入|记录|记到|记入|存到|save\b)', command, re.I))
        note_action = bool(re.match(r'^(?:追加|补充|更新|创建|新建|create\b|append\b|update\b)', command, re.I)
                           and re.search(r'笔记|note\b', command, re.I)
                           and not re.search(r'工具|接口|功能|接入|注册|适配|配置|服务|日志', command))
        destination_action = bool(re.search(
            r'(?:保存到|存入|记录到|记到|记入|存到|追加到|补充到|更新到)\s*(?:remio|lumio|mem(?:o|oo)s|笔记|记忆)', command, re.I)
            and re.match(r'^(?:把|将|保存|存入|记录|记到|记入|存到|追加|补充|更新)', command))
        memory_command = remembering or (memory_target and (storage_action or note_action or destination_action))
        if memory_command:
            if re.search(r'mem(?:o|oo)s',command,re.I):
                allowed.add('memos_write')
            elif re.match(r'^(?:追加|补充|更新|append\b|update\b)',command,re.I) or re.search(r'追加到|补充到|更新.*笔记',command):
                allowed.add('remio_update_note')
            else:
                allowed.add('remio_create_note')
            continue
        # Require an imperative at the start; mentions of actions don't grant them.
        if re.match(r'^(?:把|将|修改|改成|改为|改一下|改写|润色|缩短|重写|扩写|优化|替换|删除|新增|增加|添加|补充|保存|edit\b|rewrite\b)',command,re.I):
            if re.search(r'修改|改成|改为|改一下|改写|润色|缩短|重写|扩写|优化|替换|保存|edit\b|rewrite\b',command,re.I) or re.search(r'(删除|新增|增加|添加|补充).*(文字|段|标题|正文|文案)',command):
                allowed.add('update_text')
        if re.match(r'^(?:把|将|归档|整理|新建|创建|建立|加入|移入|移出|放进|放入|放到|重命名|改名|archive\b|organize\b)',command,re.I):
            if re.search(r'合集|归档|archive\b|organize\b',command,re.I):allowed.add('organize_collection')
        if re.match(r'^(?:生成|制作|做|画|创建|设计|create\b|generate\b)',command,re.I):
            if re.search(r'文字封面|文字卡片|信息卡|text card',command,re.I):
                allowed.add('create_card')
            elif re.search(r'图|封面|照片|插画|image|cover',command,re.I):
                allowed.add('generate_media');media_kinds.add('image')
            if re.search(r'视频|video',command,re.I):
                allowed.add('generate_media');media_kinds.add('video')
        if re.match(r'^(?:给|为|配图)',command) and re.search(r'配图',command):
            allowed.add('generate_media');media_kinds.add('image')
        if re.match(r'^(?:把|将|插入|插到|加到|放进|放入)',command) and re.search(r'插入|插到|加到|放进|放入',command):insert=True
        if re.match(r'^(?:查|搜|找|读|看|列出|分析|总结|统计|search\b|read\b|find\b|list\b)',command,re.I):allowed.update(READ_TOOLS)
        if re.search(r'(这篇|当前作品|最近|有哪些|有多少).*(文章|作品|推文|讲了|内容|修改|合集)',command):allowed.update(READ_TOOLS)
    # An explicit MemOS destination for this request overrides the default note
    # destination even when "remember" and "save to MemOS" are separate clauses.
    if 'memos_write' in allowed:
        allowed.difference_update({'remio_create_note', 'remio_update_note'})
    if allowed & {'update_text','organize_collection','create_card','generate_media'}:allowed.update(READ_TOOLS)
    if re.search(r'只预览|先预览|不要插入|不插入|别插入',plain):insert=False
    return {'allowed_tools':sorted(allowed),'allow_insert':insert and 'create_card' in allowed,'reply':None,'media_kinds':sorted(media_kinds),'requested_media_model':requested_media_model}
