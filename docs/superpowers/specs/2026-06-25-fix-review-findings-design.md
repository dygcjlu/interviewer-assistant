---
comet_change: fix-review-findings
role: technical-design
canonical_spec: openspec
---

# Bug 修复：Code Review 发现的 6 个问题

## 背景

coding agent 实现 6 个 todo 功能（commit `3736cb0`）后，code review 发现 3 个必修 bug、3 个应修 bug，以及 2 处需求未完全实现。这些问题若不修复，会导致 CI 偶发失败、PDF 导出缺失候选人姓名、EvalReport 缺少覆盖率字段、ASR 指标语义错误，以及集成测试中 mock 无法生效。

## 技术方案

### Bug 1: Metrics 测试孤立性

**问题根因**：`Metrics` 采用单例模式，新增测试用例在同一实例上累加样本和计数，导致 `test_asr_latency_none_when_no_samples` 等依赖初始状态的断言必然失败。

**修复方案**：
- 在 `src/utils/metrics.py` 的 `Metrics` 类添加 `reset()` 类方法
- 方法清空 `_instance` 为 `None`，下次调用 `get_instance()` 时重新创建实例
- 在 `tests/unit/test_utils.py` 的测试类添加 `setUp()` 方法调用 `Metrics.reset()`
- 如有 `tearDown()` 方法也调用 `Metrics.reset()` 确保清理

**实现细节**：
```python
@classmethod
def reset(cls):
    """重置单例实例（仅用于测试隔离）"""
    cls._instance = None
```

### Bug 2: PDF 导出 candidate_id 提取错误

**问题根因**：`src/web/routes.py` 的 `export_report_pdf` 使用 `interview_id.split("-")[0]` 推断 `candidate_id`，但 `interview_id` 是 UUID 格式，第一段不是 `candidate_id`，导致候选人姓名永远为空。

**修复方案 A（采纳）**：扩展 `EvalReport` 数据模型
- 在 `src/models/evaluation.py` 的 `EvalReport` 添加 `candidate_id: str` 字段
- `src/agents/eval_agent.py` 的 `_generate_eval` 方法从 `session.candidate_id` 直接填充
- `export_report_pdf` 使用 `report.candidate_id` 查询候选人姓名
- `src/storage/memory_module.py` 的 `get_eval_report` 兼容旧数据缺 `candidate_id` 时返回空字符串

**数据兼容性**：已生成的旧报告缺 `candidate_id` 字段，PDF 导出时候选人姓名仍为空（可接受），新报告正常。

### Bug 3: EvalReport 缺少覆盖率统计

**问题根因**：`EvalAgent` 生成报告时未读取 `questions.json`，报告中缺少「已覆盖 N/总计 M」字段，违反需求验收条件。

**修复方案**：
- 在 `src/models/evaluation.py` 的 `EvalReport` 添加 `question_coverage: str = ""` 字段（格式：`"已覆盖 4/7"`）
- `src/agents/eval_agent.py` 的 `_generate_eval` 方法调用 `memory.get_questions(candidate_id)` 获取问题清单
- 通过 `InterviewSession.questions_covered` 计算覆盖率（已覆盖数 / 总问题数）
- 在 LLM prompt 末尾追加覆盖率上下文供 `summary` 参考
- 将覆盖率统计写入 `EvalReport.question_coverage` 字段

**实现位置**：`_generate_eval` 方法在调用 LLM 前注入覆盖率数据到 prompt。

### Bug 4: ASR 延迟测量逻辑错误

**问题根因**：`src/audio/transcription.py` 中，`_candidate_utterance_start` 的赋值逻辑：
```python
self._candidate_utterance_start = (
    segment.start_time if not segment.is_final else None
)
```
导致单句直接 `is_final=True` 时 `_candidate_utterance_start` 被设为 `None`，该发言永远不被统计。

**修复方案**：
- 去掉条件判断，始终设置 `_candidate_utterance_start = segment.start_time`
- 在代码注释中说明当前指标测量的是「候选人发言持续时长」而非严格的 ASR 处理延迟
- 保持 `asr_latency` API 名称不变（避免破坏性变更）

**已知限制**：当前测量的是从第一段到最后一段的时间差（发言持续时长），不是 ASR 系统的处理延迟。这是已知限制，在代码注释中说明。

### Bug 5: LLM 客户端直接实例化

**问题根因**：3 处代码直接 `new OpenAICompatibleClient(settings)` 绕过 app 注入的客户端，导致集成测试中 `MockLLMClient` 无法生效。

**修复方案**：
- `src/main.py` 添加 `app.state.llm_client = llm_client`
- `src/web/routes.py` 两处（`_generate_questions_from_brief` 和 `check_question_coverage`）改为：
  ```python
  llm = getattr(request.app.state, "llm_client", None)
  if not llm:
      llm = OpenAICompatibleClient(settings)  # fallback
  ```
- `src/tools/dispatch_to_agent.py` 的 `compare_candidates` 通过 `ctx.main_agent._llm_client` 获取

**访问私有属性说明**：`dispatch_to_agent` 已依赖 `ctx` 的其他内部组件，访问 `_llm_client` 风险可控。

