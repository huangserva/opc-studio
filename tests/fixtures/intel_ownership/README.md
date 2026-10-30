# 所有权迁移/回滚真实源码夹具

干净克隆不得依赖私有 coordination 目录；以下均从本次开工已存在的原始源码逐字节复制，不改函数实现、不拼造旧版本。无数据库、配置或凭据数据。

- `intel_store_ce87.py` 来源 `coordination/backups/fixes-intel-lease-b-L/before/intel_store.py`，SHA256 `ce87b0cc702023f22cec55362995f4c61e3d3b508acea5931614a5b188f219f1`。
- `intel_lease_rollback.py` 来源 `coordination/rollback/intel-lease-b-L2-rollback.py`，SHA256 `b58409680896268f740706f34aa62d5a9cafee368d2073ae8507f0051042a25b`。
