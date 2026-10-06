"""Compatibility import; implementation belongs to wechat-assistant."""
import sys
from wechat_source_loader import source_module
sys.modules[__name__]=source_module('wechat_clue_context')
