"""HTTP transport for the 4090 media API.

Media jobs, reference uploads, progress and results travel over one HTTPS entry
point (relay.yunzhong2020.com/media/v1 -> FRP STCP visitor -> 127.0.0.1:8200).
This module deliberately has **no SSH fallback**: a transport failure is an
unknown state, never a licence to submit the same work twice over another path.

Configuration (server side only; never sent to the browser):
  MEDIA_API_BASE       e.g. https://relay.yunzhong2020.com/media/v1
  MEDIA_API_KEY_FILE   600 file holding the bearer token
  MEDIA_API_CONFIG     optional json {'base': ..., 'key_file': ...}
TLS verification is always on; plaintext is only accepted for loopback tests.
"""
import hashlib
import http.client
import json
import logging
import os
import re
import socket
import ssl
import stat
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The workbench keeps its short model ids; the media API exposes explicit names.
# krea2 maps to the API's own id and is served by the 4090 krea2 backend (8188).
MODEL_IDS = {'qi21': 'qwen-image-2.1', 'krea2': 'krea2', 'h3': 'minimax-h3'}
API_MODEL_IDS = {value: key for key, value in MODEL_IDS.items()}


_LOG = logging.getLogger(__name__)

# The visible line the workbench shows for a media failure is only ever its own fixed
# Chinese copy (the callers build that). An upstream structured `error`, if any, is at
# most a short attachment that survived every internal-artefact check below. Fail closed:
# when in doubt, show nothing rather than a leaked proxy page, path, stack or server word.
_MAX_MESSAGE_CHARS = 200

# Substrings that prove a string is an internal artefact whatever its position: markup, or a
# URL of any scheme.
_UNSAFE_MARKERS = ('<', '>', '://', 'www.')
# Proxy / web-server / transport vocabulary. Its presence means the string is a server-side
# artefact (an error page, a relay line), not a message written for a user.
_UNTRUSTED_MARKERS = ('nginx', 'apache', 'cloudflare', 'caddy', 'traefik', 'envoy', 'haproxy',
                      'gunicorn', 'uvicorn', 'hypercorn', 'fatal error',
                      'internal server error', 'bad gateway',
                      'service unavailable', 'gateway timeout', 'upstream connect error',
                      'connection reset by peer', 'no healthy upstream', 'no route to host',
                      'broken pipe')
# review-media-error-text-h M1: Python's `\b` and `\w` are Unicode-aware, and a Chinese
# ideograph *is* a word character — so in `执行失败raise HiddenFailure("payload")` there is no
# boundary between 败 and r, and every `\b`-anchored rule below stayed silent. All code / stack /
# exception / proxy shapes are therefore anchored with explicit ASCII boundaries and spelled
# with ASCII-only classes: a Chinese neighbour is always a boundary, an ASCII neighbour never is.
_AW = 'A-Za-z0-9_$'                # an ASCII word character (`$` because identifiers use it)
_LB = r'(?<![' + _AW + '])'        # ASCII left boundary — the replacement for the Unicode `\b`
_RB = r'(?![' + _AW + '])'         # ASCII right boundary — the replacement for the Unicode `\b`
_GAP = r'[A-Za-z0-9 _$.\-]'       # bounded ASCII gap inside a phrase (Chinese cannot bridge)
# IP address, optionally with a port: 10.0.0.5, 127.0.0.1:8200. The lookbehind keeps a dotted
# *version* readable (模型要求客户端版本 v1.2.3.4 is not an IP: review-media-error-text-e L1).
# review-media-error-text-f M2: `\w` also matches Chinese, so 连接10.0.0.9:8200失败 slipped past
# the old `(?<![\w.])`; the boundary is spelled in ASCII so a Chinese prefix cannot hide an IP.
_IP_PORT = re.compile(r'(?<![A-Za-z0-9.])\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?')

# Stack / traceback syntax. Matched by *shape*, never by a bare substring: a frame body is
# required after `at`, so the ordinary English "at least" / "at most" stays displayable while a
# frame glued to Chinese (`任务at main.go:11`) is refused (review-media-error-text-h M1).
_AT_BOUNDARY = _LB
_STACK_PATTERNS = (
    re.compile(_LB + r'traceback' + _RB, re.I),                             # traceback header
    re.compile(_LB + r'file\s+"[^"]*"\s*,\s*line\s+\d+', re.I),           # File "x.py", line 42
    re.compile(_LB + r"file\s+'[^']*'\s*,\s*line\s+\d+", re.I),
    re.compile(_LB + r'caused by\s*:', re.I),                               # Caused by: ...
    # java: at com.example.PrivateEngine.run(PrivateEngine.java:42)
    re.compile(_AT_BOUNDARY + r'at\s+[A-Za-z0-9_$.<>]+\.[A-Za-z0-9_$<>]+\s*\(', re.I),
    # js: at loadModel (engine.js:1:2)
    re.compile(_AT_BOUNDARY + r'at\s+[A-Za-z0-9_$.<>]+\s*\([^)]*?\d+\s*:\s*\d+', re.I),
    # native/go/python: at main.go:11, at /opt/media/main.py:42
    re.compile(_AT_BOUNDARY + r'at\s+[A-Za-z0-9_$.+-]+\.[A-Za-z0-9_$]+\s*:\s*\d+', re.I),
    # review-media-error-text-j M1: a *bare* `at ` + dotted name, with no call parentheses and no
    # line number — `调用位置at worker.run请管理员检查`. One dot is enough; a version stays
    # readable (`at v2.3.4`) because every dotted segment after the first must contain a letter.
    re.compile(_AT_BOUNDARY + r'at\s+[A-Za-z_$][A-Za-z0-9_$]*'
               r'(?:\.[A-Za-z0-9_$]*[A-Za-z_$][A-Za-z0-9_$]*)'),
    # package-qualified exception: com.example.PrivateEngineException: ...
    re.compile(_LB + r'(?:[a-z_][a-z0-9_]*\.){2,}[A-Za-z_][A-Za-z0-9_$]*\s*[:(]'),
)

# review-media-error-text-j M1: the crash / process-exit vocabulary, taken from the standard
# enumeration in one table instead of another per-crash patch. The i round added five headline
# rules one at a time and the review still found `Aborted`, `Bus error`, `Floating point
# exception`, `signal 11`, `died with signal 9`, `Killed 7231`, `status 139` and a single-dot
# `at worker.run` passing through. So: the POSIX signal set, the C-runtime fatal headlines, the
# shell exit-code range for signal deaths (128-159), and the errno / thread-dump headlines. Every
# entry is ASCII-anchored like the rest of the shape layer, and the width-normalised form is
# checked too, so a fullwidth ＳＩＧＳＥＧＶ or ｓｉｇｎａｌ １１ cannot hide.
# `Traceback`, `panic:` and `fatal error` are already refused above and in _UNTRUSTED_MARKERS.
_POSIX_SIGNALS = (r'HUP|INT|QUIT|ILL|TRAP|ABRT|BUS|FPE|KILL|USR1|SEGV|USR2|PIPE|ALRM|TERM'
                  r'|STKFLT|CHLD|CONT|STOP|TSTP|TTIN|TTOU|URG|XCPU|XFSZ|VTALRM|PROF|WINCH'
                  r'|IO|PWR|SYS')
# review-media-error-text-l M1: a shape name is spelled several equivalent ways by different
# runtimes and by copy-paste — `unhandled exception`, `unhandled-exception`, `unhandled_Exception`,
# `unhandled exceptions`, `OOM-Killer`, `OOM-Killers`. Listing the spellings one by one kept
# losing the next one, so the phrase builders below state the *word* once and let the regex carry
# the variation: blank / hyphen / underscore between the words, and an optional plural -s/-es on
# the head noun.
_FLEX_SEP = r'[\s_\-]?'


