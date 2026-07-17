# 任务 5：修复 ASR 延迟测量逻辑

**目标**：修正单句直接 `is_final=True` 时延迟未统计的问题。

**步骤**：

1. 修改 `src/audio/transcription.py` 的 `_candidate_utterance_start` 赋值逻辑，去掉 `is_final` 条件判断，始终设置起始时间：
   ```python
   # 修复前（错误）：
   self._candidate_utterance_start = (
       segment.start_time if not segment.is_final else None
   )
   
   # 修复后（正确）：
   if segment.start_time is not None:
       self._candidate_utterance_start = segment.start_time
   ```

2. 添加代码注释说明当前测量的语义：
   ```python
   # 注意：当前测量的是「候选人发言持续时长」（从第一段到最后一段的时间差），
   # 而非严格的 ASR 系统处理延迟。这是已知限制，保留 asr_latency 名称以避免 API 破坏性变更。
   ```

3. 在 `is_final` 处理块中计算并记录延迟：
   ```python
   if segment.is_final and self._candidate_utterance_start is not None:
       # 计算并记录延迟
       latency = time.time() - self._candidate_utterance_start
       self._metrics.record_asr_latency(latency)
       self._candidate_utterance_start = None
   ```

4. 提交变更：
   ```bash
   git add src/audio/transcription.py
   git commit -m "fix: record ASR latency for single final segments"
   ```

**验收标准**：
- ✅ 单句 `is_final=True` 场景延迟正常记录
- ✅ 代码注释说明当前测量语义
- ✅ 相关测试通过
