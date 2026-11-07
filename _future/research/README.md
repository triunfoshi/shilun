# Research 训练说明

本文档说明如何使用 `research/` 目录下的统一入口和子目录，完成数据集构建、模型训练、模型评估，以及二期新增的因子验证和本地研究回测。

---

## 一、目录作用

`research/` 目录用于承载训练、验证、回测和复盘相关工作区，当前建议按三层理解：

- `modeling.py`
  - 偏模型训练、数据集构建和模型评估的统一入口
- `validation/`
  - 偏因子有效性与收益证明
- `backtest_local/`
  - 偏本地研究型回测

当前根目录已包含：

- `modeling.py`
  - 统一承接 `build-dataset`、`train-regime`、`train-event`、`train-risk`、`train-entry` 和 `evaluate`
- `validation/README.md`
  - 说明因子有效性分析的职责边界
- `backtest_local/README.md`
  - 说明本地研究型回测的职责边界

二期后续原则：

- 新增因子验证脚本，优先进入 `validation/`
- 新增组合回测脚本，优先进入 `backtest_local/`
- 根目录尽量只保留通用训练和评估入口，避免再拆出多个薄脚本

---

## 二、输入数据格式

`python -m research.modeling build-dataset` 目前要求输入一个 CSV 文件，至少包含以下字段：

- `ticker`
- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`

要求：

1. `date` 可被 `pandas.to_datetime` 正常解析
2. 每个 `ticker` 的数据按时间升序排列更稳妥
3. 行情必须是未缺关键字段的日线数据

示例：

```csv
ticker,date,open,high,low,close,volume,amount
000001.SZ,2025-01-02,10.1,10.5,9.9,10.3,1200000,12360000
000001.SZ,2025-01-03,10.3,10.6,10.0,10.2,1180000,12036000
```

---

## 三、数据集构建流程

`build-dataset` 会做这些事情：

1. 计算基础行情特征
   - 趋势特征
   - 波动特征
   - 量能特征

2. 逐时间点滚动构建结构特征
   - 分型
   - 笔
   - 线段
   - 中枢
   - 背驰
   - 结构事件

3. 生成监督标签
   - `continue_10d`
   - `breakout_success`
   - `fail_5d`
   - `return_profile`
   - `drawdown_bucket`
   - `expected_return_10d`
   - `expected_drawdown_10d`

4. 合并特征与标签，生成训练数据集

---

## 四、构建数据集命令

输出 CSV：

```bash
python -m research.modeling build-dataset \
  --input ./data/daily_bars.csv \
  --output ./artifacts/dataset.csv
```

输出 Parquet：

```bash
python -m research.modeling build-dataset \
  --input ./data/daily_bars.csv \
  --output ./artifacts/dataset.parquet
```

建议优先使用 Parquet，因为：

- 速度更快
- 类型更稳定
- 对大样本更友好

---

## 五、训练 Regime 模型

状态模型目标：

- 输出 `strong_up / weak_up / range / risk_reversal` 等状态标签

训练命令：

```bash
python -m research.modeling train-regime \
  --dataset ./artifacts/dataset.parquet \
  --output-dir ./artifacts/models
```

输出产物：

- `./artifacts/models/regime_model.joblib`

---

## 六、训练 Event 模型

事件模型目标：

- `p_continue_10d`
- `p_breakout_success`
- `p_fail_5d`

训练命令：

```bash
python -m research.modeling train-event \
  --dataset ./artifacts/dataset.parquet \
  --output-dir ./artifacts/models
```

输出产物：

- `./artifacts/models/event_model.joblib`

---

## 七、训练 Risk 模型

风险模型目标：

- `expected_return_10d`
- `expected_drawdown_10d`
- `risk_level`

训练命令：

```bash
python -m research.modeling train-risk \
  --dataset ./artifacts/dataset.parquet \
  --output-dir ./artifacts/models
```

输出产物：

- `./artifacts/models/risk_model.joblib`

---

## 八、如何接回主流程

当以下三个文件同时存在时：

- `regime_model.joblib`
- `event_model.joblib`
- `risk_model.joblib`

你可以在 `PipelineConfig` 中指定模型目录：

```python
from shilun import PipelineConfig, ShilunPipeline