def _flex(phrase, plural=''):
    """Regex for a shape name: separators interchangeable, optional plural on the head noun."""
    body = _FLEX_SEP.join(re.escape(word) for word in phrase.split())
    return body + (r'(?:s|es)?' if plural else '')


_RUNTIME_FATAL_SHAPES = (
    # SIGSEGV / SIGABRT / SIGUSR1 … — matched case-sensitively, so the English word `signal` in
    # ordinary copy is not mistaken for a signal name
    ('signal-name', _LB + r'SIG(?:' + _POSIX_SIGNALS + r')' + _RB, 0),
    ('signal-name-open', _LB + r'SIG[A-Z]{2,7}\d?' + _RB, 0),
    # signal 11 / signal=9 / died with signal 9 / process died from signal
    ('signal-number', _LB + r'signal\s*=?\s*\d{1,3}' + _RB, re.I),
    ('died-with-signal', _LB + r'died' + _FLEX_SEP + r'(?:with|from)' + _FLEX_SEP + r'signal'
     + _RB, re.I),
    # status 139 / exit code 137 / exited 143 — the 128+N range a signal death reports
    ('exit-status', _LB + r'(?:exit(?:ed)?\s*(?:with\s+)?(?:status|code)?\s*=?\s*'
                          r'|status\s*=?\s*|return[- ]?code\s*=?\s*)1(?:2[89]|[34]\d|5\d)' + _RB,
     re.I),
    # Killed 7231 / Killed -9 / Killed +9 / Killed −9 / killed by signal 9 — the sign is ±, per
    # contract; the Unicode minus itself is folded to ASCII in _width_normalised
    ('killed', _LB + r'killed[\s_\-]*[+\-\u2013\u2014]?[\s_\-]*\d+' + _RB, re.I),
    ('killed-by-signal', _LB + _flex('killed by signal') + _RB, re.I),
    # review-media-error-text-k M1: the Windows runtime headline, the Windows access fault and
    # the Linux OOM killer. `Killed` on its own stays readable (`任务被 killed 标记为已取消…`) —
    # only a signed number, or the OOM-killer compound, is a crash shape.
    ('unhandled-exception', _LB + r'unhandled' + _FLEX_SEP
     + r'(?:exception|error)' + r'(?:s|es)?' + _RB, re.I),
    ('access-violation', _LB + _flex('access violation', 's') + _RB, re.I),
    ('oom-killed', _LB + r'oom' + _FLEX_SEP + r'kill(?:ed|er|ing)?' + r's?' + _RB, re.I),
    # the C-runtime fatal headlines a shell or supervisor prints verbatim
    ('aborted', _LB + r'aborted' + _RB, re.I),
    ('bus-error', _LB + _flex('bus error', 's') + _RB, re.I),
    ('floating-point', _LB + _flex('floating point exception', 's') + _RB, re.I),
    ('segmentation', _LB + _flex('segmentation fault', 's') + _RB + r'|' + _LB
     + r'segfault(?:\s+at)?', re.I),
    ('core-dumped', _LB + _flex('core dumped') + _RB, re.I),
    ('kernel-panic', _LB + _flex('kernel panic', 's') + _RB, re.I),
    ('errno', _LB + r'errno\s*=?\s*\d+' + _RB, re.I),
    ('thread-dump', _LB + _flex('exception in thread', 's') + _RB, re.I),
)
_RUNTIME_FATAL_PATTERNS = tuple(re.compile(source, flags) for _, source, flags in
                                _RUNTIME_FATAL_SHAPES)

# review-media-error-text-d M1: per-language frame rules kept losing new runtimes, so the
# second gate asks only "does this look like code?". Any hit means the string is an upstream
# artefact: no upstream text is attached, only the workbench's own fixed Chinese copy is shown.
# Deliberately shape-based, not an allow-list of languages, so a Go/Node/Rust/Kotlin stack is
# refused without adding a rule per runtime.
_SOURCE_SUFFIX = (r'cpp|cxx|cc|hpp|hh|py|pyx|pyc|jsx|tsx|js|ts|java|kts|kt|rs|rb|php|cs|swift'
                  r'|mm|m|scala|lua|pl|sh|dart|vue|exs|ex|erl|go|c|h')
_CODE_SHAPE_PATTERNS = (
    # engine.go:42, main.py:11, PrivateEngine.java:42 — a source file with a line number
    re.compile(_LB + r'[A-Za-z0-9_$./+-]*\.(?:' + _SOURCE_SUFFIX + r')\s*:\s*\d+'),
    # engine.go:42 +0x123, 0x7f3c — a hex offset/pointer never appears in user copy
    re.compile(r'\+0x[0-9a-fA-F]+|' + _LB + r'0x[0-9a-fA-F]{4,}'),
    # goroutine 1 [running]: / goroutine 27 [chan receive]:
    re.compile(_LB + r'goroutine\s+\d+\s*\[', re.I),
    re.compile(_LB + r'panic\s*:', re.I),
    # the Go runtime state inside a goroutine header
    re.compile(r'\[(?:running|select|sleep|syscall6?|semacquire|semrelease|IO wait'
               r'|chan receive|chan send|forcegc|finalizer|alarm|GC|_defer_|_G_|waiting|idle)\]'),
    # main.privateGenerate() / com.example.Engine.run( — a dotted qualified call
    re.compile(_LB + r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$<>]*)+\s*\('),
    # com.example.PrivateEngineException: / io.netty.channel.Error: — package + Exception/Error
    re.compile(_LB + r'(?:[a-z_][a-z0-9_]*\.){1,}[A-Za-z_][A-Za-z0-9_$]*'
               r'(?:Exception|Error)\s*[:(]'),
    # review-media-error-text-f M2: a Chinese sentence wrapped around a code statement does not
    # turn the statement into user-facing copy. `系统错误，请联系管理员：raise RuntimeError
    # ("new_private.py")` measured 85.7% CJK and reached all four exits verbatim.
    # raise/throw/rethrow + the exception being raised — a statement, never business copy.
    re.compile(_LB + r'(?:raise|re-?raise|throw|rethrow|rescue|except)'
               r'\s+[A-Za-z_$][A-Za-z0-9_$.]*', re.I),
    # an exception constructor call: RuntimeError("x.py") / ValueError(...) / NullPointerException()
    re.compile(_LB + r'[A-Za-z_$][A-Za-z0-9_$]*(?:Exception|Error|Throwable|Panic|Fault|Assertion)'
               r'\s*\('),
    # review-media-error-text-h M1: the far more common form is a *bare* exception name plus a
    # colon — Java/Go/Python all write `NullPointerException: null`, `ValueError: invalid
    # literal`. The constructor rule needs the `(` and missed both, even behind a Chinese colon.
    re.compile(_LB + r'[A-Za-z_$][A-Za-z0-9_$]*(?:Exception|Error|Throwable|Panic|Fault|Assertion)'
               + _RB + r'\s*[::]'),
    # PHP/JS prose frame: `in new_engine.php on line 73`, `... on line 42`
    re.compile(_LB + r'on\s+line\s+\d+', re.I),
    # Swift/Kotlin unquoted frame: `file Engine.swift, line 42`
    re.compile(_LB + r'file\s+[A-Za-z0-9_$./+-]+\s*,\s*line\s+\d+', re.I),
    # any `name.ext:42` (not only a known source suffix): config.json:42, handler.yaml:11.
    # The extension must be alphabetic, so 1.5:2 and v1.2.3.4 stay readable.
    re.compile(_LB + r'[A-Za-z_][A-Za-z0-9_$./+-]*\.[A-Za-z][A-Za-z0-9_]{0,7}\s*:\s*\d+'),
) + _RUNTIME_FATAL_PATTERNS
# Two or more lines indented by a tab or 4+ spaces is a traceback body, whatever the language.
# Checked on the raw string: whitespace collapse would erase the indentation. A Chinese business
# hint may legitimately be laid out over indented lines (请修改以下选项：…), so only lines that
# are not CJK-dominant count towards a traceback body (review-media-error-text-e L1).
_INDENTED_LINE = re.compile(r'(?:\t|[ \t]{4,})\S')

