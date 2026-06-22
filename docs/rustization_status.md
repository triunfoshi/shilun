# Rust 化架构落地状态

本文档记录“架构内容 Rust 化”的当前落地边界，避免把四期方案停留在概念层。

## 1. 当前落地

| 项目 | 状态 | 文件 |
| --- | --- | --- |
| Rust crate 骨架 | 已完成 | `shilun-core/Cargo.toml` |
| Rust 健康入口 | 已完成 | `shilun-core/src/main.rs` |
| Rust PART1 状态枚举 | 已完成 | `shilun-core/src/market_part1.rs` |
| Rust PART1 分类函数 | 已完成 | `classify_market_permission(...)` |
| Python 生产实现 | 已完成 | `shilun/market/part1.py` |
| Python API | 已完成 | `GET /api/v1/market/permission` |
| JSON 契约 | 已完成 | `docs/contracts/market_part1.schema.json` |
| 详细规则文档 | 已完成 | `docs/market_part1_state_machine.md` |

## 2. 这次 Rust 化的边界

本轮没有把整个 Python 系统重写成 Rust。原因：

```text
策略、因子、模型和解释层仍在快速变化，继续放在 Python。
系统内核、状态机、任务状态、数据契约适合先进入 Rust。
```

因此当前采用“双轨”：

```text
Python = 当前生产能力
Rust = 强类型内核和未来 sidecar 的契约骨架
```

## 3. 当前 Rust 与 Python 的职责划分

| 层 | Python 当前职责 | Rust 当前职责 | 后续方向 |
| --- | --- | --- | --- |
| PART1 状态机 | 计算指标、评分、API 输出 | 固化权限枚举和分类规则 | Rust 接管稳定评分函数 |
| 数据读取 | Mongo-first 读取日线和 stock_basic | 暂无 | Rust sidecar 接 Mongo repository |
| 页面/API | FastAPI 控制台 | 暂无 | Rust 暴露只读状态 API |
| 任务状态 | 仍在 Python 任务内部 | 暂无 | Rust 管 `task_runs`/`push_runs` |
| 策略研究 | 保留 | 不迁移 | Python strategy worker |

## 4. 下一步 Rust 化建议

P0：

1. 新增 `task_runs`、`push_runs` JSON schema。
2. Python 现有同步/快照/推送任务先写任务状态。
3. Rust `shilun-core` 增加 `TaskRun`、`PushRun` 强类型结构。

P1：

1. Rust sidecar 增加 `/health`。
2. Rust sidecar 增加 `/market/permission/rules` 只读规则说明。
3. Rust sidecar 增加 `/data/status`，只读 Mongo 数据状态。

P2：

1. Rust 接管数据同步编排。
2. Python 只保留策略分析 worker。
3. 页面长任务改为“创建任务 -> 轮询任务状态”，不再阻塞 HTTP 请求。

## 5. 验收标准

短期验收：

```text
cargo test --manifest-path shilun-core/Cargo.toml
python -m unittest tests.test_market_part1 tests.test_ui_route
```

中期验收：

```text
Rust / Python 对同一组 PART1 scores 输出完全一致的 market_permission。
Python API 返回结果满足 docs/contracts/market_part1.schema.json。
```
