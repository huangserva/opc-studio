"""content_markdown.cjs 的作品夹具：造若干篇「修复前导入」的作品。

正文里的 ## 标题、**粗体**、- 列表都留在字面量上，正是修复前的形态：
导入路径只做 HTML 白名单清洗，没有把 Markdown 转成正文格式。
每篇独立，因为重排过一次的正文再没有 Markdown 迹象，不能拿同一篇反复验证。
用法：python content_markdown_fixture.py <workspace>
"""
import json
import shutil
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402  产品 Store，E2E 走真实入库与版本

MARKDOWN = (
    '把 96.5GB 的 GLM-5.3-Flash 塞进单张 4090 48GB，我们如何跑出 41.8 token/s？\n\n'
    '一个 **96.5GB** 体积的混合专家（MoE）模型，一张 **48GB** 显存的单显卡。\n\n'
    '## 01. 账本与底牌：96.5GB 凭什么能单卡解码？\n\n'
    '### 模型与量化配方\n\n'
    '* **结构体量**：共 46 层，前 3 层为稠密层。\n'
    '* **专家总数**：42 层 × 288 = **12,096 个路由专家**。\n\n'
    '---\n\n结尾一段普通正文，没有标记。'
)


def legacy(store, title):
    """建一篇正文仍是字面量 Markdown 的作品，返回 slug。"""
    slug = store.create({'title': title, 'type': 'article'})['slug']
    head = store.article(slug) / 'versions' / store.meta(slug)['head']
    manifest = json.loads((head / 'manifest.json').read_text(encoding='utf-8'))
    markup = '<p>' + MARKDOWN.replace('\n\n', '<br><br>').replace('\n', '<br>') + '</p>'
    # 修复前的清洗器只做白名单，不转义 Markdown 字面量，所以要连转义一起关掉。
    # 只关转换的话，存进去的是 `&#42;&#42;`/`&#35;&#35;` 实体形态——那是**现在**的
    # 存储形态，不是「修复前」，重排按钮按它也排不出东西（复核 H-1a 之后踩过一次）。
    with mock.patch.object(server, 'MARKUP_LITERALS', ''), \
            mock.patch.object(server, 'MARKUP_LINE_RULES', ()):
        rich, plain = server.safe_html(markup)
    manifest['blocks'] = [{'kind': 'text', 'html': rich, 'text': plain, 'display_text': plain}]
    manifest['source_text_chars'] = len(plain)
    server.importer.write_json(head / 'manifest.json', manifest)
    # 夹具是在独立进程里建的作品，正在跑的工作台进程并不记得这次投递：
    # 它下一次扫投递目录时会把这篇再导入一遍（源是 create() 留下的空壳），
    # 于是凭空多出几个版本并把正文清空。删掉投递目录，工作台就再也不会重导。
    shutil.rmtree(store.inbox / slug, ignore_errors=True)
    return slug


def main():
    workspace = Path(sys.argv[1])
    store = server.Store(workspace)
    titles = {'button': '按钮重排用', 'huang_reflow': '黄总原话一用',
              'huang_whole': '黄总原话二用', 'trim': '精简指令用'}
    slugs = {key: legacy(store, title) for key, title in titles.items()}
    print(json.dumps(slugs, ensure_ascii=False))


if __name__ == '__main__':
    main()