---
comet_change: opensource-optimization-rollout
role: technical-design
canonical_spec: openspec
---

# 开源化优化落地 · 技术设计

## Context

`docs/opensource-review-report.md` 评审确认：项目核心技术能力已达到"能拿出手"水准，但工程规范落地（lint/覆盖率门禁缺失）、测试盲区（`main_agent.py`/`routes.py`/`pdf_export.py`）、若干功能缺陷（追问建议非真流式、候选人去重依赖文件名、token 预算用字符数估算）拉低了开源项目质感。`docs/opensource-optimization-plan.md` 已与项目负责人逐项确认本轮四阶段、11 项实施范围。用户要求对追问流式输出、候选人去重提示、工具调用可视化 3 项前端可感知行为补充基于 `cursor-ide-browser` 的端到端浏览器测试。

本设计基于 OpenSpec `proposal.md`/`design.md`/`specs/*/spec.md`（详见 `openspec/changes/opensource-optimization-rollout/`），在 brainstorming 阶段对关键假设做了代码级验证，修正了 2 处与实际代码不符的设计假设（见"设计决策"D5、D7）。

## Goals / Non-Goals

**Goals：**
- 按四阶段顺序（工程规范 → 功能小修复 → 测试补齐与重构 → 工具调用可视化）落地全部 11 项已确认工作
- 全量格式化提交与行为改动分离，便于 review
- 覆盖率门禁从 60% 起步接入 CI
- 追问流式、候选人去重三选一、工具调用可视化卡片 3 项前端行为，除自动化测试外补充 `cursor-ide-browser` 端到端验证
- `memory_module.py` 拆分保持对外接口不变

**Non-Goals：**
- 不做 `ui.py` 整体测试补齐与拆分
- 不做 Docker 化降级体验、Prompt 版本回溯、CI ubuntu matrix
- 不在本轮把覆盖率门槛一次性提到 80%

## 技术方案

### 1. 工程规范落地（阶段一）

`pyproject.toml` 统一配置 `ruff`/`black`/`isort`（88 列、双引号、`isort profile=black`）。执行顺序：先跑 `ruff check --fix .` + `black .` + `isort .` 产生一次独立的格式化提交，再开始阶段二的行为类改动，避免两类 diff 混在一起影响 review。CI（`.github/workflows/ci.yml`）新增 `ruff check .` 步骤，测试步骤改为 `pytest tests/unit tests/integration --cov=src --cov-report=term-missing --cov-fail-under=60`（60% 起步，非最终 80% 目标）。

`test_volc_stt.py` 的环境隔离问题通过 `monkeypatch.setenv` 显式清空 `VOLC_APP_ID`/`VOLC_ACCESS_TOKEN` 等变量解决，避免本地 `.env` 的 ambient 配置影响测试结果。

### 2. Token 精确计数（阶段二）

`context.py` 的 `_estimate_tokens()`、`token_usage` 属性、tail 边界计算，以及 `eval_agent.py` 的 `estimated_tokens` 计算，统一改用 `llm/client.py` 已有的 `count_tokens(messages: list[Message]) -> int`。

**关键实现约束**（brainstorming 阶段代码调研确认）：`count_tokens()` 要求 `Message` 对象列表输入，且每条消息叠加 `_PER_MESSAGE_OVERHEAD_TOKENS`。`_estimate_tokens()` 原先直接对裸字符串（`self._summary`、各轮 `interviewer_text`/`candidate_text`）计数，若逐段各自包一层 `Message` 再分别调用 `count_tokens()`，会导致 overhead 被重复叠加、计数虚高于预期。因此改造时需要在 `ContextManager` 内部构造一份"虚拟消息列表"（summary 一条 + 每轮一条），整体调用一次 `count_tokens()`。

精确计数后数值通常小于原估算（尤其中文场景），原有"8 轮触发压缩"等阈值需要用中英混杂真实对话数据回归验证，若触发时机明显偏移则同步调整常量并在改动说明中记录原因。

### 3. 追问建议真流式输出（阶段二）

`InterviewAgent.generate_suggestion()` 从调用 `llm_client.chat()`（非流式）改为调用已存在的 `llm_client.chat_stream()`，逐 token 通过 `suggestion_delta` 事件推送。

需要同步处理：
1. 日志统计（`prompt_tokens`/`completion_tokens`）从流式响应的累计结果获取，而不是依赖非流式响应体的 usage 字段
2. `asyncio.CancelledError` 取消逻辑需要在流式生成器场景下重新验证——`generate_suggestion()` 已有"取消上一次进行中的流式请求"逻辑（`_current_stream_task`），改为流式输出后需确认该取消路径在新的 `chat_stream()` 调用下依然正确工作，不产生悬挂任务或状态不一致

### 4. 候选人去重改为按真实姓名 + 三选一弹窗（阶段二）

**当前实现**（已验证）：`upload_resume()`（`src/web/routes.py`）在上传阶段用文件名派生的 `safe_stem` 通过 `get_candidate_by_name(safe_stem)` 判定重复，命中时返回 409 `duplicate_candidate` 错误。

