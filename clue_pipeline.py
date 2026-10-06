"""Compatibility adapter. WeChat rules and checkpoints live in wechat-assistant."""
from wechat_source_loader import source_module
from source_pi_executor import execute_pi
import time
_source=source_module('clue_pipeline')
class CluePipeline(_source.CluePipeline):
    def call_pi(self,identity,prompt,cancel,config,deadline,system_prompt=None):
        return execute_pi(self,identity,prompt,cancel,config,deadline,system_prompt)
