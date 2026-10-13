"""Conservative, per-message tool grants. History never grants tool access.

This is a bounded Chinese/English command grammar, not a general intent classifier.
Read-only queries are always available; mutation requests must be explicit.
"""
import html
import re
from collections import namedtuple
from html.parser import HTMLParser
from urllib.parse import urlsplit

READ_TOOLS = {'search_works', 'read_work', 'get_capabilities', 'get_workspace_context', 'get_generation_status', 'get_backend_inventory', 'get_backend_health', 'web_search', 'web_fetch', 'read', 'get_image_styles', 'read_chat_image'}
READ_TOOLS.update({'search_knowledge', 'read_knowledge', 'search_events', 'read_event', 'propose_event'})
READ_TOOLS.update({'ssh_connection_status', 'ssh_run_readonly'})
READ_TOOLS.update({'remio_status', 'remio_search', 'remio_read', 'memos_status', 'memos_search'})
READ_TOOLS.update({'search_deals','read_deal','propose_deal_update'})
MEMORY_WRITE_TOOLS = {'remio_create_note', 'remio_update_note', 'memos_write'}

def delete_kinds(command):
    """Which block kinds a delete clause may touch. 去图 stays image-only; 删段 stays text-only."""
    image=bool(re.search(r'配图|图片|截图|照片|封面|插图|去图|删图|图',command))
    text=bool(re.search(r'文字|文案|正文|标题|段落|一段|一句|段',command))
    if re.search(r'块',command) or (image and text) or not (image or text):
        return {'image','text'}
    return {'image'} if image else {'text'}

_PASTE_VERB = (r'(?:(?:请帮我|帮我|麻烦|请)(?:你|您)?)?(?:把|将)?(?:下面|以下|这段|这条|用户)?(?:的)?'
               r'(?:贴文|原文|正文|内容|文字|段落)?(?:原样|直接)?'
               r'(?:贴上|贴入|贴进|贴到|粘贴|插入|复制)(?:到|进)?'
               r'(?:当前作品|作品|末尾|文末|第[一二三四五六七八九十0-9]+条(?:推文)?(?:后面|后))?'
               r'(?:吧|呢|啊)?')
_COMPOSE_VERB = r'(?:请|再|并|然后)?(?:补写|补|加|写)(?:一句|一段|一条|几句)(?:过渡|文字|文案|说明|正文)?'

# `_paste_turn` 用的首子句判据，与 `_PASTE_VERB` 同形，只把前缀词放宽成可重复，
# 好让「把下面这段贴上」「把这段下面的内容贴上」也算粘贴指令——
# `_PASTE_VERB` 的前缀是**单选**，`把下面这段贴上` 在它那里并不匹配（实测）。
# **只在 `_paste_turn` 里放宽**：`_paste_instruction_only` / `paste_originals`
# 用的 `_PASTE_VERB` 一个字符都不动，粘贴原文的判定口径因此完全不变
# （tests/test_fixes_b2.py 与 b3 把那套语法钉得很死）。
_PASTE_TURN_LEAD = (r'(?:(?:请帮我|帮我|麻烦|请)(?:你|您)?)?(?:把|将)?'
                   r'(?:(?:下面|以下|这段|这条|用户|的))*'
                   r'(?:贴文|原文|正文|内容|文字|段落)?(?:原样|直接)?'
                   r'(?:贴上|贴入|贴进|贴到|粘贴|插入|复制)(?:到|进)?'
                   r'(?:当前作品|作品|末尾|文末|第[一二三四五六七八九十0-9]+条(?:推文)?(?:后面|后))?'
                   r'(?:吧|呢|啊)?')


def _paste_turn(message):
    """这一轮是不是「把某段内容贴进去」的粘贴轮。

    口径：**首个子句是粘贴/补写指令**。只取第一行（换行后是贴文本体），
    挖掉围栏/引号字面量后按标点切子句，首子句必须是粘贴/补写指令。

    **h5 与 h3 的区别**：h3 用 `_paste_turn` 跳过整个无指代收权，这一轮所有改稿
    操作全部豁免——粘贴命令搭车的 delete_block/move_block/set_text 也不被挡。
    h5 只用 `_paste_turn` 判断「这是不是粘贴轮」，具体放行什么操作由调用方的
    收窄逻辑决定（只保留 insert_text，其余一律收回），从结构上杜绝搭车越权。
    """
    head = str(message or '').split('\n', 1)[0]
    head = re.sub(r'```[\s\S]*?```|「[^」]*」|“[^”]*”|\"[^\"\n]*\"|`[^`]*`', '', head)
    head = re.sub(r'[ \t\u3000]+', '', head)
    clauses = [part for part in re.split(r'[，,。.!！；;：:]', head) if part]
    if not clauses:
        return False
    lead = clauses[0]
    return bool(re.fullmatch(_PASTE_TURN_LEAD, lead) or re.fullmatch(_COMPOSE_VERB, lead))