**目标行为**：去重判定后移到简历解析完成、拿到真实姓名之后（`dispatch_to_agent` 触发的解析流程完成时）。检测到重名后，弹窗提示面试官"覆盖已有档案 / 保留两份独立档案 / 取消本次上传"三选一（**已与用户确认**，复用现有 409 提示的前端交互模式）：
- **覆盖已有档案**：用新解析数据覆盖已有候选人记录
- **保留两份独立档案**：为新简历创建一个姓名相同但档案独立的新候选人记录
- **取消本次上传**：不创建也不修改任何候选人档案

涉及 `src/web/routes.py`（去重校验点后移）、`src/storage/memory_module.py`（`get_candidate_by_name` 相关方法）、`src/web/ui.py`（三选一弹窗交互）。

### 5. PDF 导出中文渲染测试（阶段二）

针对 `src/utils/pdf_export.py`（当前 0% 覆盖）编写"生成 → 回读校验"集成测试：用已有依赖 `pymupdf` 读取生成的 PDF 提取文本，断言中文内容正确出现、无乱码字符。

### 6. MainAgent + routes 测试补齐、memory_module 拆分（阶段三）

- `main_agent.py`：补充工具调用循环、`_trim_history` 边界处理（含孤儿 tool 消息场景）、Memory Nudge 触发条件的单元测试
- `routes.py`：补充剩余错误分支（404/409）测试
- `memory_module.py` 按职责拆分为 `candidate_store.py`（候选人 CRUD）、`interview_store.py`（面试生命周期 + WAL）、`eval_store.py`（评价报告持久化），`MemoryModule` 保留为 Facade，对外方法签名与行为不变。**执行顺序**：先完成阶段二对 `memory_module.py`（候选人去重）的改动并测试通过，再进行拆分，避免两者同时改动同一文件造成冲突排查困难。拆分前后运行完整单元 + 集成测试套件作为回归基线。

### 7. Agent 工具调用可视化（阶段四，方案已修正）

**代码调研发现**（brainstorming 阶段验证，修正了 OpenSpec open 阶段的原始假设）：`ui.py` 的 `_render_tool_call_row()`（第 978 行）已经渲染一行"药丸型"提示（`⚙ 工具名 · 参数摘要`），但后端 `MainAgent`（`main_agent.py` 第 286 行）的工具调用循环只在发起调用时 `yield {"type": "tool_call", ...}`，工具执行结果（`result_str`）只写入内部消息历史供 LLM 使用，从未推送给前端。`docs/arc/api.md` 目前也只记录了 `tool_call` 一种事件。

**采用方案**（已与用户确认为方案A）：
1. `main_agent.py` 工具调用循环在拿到 `result_str` 后，新增推送 `{"type": "tool_result", "tool_call_id": ..., "name": ..., "result_summary": ..., "success": ...}` 事件
2. `docs/arc/api.md` 的 SSE 事件类型表补充 `tool_result` 说明
3. `ui.py` 的 `_chat_stream()` 增加对 `tool_result` 事件的解析分支
4. 将现有 `_render_tool_call_row()` 升级为可展开折叠卡片（`ui.expansion`）：收到 `tool_call` 时先渲染"进行中"状态卡片，收到对应 `tool_result` 后按 `tool_call_id` 原地更新为最终状态（含结果摘要/失败标记），不新增重复卡片
5. 一次对话中的多次工具调用按时间顺序展示为独立卡片

### 8. 端到端浏览器测试策略

3 项前端可感知行为（追问流式展示、候选人去重三选一弹窗、工具调用可视化卡片）统一使用 `cursor-ide-browser` MCP（遵循 `.cursor/rules/browser-testing.mdc`），作为对应任务的**验收步骤**在构建阶段手动/半自动执行并记录结果，不写成 CI 自动化用例——与项目现有"`tests/e2e` 依赖真实 LLM，不进 CI"的既定原则保持一致。

## 测试策略

- 每项功能改动配套单元/集成测试（详见 `openspec/changes/opensource-optimization-rollout/tasks.md`）
- 3 项前端行为的浏览器端到端验证记录在对应任务的验收步骤中
- `memory_module.py` 拆分前后运行完整测试套件确认无回归
- CI 新增 lint（`ruff check .`）与覆盖率门禁（`--cov-fail-under=60`）

## Risks / Trade-offs

| 风险/取舍 | 缓解措施 |
|---|---|
| tiktoken 精确计数导致压缩/分块触发阈值偏移 | 用中英混杂真实数据回归验证，偏移显著则调整常量并记录原因 |
| 追问流式化后取消逻辑的边界未覆盖 | 新增专门的取消场景单元测试 + 端到端浏览器测试手动触发中途中止 |
| `memory_module.py` 拆分与去重改动耦合（都涉及 `get_candidate_by_name`） | 任务顺序上拆分排在去重改动之后 |
| 工具调用可视化新增 `tool_result` 事件后前端关联逻辑复杂度上升 | 事件携带明确的调用标识，前端按标识索引已创建卡片进行原地更新，配套专项测试 |
| 覆盖率门槛设为 60%（非规则要求的 80%） | 后续节奏留待阶段三结束后评估，本轮不强制决定（Open Question） |

## Open Questions

1. 覆盖率门槛从 60% 提升到 80% 的节奏（是否分阶段设定中间目标，如阶段三结束后调到 70%）——留待阶段三结束后视实际覆盖率评估，不阻塞本轮构建。
