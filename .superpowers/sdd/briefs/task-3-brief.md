# 任务 3：修复 PDF 导出 candidate_id 提取错误

**目标**：修正 `export_report_pdf` 使用正确的 `candidate_id` 获取候选人姓名。

**依赖**：任务 2 已完成（EvalReport 包含 candidate_id 字段）

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

3. 提交变更：
   ```bash
   git add src/agents/eval_agent.py src/web/routes.py
   git commit -m "fix: use report.candidate_id for PDF export instead of parsing interview_id"
   ```

**验收标准**：
- ✅ `EvalAgent` 生成报告时填充 `candidate_id`
- ✅ PDF 导出正确使用 `report.candidate_id` 查询候选人姓名
- ✅ 旧报告（缺 `candidate_id`）导出时姓名为空但不报错
