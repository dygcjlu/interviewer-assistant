---
change: fix-review-findings
design-doc: docs/superpowers/specs/2026-06-25-fix-review-findings-design.md
base-ref: 3736cb058e2b8c27cba728d278857af97d85ce14
---

# 实施计划：修复 Code Review 发现的 6 个问题

## 概述

本计划修复 code review 发现的 6 个 bug：
1. Metrics 测试孤立性问题（单例污染）
2. PDF 导出 candidate_id 提取错误
3. EvalReport 缺少覆盖率统计
4. ASR 延迟测量逻辑错误
5. LLM 客户端直接实例化（绕过 mock）
6. 覆盖检测应由后端触发

总影响范围：9 个文件，包括数据模型扩展、测试修复、依赖注入修正。

## 任务清单

### 任务 1：修复 Metrics 单例测试孤立性问题

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

---

### 任务 2：扩展 EvalReport 数据模型

**目标**：为 `EvalReport` 添加 `candidate_id` 和 `question_coverage` 字段，为后续 bug 修复打基础。

**步骤**：

1. 修改 `src/models/evaluation.py`，在 `EvalReport` 类添加两个字段：
   ```python
   @dataclass
   class EvalReport:
       interview_id: str
       candidate_id: str = ""  # 新增：候选人 ID
       overall_rating: str = ""
       strengths: list[str] = field(default_factory=list)
       weaknesses: list[str] = field(default_factory=list)
       recommendation: str = ""
       summary: str = ""
       question_coverage: str = ""  # 新增：问题覆盖率统计，格式 "已覆盖 4/7"
       generated_at: str = ""
   ```

2. 修改 `src/storage/memory_module.py` 的 `get_eval_report` 方法，兼容旧数据：
   ```python
   async def get_eval_report(self, interview_id: str) -> Optional[EvalReport]:
       # ... 现有读取逻辑 ...
       if data:
           # 兼容旧数据缺 candidate_id 和 question_coverage
           if "candidate_id" not in data:
               data["candidate_id"] = ""
           if "question_coverage" not in data:
               data["question_coverage"] = ""
           return EvalReport(**data)
       return None
   ```

3. 运行相关单元测试验证数据模型变更：
   ```bash
   python -m pytest tests/unit/ -k "EvalReport" -v
   ```

4. 提交变更：
   ```bash
   git add src/models/evaluation.py src/storage/memory_module.py
   git commit -m "feat: add candidate_id and question_coverage to EvalReport"
   ```

**验收标准**：
- ✅ `EvalReport` 包含 `candidate_id` 和 `question_coverage` 字段
- ✅ `get_eval_report` 兼容旧数据（缺字段时返回默认值）
- ✅ 相关测试通过

---

### 任务 3：修复 PDF 导出 candidate_id 提取错误

**目标**：修正 `export_report_pdf` 使用正确的 `candidate_id` 获取候选人姓名。

**步骤**：

1. 修改 `src/agents/eval_agent.py` 的 `_generate_eval` 方法，从 session 获取 `candidate_id`：
   ```python
   async def _generate_eval(self, session: InterviewSession) -> EvalReport:
       # ... 现有逻辑 ...
       
       report = EvalReport(
           interview_id=session.interview_id,
           candidate_id=session.candidate_id,  # 新增：直接从 session 获取
           overall_rating=result.get("overall_rating", ""),
           # ... 其他字段 ...
       )
       return report
   ```

2. 修改 `src/web/routes.py` 的 `export_report_pdf` 函数，使用 `report.candidate_id`：
   ```python
   @app.get("/api/reports/{interview_id}/pdf")
   async def export_report_pdf(interview_id: str, request: Request):
       memory = request.app.state.memory
       report = await memory.get_eval_report(interview_id)
       
       if not report:
           raise HTTPException(status_code=404, detail="Report not found")
       
       # 修复：使用 report.candidate_id 而非从 interview_id 推断
       candidate_name = ""
       if report.candidate_id:
           profile = await memory.get_candidate_profile(report.candidate_id)
           if profile:
               candidate_name = profile.name
       
       # ... 生成 PDF 逻辑 ...
   ```

3. 手动测试验证修复（需要先完成任务 4 生成新报告）：
   - 上传简历并生成面试简报
   - 开始并完成面试
   - 生成评价报告
   - 导出 PDF，确认包含候选人姓名

4. 提交变更：
   ```bash
   git add src/agents/eval_agent.py src/web/routes.py
   git commit -m "fix: use report.candidate_id for PDF export instead of parsing interview_id"
   ```

**验收标准**：
- ✅ `EvalAgent` 生成报告时填充 `candidate_id`
- ✅ PDF 导出正确显示候选人姓名
- ✅ 旧报告（缺 `candidate_id`）导出时姓名为空但不报错

---

### 任务 4：为 EvalReport 添加问题覆盖率统计

