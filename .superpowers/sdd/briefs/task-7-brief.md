# 任务 7：覆盖检测改为后端触发

**目标**：从前端轮询改为后端在追问建议生成后自动触发。

**依赖**：任务 6 已完成（llm_client 注入可用）

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
                   llm_client=ctx.main_agent._llm,
                   candidate_id=ctx.session.candidate.id,
                   session=ctx.session
               )
           )
   ```

3. 保留前端 `/api/check-coverage` 端点和覆盖率展示逻辑（不删除，仅不再主动轮询）。

4. 提交变更：
   ```bash
   git add src/web/routes.py src/tools/dispatch_to_agent.py
   git commit -m "feat: trigger coverage check from backend after suggestion"
   ```

**验收标准**：
- ✅ 追问建议生成后自动触发覆盖检测
- ✅ 异步任务失败静默不影响面试
- ✅ 前端覆盖率展示正常工作
