# shilun-core

`shilun-core` 是石论四期 Rust 化的第一块骨架，目标是先把稳定、低频变化、需要强类型约束的系统内核从 Python 策略层中分离出来。

当前状态：

- 已建立 Rust crate。
- 已落地 PART1 大盘权限的强类型枚举、评分结构和状态分类函数。
- 暂不连接 Mongo，也不启动 HTTP sidecar，避免在契约未稳定前制造双写复杂度。

短期迁移顺序：

1. 用 `docs/contracts/market_part1.schema.json` 锁定 Python API 与 Rust 内核之间的 JSON 契约。
2. 保持 Python `shilun/market/part1.py` 为当前生产实现。
3. Rust 先沉淀稳定状态机、任务状态、数据契约。
4. 后续再接入 HTTP sidecar：`/health`、`/data/status`、`/tasks`、`/market/permission`。

本地验证：

```bash
cargo test --manifest-path shilun-core/Cargo.toml
```