# A '/' or '\' counts as a filesystem path only in path shape: a Windows drive, a backslash
# separated segment, a leading-slash directory form (/srv/private/config.json), or two or more
# name segments (a/b). Fractions, ratios and dates are exempt — see _RATIO below.
_PATH_PATTERNS = (
    re.compile(r'[A-Za-z]:[\\/]'),                  # C:\x\y, D:/a/b
    re.compile(r'\\[^\s\\]+'),                      # backslash separated segments
    re.compile(r'(?<![A-Za-z0-9_:/\\])/[^\s/]+'),   # /srv/private/config.json, /opt/media
    re.compile(r'(?<![A-Za-z0-9_:/\\])[^\s/\\]+/[^\s/]+'),  # a/b: two or more name segments
)
# 1/2, 3/4, 16/9, 2026-10-06: a number over a number is a fraction/ratio/date, not a path.
_RATIO = re.compile(r'(?<![\d/])\d{1,4}/\d{1,4}(?![\d/])')

# Conditions we can name in our own words. The upstream wording never reaches the user, only
# this fixed Chinese line does, so a rewrite stays safe even inside a stack-carrying body.
_REWRITES = (
    # review-media-error-text-d L1: `VRAM` is a resource name and `out of memory` does not say
    # whose memory ran out, so the old bare triggers diagnosed "requires 16 GB VRAM", "low VRAM
    # mode", "this is not an out of memory error" and a CPU allocator OOM as a graphics-card
    # failure. Only an explicitly GPU/CUDA out-of-memory condition is named in our own words.
    # review-media-error-text-h M1: the boundaries are ASCII, so `运行失败GPU out of memory`
    # (no space before GPU) is recognised just like the spaced English wording.
    (re.compile(r'cuda\s+out of memory|cuda\s+error\s*:\s*out of memory|cuda\s+oom'
                r'|torch\.cuda\.OutOfMemoryError'
                r'|' + _LB + r'(?:gpu|vram)\s+out of memory'
                r'|显存不足|显存不够|显存溢出', re.I),
     '显卡显存不足，请稍后重试或降低尺寸'),
)
# "this is not an out of memory error" must not be turned into an out-of-memory diagnosis.
# review-media-error-text-e L2: the guard also covers the `OOM` abbreviation and preventive
# advice ("to avoid CUDA out of memory, choose a smaller size" is guidance, not a failure).
_OOM_NEGATION = re.compile(_LB + r'(?:not|never|no)' + _RB + r'[^.。;；\n]{0,24}?'
                           r'(?:out of memory|' + _LB + r'OOM' + _RB + r')'
                           + r'|' + _LB + r'(?:avoid|prevent|instead of)' + _RB
                           + r'[^.。;；\n]{0,24}?(?:out of memory|' + _LB + r'OOM' + _RB + r')'
                           + r'|避免|防止|以免|不是显存|并非显存|没有显存|非显存(?:不足|不够|溢出)',
                           re.I)


def _gpu_out_of_memory(text):
    """True only for an explicit GPU/CUDA out-of-memory statement, never a negated one."""
    if _OOM_NEGATION.search(text):
        return False
    return any(pattern.search(text) for pattern, _ in _REWRITES)


def _looks_like_path(text):
    """True for path *shapes* only; the fraction/ratio exemption is applied first."""
    return any(pattern.search(_RATIO.sub(' ', text)) for pattern in _PATH_PATTERNS)


# --- review-media-error-text-e: the upstream string is attached only as an allow-list -------
# Four rounds of "what counts as code" rules kept losing the next runtime (PHP `in engine.php
# on line 42`, Swift `file Engine.swift, line 42`, a bare Python repr). The closing rule is a
# whitelist instead: upstream text is attached only when it is *written for a Chinese-reading
# user* — CJK-dominant, free of every code/path/proxy shape below, and short. Everything else
# (including all English-dominant text) is dropped and only the workbench's own fixed Chinese
# copy is shown; a known English condition is first translated to our own wording, so the
# original never reaches the user.
_CJK_MIN_RATIO = 0.5
# review-media-error-text-f M1: the old range table counted CJK *punctuation*, curly quotes and
# the whole U+FF00–FFEF fullwidth block as Chinese reading units, so a run of Chinese full stops
# in front of `RuntimeError("new_private.py")` measured 85.7%, and the same name written in
# fullwidth Latin measured 100%. Only real letters count now: CJK ideographs and the phonetic
# scripts used to write them. Punctuation, symbols and every fullwidth form (including fullwidth
# Latin) are not Chinese units.
_CJK_SCRIPT_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F),
                      (0x3041, 0x309F), (0x30A1, 0x30FF), (0x3105, 0x312F))


def _is_cjk(char):
    """CJK ideograph or kana/bopomofo phonetic letter — never punctuation or fullwidth Latin."""
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_SCRIPT_RANGES)


def _is_punctuation(char):
    """Punctuation or symbol in any script (, 。 「 」 “ ” … + %).

    Counting rule (review-media-error-text-f M1): punctuation is *neither* a Chinese unit nor a
    unit of its own — it only separates, exactly like whitespace. Chinese copy may of course use
    punctuation; using punctuation alone can never prove the string was written for a reader.
    """
    return unicodedata.category(char)[0] in ('P', 'S')


def _width_normalised(text):
    """Fullwidth/compatibility forms folded to their ASCII reading for the *checks* only.

    `１０．０．０．９：８２００` -> `10.0.0.9:8200`; a fullwidth exception name folds to its
    ASCII spelling the same way. What a user may ever see stays the original collapsed string.

    review-media-error-text-l M1: NFKC folds the fullwidth signs but leaves every Unicode dash
    alone — `Killed −9` with U+2212 MINUS SIGN is the same crash headline as `Killed -9` and
    stayed visible. The dash block is folded to ASCII `-` here as well, so the shape checks see
    one spelling; the string a user could ever be shown is still the original.
    """
    return unicodedata.normalize('NFKC', text).translate(_DASH_FOLD)


# U+2212 MINUS SIGN and the dash/hyphen variants a copy-paste or a CJK input method produce
_DASH_FOLD = str.maketrans({code: '-' for code in (
    0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2043, 0x2212, 0x207b, 0xfe63)})


def _cjk_ratio(text):
    """Share of CJK among the reading units of *text* (whitespace and punctuation ignored).

    A CJK glyph counts as one unit; a run of other letters/digits (a Latin word, a number, a
    model id, a file name) counts as one unit too. Counting raw characters instead would drop
    `model.safetensors 不存在，请选择已安装模型` — 17 characters of one model name against 12
    Chinese characters — which is exactly the business copy the user needs, while every English
    stack line stays far below the threshold either way. Fullwidth forms are normalised first so
    fullwidth Latin counts as Latin, and punctuation separates without ever being counted.
    """
    units = cjk = 0
    in_latin = False
    for char in _width_normalised(text):
        if char.isspace() or _is_punctuation(char):
            in_latin = False
            continue
        if _is_cjk(char):
            units += 1
            cjk += 1
            in_latin = False
        elif not in_latin:
            units += 1
            in_latin = True
    return 0.0 if units == 0 else cjk / units


def _cjk_dominant(text):
    return _cjk_ratio(text) >= _CJK_MIN_RATIO


def _indented_code_lines(raw):
    """How many raw lines are indented *and* not CJK-dominant: a traceback body shape."""
    return sum(1 for line in raw.splitlines()
               if _INDENTED_LINE.match(line) and not _cjk_dominant(line))


