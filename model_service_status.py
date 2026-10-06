"""Compatibility import for the shared source backend probe."""
import sys
from wechat_source_loader import source_module
sys.modules[__name__]=source_module('model_service_status')