**目标**：生成评价报告时包含「已覆盖 N/总计 M」统计。

**步骤**：

1. 修改 `src/agents/eval_agent.py` 的 `_generate_eval` 方法，添加覆盖率计算逻辑：
   ```python
   async def _generate_eval(self, session: InterviewSession) -> EvalReport:
       """生成面试评价报告"""
       # 获取问题清单计算覆盖率
       questions = await self._memory.get_questions(session.candidate_id)
       total_questions = len(questions) if questions else 0
       covered_count = len(session.questions_covered)
       coverage_text = f"已覆盖 {covered_count}/{total_questions}" if total_questions > 0 else ""
       
       # 构建 prompt（现有逻辑）
       prompt = self._build_eval_prompt(session)
       
       # 在 prompt 末尾追加覆盖率上下文
       if coverage_text:
           prompt += f"\n\n问题覆盖情况：{coverage_text}"
       
       # 调用 LLM 生成报告（现有逻辑）
       result = await self._llm_client.generate(
           system_prompt=self._system_prompt,
           user_prompt=prompt,
           response_format={"type": "json_object"}
       )
       
       # ... 解析结果 ...
       
       report = EvalReport(
           interview_id=session.interview_id,
           candidate_id=session.candidate_id,
           overall_rating=result.get("overall_rating", ""),
           strengths=result.get("strengths", []),
           weaknesses=result.get("weaknesses", []),
           recommendation=result.get("recommendation", ""),
           summary=result.get("summary", ""),
           question_coverage=coverage_text,  # 新增：填充覆盖率
           generated_at=datetime.now().isoformat()
       )
       return report
   ```

2. 运行集成测试验证覆盖率字段：
   ```bash
   python -m pytest tests/integration/ -k "eval" -v
   ```

3. 手动验证完整流程：
   - 生成包含多个问题的面试简报
   - 完成面试（部分覆盖问题）
   - 生成评价报告
   - 确认报告包含「已覆盖 X/Y」字段

4. 提交变更：
   ```bash
   git add src/agents/eval_agent.py
   git commit -m "feat: add question coverage statistics to EvalReport"
   ```

**验收标准**：
- ✅ `EvalReport.question_coverage` 包含正确格式的覆盖率统计
- ✅ 覆盖率上下文注入到 LLM prompt
- ✅ 无问题清单时 `question_coverage` 为空字符串

---

### 任务 5：修复 ASR 延迟测量逻辑

**目标**：修正单句直接 `is_final=True` 时延迟未统计的问题。

**步骤**：

1. 修改 `src/audio/transcription.py` 的 `_candidate_utterance_start` 赋值逻辑：
   ```python
   # 在 _handle_candidate_segment 方法中
   async def _handle_candidate_segment(self, segment: TranscriptionSegment):
       if segment.start_time is not None:
           # 修复：始终设置起始时间，去掉 is_final 判断
           self._candidate_utterance_start = segment.start_time
       
       # ... 其他逻辑 ...
       
       if segment.is_final and self._candidate_utterance_start is not None:
           # 计算并记录延迟
           latency = time.time() - self._candidate_utterance_start
           self._metrics.record_asr_latency(latency)
           self._candidate_utterance_start = None
   ```

2. 添加代码注释说明当前测量的语义：
   ```python
   # 注意：当前测量的是「候选人发言持续时长」（从第一段到最后一段的时间差），
   # 而非严格的 ASR 系统处理延迟。这是已知限制，保留 asr_latency 名称以避免 API 破坏性变更。
   ```

3. 编写单元测试验证修复（在 `tests/unit/test_audio.py` 或新建测试文件）：
   ```python
   async def test_asr_latency_single_final_segment():
       """验证单句直接 is_final=True 时延迟正常记录"""
       # Mock TranscriptionSegment with is_final=True
       segment = TranscriptionSegment(
           text="完整句子",
           is_final=True,
           start_time=time.time()
       )
       
       # ... 调用 _handle_candidate_segment ...
       
       # 验证延迟已记录
       latency = Metrics.get_instance().get_asr_latency()
       assert latency is not None
       assert latency >= 0
   ```

4. 运行音频相关测试：
   ```bash
   python -m pytest tests/unit/test_audio.py -v
   ```

5. 提交变更：
   ```bash
   git add src/audio/transcription.py tests/unit/test_audio.py
   git commit -m "fix: record ASR latency for single final segments"
   ```

**验收标准**：
- ✅ 单句 `is_final=True` 场景延迟正常记录
- ✅ 代码注释说明当前测量语义
- ✅ 相关测试通过

---

### 任务 6：修复 LLM 客户端直接实例化问题

**目标**：修正 3 处代码绕过 app 注入的 LLM 客户端，确保集成测试 mock 生效。

**步骤**：