# Known English conditions we can name in our own words. Matched exactly or by a bounded
# pattern; on a hit only our Chinese line is shown, the upstream wording never appears. Kept
# deliberately small — an unknown English message is dropped, not paraphrased.
# Known English conditions we can name in our own words. Matched by the *complete* phrase, with
# ASCII boundaries; on a hit only our Chinese line is shown, the upstream wording never appears.
# Kept deliberately small — an unknown English message is dropped, not paraphrased.
#
# review-media-error-text-h M2 fixed two things at once, entry by entry:
#   1. boundary — the Unicode `\b` never matched when Chinese sat right next to the English
#      phrase, so `请补全输入prompt is required然后再重新提交` passed through untranslated;
#   2. partial conditions — a phrase that only *contains* the shape of a condition was read as
#      the condition itself. `images? (?:are|required)` matched `images are processing` and
#      turned a normal "still processing, please wait" hint into 「请至少提供一张参考图」. The
#      same risk existed in `maximum (number of) images` (an upper limit stated, not exceeded),
#      bare `rate limit` (a limit named, not hit), bare `timeout` (a timeout value named, not a
#      timeout that happened) and the `model … not found` gap, which Chinese could bridge. Every
#      entry now states the whole condition (verb included) and the gap is ASCII-only.
_KNOWN_EN_MESSAGES = (
    (re.compile(_LB + r'(?:at least one (?:reference )?image'
                r'|no images? (?:provided|supplied|given|uploaded)'
                r'|images? (?:are )?required'
                r'|image (?:is )?required'
                r'|missing (?:reference )?image'
                r'|(?:provide|upload|attach) (?:at least )?(?:one|a|an|the) image)' + _RB, re.I),
     '请至少提供一张参考图'),
    (re.compile(_LB + r'(?:prompt (?:is )?required|empty prompt|missing prompt'
                r'|prompt (?:must not be|cannot be|can not be) empty'
                r'|prompt (?:is )?(?:blank|too short))' + _RB, re.I),
     '请填写提示词'),
    (re.compile(_LB + r'(?:unsupported (?:image |file |media )?format|invalid image format'
                r'|image format (?:is )?invalid'
                r'|(?:image|file|media) format (?:is )?(?:unsupported|not supported)'
                r'|unsupported (?:file|image|media) type)' + _RB, re.I),
     '图片格式不支持'),
    (re.compile(_LB + r'(?:model' + _GAP + r'{0,40}?not found|unknown model'
                r'|no such model|model' + _GAP + r'{0,40}?not available)' + _RB, re.I),
     '所选模型不可用'),
    (re.compile(_LB + r'(?:too many (?:images|files|references)'
                r'|image (?:count|number) (?:exceeds|over|is over|too high)'
                r'|maximum (?:number of )?(?:images|files|references) (?:exceeded|reached|is over)'
                r'|(?:image|file|reference) (?:count|number|limit) exceeded)' + _RB, re.I),
     '参考图数量超出上限'),
    (re.compile(_LB + r'(?:rate[- ]?limit(?:ed)|rate[- ]?limit (?:exceeded|reached|hit)'
                r'|too many requests|quota (?:exceeded|exhausted|reached)'
                r'|(?:request|api) quota (?:exceeded|exhausted))' + _RB, re.I),
     '请求过于频繁，请稍后再试'),
    (re.compile(_LB + r'(?:timed out|request (?:timed out|timeout)'
                r'|(?:request|upstream|read|connect|connection|job|worker) timeout'
                r'|timeout (?:exceeded|reached|after \d+))' + _RB, re.I),
     '媒体服务处理超时'),
)


def _known_english_message(text):
    """Fixed Chinese for a recognised English condition; '' when we cannot name it safely.

    review-media-error-text-j M2: a table entry only speaks when the clause it was matched in
    *asserts* the condition, so the search walks every match and asks that clause which of the
    five states the condition is in (see _condition_state).

    review-media-error-text-l M2: when several entries assert, the one a real-failure pointer
    names wins over table order — `与prompt is required有关的是说明文字，真正错误是request
    timeout` must produce the timeout line, not the prompt line.
    """
    first = ''
    for pattern, copy in _KNOWN_EN_MESSAGES:
        for match in pattern.finditer(text):
            if _condition_state(text, match.start(), match.end()) != 'assert':
                continue
            if not first:
                first = copy
            if _names_the_fault(text, match.start(), _PRIMARY_FAULT):
                return copy
    return first


# --- review-media-error-text-j M2: the guard is per clause and bound to the matched phrase ---
# The i round gated the whole string on one switch-word regex: a single 不是 / 已上传 / 参数 /
# 已确认 anywhere silenced *every* table entry. That both missed the plainest negation
# (`本次未发生unsupported image format…` became 「图片格式不支持」) and blocked a real failure
# stated in another clause (`这不是网络问题；实际错误为prompt is required` stayed verbatim).
# Now the match itself is located, only its own clause is inspected, and the clause is classified
# into one of five states; only `assert` is translated, everything else keeps its own wording and
# falls back to the allow-list (Chinese-dominant copy survives, English-dominant is still dropped).
#
# `已确认` is deliberately *not* a guard marker any more: `已确认本次错误为request timeout` is an
# assertion that strengthens the failure, not a denial of it.
_CLAUSE_BREAK = re.compile(r'[。；;！!？?\n，,：:]')
# a sentence end: nothing before it can govern a condition that comes after it
_HARD_BREAK = re.compile(r'[。；;！!？?\n]')
# `而是` (and friends) discharge the negation in front of them: the sentence then names the
# condition that *did* happen
_CONTRAST = re.compile(r'而是|其实是|实际是|实际上|实为|真实|真正|然而')
# quote pairs that mark a phrase as *cited text* rather than a stated condition; they never split
# a clause, so a quoted phrase is caught by position, not by its surroundings
_QUOTE_PAIRS = ((u'“', u'”'), (u'「', u'」'), (u'『', u'』'), ('"', '"'), (u'＂', u'＂'), ("'", "'"))
# A quote mark alone does not decide anything, and it does not create a citation either. The
# coordinator's ruling (review-media-error-text-j): quoting does not change an assertion —
# `请补全输入『prompt is required』然后再提交` is still the failure the user must fix. A quoted
# phrase is only `quote` when the clause does *not* carry an assert verb — and review-media-error-
# text-l M2 tightened both halves of that sentence: a negation outranks the quote branch
# (`本次没有发生“X”报错` denies the condition, the trailing `报错` notwithstanding), and an assert
# verb *inside* the quotation belongs to the cited text, not to us
# (`帮助示例“请修改prompt is required”仅供阅读参考` stays a citation).
# An explicit statement that this condition is what happened this time.
_ASSERT_MARKER = re.compile(
    r'实际错误|真实错误|真正错误|当前仍报|仍报|仍然报|本次错误|该错误|错误为|报错|失败原因'
    r'|原因就?是|提示.{0,3}为|请补全|请补充|请重新选择|请重新上传|请上传|请提供|请检查输入'
    r'|请更换|请修改|当前错误')
# review-media-error-text-l M2: the pointer that names *which* condition is this run's real
# failure. When several table entries match, the one a *primary* pointer names wins over table
# order (`与prompt is required有关的是说明文字，真正错误是request timeout` → the timeout). A weak
# pointer (`仍报`) still asserts its own condition but does not outrank a primary one.
_TRUE_FAULT = re.compile(
    r'真正错误|真实错误|实际错误|当前仍报|仍报|仍然报|真正原因|实际原因|本次错误|错误就?是|错误为')
_PRIMARY_FAULT = re.compile(
    r'真正错误|真实错误|实际错误|真正原因|实际原因|本次错误为|错误就?是|错误为')
