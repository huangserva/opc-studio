"""采集 fixture 的模块依赖补丁：后台退出后恢复，再核对身份。"""
from unittest.mock import patch
import intake_store


def guard_intake_dependencies(case):
    originals = {name: getattr(intake_store, name) for name in ('WebTools', 'ffprobe')}

    def assert_restored():
        for name, original in originals.items():
            case.assertIs(getattr(intake_store, name), original, name + ' 未恢复')

    # cleanup 后进先出：先登记身份检查，所有依赖补丁恢复后再执行它。
    case.addCleanup(assert_restored)


def patch_intake_dependency(case, name, value):
    patcher = patch.object(intake_store, name, value)
    patcher.start()
    case.addCleanup(patcher.stop)
    return value