def _paste_instruction_only(value):
    """Bounded instruction area; never discard arbitrary inline prose."""
    # Only a quoted compose instruction is part of the directive. Quoted paste
    # prose on this line cannot be thrown away in favour of a later paragraph.
    value = re.sub(r'((?:补写|补|加|写)(?:一句|一段|一条|几句)(?:过渡|文字|文案|说明|正文)?)'
                   r'(?:「[^」]*」|“[^”]*”)', r'\1', value)
    clauses = [part.strip() for part in re.split(r'[，,。.!！；;：:]', value) if part.strip()]
    return all(re.fullmatch(_PASTE_VERB, clause) or re.fullmatch(_COMPOSE_VERB, clause) for clause in clauses)


_COMPOSE_QUOTE = re.compile(
    r'(?:补写|补|加|写)(?:一句|一段|一条|几句)(?:过渡|文字|文案|说明|正文)?'
    r'(?:「([^」]*)」|“([^”]*)”)'
)
# 协议前不能再粘着字母、数字、下划线、点或连字符。汉字紧邻仍算边界。
# 捕获停在汉字、CJK 标点（\u3000-\u303f）和全角标点，也停在破折号、省略号。
# 句号逗号等 ASCII 标点先留在捕获里，再由 _trim_url_punctuation 只剥尾部。
_ORIGINAL_URL = re.compile(
    r'(?<![A-Za-z0-9_.\-])(?i:https?://|mailto:)'
    r'[^\s<>"「」“”‘’\u4e00-\u9fff。，、；：！？\u3000-\u303f'
    r'\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65\u2014\u2026]+'
)
_URL_TAIL_PUNCT = set(".,!?;:'\"")


def _trim_url_punctuation(url):
    """Strip trailing ASCII punctuation and an unpaired ')' from a captured URL."""
    text = url
    while text:
        if text[-1] in _URL_TAIL_PUNCT:
            text = text[:-1]
            continue
        if text[-1] == ')' and text.count(')') > text.count('('):
            text = text[:-1]
            continue
        break
    return text


def _compose_quote_conflicts(instruction, other):
    """A long or sentence-like compose quote cannot be discarded for another line."""
    other = (other or '').strip()
    if not other or not re.search(r'[\u4e00-\u9fffA-Za-z0-9]', other):
        return False
    for match in _COMPOSE_QUOTE.finditer(instruction or ''):
        quoted = (match.group(1) if match.group(1) is not None else match.group(2) or '').strip()
        sentence = re.search(r'[。！？!?]', quoted)
        # 无句末标点时超过 12 字就当正文，避免 13–20 字的引号被当成过渡丢掉。
        # 有句末标点时，标点后面还有字，或整段超过 8 字，才当正文；「下周见！」仍是过渡。
        if (len(quoted) > 12
                or (sentence and sentence.end() < len(quoted))
                or (sentence and len(quoted) > 8)):
            return True
    return False


def _paste_body_present(value):
    """Newlines alone are not a passage. Spaces, tabs and U+3000 are."""
    return bool(str(value or '').replace('\n', '').replace('\r', ''))