# review-media-error-text-l M2: a negation whose predicate sits *behind* the condition. Matched as
# a phrase, not as one keyword with a one-character leash: `X与此无关`, `X无关`, `X没有关系`,
# `X只是文档里的标签`.
# review-media-error-text-m M-1: the postposed "it is only …" family, stated once
_POST_EXCLUSIVE = r'(?:只是|仅是|仅仅是|不过是|无非是)'
# review-media-error-text-m M-2: an assert word that only names a document — `报错说明里的…`,
# `错误示例中的…` — states no failure, so it must not outvote a postposed exclusion
_DESCRIPTION_TAIL = re.compile(r'(?:说明|文档|示例|例子|帮助|里的|中的)')
_POST_NEGATE = re.compile(
    r'(?:与?[\u4e00-\u9fff]{0,3}?(?:无关|无关系|没有关系|不相干)'
    # review-media-error-text-m M-1: the exclusive phrase is one mechanism, not one keyword —
    # 只是/仅是/仅仅是/不过是/无非是 all deny that the phrase is this run's state
    + r'|' + _POST_EXCLUSIVE + r'[\u4e00-\u9fff]{0,5}?(?:标签|说明|示例|举例|文字)'
    r'|不代表|不属于|并非本次|不是本次)')
# `与其说X不如说Y` — X is the rejected alternative, Y is what is claimed
_ALTERNATIVE = re.compile(r'与其说')
_ALTERNATIVE_PICK = re.compile(r'不如')

_CONDITION_NEGATE = re.compile(
    r'未发生|未出现|未产生|未触发|没有出现|没有发生|没有|不存在|无关|不是|并非|不为|不会|不曾'
    r'|不可能|已排除|排除|不代表|误报|并非本次|不是本次'
    + r'|' + _LB + r'(?:not\s+the\s+(?:reason|cause)|is\s+not\s+the|was\s+not\s+the|did\s+not'
    + r'|never\s+(?:occurred|happened|triggered)|no\s+(?:error|failure)\s+(?:occurred|happened)'
    + r'|without\s+(?:any\s+)?error|false\s+alarm)' + _RB, re.I)
_CONDITION_CONFIGURE = re.compile(
    # a configuration value, a preventive switch, or the phrase used as a name/term
    r'设置为|设置成|请设置|请调整|请将|配置为|调整为|修改为|修改成|改成|开启|启用|禁用|关闭'
    r'|默认值|取值|上限为|阈值|参数|配置项|字段名|参数名|名称|指的是|是指|含义|意思是|理解为|术语'
    # review-media-error-text-l M2: the phrase is named as documentation, not as this run's state
    r'|说明文字|供阅读参考|仅供阅读|帮助示例|文档中|文档里|帮助页'
    # review-media-error-text-m M-2: `报错说明里的X` names the explanation, not the failure
    r'|报错说明|错误说明|故障说明|异常说明|说明里的|说明中的|示例里的|示例中的'
    + r'|' + _LB + r'(?:merely|only\s+as|refers?\s+to|means|name\s+of'
    + r'|configuration\s+(?:name|key|option|value))' + _RB, re.I)
_CONDITION_SATISFIED = re.compile(
    # the condition is reported as already met, so it cannot be this run's failure. The English
    # markers are full phrases on purpose: a bare `provided` / `uploaded` / `supplied` is part of
    # the condition itself (`no image provided`) and must never read as "already satisfied".
    r'(?<!不)支持|已上传|已提供|已满足|已包含|已填写|已解决|已修复|当前已|已有|满足要求|符合要求'
    + r'|' + _LB + r'(?:already\s+(?:provided|uploaded|supplied|satisfied|met|fixed|resolved)'
    + r'|condition\s+satisfied|requirements?\s+(?:are\s+)?met'
    + r'|no\s+(?:further|more)\s+(?:image|input)|is\s+enough)' + _RB, re.I)


def _inside_quote(text, start, end):
    """True when [start,end) sits inside quote marks — the phrase is cited, not stated."""
    for open_q, close_q in _QUOTE_PAIRS:
        if open_q == close_q:
            marks = [i for i, char in enumerate(text) if char == open_q]
            for index in range(0, len(marks) - 1, 2):
                if marks[index] < start and end <= marks[index + 1]:
                    return True
        else:
            if (text.find(close_q, end) > -1
                    and any(char == open_q for char in text[:start])):
                return True
    return False


def _clause_of(text, start):
    """The clause holding position *start*: cut at Chinese/ASCII sentence punctuation."""
    begin = _clause_start(text, start)
    following = _CLAUSE_BREAK.search(text, start)
    return text[begin:following.start()] if following else text[begin:]


def _clause_start(text, start):
    """Where the clause holding *start* begins (any clause punctuation, newline included)."""
    begin = 0
    for break_at in _CLAUSE_BREAK.finditer(text):
        if break_at.start() >= start:
            break
        begin = break_at.end()
    return begin


def _sentence_start(text, start):
    """Where the sentence holding *start* begins: only 。；;！？ and newline end one."""
    begin = 0
    for break_at in _HARD_BREAK.finditer(text):
        if break_at.start() >= start:
            break
        begin = break_at.end()
    return begin


def _strip_quotes(clause):
    """The clause with every quoted span removed (review-media-error-text-l M2).

    An assert verb that belongs to the cited text must not decide anything about our own state:
    `帮助示例“请修改prompt is required”仅供阅读参考` quotes a help example that happens to start
    with 请修改 — that `请修改` is the documentation's wording, not a failure we are stating.
    """
    out = []
    index = 0
    while index < len(clause):
        for open_q, close_q in _QUOTE_PAIRS:
            close = clause.find(close_q, index + 1)
            if clause[index] == open_q and close > index:
                index = close + 1
                break
        else:
            out.append(clause[index])
            index += 1
    return ''.join(out)


def _names_the_fault(text, start, pointer=_TRUE_FAULT):
    """True when a real-failure pointer (`真正错误是/实际错误为/当前仍报…`) sits right before *start*.

    review-media-error-text-m M-3: the window starts at the *sentence*, not the clause — the colon
    in `当前真正错误是：request timeout` introduces the condition, it does not cut the pointer off.
    The ≤6 character gap still keeps the pointer attached to the condition it names.
    """
    sentence_start = _sentence_start(text, start)
    for found in pointer.finditer(text, sentence_start, start):
        if start - found.end() <= 6 and not _HARD_BREAK.search(text[found.end():start]):
            return True
    return False


def _assert_before(text, clause_start, start):
    """An assert verb in front of the condition — but one that only names a document does not
    assert anything (review-media-error-text-m M-2: `报错说明里的X不代表本次故障`)."""
    for marker in _ASSERT_MARKER.finditer(text, clause_start, start):
        if not _DESCRIPTION_TAIL.match(text, marker.end()):
            return True
    return False


