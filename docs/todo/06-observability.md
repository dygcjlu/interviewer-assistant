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

- [ ] `GET /metrics` 返回中新增 `asr_latency_p50_ms`、`asr_latency_p99_ms` 字段
- [ ] `GET /metrics` 返回中新增 `suggestion_trigger_auto_count`、`suggestion_trigger_manual_count` 字段
- [ ] ASR 延迟在每次转写完成后自动记录，进程重启后重置
- [ ] 追问触发次数在每次触发时自动累加，自动/手动分别计数
- [ ] 所有新增字段有单元测试覆盖
