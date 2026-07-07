# #6 可观测性补强

## 目标

在现有 `GET /metrics` 端点基础上，补充 ASR 延迟和追问触发次数指标，完善生产意识信号。

## 范围

- 现有端点 `GET /metrics` 已返回 LLM 累积指标（`src/utils/metrics.py`）
- 新增指标：
  - ASR 延迟（P50、P99，单位毫秒）
  - 追问触发次数（自动触发 / 手动触发分别统计）
- 输出格式保持简单 JSON，不引入 Prometheus 格式

## 验收条件

- [x] `GET /metrics` 返回中新增 `asr_latency_p50_ms`、`asr_latency_p99_ms` 字段
  - 实现：`src/utils/metrics.py` `Metrics.to_dict()`（第 87–101 行）返回 `asr_latency_p50_ms`/`asr_latency_p99_ms`；路由 `src/web/routes.py` 第 1049–1054 行 `GET /metrics` 直接透传 `Metrics.get().to_dict()`。
- [x] `GET /metrics` 返回中新增 `suggestion_trigger_auto_count`、`suggestion_trigger_manual_count` 字段
  - 实现：同上 `to_dict()` 中包含这两个字段。
- [x] ASR 延迟在每次转写完成后自动记录，进程重启后重置
  - 实现：`src/audio/transcription.py` 约第 71–84 行，转写最终结果产生时调用 `Metrics.get().record_asr_latency(elapsed_ms)`；`Metrics` 为进程内单例（`src/utils/metrics.py` 第 34–38 行），无持久化，进程重启自然重置。单元测试 `tests/unit/test_transcription.py::test_single_final_segment_records_asr_latency` 覆盖记录路径。
- [x] 追问触发次数在每次触发时自动累加，自动/手动分别计数
  - 实现：自动触发计数见 `src/audio/trigger.py` 第 112 行 `Metrics.get().record_suggestion_trigger("auto")`；手动触发计数见 `src/web/routes.py` 第 473 行 `Metrics.get().record_suggestion_trigger("manual")`；`record_suggestion_trigger()`（`src/utils/metrics.py` 第 56–60 行）按 `mode` 分别累加 `suggestion_trigger_auto_count`/`suggestion_trigger_manual_count`。
- [x] 所有新增字段有单元测试覆盖
  - 实现：`tests/unit/test_utils.py` 第 177–215 行覆盖 `record_asr_latency`、`asr_latency_p50_ms`/`asr_latency_p99_ms`（含无样本时为 `None` 的分支）、`record_suggestion_trigger("auto"/"manual")` 及其在 `to_dict()` 中的计数。
