# Inject Candidate History into Agent Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在候选人被选中时，将其历史面试摘要注入 MainAgent 的 Layer 3 系统提示，使 LLM 能直接感知历史记录，消除"没有面试记录"的错误回答。

**Architecture:** 纯上下文注入方案——不新增工具，只在两个地方各做一处外科手术式修改：① `MainAgent.set_candidate_context()` 新增可选参数 `history_summary`，将其追加到 Layer 3；② `routes.py` 的 `select_candidate` 路由在调用该方法前异步读取历史摘要并传入。改动全程向后兼容，无破坏性变更。

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, unittest.mock

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| Modify | `src/agents/main_agent.py` | `set_candidate_context()` 新增 `history_summary` 可选参数 |
| Modify | `src/web/routes.py` | `select_candidate` 路由加载历史摘要并传入 `set_candidate_context` |
| Modify | `tests/unit/test_agents.py` | 新增单元测试：验证 `history_summary` 写入系统提示 |
| Modify | `tests/integration/test_candidate_select.py` | 新增集成测试：验证路由正确透传历史摘要 |

---

## Task 1：扩展 `MainAgent.set_candidate_context()`

**Files:**
- Modify: `src/agents/main_agent.py:144-160`
- Test: `tests/unit/test_agents.py`

- [ ] **Step 1: 在 `test_agents.py` 末尾写失败测试**

打开 `tests/unit/test_agents.py`，在文件末尾追加如下测试类：

```python
# ── MainAgent.set_candidate_context ─────────────────────────────────────────


@pytest.mark.unit
class TestMainAgentSetCandidateContext:
    def _make_agent(self) -> "MainAgent":
        from src.agents.main_agent import MainAgent
        from src.framework.tool_registry import ToolRegistry
        from src.storage.memory_module import MemoryModule
        from src.storage.user_memory import UserMemoryStore
        from unittest.mock import MagicMock, AsyncMock

        llm = AsyncMock()
        tools = MagicMock(spec=ToolRegistry)
        memory = MagicMock(spec=MemoryModule)
        user_memory = MagicMock(spec=UserMemoryStore)
        user_memory.render.return_value = ""
        return MainAgent(llm, tools, memory, user_memory)

    def test_set_candidate_context_includes_history_summary(self):
        """传入 history_summary 后，_build_system_prompt() 应包含该内容。"""
        from src.agents.main_agent import MainAgent
        agent = self._make_agent()
        profile = CandidateProfile(id="c-001", name="王喜龙")
        history = "候选人 王喜龙 历史面试记录：\n第1次面试：2025-01-01，评分 6.0，结论 weak_hire"

        agent.set_candidate_context(profile, history_summary=history)
        prompt = agent._build_system_prompt()

        assert "历史面试记录" in prompt
        assert "weak_hire" in prompt

    def test_set_candidate_context_without_history_summary(self):
        """不传 history_summary 时，系统提示不含"历史面试记录"字样（向后兼容）。"""
        agent = self._make_agent()
        profile = CandidateProfile(id="c-001", name="李四")

        agent.set_candidate_context(profile)
        prompt = agent._build_system_prompt()

        assert "历史面试记录" not in prompt

    def test_set_candidate_context_history_summary_none_is_ignored(self):
        """显式传 history_summary=None 等同于不传，不影响提示词。"""
        agent = self._make_agent()
        profile = CandidateProfile(id="c-001", name="张三")

        agent.set_candidate_context(profile, history_summary=None)
        prompt = agent._build_system_prompt()

        assert "历史面试记录" not in prompt
```

- [ ] **Step 2: 运行测试，确认 FAIL**

```bash
.venv\Scripts\python -m pytest tests/unit/test_agents.py::TestMainAgentSetCandidateContext -v
```

预期输出：`FAILED` — 因为 `set_candidate_context` 还不接受 `history_summary` 参数。

- [ ] **Step 3: 修改 `src/agents/main_agent.py`**

将第 144-160 行的 `set_candidate_context` 方法替换为：

```python
def set_candidate_context(
    self,
    profile: CandidateProfile,
    interview_brief: str | None = None,
    history_summary: str | None = None,
) -> None:
    parts = [f"\n当前候选人：{profile.name}（ID: {profile.id}）"]
    if profile.current_position:
        parts.append(f"职位：{profile.current_position}")
    if profile.years_of_experience is not None:
        parts.append(f"工作年限：{profile.years_of_experience} 年")
    if profile.skills:
        parts.append(f"技能：{', '.join(profile.skills[:15])}")
    if profile.resume_content:
        parts.append(f"简历内容：\n{profile.resume_content[:1500]}")
    if interview_brief:
        parts.append(f"面试简报（前800字）：\n{interview_brief[:800]}")
    if history_summary:
        parts.append(f"历史面试记录：\n{history_summary}")
    self._layer3_candidate = "\n".join(parts)
    self._cached_system_prompt = None
    logger.info("MainAgent: candidate context updated for %s", profile.name)
```

- [ ] **Step 4: 运行测试，确认 PASS**

```bash
.venv\Scripts\python -m pytest tests/unit/test_agents.py::TestMainAgentSetCandidateContext -v
```