def paste_originals(message):
    """Only explicit complete pasted content; ambiguous instructions fail closed.

    Accept a labelled passage, a fenced passage, quoted passage, or content after
    an instruction-only first line. Never let the model choose a substring span.
    Instruction matching may ignore whitespace around the instruction. The
    returned passage keeps its own leading and trailing spaces, tabs and
    ideographic spaces.
    """
    raw = str(message or '').lstrip()
    if not raw.strip():
        return ()
    marker = r'(?:贴文原文|指定原文段落|贴文内容|原文)[：:]'
    instruction, separator, remainder = raw.partition('\n')
    # Only an instruction-line label or the first body-line label defines a
    # boundary. A later label belongs to the original and must remain intact.
    labelled = re.search(marker, instruction)
    if labelled:
        head = instruction[:labelled.start()]
        if not _paste_instruction_only(head):
            return ()
        tail = raw[labelled.end():]
        if _compose_quote_conflicts(head, tail):
            return ()
        if not _paste_body_present(tail):
            return ()
        return (raw[labelled.start():], tail)
    if separator:
        # Don't silently discard inline body following an instruction sentence.
        if not _paste_instruction_only(instruction):
            return ()
        if _compose_quote_conflicts(instruction, remainder):
            return ()
        content = remainder
        if not _paste_body_present(content):
            return ()
        # A blank line before 「原文：」 is still the label boundary. Spaces in
        # front of that label are not body indent; any other leading space is.
        rest = content.lstrip('\n')
        indent = re.match(r'[ \t\u3000]*', rest).group(0)
        body_label = re.match(marker, rest[len(indent):])
        if body_label:
            body = rest[len(indent) + body_label.end():]
            if not _paste_body_present(body):
                return ()
            return (rest[len(indent):], body)
        fence_source = content.strip('\n')
        fence = re.fullmatch(r'[ \t\u3000]*```[^\n]*\n([\s\S]*?)\n?```[ \t\u3000]*', fence_source)
        if fence:
            return (fence.group(1),)
        return (content,)
    # Quotes about a requested transition aren't the pasted original. Require
    # an explicit paste boundary, rather than accepting any quote in the turn.
    flat = raw.strip()
    quoted = re.fullmatch(r'([^「“]*)(?:「([^」]+)」|“([^”]+)”)', flat)
    if quoted and _paste_instruction_only(quoted.group(1)) and quoted.group(1).strip():
        return (quoted.group(2) or quoted.group(3),)
    quoted = re.fullmatch(r'(?:把|将)(?:「([^」]+)」|“([^”]+)”)(?:贴上|贴入|贴进|插入)', flat)
    if quoted:
        return (quoted.group(1) or quoted.group(2),)
    return ()


def paste_links_allowed(markup, original):
    """Allow an anchor only when the original contains that URL.

    Scheme and host ignore case. Path, query and fragment stay exact.
    An empty path compares as '/'. Default ports 443 and 80 compare as omitted.
    A URL in the original stops at Han characters or punctuation.
    """
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hrefs = []

        def handle_starttag(self, tag, attrs):
            if tag != 'a':
                return
            attributes = dict(attrs)
            if 'href' not in attributes:
                return
            # 空 href 也要记下来，后面按不允许拒绝。
            self.hrefs.append(attributes['href'])

    parser = Links()
    parser.feed(markup)
    allowed = set()
    for match in _ORIGINAL_URL.finditer(original or ''):
        # 原文只解码一次。href 已由 HTMLParser 解码，_link_identity 不再解码。
        captured = _trim_url_punctuation(html.unescape(match.group(0)))
        identity = _link_identity(captured)
        if identity is not None:
            allowed.add(identity)
    for href in parser.hrefs:
        identity = _link_identity(href)
        if identity is None or identity not in allowed:
            return False
    return True


def _link_identity(url):
    """Scheme and host ignore case; path, query and fragment stay exact.

    Any parse failure returns None. The href is then refused, and an
    unparseable URL in the original is left out of the allowlist.
    Entities are decoded once before this helper. A second unescape would
    turn ``&amp;amp;`` into ``&`` and treat it as the same query.
    """
    try:
        parts = urlsplit((url or '').strip())
        scheme = parts.scheme.lower()
        if scheme == 'mailto':
            local, separator, host = parts.path.rpartition('@')
            # 本地部分保持区分大小写：这是有意的严格，不按邮箱服务商习惯折叠。
            if separator:
                return (scheme, local, host.lower(), parts.query, parts.fragment)
            return (scheme, parts.path, '', parts.query, parts.fragment)
        if scheme in ('http', 'https'):
            port = parts.port
            if (scheme, port) in (('https', 443), ('http', 80)):
                port = None
            path = parts.path or '/'
            return (scheme, (parts.hostname or '').lower(), port, path, parts.query, parts.fragment)
    except Exception:
        return None
    return None


