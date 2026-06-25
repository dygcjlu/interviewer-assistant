# 任务 1：修复 Metrics 单例测试孤立性问题

**目标**：解决 `Metrics` 单例在测试间污染状态导致断言失败的问题。

**步骤**：

1. 在 `src/utils/metrics.py` 的 `Metrics` 类添加 `reset()` 类方法，用于测试隔离：
   ```python
   @classmethod
   def reset(cls):
       """重置单例实例（仅用于测试隔离）
       
       警告：此方法仅供测试使用，生产代码不应调用。
       """
       cls._instance = None
   ```

2. 修改 `tests/unit/test_utils.py`，在测试类中添加 `setUp` 方法：
   ```python
   def setUp(self):
       """每个测试前重置 Metrics 单例"""
       Metrics.reset()
   ```

3. 如果存在 `tearDown` 方法，也添加 `Metrics.reset()` 调用确保清理。

4. 运行单元测试验证修复：
   ```bash
   python -m pytest tests/unit/test_utils.py::TestMetrics -v
   ```
   确认 `test_asr_latency_none_when_no_samples` 等测试通过。

5. 添加新的单元测试验证 `reset()` 方法正确清空状态：
   ```python
   def test_metrics_reset(self):
       """验证 reset() 方法清空单例状态"""
       metrics = Metrics.get_instance()
       metrics.record_asr_latency(1.5)
       
       Metrics.reset()
       
       new_metrics = Metrics.get_instance()
       assert new_metrics is not metrics  # 新实例
       assert new_metrics.get_asr_latency() is None  # 无样本
   ```

6. 提交变更：
   ```bash
   git add src/utils/metrics.py tests/unit/test_utils.py
   git commit -m "fix: add Metrics.reset() for test isolation"
   ```

**验收标准**：
- ✅ `Metrics.reset()` 方法正确清空 `_instance`
- ✅ 所有 Metrics 相关测试通过且不互相干扰
- ✅ 新增测试验证 `reset()` 行为