预期输出：`3 passed`

- [ ] **Step 5: 确认无回归**

```bash
.venv\Scripts\python -m pytest tests/unit/test_agents.py -v
```

预期：全部通过，无新失败。

- [ ] **Step 6: Commit**

```bash
git add src/agents/main_agent.py tests/unit/test_agents.py
git commit -m "feat: add history_summary param to MainAgent.set_candidate_context"
```

---

## Task 2：修改路由 `select_candidate` 传入历史摘要

**Files:**
- Modify: `src/web/routes.py:116-128`（`select_candidate` 函数内）
- Test: `tests/integration/test_candidate_select.py`

- [ ] **Step 1: 在 `test_candidate_select.py` 末尾写失败测试**

打开 `tests/integration/test_candidate_select.py`，在文件末尾追加：

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_candidate_injects_history_into_main_agent(client):
    """选中有历史记录的候选人后，MainAgent 的系统提示应包含历史面试摘要。"""
    from unittest.mock import AsyncMock, patch
    from src.storage.memory_module import CandidateHistory, InterviewSummary
    from datetime import datetime

    await _seed(client, "cid-hist-001", "历史候选人")

    fake_history = CandidateHistory(
        past_interviews=[
            InterviewSummary(
                session_id="s-old",
                date=datetime(2025, 1, 1, 10, 0),
                score=6.0,
                recommendation="weak_hire",
                summary="表现一般",
            )
        ],
        history_summary="候选人 历史候选人 历史面试记录：\n第1次面试：2025-01-01，评分 6.0，结论 weak_hire",
    )

    memory = client._transport.app.state.memory_module
    main_agent = client._transport.app.state.main_agent

    with patch.object(memory, "get_candidate_history", new=AsyncMock(return_value=fake_history)):
        r = await client.post(
            "/api/candidate/select", json={"candidate_id": "cid-hist-001"}
        )

    assert r.status_code == 200
    prompt = main_agent._build_system_prompt()
    assert "历史面试记录" in prompt
    assert "weak_hire" in prompt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_candidate_no_history_does_not_break(client):
    """候选人无历史记录时，select 路由正常完成，不注入历史字段。"""
    from unittest.mock import AsyncMock, patch

    await _seed(client, "cid-hist-002", "无历史候选人")

    memory = client._transport.app.state.memory_module

    with patch.object(memory, "get_candidate_history", new=AsyncMock(return_value=None)):
        r = await client.post(
            "/api/candidate/select", json={"candidate_id": "cid-hist-002"}
        )

    assert r.status_code == 200
```

- [ ] **Step 2: 运行测试，确认 FAIL**

```bash
.venv\Scripts\python -m pytest tests/integration/test_candidate_select.py::test_select_candidate_injects_history_into_main_agent -v
```

预期：`FAILED` — 路由尚未调用 `get_candidate_history`，`main_agent` 的系统提示不含历史字段。

- [ ] **Step 3: 修改 `src/web/routes.py` 中的 `select_candidate`**

找到以下代码块（大约第 116-128 行）：

```python
    # Load interview brief
    brief: str = ""
    if controller is not None:
        session = await controller.get_session()
        if session and session.interview_brief:
            brief = session.interview_brief
    if not brief:
        brief = memory.get_brief(body.candidate_id)

    # Update MainAgent context
    if main_agent is not None:
        main_agent.set_candidate_context(candidate, interview_brief=brief)
```

将其替换为：

```python
    # Load interview brief
    brief: str = ""
    if controller is not None:
        session = await controller.get_session()
        if session and session.interview_brief:
            brief = session.interview_brief
    if not brief:
        brief = memory.get_brief(body.candidate_id)

    # Load candidate history summary
    history_summary: str | None = None
    candidate_history = await memory.get_candidate_history(body.candidate_id)
    if candidate_history:
        history_summary = candidate_history.history_summary

    # Update MainAgent context
    if main_agent is not None:
        main_agent.set_candidate_context(
            candidate,
            interview_brief=brief,
            history_summary=history_summary,
        )
```

- [ ] **Step 4: 运行测试，确认 PASS**

```bash
.venv\Scripts\python -m pytest tests/integration/test_candidate_select.py -v
```

预期：`5 passed`（原 3 个 + 新增 2 个）。

- [ ] **Step 5: 运行完整测试套件，确认无回归**

```bash
.venv\Scripts\python -m pytest tests/unit/ tests/integration/ -x -q
```

预期：全部通过，无新失败。

- [ ] **Step 6: Commit**

```bash
git add src/web/routes.py tests/integration/test_candidate_select.py
git commit -m "feat: inject candidate history summary into MainAgent context on candidate select"
```

---

## 验收标准

1. `pytest tests/unit/test_agents.py::TestMainAgentSetCandidateContext` — 3 passed
2. `pytest tests/integration/test_candidate_select.py` — 5 passed（原 3 + 新增 2）
3. 手动验证：启动服务，选中有历史记录的候选人（如王喜龙），在 MainAgent 对话中问"他面试过吗"，回复应包含上次面试时间、评分和结论。