def _negation_state(text, start, end):
    """Does a negation govern the condition at *start*, and does it survive?

    Five scope rules, applied to the *governing* negation instead of to any marker in the clause
    (review-media-error-text-j M2, review-media-error-text-k M2, review-media-error-text-l M2):

    1. rejected alternative — `与其说X不如说Y`: X is denied, Y is what is claimed;
    2. parity — `本次并非没有出现X` / `本次不可能没有发生X` carry two negations, which assert;
       the count is taken inside the clause that holds the governing negation, so a coordinate
       clause (`未出现A，也未发生B`) counts once per condition;
    3. contrast — `不是X而是Y`: `而是` discharges the negation in front of it, so Y is asserted;
    4. reach — a negation may reach its object across a colon or comma and across a long
       attributive run (`未发生此前日志里反复提及的那一种错误：X`), but never across a sentence
       end, an assert verb, or a contrast (`这不是网络问题；实际错误为X`);
    5. postposition — the predicate can follow the condition (`X与此无关`, `X只是文档里的标签`),
       matched as a phrase rather than as one keyword with a one-character leash, and only when no
       assert verb already stands in front of the condition (`本次错误为X，不是网络问题`).

    Returns 'negate', 'assert' or None (no negation governs this condition).
    """
    sentence_start = _sentence_start(text, start)
    for alternative in _ALTERNATIVE.finditer(text, sentence_start, start):
        if _ALTERNATIVE_PICK.search(text, alternative.end(), start):
            return 'assert'      # 与其说X不如说Y — this match is Y, the claimed alternative
        return 'negate'          # this match is X, the rejected alternative
    clause_start = _clause_start(text, start)
    negations = list(_CONDITION_NEGATE.finditer(text, clause_start, start))
    if not negations:
        # the object may sit behind a colon/comma that introduces it, and a long attributive run
        # must not cut a negation off from its object (review-media-error-text-l M2)
        for negation in _CONDITION_NEGATE.finditer(text, sentence_start, start):
            gap = text[negation.end():start]
            if (len(gap) <= 40 and not _HARD_BREAK.search(gap)
                    and not _CONTRAST.search(gap) and not _ASSERT_MARKER.search(gap)):
                negations = [negation]
                clause_start = _clause_start(text, negation.start())
                break
    if negations:
        last = negations[-1]
        if _CONTRAST.search(text[last.end():start]):
            return 'assert'      # 不是X而是Y — the negation names X, Y stands on its own
        if _ASSERT_MARKER.search(text[last.end():start]):
            return None          # the negation had another object; this one is asserted
        parity = len(list(_CONDITION_NEGATE.finditer(text, clause_start, start)))
        return 'negate' if parity % 2 else 'assert'
    if not _assert_before(text, clause_start, start):
        # the predicate may also follow the condition: `与X无关`, `X只是文档里的标签`
        for postposed in _POST_NEGATE.finditer(text, end, min(len(text), end + 12)):
            if postposed.start() - end <= 6:
                return 'negate'
    return None


def _condition_state(text, start, end):
    """Which of the five states the scope around a matched condition puts it in.

    assert   the condition is stated as what happened  -> translate to our own copy
    negate   a negation governs it and survives (`未发生X`, `没有出现：X`, `X与此无关`)
    quote    the phrase is cited inside quote marks and the clause carries no assert verb of ours
    configure  the phrase is a configuration value / switch / term name / documentation
    satisfied  the clause reports the condition as already met

    review-media-error-text-l M2 ordering: the negation is asked *first*, so a quote mark or a
    trailing 报错 can no longer outvote an explicit denial; the quote branch then looks only at
    assert verbs written *outside* the quotation; a real-failure pointer outranks configure and
    satisfied.
    """
    clause = _clause_of(text, start)
    governed = _negation_state(text, start, end)
    if governed:
        return governed
    if _inside_quote(text, start, end):
        # review-media-error-text-k M2: quoting never changes an assertion by itself
        return 'assert' if _ASSERT_MARKER.search(_strip_quotes(clause)) else 'quote'
    if _names_the_fault(text, start):
        return 'assert'
    if _CONDITION_CONFIGURE.search(clause):
        return 'configure'
    if _CONDITION_SATISFIED.search(clause):
        return 'satisfied'
    return 'assert'


# review-media-error-text-j M2: the whole-string `_CONDITION_NOT_STATED` switch of the i round is
# gone — the table now asks the clause that *contains the match* (see _condition_state).


def safe_upstream_text(text, cap=_MAX_MESSAGE_CHARS):
    """Strict **allow-list** for any upstream string we might surface to a user.

    The whitespace-collapsed string (capped to *cap* chars) is returned only when all three
    hold: (a) it is CJK-dominant — at least half of its reading units are Chinese characters,
    so it was written for our user rather than emitted by a runtime;
    (b) it carries no internal artefact: no markup, no URL, no IP:port, no server/proxy phrase,
    no stack frame or traceback syntax, nothing that looks like code (source file with a line
    number, hex offset, goroutine header, crash / process-exit headline from _RUNTIME_FATAL_SHAPES
    — POSIX signal names, `signal 11`, `status 139`, `Killed 7231`, Aborted, Bus error, Floating
    point exception, Segmentation fault, core dumped, kernel panic, errno N, `Exception in thread`
    — a dotted or package-qualified call, bare `at ` + dotted name, bare exception name, indented
    non-Chinese lines) and no filesystem path shape (so 1/2 张 and 16/9 stay readable);
    (c) it is at most *cap* characters.
    Anything else is dropped: an explicitly GPU/CUDA out-of-memory condition becomes our own
    fixed Chinese line, a recognised English condition (at least one image / prompt required /
    unsupported format / model not found / too many images / rate limit / timeout) becomes the
    matching Chinese line — that mapping is applied *before* the Chinese allow-list branch, so a
    Chinese prefix cannot keep the raw English visible, but only when the clause carrying the
    match asserts the condition: a phrase that is denied, quoted, configured or already satisfied
    in its own clause keeps its own wording (review-media-error-text-j M2). Every other
    English-dominant or unknown string returns ''. Shape checks and the CJK count also see the
    width-normalised form of the string, so a fullwidth IP or a fullwidth exception name cannot
    hide an artefact.
    """
    if not isinstance(text, str):
        return ''
    if _gpu_out_of_memory(text):
        return _REWRITES[0][1]
    raw = text
    text = ' '.join(text.split())
    if not text:
        return ''
    if len(text) > cap:
        return ''
    # Every shape check runs on the collapsed string *and* on its width-normalised form, so a
    # fullwidth IP or a ＲｕｎｔｉｍｅＥｒｒｏｒ( cannot dress an artefact up as user copy.
    norm = _width_normalised(text)
    forms = (text,) if norm == text else (text, norm)
    # newline kept as a clause boundary, every other whitespace collapsed
    lines_form = '\n'.join(' '.join(line.split()) for line in raw.splitlines())
    lowered = [form.lower() for form in forms]
    if any(bad in low for low in lowered for bad in _UNSAFE_MARKERS):
        return ''
    if any(_IP_PORT.search(form) for form in forms):
        return ''
    if any(marker in low for low in lowered for marker in _UNTRUSTED_MARKERS):
        return ''
    if any(pattern.search(form) for form in forms for pattern in _STACK_PATTERNS):
        return ''
    if any(pattern.search(form) for form in forms for pattern in _CODE_SHAPE_PATTERNS):
        return ''
    if _indented_code_lines(raw) >= 2:
        return ''
    if any(_looks_like_path(form) for form in forms):
        return ''
    # review-media-error-text-f M3: a condition we can name in our own words is replaced by our
    # own copy *before* the Chinese allow-list branch, otherwise a Chinese prefix kept the raw
    # English visible (`请检查输入并重新提交：prompt is required` passed through verbatim).
    # review-media-error-text-j M2: the table decides per match and per clause — a denied,
    # quoted, configured or already-satisfied phrase keeps its own wording, while a real failure
    # stated in any clause is still translated.
    # review-media-error-text-k M2: the table also sees the line structure. Collapsing newlines
    # first made `提示词已填写\n当前仍报prompt is required…` one clause, so the first line's
    # satisfied marker covered the next line's real failure.
    for form in ((lines_form,) if lines_form not in forms else ()) + forms:
        known = _known_english_message(form)
        if known:
            return known
    # Allow-list: only Chinese-dominant copy written for the user is attached verbatim.
    if _cjk_dominant(text):
        return text
    return ''


def _safe_error_message(raw):
    """One user-safe line from an upstream error body; '' whenever the body is not trusted.

    Fail closed: a non-JSON body (nginx/proxy HTML), a truncated JSON document, a missing
    or non-string error field, or any text carrying markup/path/URL/stack/server words all
    return '' — the workbench's own Chinese copy is shown without it.
    """
    if not raw:
        return ''
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        return ''
    if not isinstance(document, dict):
        return ''
    value = document.get('error')
    if isinstance(value, dict):
        value = value.get('message') or value.get('detail')
    if not isinstance(value, str):
        return ''
    return safe_upstream_text(value)