_CommandSegment=namedtuple('_CommandSegment','text question boundary')


def _segments(text):
    """Split into command segments and keep the raw delimiter span before each one.

    Question terminators stay on their own segment; sentence dots never split
    dotted tokens or URLs. `boundary` is the delimiter text that closed the
    previous segment, so a caller can tell whether a following question may
    attach backwards: a comma, 然后/但是, newline or colon may, a sentence
    terminator (。；;！？!? or an ASCII sentence dot) may not.
    """
    urls=[m.span() for m in re.finditer(r'https?://[^\s，。；;！？,]+',text,re.I)]
    pattern=r'[，,。；;！？!?\n：:]|但是|然后|\.'
    # First preserve URL/dotted-token protection, then join adjacent terminators.
    delimiters=[]
    for match in re.finditer(pattern,text):
        if any(a<=match.start()<b for a,b in urls):
            continue
        if match.group()=='.' and 0<match.start()<len(text)-1:
            pair=text[match.start()-1]+text[match.start()+1]
            if re.fullmatch(r'[A-Za-z0-9_]{2}',pair):
                continue
        delimiters.append(match)
    segments=[]
    start=0
    index=0
    terminators='！？!?。.；;'
    boundary=''
    while index<len(delimiters):
        match=delimiters[index]
        end=match.end()
        question=match.group() in ('？','?')
        # A question attached through empty newline/colon boundaries belongs
        # to this clause. Without a question, retain the existing boundaries.
        attached=terminators+'\n：:'
        if match.group() in attached:
            last=index
            while last+1<len(delimiters):
                following=delimiters[last+1]
                if following.group() not in attached or text[delimiters[last].end():following.start()].strip():
                    break
                last+=1
            if any(m.group() in ('？','?') for m in delimiters[index:last+1]):
                question=True
                end=delimiters[last].end()
                index=last
        if match.group() in terminators:
            while index+1<len(delimiters):
                next_index=index+1
                while next_index<len(delimiters) and delimiters[next_index].group().isspace():
                    next_index+=1
                if next_index==len(delimiters):break
                following=delimiters[next_index]
                if following.group() not in terminators or text[end:following.start()].strip():
                    break
                question=question or following.group() in ('？','?')
                end=following.end()
                index=next_index
        segments.append(_CommandSegment(text[start:match.start()],question,boundary))
        boundary=text[match.start():end]
        start=end
        index+=1
    segments.append(_CommandSegment(text[start:],False,boundary))
    return segments


def _command_clauses(text):
    """Keep question terminators; sentence dots never split dotted tokens or URLs."""
    for segment in _segments(text):
        yield segment.text,segment.question


# 24i-B: an associated question revokes only the command group it questions.
# A sentence terminator closes the group before it, so a later isolated
# 「可以吗？」 never reaches back across 。；;！？!? or an ASCII sentence dot
# (coordinator decision). Dotted tokens and URLs are never delimiters, so their
# dots are not group breaks either.
_GROUP_BREAK=set('。；;！？!?.')
_TAIL_PUNCTUATION='？?！!。. \t\u3000'
# Closed whole-segment list. Text that merely contains 可以 is never a tail.
_CONFIRMATION_TAIL=re.compile(
    r'(?:吗|么|可以吗|可以么|可以不|可以|行吗|行不行|行不|好不好|好吗|好么|是不是|对不对|对吧|是吧|'
    r'没问题吧|确认|确认吗|ok|okay|please confirm|pls confirm|confirm)',re.I)


def _is_confirmation_tail(value):
    """Whole-segment confirmation tail: 吗/吗？、可以吗、好吗、行不行、OK?、please confirm?."""
    core=(value or '').strip().rstrip(_TAIL_PUNCTUATION)
    return bool(core) and bool(_CONFIRMATION_TAIL.fullmatch(core))


def _attach_target(segments,index):
    """Nearest preceding non-empty segment, unless a sentence terminator intervenes."""
    for j in range(index-1,-1,-1):
        if any(ch in _GROUP_BREAK for ch in segments[j+1].boundary):
            return None
        if segments[j].text.strip():
            return j
    return None


