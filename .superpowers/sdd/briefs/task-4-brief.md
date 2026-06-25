# 任务 4：为 EvalReport 添加问题覆盖率统计

**目标**：生成评价报告时包含「已覆盖 N/总计 M」统计。

**依赖**：任务 2 已完成（EvalReport 包含 question_coverage 字段）

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
       result = await self._llm_client.generate(...)
       
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

2. 提交变更：
   ```bash
   git add src/agents/eval_agent.py
   git commit -m "feat: add question coverage statistics to EvalReport"
   ```

**验收标准**：
- ✅ `EvalReport.question_coverage` 包含正确格式的覆盖率统计
- ✅ 覆盖率上下文注入到 LLM prompt
- ✅ 无问题清单时 `question_coverage` 为空字符串