pipeline = ShilunPipeline(
    config=PipelineConfig(
        model_dir="./artifacts/models"
    )
)
```

此时 `pipeline` 会：

1. 优先尝试加载已训练模型
2. 若加载或推理失败，则自动回退到 `RuleFallbackModel`

所以模型接入是安全的，不会因为模型文件问题直接把主流程打崩。

---

## 九、模型评估

训练完成后，可以用评估脚本输出 JSON 报告：

```bash
python -m research.modeling evaluate \
  --dataset ./artifacts/dataset.parquet \
  --model-dir ./artifacts/models \
  --output ./artifacts/evaluation/report.json
```

如果不传 `--output`，会直接打印到终端。

当前报告包含：

1. 样本切分规模
   - `train`
   - `validation`
   - `test`

2. Regime 模型
   - `accuracy`

3. Event 模型
   - `auc`
   - `brier`
   - `accuracy_at_0_5`
   - `deciles`

4. Risk 模型
   - `mae`
   - `rmse`

5. 特征重要性
   - `regime`
   - `event`
   - `risk`

---

## 十、当前模型目录约定

建议统一目录结构：

```text
artifacts/
  dataset.parquet
  models/
    regime_model.joblib
    event_model.joblib
    risk_model.joblib
```

如果后续做版本化，建议改成：

```text
artifacts/
  datasets/
    dataset_v1.parquet
  models/
    v1/
      regime_model.joblib
      event_model.joblib
      risk_model.joblib
```

---

## 十一、特征说明

训练脚本当前使用两类特征：

1. 基础特征
   - `return_10d`
   - `return_20d`
   - `ma20_slope`
   - `ma60_slope`
   - `atr_pct`
   - `realized_vol_20`
   - `price_vs_ma20_z`
   - `price_vs_ma60_z`
   - `trend_r2_20`
   - `trend_r2_60`
   - `efficiency_ratio_20`
   - `efficiency_ratio_60`
   - `obv_slope_10`
   - `vwap_distance`
   - `breakout_volume_percentile`
   - `pullback_volume_shrink_ratio`

2. 结构特征
   - `last_bi_direction`
   - `last_bi_amplitude_pct`
   - `segment_direction`
   - `segment_amplitude_pct`
   - `segment_impulse_score`
   - `center_width_pct`
   - `center_shift_direction`
   - `leave_center_strength`
   - `return_test_depth`
   - `divergence_score`
   - `divergence_state`

其中字符串类别特征会在模型封装层自动编码。

---

## 十二、标签说明

当前训练脚本使用这些标签：

1. `continue_10d`
   - 未来 10 日是否延续

2. `breakout_success`
   - 突破后是否成功

3. `fail_5d`
   - 未来 5 日内是否快速失效

4. `return_profile`
   - 未来收益分桶

5. `drawdown_bucket`
   - 未来回撤分桶

6. `expected_return_10d`
   - 未来 10 日期望收益

7. `expected_drawdown_10d`
   - 未来 10 日期望回撤

---

## 十三、当前环境限制

当前宿主环境的 `lightgbm` 存在 OpenMP 运行时问题，表现为：

- 直接运行 LightGBM 训练时，某些环境可能因为共享内存或 OpenMP 配置报错

为避免主流程测试被环境拖死，项目中做了两件事：

1. LightGBM 集成测试默认跳过
   - 只有设置 `RUN_LIGHTGBM_TESTS=1` 才会执行

2. 主流程 `pipeline` 采用“已训练模型优先、fallback 兜底”
   - 即使真实模型不可用，也能继续运行

如果你要在本机实际训练，建议优先检查：

- `libomp` 是否正确安装
- OpenMP 线程环境是否正常
- 是否需要限制线程数

例如可以尝试：

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
python -m research.modeling train-regime \
  --dataset ./artifacts/dataset.parquet \
  --output-dir ./artifacts/models
```

---

## 十四、建议训练顺序

建议按以下顺序执行：

1. 先构建数据集
2. 训练 `regime_model`
3. 训练 `event_model`
4. 训练 `risk_model`
5. 跑 `python -m research.modeling evaluate` 输出评估报告
6. 将模型目录接入 `PipelineConfig(model_dir=...)`
7. 使用实际标的跑 `pipeline.run()` 校验输出

---

## 十五、下一步建议

当前训练层已经具备基础可用性，但后续仍建议继续完善：

1. 增加评估报告脚本
2. 增加特征重要性输出
3. 增加模型版本和数据版本登记
4. 增加概率校准模块
5. 增加 walk-forward 验证脚本
