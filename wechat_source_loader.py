"""Load the configured local WeChat source plugin; never download or fall back to duplicate processors."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
_lock=threading.RLock()
def source_module(name):
    with _lock:
        if 'wechat_source' not in sys.modules:
            configured=os.environ.get('ARTICLE_STUDIO_WECHAT_MODULE')
            if not configured:
                config=Path(__file__).parent/'workspace/sources/wechat.json'
                try:configured=json.loads(config.read_text())['module_dir']
                except (OSError,ValueError,KeyError):raise ValueError('微信来源模块未配置；未启动另一套分析器') from None
            root=Path(configured).expanduser().resolve()/'wechat_source'
            if not (root/'__init__.py').is_file():raise ValueError('已配置的微信来源模块不可用')
            spec=importlib.util.spec_from_file_location('wechat_source',root/'__init__.py',submodule_search_locations=[str(root)])
            module=importlib.util.module_from_spec(spec);sys.modules['wechat_source']=module
            try:spec.loader.exec_module(module)
            except Exception:sys.modules.pop('wechat_source',None);raise
        return importlib.import_module('wechat_source.'+name)