def _questioned_segments(segments):
    """Indices whose command group is being asked about, so it grants nothing."""
    questioned=set()
    for index,segment in enumerate(segments):
        if segment.question:
            questioned.add(index)
        # A URL captured with an empty query ends in a bare '?': ambiguous, ask first.
        # A non-empty query (?x=1) is data and stays untouched.
        if re.search(r'\?\s*$',segment.text):
            questioned.add(index)
            continue
        empty=not segment.text.strip()
        if empty and not segment.question:
            continue
        if not (empty or _is_confirmation_tail(segment.text)):
            continue
        target=_attach_target(segments,index)
        if target is not None:
            questioned.add(target)
    return questioned


READ_ONLY_WITHOUT_WORK = READ_TOOLS | MEMORY_WRITE_TOOLS | {
    'search_knowledge', 'read_knowledge', 'search_events', 'read_event',
    'get_workspace_context', 'propose_event',
}
# 没有打开作品时，下面这些只是「没有指代对象的指代」：用户说的「当前作品」在页面上
# 并不存在，给了改稿工具就只能让助手 search 完自己挑一篇改。
# 指明目标的明确操作（删除第二段、插入一段文字）不受影响，仍按逐子句规则授权，
# 并且执行层照样要求本轮先 read_work（复核 M-4，协调者选定的 B 口径）。
_UNANCHORED_TARGET = re.compile(
    r'当前作品|当前文章|当前这篇|当前稿|这篇(?:文章|稿|正文)?|这篇文章|本文|本篇|'
    r'(?<!小)正文|(?<!小)标题|当前标题|当前正文', re.I)

# 没打开作品时的改稿工具集合（见 M-4 的 B 口径）。
EDIT_TOOLS = frozenset({'update_text', 'edit_work', 'reflow_markdown'})
EDIT_GRANTS = frozenset({'set_text', 'insert_text', 'delete_block', 'move_block',
                         'insert_image', 'reflow_markdown'})
# 打开着作品时每轮交给模型的完整名单。改稿、生成媒体、写笔记、归档合集都在内：
# 「不给 AI 限制，给 AI 帮助」，改不改、怎么改交给模型自己判断。
OPEN_WORK_TOOLS = frozenset(READ_TOOLS | MEMORY_WRITE_TOOLS | {
    'update_text', 'edit_work', 'reflow_markdown', 'organize_collection',
    'create_card', 'generate_media',
    'search_knowledge', 'read_knowledge', 'search_events', 'read_event',
    'get_workspace_context', 'propose_event',
})
OPEN_WORK_GRANTS = EDIT_GRANTS


def _open_work_policy(pasted_text=False):
    """当前打开着一篇作品时的完整授权：不收回任何工具，由模型自己判断。"""
    policy = {'allowed_tools': sorted(OPEN_WORK_TOOLS), 'allow_insert': True, 'reply': None,
              'media_kinds': ['image', 'video'], 'requested_media_model': None,
              'edit_grants': sorted(OPEN_WORK_GRANTS)}
    if pasted_text:
        policy['text_source'] = 'user_paste'
    return policy


