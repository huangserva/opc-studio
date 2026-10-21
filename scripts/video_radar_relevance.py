"""配置驱动的AI内容规则；AI创作活动相关，泛AI标签弱，明确否认AI制作丢弃。"""
import re

# 后缀逐字符判断；跳过字符不能通过正则回退成为终止边界。
DEFAULT_NEGATIVE_BOUNDARY = {'mode': 'end_or_non_word', 'extra_word_chars': []}
FORMAT_SKIP_CHARS = frozenset('\u200b\u200c\u200d\u2060\ufeff')


def _skippable(char):
    return char.isspace() or char in FORMAT_SKIP_CHARS


def _boundary_config(config):
    """仅支持模式与逐字面扩展字符；拒绝旧正则片段及未知配置。"""
    supplied = config.get('negative_ai_boundary', {})
    if not isinstance(supplied, dict):
        raise ValueError('negative_ai_boundary 必须是对象')
    if supplied.keys() - DEFAULT_NEGATIVE_BOUNDARY.keys():
        raise ValueError('negative_ai_boundary 含未知配置键')
    boundary = {**DEFAULT_NEGATIVE_BOUNDARY, **supplied}
    if boundary['mode'] not in ('end_or_non_word', 'end_only'):
        raise ValueError('未知的 negative_ai_boundary.mode：%r' % (boundary['mode'],))
    extra = boundary['extra_word_chars']
    if not isinstance(extra, list) or any(
        not isinstance(char, str) or len(char) != 1 or _skippable(char)
        for char in extra
    ):
        raise ValueError('extra_word_chars 必须是非空白单字符组成的列表')
    return boundary['mode'], frozenset(extra)


def _remove_negations(text, negative, production, capture, boundary):
    mode, extra = boundary
    production_pattern = re.compile(r'\s*(?:' + '|'.join(re.escape(p) for p in production) + ')', re.I) if production else None
    real_capture = any(word in text for word in capture)
    negative_pattern = re.compile(negative, re.I)
    parts, cursor, denied = [], 0, 0
    for match in negative_pattern.finditer(text):
        if match.start() < cursor:
            continue
        visible = match.end()
        while visible < len(text) and _skippable(text[visible]):
            visible += 1
        # 后续文字若恰好开启同规则的下一否定片段，当前片段也终止。
        terminal = visible == len(text) or negative_pattern.match(text, visible) is not None or (
            mode == 'end_or_non_word'
            and not text[visible].isalnum() and text[visible] not in extra
        )
        # 顺序保留原来的终止边界、制作词、实拍词三个分支。
        if terminal:
            end = visible
        else:
            made = production_pattern.match(text, match.end()) if production_pattern else None
            if made:
                end = made.end()
            elif real_capture:
                end = match.end()
            else:
                continue
        parts.extend((text[cursor:match.start()], ' '))
        cursor, denied = end, denied + 1
    parts.append(text[cursor:])
    return ''.join(parts), denied


def _positive(text, config):
    for pattern in config.get('ai_creation_activity_patterns', []):
        if re.search(re.escape(pattern), text, re.I):
            return 'relevant', 'AI创作活动标签'
    for term in config['relevant_terms']:
        pattern = re.escape(term)
        if term.isascii():
            pattern = r'(?<![a-zA-Z])' + pattern + r'(?![a-zA-Z])'
        if re.search(pattern, text, re.I):
            return 'relevant', '工具或技术词：' + term
    if re.search(r'(?<![a-zA-Z])ai(?![a-zA-Z])', text, re.I):
        return 'relevant', '独立AI词'
    return None, '未命中AI相关词'


def classify(text, config):
    boundary = _boundary_config(config)
    text = str(text or '')
    # 泛 #AI 不证明正文有AI内容，先移走；AI生成、AI短片等具体标签保留。
    generic = config.get('generic_ai_tag_pattern')
    semantic, generic_count = re.subn(generic, ' ', text, flags=re.I) if generic else (text, 0)
    prefixes = config.get('negative_ai_prefixes', [])
    production = config.get('negative_ai_production_terms', [])
    capture = config.get('negative_ai_real_capture_terms', [])
    if prefixes:
        negative = '(?:' + '|'.join(re.escape(p) for p in prefixes) + r')\s*ai(?![a-zA-Z])'
        # 只消去否定片段，再检查片段之外的AI议题/工具/活动。
        remaining, denied = _remove_negations(semantic, negative, production, capture, boundary)
        if denied:
            positive = _positive(remaining, config)
            if positive[0] is not None:
                return positive
            return None, '否定AI制作且无技术语义'
    positive = _positive(semantic, config)
    if positive[0] is not None:
        return positive
    if generic_count:
        return 'weak', '仅泛AI标签'
    return positive