def _note_upstream_error(route, code, raw):
    """Internal trace only: status code and body size, never the body itself."""
    try:
        _LOG.warning('媒体服务 %s 返回 HTTP %s（响应体 %d 字节，内容已省略）',
                     route, code, len(raw or b''))
    except Exception:
        pass


def _unavailable_hint(code):
    """Actionable line for the codes that usually mean an unreachable or busy service."""
    if code in (502, 503, 504):
        return '媒体服务可能正在维护或重启，请稍后再试'
    if code == 429:
        return '请求过于频繁，请稍后再试'
    if code == 408:
        return '媒体服务响应超时，请稍后再试'
    return ''


class MediaApiError(ValueError):
    """The API answered and rejected the request (or returned a non-2xx we can read)."""


class MediaApiUnknown(MediaApiError):
    """Transport failed or the server answered 5xx: the job state is unknown.

    Callers must keep the idempotency key and reconcile the same API job later;
    they must never resubmit blindly and must never fall back to SSH.
    """

    def __init__(self, message, state='unknown'):
        super().__init__(message)
        self.state = state


def _loopback(host):
    return host in ('127.0.0.1', 'localhost', '::1')


def resolve_config(base=None, key_file=None):
    """Resolve base url + bearer token. Missing configuration is an explicit error."""
    if not base:
        base = os.environ.get('MEDIA_API_BASE', '').strip()
    if not key_file:
        key_file = os.environ.get('MEDIA_API_KEY_FILE', '').strip()
    if not base or not key_file:
        path = os.environ.get('MEDIA_API_CONFIG', '').strip() or str(
            Path.home() / '.config' / 'article-studio' / 'media_api.json')
        try:
            document = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            document = {}
        if not isinstance(document, dict):
            document = {}
        base = base or str(document.get('base', '')).strip()
        key_file = key_file or str(document.get('key_file', '')).strip()
    if not base:
        raise MediaApiError(
            '未配置媒体服务地址（MEDIA_API_BASE）。媒体任务不再通过 SSH 提交，'
            '请先配置 HTTPS 入口，例如 https://relay.yunzhong2020.com/media/v1')
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == 'http' and not _loopback(parts.hostname or ''):
        raise MediaApiError('MEDIA_API_BASE 非本机地址时必须使用 https')
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        raise MediaApiError('MEDIA_API_BASE 不是合法的 http/https 地址')
    base = base.rstrip('/')
    if not key_file:
        key_file = str(Path.home() / '.config' / 'article-studio' / 'media-api.key')
    path = Path(key_file)
    try:
        mode = path.stat().st_mode
    except OSError:
        raise MediaApiError(f'未找到媒体服务密钥文件 {key_file}（权限须为 600）') from None
    if mode & 0o077:
        raise MediaApiError(f'密钥文件 {key_file} 权限过宽，须为 600 后才能使用')
    try:
        token = path.read_text().strip()
    except OSError:
        raise MediaApiError(f'无法读取媒体服务密钥文件 {key_file}') from None
    if not token:
        raise MediaApiError(f'媒体服务密钥文件 {key_file} 为空')
    return base, token