def policy_for(message, work_open=False):
    """work_open：当前页面上确实打开着一篇作品。

    黄总 10-11 17:4x：「根本不能够去给 AI 做什么限制，而是给 AI 做什么帮助。」
    打开着作品就把助手能做的全部工具每轮都交给模型，由它自己理解用户意思决定
    改不改、怎么改，不再用正则判断「是不是命令 / 是不是提问」来收回工具。
    没有打开作品时**只收回无指代指代**（复核 M-4 的 B 口径）：「当前作品/这篇/
    正文/标题」这类说法不知道改哪一篇，整句的改稿工具一律不给；已经指明了目标的
    明确操作（「删除第二段」「把 Rea 项目第二段改写」）沿用逐子句规则照旧授权，
    执行层仍要求本轮先 read_work 那一篇。
    """
    plain=message.strip()
    greeting=re.fullmatch(r'(?:你好|您好|嗨|哈喽|哈啰|早上好|晚上好|下午好|早|在吗|谢谢|谢了|好的|收到|hello|hi|hey|thanks|thank you)[呀啊哦哈\s！!。.?？~～]*',plain,re.I)
    if greeting:
        answer='黄总，不客气。' if re.match(r'谢谢|谢了|thanks|thank you',plain,re.I) else '黄总，您好！'
        if re.match(r'好的|收到',plain):answer='好的，黄总。'
        return {'allowed_tools':[], 'allow_insert':False, 'reply':answer, 'edit_grants':[]}
    # Quoted examples, code and embedded content are not executable requests.
    text=re.sub(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`',' ',plain)
    if work_open:
        # 打开着作品：工具全给，但用户点名的生成模型仍要提取——
        # 取消关键词闸门不等于丢掉参数语义（复核 M-3：「用 Krea2 生成」不能被默认偏好覆盖）。
        policy=_open_work_policy(pasted_text=_paste_body_present(plain))
        named=_grants_for(_segments(text), frozenset(), plain)
        if named.get('requested_media_model'):
            policy['requested_media_model']=named['requested_media_model']
        return policy
    # Pass 1 assigns every question to its command group; pass 2 grants only
    # surviving groups, with the pre-existing per-clause grammar. Nothing is
    # subtracted from the global set afterwards.
    segments=_segments(text)
    revoked=_questioned_segments(segments)
    policy=_grants_for(segments,revoked,plain)
    if revoked:
        # A revoked clause can drop the explicit MemOS destination that used to
        # override the default note tool. Recomputing must never hand out a memory
        # write tool the message did not already grant: keep the safe upper bound.
        unrevoked=_grants_for(segments,frozenset(),plain)
        widened=((set(policy['allowed_tools'])&MEMORY_WRITE_TOOLS)
                 -(set(unrevoked['allowed_tools'])&MEMORY_WRITE_TOOLS))
        if widened:
            policy['allowed_tools']=sorted(set(policy['allowed_tools'])-widened)
    # 没有打开作品：只对「当前作品/这篇/正文」这类没有指代对象的指代收权（复核 M-4 的 B 口径）。
    # 指明目标的明确操作照旧授权；生成、笔记、搜索、合集这些全局工具维持原有口径。
    #
    # h5 收窄粘贴豁免（取代 h3/h4 的整体豁免）：粘贴轮只保留 insert_text 授权，
    # delete_block/move_block/set_text/insert_image 一律收回。从结构上保证粘贴豁免
    # 不可能放行非粘贴操作，不需要枚举动词黑名单。
    # h5 新增目标绑定：work_open=False 时粘贴豁免设置 paste_requires_open_work 标记，
    # 执行层据此拒绝写入——只能走到服务端边界提示或「请先指明/打开要贴入的作品」。
    if _UNANCHORED_TARGET.search(plain) and (EDIT_TOOLS & set(policy['allowed_tools'])):
        if _paste_turn(plain):
            # 粘贴轮：只保留 insert_text（粘贴/补写必需），其余改稿授权一律收回。
            # edit_work 保留当且仅当 insert_text 在授权中（否则没有可写操作）。
            _PASTE_KEEP_GRANTS = frozenset({'insert_text'})
            revoked_grants = EDIT_GRANTS - _PASTE_KEEP_GRANTS
            policy['edit_grants'] = [g for g in policy['edit_grants'] if g not in revoked_grants]
            if not policy['edit_grants']:
                # 没有任何可写授权，收回全部改稿工具
                policy['allowed_tools'] = sorted(set(policy['allowed_tools']) - EDIT_TOOLS)
                policy['allow_insert'] = False
                for key in ('edit_targets', 'composed_insert', 'text_source'):
                    if key in policy:
                        policy.pop(key)
            else:
                # 有 insert_text：保留 edit_work，收回 update_text 和 reflow_markdown
                policy['allowed_tools'] = sorted(
                    (set(policy['allowed_tools']) - EDIT_TOOLS) | {'edit_work'})
                # delete_block 的目标集已不需要
                policy.pop('edit_targets', None)
            # 目标绑定：work_open=False 时粘贴豁免不能落成写入，只能走边界提示
            if not work_open:
                policy['paste_requires_open_work'] = True
        else:
            # 非粘贴轮：完全收回（原 h2 逻辑）
            policy['allowed_tools'] = sorted(set(policy['allowed_tools']) - EDIT_TOOLS)
            policy['edit_grants'] = [g for g in policy['edit_grants'] if g not in EDIT_GRANTS]
            policy['allow_insert'] = False
            for key in ('edit_targets', 'composed_insert', 'text_source'):
                if key in policy:
                    policy.pop(key)
    return policy


def _grants_for(segments,revoked,plain):
    # Read-only business queries do not require a particular wording. Mutation grants
    # remain separate and are checked again by the Python executor.
    allowed=set(READ_TOOLS);insert=False;media_kinds=set();requested_media_model=None;edit_grants=set()
    edit_target_sets={};composed_text=False;pasted_text=False
    for index,segment in enumerate(segments):
        clause,clause_question=segment.text,segment.question
        c=clause.strip()
        if not c:continue
        # 子句终止问号统一阻止所有写授权，不影响相邻命令与只读工具。
        # 24i-B：被关联疑问归属到的整组同样不授予任何写权限。
        if clause_question or index in revoked:continue
        if re.search(r'不要|不用|不需要|暂不|先不|别|禁止|未授权|不许|do not|don.t',c,re.I):continue
        if re.search(r'怎么|如何|为什么|建议|方案|如果|假如|是否|能否|上次|之前|曾经|我说|他说|她说|我只是|能做什么|可以做什么|how to|can you explain',c,re.I):
            if re.search(r'建议|看看|查询|查找|搜索|分析|总结|统计',c):allowed.update(READ_TOOLS)
            continue
        command=re.sub(r'^(?:黄总[\s，]*)?(?:(?:请你|请|帮我|帮忙|麻烦你|麻烦|给我|替我|能帮我|可以帮我|please)\s*)+','',c,flags=re.I)
        # Formatting is a bounded mutation intent, never an inferred object or question.
        if re.search(r'加粗|\b(?:unbold|bold)\b',command,re.I):
            # 疑问/建议/历史只按当前子句判定：同一句里另一子句的明确命令不被牵连。
            uncertain = re.search(r'要不要|要不|吗|么|建议|上次|之前|曾经|刚才说|是否|能否|[？?]',c,re.I)
            target = re.search(r'当前作品|当前文章|这篇|这段|正文|标题|第[一二三四五六七八九十0-9]+段|(?:给|为|把|将)(?:[A-Za-z0-9_-]+|[\u4e00-\u9fff]{2,})项目|\bcurrent\s+(?:work|article|text|paragraph|title)\b',command,re.I)
            imperative = re.match(r'^(?:再|另外|同时|还要|并且|并)?(?:给|为|把|将|按照图片|按图片|取消|加粗|修改|改成|改为|bold\b|unbold\b)',command,re.I)
            # 整条子句是疑问/建议/回顾时不授权任何写意图。只是对象不明或祈使不成立时，
            # 仅放弃格式授权，同一子句的插入/删除/移动/合集/记忆意图继续按原口径判定。
            if uncertain:
                continue
            if not clause_question and not re.search(r'某个|某篇|一个项目|哪个|那个',command) and target and imperative:
                allowed.add('update_text')
                edit_grants.add('set_text')
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
        # Structural draft edits (edit_work). Grants stay per-operation: “删除第二段” never
        # allows an image insert, “把截图插到第一条后” never allows deleting or reordering.
        # A clause ending in 吗/？ or asking about recovery is a question, not a command.
        question=bool(re.search(r'吗\s*$|[？?]$|还能|能不能|可不可以|是不是|恢复|还能不能',c))
        follow=r'(?:再|另外|同时|还要|并且|并)?'
        if not question and re.match(follow+r'(?:把|将|插入|插到|插进|加到|放进|放入|配上|补上|补一张|添加|新增|增加|补充)',command) \
                and re.search(r'插入|插到|插进|加到|放进|放入|配上|补上|补一张|添加|新增|增加|补充',command) \
                and re.search(r'图片|截图|照片|附件|卡片|卡|封面|素材|图',command):
            allowed.add('edit_work');edit_grants.add('insert_image')
        if not question and re.match(follow+r'(?:把|将|在|插入|插到|插进|加|新增|增加|添加|补充|补一段|补一句|补上|贴一段)',command) \
                and re.search(r'插入|插到|插进|加到|加入|新增|增加|添加|补充|加一段|加一句|补一段|补一句|贴一段',command) \
                and re.search(r'文字|文案|正文|标题|段落|一段|一句|段',command):
            allowed.add('edit_work');edit_grants.add('insert_text');composed_text=True
        # 贴文 pastes this message. 贴图 / 丢图 are image inserts and must not take the text grant.
        if not question and not re.search(r'贴图|丢图|一张图|这张图',command) \
                and re.search(r'(?:^贴文$|^帖文$|贴上|贴进|贴入|把[^，。]{0,20}贴文|将[^，。]{0,20}贴文|把[^，。]{0,12}贴上)',command) \
                and re.search(r'贴文|帖文|文字|文案|正文|段落|推文|这段|这篇|一句',command):
            allowed.add('edit_work');edit_grants.add('insert_text');pasted_text=True
        if not question and re.search(r'丢图|丢(?:一张|这张|个)?图|把图丢到|把图丢进|贴图|贴(?:一张|这张)图',command):
            allowed.add('edit_work');edit_grants.add('insert_image')
        delete_command=not question and not re.search(r'什么|啥意思|是啥',command) and (
            re.fullmatch(r'(?:去图|删图|去掉图|去掉图片|去掉配图)',command) or (
                re.match(follow+r'(?:把|将|删除|删掉|删去|删|移除|去掉|去图|删图)',command)
                and re.search(r'删除|删掉|删去|移除|去掉|删了|去图|删图',command)
                and re.search(r'段|块|图|封面|照片|配图|标题|正文|文字|第\s*[0-9一二三四五六七八九十两]+\s*[条张个]',command)))
        if delete_command:
            allowed.add('edit_work');edit_grants.add('delete_block')
            edit_target_sets.setdefault('delete_block',set()).update(delete_kinds(command))
        # “移回 / 挪回 / 放回 / 调回 / 换回” are explicit moves, not new inserts. “放到” is
        # deliberately left out: it can mean insert or move, so it stays ungranted.
        # 合集/归档 wording is a collection request, never a block move.
        if not question and not re.search(r'合集|归档|archive',command,re.I) \
                and re.match(follow+r'(?:把|将|移到|挪到|移动|移至|移回|调整|重排|换到|调到|挪回|放回|调回|换回)',command) \
                and re.search(r'移到|挪到|移动到|移至|移回|挪回|放回|调回|换回|换到|调到|移动|重排|调整.{0,6}顺序',command):
            allowed.add('edit_work');edit_grants.add('move_block')
        if re.match(r'^(?:查|搜|找|读|看|列出|分析|总结|统计|search\b|read\b|find\b|list\b)',command,re.I):allowed.update(READ_TOOLS)
        if re.search(r'(这篇|当前作品|最近|有哪些|有多少).*(文章|作品|推文|讲了|内容|修改|合集)',command):allowed.update(READ_TOOLS)
    # An explicit MemOS destination for this request overrides the default note
    # destination even when "remember" and "save to MemOS" are separate clauses.
    if 'memos_write' in allowed:
        allowed.difference_update({'remio_create_note', 'remio_update_note'})
    # Text-block rewrites keep using update_text; edit_work may carry the same change so a
    # turn mixing prose and structural edits saves one version instead of two.
    if 'update_text' in allowed:edit_grants.add('set_text')
    if allowed & {'update_text','organize_collection','create_card','generate_media','edit_work'}:allowed.update(READ_TOOLS)
    if re.search(r'只预览|先预览|不要插入|不插入|别插入',plain):
        # “只预览” covers structural edits too: nothing may write this turn except a plain
        # text rewrite, which keeps its own existing grant.
        insert=False
        edit_grants.difference_update({'insert_image','insert_text','move_block','delete_block'})
        edit_target_sets.pop('delete_block',None)
        pasted_text=False
    if not edit_grants:allowed.discard('edit_work')   # nothing writable left this turn
    policy={'allowed_tools':sorted(allowed),'allow_insert':insert and 'create_card' in allowed,'reply':None,'media_kinds':sorted(media_kinds),'requested_media_model':requested_media_model,'edit_grants':sorted(edit_grants)}
    if 'delete_block' in edit_grants and 'delete_block' in edit_target_sets:
        policy['edit_targets']={'delete_block':sorted(edit_target_sets['delete_block'])}
    # 贴文和「补一段/加一句」可以同时出现。贴文那一次仍必须是用户原文的子串，
    # 改写另算一次插入，所以这里不再因为 composed_text 就把 text_source 拿掉。
    if 'insert_text' in edit_grants and pasted_text:
        policy['text_source']='user_paste'
    if 'insert_text' in edit_grants and composed_text:
        policy['composed_insert']=True
    return policy
