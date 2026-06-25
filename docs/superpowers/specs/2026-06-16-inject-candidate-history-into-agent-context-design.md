# 设计文档：将候选人历史面试记录注入 MainAgent 上下文

**日期**：2026-06-16  
**状态**：待实现  
**问题来源**：MainAgent 对已有面试记录的候选人错误回答"没有面试记录"

---

## 1. 问题描述

用户询问候选人王喜龙是否面试过。系统回答"目前没有记录"，但该候选人实际上已有一次完整的面试记录（评分 6.0，结论 weak_hire）。

### 1.1 根因链路

```
用户提问 "王喜龙有没有面试过？"
  │
  ▼
LLM 无法在系统提示中找到面试历史（Layer 3 未包含）
  │
  ▼
LLM 调用 manage_user_memory(action="list") 查询
  │
  ▼
返回结果：仅面试官偏好设置，无候选人历史
  │
  ▼
LLM 基于空结果推断："没有面试记录" ← 错误回答
```

### 1.2 数据实际存在的位置

```
candidates/{candidate_id}/interviews/index.md   ← 面试历史索引（YAML frontmatter + Markdown 表格）
```

对应 MemoryModule 方法：`get_candidate_history(candidate_id)` → 返回 `CandidateHistory`

---

## 2. 缺口分析

| 层次 | 现状 | 缺口 |
|------|------|------|
| 数据存储 | `candidates/{id}/interviews/index.md` 正确记录每次面试 | ✅ 无缺口 |
| 读取方法 | `MemoryModule.get_candidate_history()` 可读取并格式化 | ✅ 无缺口 |
| 路由层 | `POST /api/candidate/select` 调用 `set_candidate_context()` | ❌ **未调用 `get_candidate_history()`，未传入历史** |
| Agent 上下文 | `set_candidate_context()` 组装 Layer 3 系统提示 | ❌ **不接受也不展示历史面试摘要** |
| Agent 工具 | `MainAgent` 只有 `dispatch_to_agent` + `manage_user_memory` | ❌ **没有查询候选人历史的工具（可接受，改用上下文注入）** |

---

## 3. 解决方案

### 方案选择：上下文注入（推荐）

**不新增工具**，在选中候选人时将历史面试摘要直接写入 Layer 3 系统提示。

优点：
- 无额外工具调用延迟
- LLM 无需主动"查询"，自然感知历史
- 改动最小，外科手术式修改

### 3.1 修改 `src/agents/main_agent.py`

`set_candidate_context()` 新增 `history_summary` 参数，追加到 Layer 3：

```python
def set_candidate_context(
    self,
    profile: CandidateProfile,
    interview_brief: str | None = None,
    history_summary: str | None = None,   # 新增
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
    if history_summary:                    # 新增
        parts.append(f"历史面试记录：\n{history_summary}")
    self._layer3_candidate = "\n".join(parts)
    self._cached_system_prompt = None
    logger.info("MainAgent: candidate context updated for %s", profile.name)
```

### 3.2 修改 `src/web/routes.py`

在 `POST /api/candidate/select` 中，选中候选人后异步加载历史面试摘要：

```python
# 现有代码
brief: str = ""
if controller is not None:
    ...
if not brief:
    brief = memory.get_brief(body.candidate_id)

# 新增：加载候选人历史面试摘要
history_summary: str | None = None
candidate_history = await memory.get_candidate_history(body.candidate_id)
if candidate_history:
    history_summary = candidate_history.history_summary

# 修改调用，传入 history_summary
if main_agent is not None:
    main_agent.set_candidate_context(
        candidate,
        interview_brief=brief,
        history_summary=history_summary,   # 新增
    )
```

---

## 4. 影响范围

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agents/main_agent.py` | 函数签名扩展 | `set_candidate_context()` 新增可选参数，向后兼容 |
| `src/web/routes.py` | 逻辑新增 | `candidate_select` 路由新增一次异步读取 |
| `src/tools/dispatch_to_agent.py` | 可选同步 | 简报生成后调用 `set_candidate_context()` 的两处，可按需补充 `history_summary` |

`dispatch_to_agent.py` 中的两处调用（第 144 行、第 161 行）暂不修改，因为简报生成时上下文已注入；如后续有需要再同步。

---

## 5. 验证标准

1. **手动验证**：选中王喜龙后，在 MainAgent 对话中询问"他面试过吗"，应回答包含上次面试时间、评分和结论。
2. **单元测试**：`test_set_candidate_context_includes_history_summary`：确认传入 `history_summary` 后，`_build_system_prompt()` 输出包含该内容。
3. **路由测试**：mock `memory.get_candidate_history()` 返回历史，验证 `set_candidate_context` 被正确参数调用。

---

## 6. 不在此次修复范围

- `dispatch_to_agent.py` 中的两处 `set_candidate_context()` 调用（低优先级）
- 新增"查询候选人历史"工具（上下文注入已足够，工具方案增加复杂度）
- `CandidateProfile.history_summary` 字段的使用（现有字段未被任何地方赋值，建议独立清理）
