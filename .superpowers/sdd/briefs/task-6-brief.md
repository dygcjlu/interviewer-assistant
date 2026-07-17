# 任务 6：修复 LLM 客户端直接实例化问题

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

2. 修改 `src/web/routes.py` 的 `_generate_questions_from_brief` 函数，使用注入的客户端：
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

3. 修改 `src/web/routes.py` 的 `check_question_coverage` 函数（类似处理）。

4. 修改 `src/tools/dispatch_to_agent.py` 的 `compare_candidates` 函数，通过 ctx 获取：
   ```python
   async def compare_candidates(ctx: InterviewContext, candidate_ids: list[str]) -> str:
       """对比多名候选人"""
       # 通过 ctx.main_agent 获取 llm_client
       llm_client = ctx.main_agent._llm_client
       
       # ... 其他逻辑 ...
   ```

5. 更新调用 `_generate_questions_from_brief` 的地方，传入 `request` 参数。

6. 提交变更：
   ```bash
   git add src/main.py src/web/routes.py src/tools/dispatch_to_agent.py
   git commit -m "fix: use injected llm_client instead of direct instantiation"
   ```

**验收标准**：
- ✅ `app.state.llm_client` 正确注入
- ✅ 3 处代码优先使用注入的客户端
- ✅ 集成测试中 mock 客户端生效
- ✅ fallback 逻辑保留向后兼容
