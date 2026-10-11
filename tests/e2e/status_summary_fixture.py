"""27/28 专用注入：仅启用环境开关的隔离fixture进程加载。"""
import json, sqlite3, time, traceback
from pathlib import Path

def install(workspace):
    import collect_core
    from knowledge_store import Knowledge
    control = Path(workspace) / 'status-summary-control.json'
    def mode():
        try: return json.loads(control.read_text())
        except (OSError, ValueError): return {}
    class Probe(collect_core.Adapter):
        platform = 'wechat_msg'
        calls = 0
        def status(self):
            self.calls += 1
            if self.calls == 1:
                deadline = time.monotonic() + 30
                while mode().get('status27') and not mode().get('release'):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('E2E27探针未放行')
                    time.sleep(.02)
                return {'available': True, 'count': 27, 'extra': {'busy': False}, 'detail': 'E2E27已核实'}
            raise OSError('E2E27刷新失败')
    original_status = collect_core.Registry.status
    original_listing = collect_core.Registry.listing
    def prepare(self):
        state = mode()
        if state.get('status27'):
            if not hasattr(self, '_e2e27_probe'):
                self._e2e27_original_adapter = self.adapters.get('wechat_msg')
                self._e2e27_original_quota = self.quota.states
                with self._status_lock:
                    self._e2e27_cache = dict(self._status_cache)
                    self._e2e27_errors = dict(self._status_errors)
                self._e2e27_probe = Probe()
                self.register(self._e2e27_probe)
                def quota():
                    time.sleep(4)
                    return {}
                self.quota.states = quota
            if state.get('expire') and not getattr(self, '_e2e27_expired', False):
                key = ('wechat_msg', id(self._e2e27_probe))
                with self._status_lock:
                    if key in self._status_cache:
                        stamp, checked, value = self._status_cache[key]
                        self._status_cache[key] = (stamp - collect_core.STATUS_CACHE_SECONDS - 1, checked, value)
                        self._e2e27_expired = True
        elif hasattr(self, '_e2e27_probe'):
            key = ('wechat_msg', id(self._e2e27_probe))
            with self._status_lock:
                pending = [self._status_workers[k] for k in (key, 'quota') if k in self._status_workers]
            for done in pending:
                if not done.wait(5):
                    raise AssertionError('E2E27后台探针未退出，不能恢复')
            original_adapter = self._e2e27_original_adapter
            if original_adapter is None:
                self.adapters.pop('wechat_msg', None)
            else:
                self.adapters['wechat_msg'] = original_adapter
            self.quota.states = self._e2e27_original_quota
            with self._status_lock:
                for mapping, saved in ((self._status_cache, self._e2e27_cache),
                                       (self._status_errors, self._e2e27_errors)):
                    mapping.pop(key, None)
                    if 'quota' in saved:
                        mapping['quota'] = saved['quota']
                    else:
                        mapping.pop('quota', None)
                assert key not in self._status_workers
            for name in list(vars(self)):
                if name.startswith('_e2e27_'):
                    delattr(self, name)
            self._e2e27_cleaned = True
    def listing(self):
        prepare(self)
        return original_listing(self)
    def status(self):
        prepare(self)
        result = original_status(self)
        result['e2e27_cleaned'] = bool(getattr(self, '_e2e27_cleaned', False)) and not hasattr(self, '_e2e27_probe')
        return result
    collect_core.Registry.listing = listing
    collect_core.Registry.status = status
    original_db = Knowledge.db
    def knowledge_db(self):
        if mode().get('summary28') and any(f.name == '_batch_summary' for f in traceback.extract_stack()):
            raise sqlite3.OperationalError('E2E28保留数查询失败')
        return original_db(self)
    Knowledge.db = knowledge_db