class MediaApi:
    """Small, explicit HTTP client. One instance per workbench process."""

    def __init__(self, base=None, key_file=None, timeout=20, opener=None):
        self.base, self.token = resolve_config(base, key_file)
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self._last_error = None
        self._checked_at = None

    # ---------- plumbing ----------
    def headers(self, extra=None, auth=True):
        values = {'Accept': 'application/json', 'User-Agent': 'article-studio/1.0'}
        if auth:
            values['Authorization'] = 'Bearer ' + self.token
        values.update(extra or {})
        return values

    def _call(self, method, path, body=None, headers=None, timeout=None, max_bytes=None):
        """One request. Reading the body is inside the same error classification, so a
        connection lost after the response headers is an unknown state, not a failure."""
        url = self.base + path
        request = urllib.request.Request(url, data=body, method=method,
                                         headers=self.headers(headers))
        try:
            response = self.opener.open(request, timeout=timeout or self.timeout)
            try:
                status, message = response.status, response.headers
                length = int(message.get('Content-Length') or 0)
                if max_bytes is not None and length and length > max_bytes:
                    raise MediaApiUnknown(f'媒体服务响应体超出上限（{length} 字节）', state='unknown')
                payload = response.read(max_bytes) if max_bytes else response.read()
            except MediaApiUnknown:
                raise
            except (http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                    ssl.SSLError) as exc:
                raise MediaApiUnknown(
                    f'响应接收不完整（{exc.__class__.__name__}）；服务器可能已处理该请求',
                    state='unknown') from None
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            raw = b''
            try:
                raw = exc.read(4000)
            except Exception:
                pass
            try:
                exc.close()          # release the error body instead of leaking it to GC
            except Exception:
                pass
            message = _safe_error_message(raw)
            _note_upstream_error(path, exc.code, raw)
            if exc.code in (408, 429) or exc.code >= 500:
                # the request may have been executed; treat it as unknown state
                detail = '，'.join(part for part in (
                    f'媒体服务暂时不可用（HTTP {exc.code}）', _unavailable_hint(exc.code), message) if part)
                raise MediaApiUnknown(
                    f'{detail}；已提交的任务不会自动重复提交，可点“重新核实”按原任务编号查询',
                    state='unknown') from None
            self._last_error = f'HTTP {exc.code}'
            raise MediaApiError(
                f'媒体服务拒绝了本次请求（HTTP {exc.code}）'
                + (f'：“{message}”' if message else '，未返回可显示的说明')) from None
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                ssl.SSLError) as exc:
            reason = safe_upstream_text(str(getattr(exc, 'reason', exc))) or '网络连接异常'
            raise MediaApiUnknown(
                f'无法确认媒体服务是否收到请求（{reason}）；已提交的任务不会自动重复提交',
                state='unknown') from None
        return status, message, payload

    def get_json(self, path, params=None, timeout=None):
        query = ('?' + urllib.parse.urlencode(params)) if params else ''
        status, _, payload = self._call('GET', path + query, timeout=timeout)
        try:
            return json.loads(payload)
        except ValueError:
            raise MediaApiUnknown(f'媒体服务 {path} 返回了无法解析的响应', state='unknown') from None

    def post_json(self, path, payload, headers=None, timeout=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        values = {'Content-Type': 'application/json'}
        values.update(headers or {})
        status, _, raw = self._call('POST', path, body=body, headers=values, timeout=timeout)
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            raise MediaApiUnknown(f'媒体服务 {path} 的响应无法解析', state='unknown') from None

    # ---------- read-only contracts ----------
    def liveness(self):
        """Unauthenticated process liveness only; never implies GPU or model readiness."""
        return self.get_json('/health', timeout=10)

    def status(self):
        return self.get_json('/status', timeout=45)

    def models(self):
        return self.get_json('/models', timeout=30)

    def backends(self, action='health', model=None):
        """model is a workbench label (qi21/h3/krea2) or None for all fixed services."""
        params = {}
        if model:
            params['model'] = model
        return self.get_json('/backends/' + action, params or None, timeout=60)

    def job(self, api_job_id):
        return self.get_json('/jobs/' + api_job_id, timeout=20)

    def jobs(self):
        return self.get_json('/jobs', timeout=30).get('jobs', [])

    def find_by_request(self, request_id):
        """Adopt an API job whose submit response was lost. Never creates a job."""
        if not request_id:
            return None
        for item in self.jobs():
            if item.get('request_id') == request_id:
                return item
        return None

    def cancel(self, api_job_id):
        return self.post_json('/jobs/' + api_job_id + '/cancel', {}, timeout=30)[1]

    # ---------- references ----------
    def upload(self, path, kind, name, meta=None, timeout=180):
        """Raw-body upload of one normalised local asset; returns the API file record."""
        source = Path(path)
        length = source.stat().st_size
        if length <= 0:
            raise MediaApiError('参考素材为空，未上传')
        digest = hashlib.sha256()
        with source.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        headers = {'X-File-Kind': kind,
                   # display metadata only; the API stores <uuid>.<ext>. Names are quoted so
                   # CJK, spaces and parentheses (e.g. 'AI图片 (4).png') survive the header.
                   'X-File-Name': urllib.parse.quote(str(name).strip()[:120]),
                   'X-Sha256': digest.hexdigest(),
                   'Content-Type': 'application/octet-stream',
                   'Content-Length': str(length)}
        if meta:
            headers['X-File-Meta'] = json.dumps(meta, ensure_ascii=False)
        status, record = self._upload_stream(source, headers, timeout)
        if not record.get('file_id'):
            raise MediaApiUnknown('素材上传响应缺少 file_id，未确认登记', state='unknown')
        return record

    def _upload_stream(self, source, headers, timeout):
        request = urllib.request.Request(self.base + '/files', method='POST',
                                         headers=self.headers(headers))
        # urllib's Request.data setter drops any Content-length header it holds (issue
        # 16464), which silently downgraded this request to Transfer-Encoding: chunked.
        # The 4090 API sizes the upload from Content-Length, so attach the body first and
        # declare the length afterwards; the file still streams in blocks on the wire.
        handle = source.open('rb')
        try:
            request.data = handle
            request.add_unredirected_header('Content-Length', headers['Content-Length'])
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = b''
            try:
                raw = exc.read(2000)
            except Exception:
                pass
            try:
                exc.close()
            except Exception:
                pass
            message = _safe_error_message(raw)
            _note_upstream_error('/files', exc.code, raw)
            if exc.code >= 500 or exc.code in (408, 429):
                detail = '，'.join(part for part in (
                    f'素材上传失败（HTTP {exc.code}）', _unavailable_hint(exc.code), message) if part)
                raise MediaApiUnknown(
                    f'{detail}；素材未确认登记，生成任务尚未提交，不会自动重复上传',
                    state='unknown') from None
            raise MediaApiError(
                f'素材上传被拒绝（HTTP {exc.code}）'
                + (f'：“{message}”' if message else '，未返回可显示的说明')) from None
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                ssl.SSLError) as exc:
            reason = safe_upstream_text(str(getattr(exc, 'reason', exc))) or '网络连接异常'
            raise MediaApiUnknown(f'素材上传中断（{reason}），生成尚未提交',
                                  state='unknown') from None
        finally:
            handle.close()
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            raise MediaApiUnknown('素材上传响应无法解析，未确认登记', state='unknown') from None

    # ---------- jobs ----------
    def submit(self, params, request_id, timeout=45):
        """Create one API job. A stable request_id makes retries and restarts safe."""
        payload = dict(params)
        payload['request_id'] = request_id
        status, job = self.post_json('/jobs', payload,
                                     headers={'Idempotency-Key': str(request_id)}, timeout=timeout)
        if not job.get('id'):
            raise MediaApiUnknown('任务提交响应缺少任务编号，未确认是否已排队', state='unknown')
        return job

    def result_meta(self, api_job_id):
        """HEAD the result route: content type, server sha256 and byte count."""
        _, message, _ = self._call('HEAD', '/jobs/' + api_job_id + '/result', timeout=30)
        try:
            size = int(message.get('Content-Length') or 0)
        except ValueError:
            size = 0
        return {'content_type': message.get('Content-Type') or '',
                'sha256': (message.get('X-Content-Sha256') or '').lower(), 'bytes': size}

    def download_result(self, api_job_id, destination, expect_sha256=None, expect_bytes=None,
                        timeout=600, max_bytes=512 * 1024 * 1024):
        """Stream the API output to a local file: atomic write + sha256 verification."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / ('.' + destination.name + '-' + os.urandom(8).hex())
        digest = hashlib.sha256()
        received = 0
        claimed = ''
        try:
            request = urllib.request.Request(self.base + '/jobs/' + api_job_id + '/result',
                                             headers=self.headers({'Accept': 'application/octet-stream'}))
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                try:
                    exc.close()
                except Exception:
                    pass
                if exc.code >= 500 or exc.code in (408, 429):
                    raise MediaApiUnknown(f'结果下载失败（HTTP {exc.code}）', state='unknown') from None
                raise MediaApiError(f'结果下载失败（HTTP {exc.code}）') from None
            except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError,
                    OSError, ssl.SSLError) as exc:
                reason = safe_upstream_text(str(getattr(exc, 'reason', exc))) or '网络连接异常'
                raise MediaApiUnknown(f'结果下载中断（{reason}）', state='unknown') from None
            try:
                with response:
                    # email.message headers are case-insensitive, so a lowercase
                    # x-content-sha256 from any proxy is still found.
                    claimed = (response.headers.get('X-Content-Sha256') or '').lower()
                    with temporary.open('wb') as handle:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_bytes:
                                raise MediaApiUnknown('结果体积超出上限，未标记完成', state='unknown')
                            handle.write(chunk)
                            digest.update(chunk)
            except MediaApiUnknown:
                raise
            except (http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                    ssl.SSLError) as exc:
                raise MediaApiUnknown(
                    f'结果下载在响应头之后中断（{exc.__class__.__name__}）', state='unknown') from None
            if claimed and claimed != digest.hexdigest():
                raise MediaApiUnknown('结果下载校验不一致（传输损坏），未标记完成', state='unknown')
            if expect_sha256 and expect_sha256.lower() != digest.hexdigest():
                raise MediaApiUnknown('结果与服务器记录的 sha256 不一致，未标记完成', state='unknown')
            if expect_bytes not in (None, received):
                raise MediaApiUnknown('结果字节数与服务器记录不一致，未标记完成', state='unknown')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {'bytes': received, 'sha256': digest.hexdigest(), 'content_sha256': claimed}

    def events(self, api_job_id, since, on_event, stop, max_seconds=3600):
        """Consume the API's SSE progress stream. Returns the last seen event id."""
        last = since
        deadline = time.monotonic() + max_seconds
        query = '?' + urllib.parse.urlencode({'since': last})
        request = urllib.request.Request(self.base + '/jobs/' + api_job_id + '/events' + query,
                                         headers=self.headers({'Accept': 'text/event-stream'}))
        try:
            response = self.opener.open(request, timeout=60)
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                ssl.SSLError) as exc:
            reason = safe_upstream_text(str(getattr(exc, 'reason', exc))) or '网络连接异常'
            raise MediaApiUnknown(f'进度流无法建立（{reason}）', state='unknown') from None
        pending = {}
        try:
            while time.monotonic() < deadline and not stop.is_set():
                try:
                    raw = response.readline()
                except (http.client.HTTPException, socket.timeout, TimeoutError, OSError) as exc:
                    # A dropped stream is not a lost job: report it and let the caller
                    # reconnect with the last acknowledged event id.
                    raise MediaApiUnknown(f'进度流中断（{exc.__class__.__name__}）', state='unknown') from None
                if not raw:
                    break
                line = raw.decode(errors='replace').rstrip('\r\n')
                if not line:
                    # blank line terminates one SSE event record
                    if pending.get('data') is None:
                        pending = {}
                        continue
                    try:
                        envelope = json.loads(pending['data'])
                    except ValueError:
                        pending = {}
                        continue
                    seq = envelope.get('seq') or pending.get('id') or last
                    try:
                        last = int(seq)
                    except (TypeError, ValueError):
                        pass
                    on_event({'type': pending.get('event') or envelope.get('type'),
                              'seq': last, 'ts': envelope.get('ts'), 'data': envelope.get('data') or {}})
                    pending = {}
                    continue
                if line.startswith(': '):
                    continue
                field, _, value = line.partition(': ')
                if field in ('id', 'event'):
                    pending[field] = value.strip()
                elif field == 'data':
                    pending['data'] = value
        finally:
            response.close()
        return last