### Bug 6: 覆盖检测应由后端触发

**问题根因**：覆盖检测依赖前端轮询 `/api/check-coverage`，若 UI 未激活则不触发。

**修复方案**：
- 提取覆盖检测逻辑为独立函数（供 routes 和 dispatch 共用）
- `src/tools/dispatch_to_agent.py` 的 `_apply_side_effects` 在 `result_type == "suggestion"` 后异步触发覆盖检测：
  ```python
  asyncio.create_task(_auto_check_coverage(ctx, candidate_id))
  ```
- 前端保留覆盖率展示逻辑，不再驱动主动触发

**实现细节**：异步任务失败时静默（不影响主流程面试继续）。

## 关键取舍

### 数据模型扩展
- **取舍**：扩展 `EvalReport` 添加 `candidate_id` 和 `question_coverage` 是破坏性变更
- **理由**：避免复杂的 ID 提取逻辑，直接在数据模型中包含所需字段
- **兼容性**：通过默认值和读取时兼容处理降低影响

### ASR 延迟语义
- **取舍**：保留 `asr_latency` API 名称，不改为 `utterance_duration`
- **理由**：避免 API 破坏性变更，在代码注释中说明实际语义
- **限制**：当前测量的是发言持续时长，不是严格的 ASR 处理延迟

### 私有属性访问
- **取舍**：`dispatch_to_agent` 访问 `ctx.main_agent._llm_client`（私有属性）
- **理由**：该模块已深度耦合 `ctx` 内部结构，统一通过上下文获取依赖
- **风险**：可控，后续可重构为显式依赖注入

### 异步覆盖检测
- **取舍**：使用 `asyncio.create_task` 异步触发，失败静默
- **理由**：覆盖检测失败不应阻塞面试主流程
- **风险**：任务失败无可见日志，但不影响核心功能

## 测试策略

### 单元测试
1. **Metrics 孤立性**：
   - 修改 `tests/unit/test_utils.py`，添加 `setUp` 调用 `Metrics.reset()`
   - 运行 `test_asr_latency_none_when_no_samples` 两次验证不互相干扰
   - 新增测试验证 `Metrics.reset()` 清空样本和计数

2. **EvalReport 字段**：
   - 验证 `candidate_id` 和 `question_coverage` 字段存在
   - 验证旧数据兼容（缺 `candidate_id` 时返回空字符串）

### 集成测试
1. **LLM 客户端注入**：
   - Mock `llm_client` 注入到 `app.state`
   - 验证 `_generate_questions_from_brief`、`check_question_coverage`、`compare_candidates` 三处使用注入的 mock 实例

2. **ASR 延迟统计**：
   - 模拟单句 `is_final=True` 场景，验证延迟正常记录

3. **覆盖检测后端触发**：
   - Mock `dispatch_to_agent._apply_side_effects`，验证在 `suggestion` 后触发异步覆盖检测

### 手动验证
完整面试流程：
1. 上传简历
2. 生成面试简报（触发问题清单生成）
3. 开始面试并实时转写
4. 结束面试并生成评价报告
5. 导出 PDF

检查点：
- PDF 包含候选人姓名（Bug 2）
- 报告包含覆盖率统计（Bug 3）
- ASR 延迟正常记录（Bug 4）
- 覆盖检测自动触发（Bug 6）

## 风险与限制

### 已知风险
1. **旧 EvalReport 数据**：已保存的报告缺 `candidate_id` 字段，PDF 导出时候选人姓名仍为空（可接受，新报告正常）
2. **ASR 延迟语义**：实际测量的是发言持续时长，不是 ASR 处理延迟（已知限制，在代码注释中说明）
3. **异步任务失败**：`asyncio.create_task` 失败静默，但不影响主流程（面试继续）

### 技术债务
1. **私有属性访问**：`dispatch_to_agent` 访问 `_llm_client` 私有属性，后续可重构为显式依赖注入
2. **单例模式测试孤立性**：`Metrics.reset()` 是为测试添加的方法，生产代码不应调用

## 影响范围

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/utils/metrics.py` | 新增方法 | 添加 `Metrics.reset()` 类方法 |
| `tests/unit/test_utils.py` | 修改 | setUp 调用 `reset()`，修复断言依赖顺序问题 |
| `src/models/evaluation.py` | 扩展字段 | 添加 `candidate_id`、`question_coverage` 字段 |
| `src/agents/eval_agent.py` | 修改 | 生成报告时注入问题覆盖率和 candidate_id |
| `src/web/routes.py` | 修改 | 修复 `export_report_pdf` candidate_id 提取；修复 2 处 LLM 直接实例化 |
| `src/audio/transcription.py` | 修改 | 修正 ASR 延迟测量逻辑 |
| `src/tools/dispatch_to_agent.py` | 修改 | 修复 LLM 直接实例化；覆盖检测改为后端触发 |
| `src/main.py` | 新增 | 添加 `app.state.llm_client` 注入 |
| `src/storage/memory_module.py` | 修改 | `get_eval_report` 兼容旧数据缺 `candidate_id` |

总计 **9 个文件** 受影响。