1. 修改 `src/main.py`，将 `llm_client` 注入到 `app.state`：
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       settings = Settings()
       
       # ... 初始化各组件 ...
       llm_client = OpenAICompatibleClient(settings)
       
       # 注入到 app.state 供 routes 使用
       app.state.llm_client = llm_client
       app.state.memory = memory
       # ... 其他注入 ...
       
       yield
       
       # ... 清理逻辑 ...
   ```

2. 修改 `src/web/routes.py` 的 `_generate_questions_from_brief` 函数：
   ```python
   async def _generate_questions_from_brief(
       candidate_id: str, 
       brief: str, 
       memory: MemoryModule,
       request: Request  # 新增参数
   ):
       """生成面试问题清单"""
       settings = Settings()
       
       # 使用注入的 llm_client，fallback 到直接实例化
       llm = getattr(request.app.state, "llm_client", None)
       if not llm:
           llm = OpenAICompatibleClient(settings)
       
       # ... 其他逻辑 ...
   ```

3. 修改 `src/web/routes.py` 的 `check_question_coverage` 函数：
   ```python
   @app.post("/api/check-coverage")
   async def check_question_coverage(req: CoverageCheckRequest, request: Request):
       settings = Settings()
       memory = request.app.state.memory
       
       # 使用注入的 llm_client
       llm = getattr(request.app.state, "llm_client", None)
       if not llm:
           llm = OpenAICompatibleClient(settings)
       
       # ... 其他逻辑 ...
   ```

4. 修改 `src/tools/dispatch_to_agent.py` 的 `compare_candidates` 函数：
   ```python
   async def compare_candidates(ctx: InterviewContext, candidate_ids: list[str]) -> str:
       """对比多名候选人"""
       # 通过 ctx.main_agent 获取 llm_client
       llm_client = ctx.main_agent._llm_client
       
       # ... 其他逻辑 ...
   ```

5. 更新调用 `_generate_questions_from_brief` 的地方，传入 `request` 参数：
   ```python
   # 在 start_interview 或相关 route 中
   await _generate_questions_from_brief(
       candidate_id=candidate_id,
       brief=brief,
       memory=memory,
       request=request  # 传入 request
   )
   ```

6. 编写或更新集成测试验证 mock 生效：
   ```python
   async def test_llm_client_injection():
       """验证 routes 使用注入的 LLM 客户端"""
       # Mock llm_client
       mock_llm = MockLLMClient()
       app.state.llm_client = mock_llm
       
       # 调用 _generate_questions_from_brief
       # ... 验证 mock_llm 被调用 ...
   ```

7. 运行集成测试：
   ```bash
   python -m pytest tests/integration/ -v
   ```

8. 提交变更：
   ```bash
   git add src/main.py src/web/routes.py src/tools/dispatch_to_agent.py tests/integration/
   git commit -m "fix: use injected llm_client instead of direct instantiation"
   ```

**验收标准**：
- ✅ `app.state.llm_client` 正确注入
- ✅ 3 处代码优先使用注入的客户端
- ✅ 集成测试中 mock 客户端生效
- ✅ fallback 逻辑保留向后兼容

---

### 任务 7：覆盖检测改为后端触发

**目标**：从前端轮询改为后端在追问建议生成后自动触发。

**步骤**：

1. 在 `src/web/routes.py` 提取覆盖检测逻辑为独立函数：
   ```python
   async def _auto_check_coverage(
       memory: MemoryModule,
       llm_client,
       candidate_id: str,
       session: InterviewSession
   ):
       """自动检测问题覆盖情况（后端触发）"""
       try:
           questions = await memory.get_questions(candidate_id)
           if not questions:
               return
           
           # 获取完整对话历史
           exchanges = session.conversation_history
           if not exchanges:
               return
           
           # 调用 LLM 检测覆盖
           # ... 复用 check_question_coverage 的逻辑 ...
           
       except Exception as e:
           # 静默失败，不影响主流程
           logger.warning(f"Auto coverage check failed: {e}")
   ```

2. 修改 `src/tools/dispatch_to_agent.py` 的 `_apply_side_effects` 方法：
   ```python
   async def _apply_side_effects(
       self,
       result_type: str,
       result_content: str,
       ctx: InterviewContext
   ):
       """应用副作用（录音、建议、覆盖检测）"""
       # ... 现有逻辑 ...
       
       if result_type == "suggestion":
           # 异步触发覆盖检测
           asyncio.create_task(
               _auto_check_coverage(
                   memory=ctx.memory,
                   llm_client=ctx.main_agent._llm_client,
                   candidate_id=ctx.session.candidate_id,
                   session=ctx.session
               )
           )
   ```

3. 保留前端 `/api/check-coverage` 端点和覆盖率展示逻辑（不删除，仅不再主动轮询）。

4. 手动测试验证后端触发：
   - 启动面试
   - 候选人回答问题
   - 观察日志确认追问建议生成后自动触发覆盖检测
   - 前端覆盖率展示更新

5. 提交变更：
   ```bash
   git add src/web/routes.py src/tools/dispatch_to_agent.py
   git commit -m "feat: trigger coverage check from backend after suggestion"
   ```

**验收标准**：
- ✅ 追问建议生成后自动触发覆盖检测
- ✅ 异步任务失败静默不影响面试
- ✅ 前端覆盖率展示正常工作

---

### 任务 8：完整集成测试与验证

**目标**：验证所有 6 个 bug 修复后系统正常工作。

**步骤**：

1. 运行完整测试套件：
   ```bash
   # 单元测试
   python -m pytest tests/unit/ -v
   
   # 集成测试
   python -m pytest tests/integration/ -v
   ```

2. 手动验证完整面试流程：
   
   **准备阶段**：
   - 启动服务：`python -m src.main`
   - 上传候选人简历 PDF
   - 验证简历解析为 Markdown
   
   **面试简报**：
   - 生成面试简报（触发问题清单生成）
   - 确认问题清单正确保存（验证 Bug 5 修复）
   
   **面试过程**：
   - 开始面试并实时转写
   - 候选人回答问题
   - 验证追问建议自动生成
   - 验证覆盖检测自动触发（观察日志或前端覆盖率更新）（Bug 6）
   - 验证 ASR 延迟指标正常记录（Bug 4）
   
   **面试结束**：
   - 结束面试
   - 生成评价报告
   - 验证报告包含 `candidate_id` 字段（Bug 2）
   - 验证报告包含「已覆盖 X/Y」统计（Bug 3）
   
   **PDF 导出**：
   - 导出评价报告为 PDF
   - 验证 PDF 包含候选人姓名（Bug 2）
   - 验证 PDF 包含覆盖率统计（Bug 3）

3. 验证 Metrics 测试孤立性（Bug 1）：
   ```bash
   # 连续运行多次确认无污染
   python -m pytest tests/unit/test_utils.py::TestMetrics -v -x 3
   ```

4. 检查日志确认无异常错误：
   ```bash
   tail -n 100 logs/app.log
   ```

5. 记录验证结果到 `docs/superpowers/reports/2026-06-25-fix-review-findings-verify.md`（简要记录测试结果和检查点）。

6. 提交验证报告：
   ```bash
   git add docs/superpowers/reports/2026-06-25-fix-review-findings-verify.md
   git commit -m "docs: add verification report for bug fixes"
   ```

**验收标准**：
- ✅ 所有单元测试和集成测试通过
- ✅ 手动验证 6 个 bug 均已修复
- ✅ 完整面试流程正常工作
- ✅ 无回归问题

---

## 实施顺序说明

任务顺序已按依赖关系排列：
1. **任务 1**（Metrics 孤立性）独立，可优先修复
2. **任务 2**（数据模型扩展）为任务 3、4 打基础
3. **任务 3**（PDF 导出）依赖任务 2
4. **任务 4**（覆盖率统计）依赖任务 2
5. **任务 5**（ASR 延迟）独立，可并行
6. **任务 6**（LLM 注入）独立，可并行
7. **任务 7**（覆盖检测触发）依赖任务 6（需要 `llm_client` 注入）
8. **任务 8**（集成验证）必须最后执行

## 风险与注意事项

### 破坏性变更
- `EvalReport` 数据模型扩展可能导致旧代码依赖问题，已通过默认值和兼容性处理降低影响
- 旧的评价报告缺 `candidate_id` 字段，PDF 导出时姓名为空（可接受）

### 测试覆盖
- Bug 1、4、5 有明确的单元/集成测试验证
- Bug 2、3、6 主要依赖手动验证（涉及完整面试流程）

### 技术债务
- `dispatch_to_agent` 访问 `_llm_client` 私有属性（可控，后续可重构）
- `Metrics.reset()` 方法仅供测试使用（已在注释中说明）

### 异步任务失败
- 覆盖检测异步任务失败静默（不影响主流程），但无可见日志（已知限制）

## 估时

- 任务 1：20 分钟
- 任务 2：15 分钟
- 任务 3：20 分钟
- 任务 4：25 分钟
- 任务 5：20 分钟
- 任务 6：30 分钟
- 任务 7：25 分钟
- 任务 8：30 分钟

**总计**：约 3 小时

## 完成标准

所有 6 个 bug 修复完成且通过验证：
- ✅ Metrics 测试孤立性问题解决
- ✅ PDF 导出包含候选人姓名
- ✅ 评价报告包含问题覆盖率统计
- ✅ ASR 延迟正确记录单句场景
- ✅ 集成测试中 mock LLM 客户端生效
- ✅ 覆盖检测由后端自动触发
- ✅ 所有测试通过
- ✅ 无回归问题
