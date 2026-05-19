# 代码审查分析报告

> 生成时间：2026-05-19
> 覆盖范围：`src/` 全部模块（agents、framework、storage、models、tools、web）

---

## 一、问题清单

### P0 问题（严重影响核心功能，必须优先修复）

---

#### P0-1：候选人上下文未注入提示词（Issue 1）

**现象**  
在 Web 界面选择候选人后，向 Agent 提问候选人相关内容，Agent 回复称没有候选人信息。

**根因**  
`PromptBuilder._build_fixed_zone()` 虽然已注入候选人姓名、学历、技能、简历摘要，但：
1. 未将简历 Markdown 文件路径告知 Agent，导致 Agent 无法定位完整简历
2. 无 `read_resume_markdown` 工具，Agent 无法按需读取完整简历内容
3. `INTERVIEW_AGENT_SYSTEM_PROMPT` 和 `EVAL_AGENT_SYSTEM_PROMPT` 的 `tool_names=[]` / `["skill_view"]`，均未包含简历读取工具

**影响范围**  
- `src/framework/prompt_builder.py`：`_build_fixed_zone` 未输出简历路径
- `src/tools/resume_parser.py`：缺少 `read_resume_markdown` 工具
- `src/main.py`：Agent 的 `tool_names` 未注册新工具
- `src/agents/prompts.py`：prompt 未说明简历文件可查阅

**解决方案**  
1. `_build_fixed_zone` 追加：`简历完整内容路径：{c.resume_markdown_path}`（路径非空时）
2. `resume_parser.py` 新增 `read_resume_markdown(file_path: str) -> str` 工具
3. `main.py` 注册该工具；`interview_config.tool_names` 和 `resume_config.tool_names` 加入 `read_resume_markdown`
4. `RESUME_AGENT_SYSTEM_PROMPT` 说明：详细简历内容已保存在系统提示指定路径，可调用 `read_resume_markdown` 工具按需获取

---

#### P0-2：工具名不一致导致 LLM 调用失败（新发现）

**现象**  
简历解析时 Agent 无法调用正确的工具，导致解析失败或使用 fallback 文本。

**根因**  
`resume_agent.py` 的 user 消息中写了"调用 `parse_resume_pdf` 工具"，但 `main.py` 注册的工具名是 `parse_resume`（使用函数名作为工具名），LLM 收到的工具 schema 中也是 `parse_resume`。LLM 因此可能困惑或跳过工具调用。

**影响范围**  
- `src/agents/resume_agent.py` 第 48 行

**解决方案**  
将 `resume_agent.py` user 消息中的 `parse_resume_pdf` 改为 `parse_resume`

---

#### P0-3：候选人基本信息结构化字段不完整（Issue 5）

**现象**  
PDF 解析后缺少 `years_of_experience`（工作年限）和 `current_position`（当前职位）字段，系统无法展示和注入这两个常用维度。

**根因**  
- `CandidateProfile` 数据模型中无 `years_of_experience`、`current_position` 字段
- `ResumeAgent` 的 user 消息未要求 LLM 输出这两个字段
- `_update_candidate_from_data()` 也未处理这两个字段的映射
- `RESUME_AGENT_SYSTEM_PROMPT` 中无对应提取指令

**影响范围**  
- `src/models/candidate.py`
- `src/agents/resume_agent.py`
- `src/agents/prompts.py`
- `src/storage/memory_module.py`（序列化/反序列化）

**解决方案**  
1. `CandidateProfile` 添加 `years_of_experience: int | None = None` 和 `current_position: str | None = None`
2. `_update_candidate_from_data()` 映射新字段
3. `RESUME_AGENT_SYSTEM_PROMPT` 要求提取这两个字段
4. `resume_agent.py` user 消息字段列表中加入新字段
5. `memory_module.py` 的 `_profile_to_json` / `_profile_from_row` 支持新字段
6. `prompt_builder._build_fixed_zone` 展示新字段

---

### P1 问题（影响数据质量和基础功能）

---

#### P1-1：简历导入无去重逻辑（Issue 2）

**现象**  
每次导入都生成新 `candidate_id`，同一候选人重复导入会产生重复记录，浪费存储并造成历史数据碎片化。

**根因**  
`CandidateRepository.insert` 使用 `INSERT OR REPLACE`，主键是 UUID，每次上传均生成新 UUID。`upload_resume` 路由未按候选人姓名查重。

**影响范围**  
- `src/web/routes.py`：`upload_resume` 无去重检查
- `src/web/ui.py`：上传后无覆盖确认弹窗

**解决方案**  
1. `upload_resume` 路由：从文件名提取候选人名（去掉 `.pdf` 扩展名），调用 `search_candidates` 精确匹配，若存在同名候选人，返回 `409` + 已有 `candidate_id`
2. UI：捕获 409，弹出确认弹窗，用户选择"覆盖"则携带现有 `candidate_id` 重传，"取消"则中止
3. 覆盖时重用原 `candidate_id`，更新简历内容和题目

---

#### P1-2：缺少候选人删除功能（Issue 3 部分）

**现象**  
无删除候选人入口，候选人数据只增不减，无法清理测试数据或过期记录。

**根因**  
`CandidateRepository` 无 `delete` 方法；API 无 `DELETE /api/candidates/{id}` 端点；UI 无删除操作。

**影响范围**  
- `src/storage/repositories.py`
- `src/storage/memory_module.py`
- `src/web/routes.py`
- `src/web/ui.py`

**解决方案**  
1. `CandidateRepository.delete(candidate_id)` — 删除候选人行
2. `MemoryModule.delete_candidate(candidate_id)` — 级联删除：DB 记录 + 本地 PDF/MD 文件 + 关联面试记录（由 SQLite `FOREIGN KEY CASCADE` 处理，需要在 schema 中加 `ON DELETE CASCADE`）
3. `DELETE /api/candidates/{candidate_id}` 路由
4. UI 左侧候选人列表每行带删除图标，点击确认后调用删除 API

---

#### P1-3：清空候选人下拉不清空状态（新发现）

**现象**  
点击下拉框的清除按钮（`clearable=True`）后，`state["candidate_id"]` 仍保留原值，之后点击"开始面试"会用旧候选人 ID 发起面试，与页面显示不符。

**根因**  
`ui.py` 的 `_on_candidate_select(None)` 直接 `return`，未清空 `state["candidate_id"]` 和 `state["candidate_name"]`。

**影响范围**  
- `src/web/ui.py`：`_on_candidate_select` 函数

**解决方案**  
`_on_candidate_select(None)` 时清空 `state["candidate_id"] = None`，`state["candidate_name"] = "—"`，刷新顶栏显示

---

### P2 问题（影响用户体验和数据完整性）

---

#### P2-1：缺少候选人详情查看界面（Issue 4）

**现象**  
选中候选人后无法查看其完整信息。历史面试记录 API 已实现但 UI 不使用。

**根因**  
UI 仅有单页 Agent 对话界面；无专门展示候选人简历（Markdown 渲染）、历史面试记录、评价报告的详情视图。`GET /api/candidates/{id}/history` API 可用但未被 UI 调用。

**影响范围**  
- `src/web/ui.py`：需新增候选人详情区域
- `src/web/routes.py`：现有 history API 已具备，可直接复用

**解决方案**  
1. 重构 UI 布局，增加左侧候选人列表面板（可滚动，支持至少 10 条）
2. 点击候选人后，主区域显示候选人详情：
   - 简历 Tab（Markdown 渲染）
   - 面试题目 Tab
   - 历史面试 Tab（调用 `/api/candidates/{id}/history`）
   - 评价报告 Tab
3. 将原底部下拉候选人选择器迁移至左侧面板

---

#### P2-2：上传简历后不自动选中新候选人（新发现）

**现象**  
上传简历成功后，刷新了候选人下拉列表，但新候选人未被自动设为当前选中项，需要用户手动再次点击选择。

**根因**  
`_handle_upload` 在上传成功后仅调用 `_load_candidates()` 刷新下拉选项，未调用 `candidate_sel.set_value(cid)` 设置选中值。

**影响范围**  
- `src/web/ui.py`：`_handle_upload` 函数

**解决方案**  
上传成功后，调用 `candidate_sel.set_value(cid)` 自动选中新上传的候选人

---

#### P2-3：EvalReport 历史数据未与候选人详情联动（新发现）

**现象**  
面试结束后生成的评价报告已持久化，但 UI 无法通过候选人详情页查看历史报告。

**根因**  
`GET /api/interview/eval?interview_id=...` API 存在，但候选人详情视图未集成；`GET /api/candidates/{id}/history` 返回了 `past_interviews` 列表但 UI 未使用。

**影响范围**  
- `src/web/ui.py`：需要接入 history API 并渲染 `past_interviews` 中的报告

**解决方案**  
在候选人详情的"历史面试"Tab 中，展示 past_interviews 列表，每条面试记录可展开显示评价报告（调用 `GET /api/interview/eval?interview_id=...`）

---

#### P2-4：候选人列表 UX 需改进（Issue 3 部分）

**现象**  
候选人选择仅有底部下拉框，不直观；候选人数量多时难以管理；不支持快速切换和删除。

**根因**  
当前 UI 设计将候选人选择器放在底部 toolbar，与主体操作区割裂；无独立列表面板。

**影响范围**  
- `src/web/ui.py`：整体布局

**解决方案**  
将底部下拉移至左侧固定面板，展示候选人卡片列表：
- 每行：姓名 + 技能标签（前 3 个）+ 当前职位
- 右侧：删除按钮（带确认）
- 底部：上传简历按钮
- 支持滚动，最多显示 50 条

---

#### P2-5：`history_summary` 字段在持久化层无专属存储（新发现）

**现象**  
`CandidateProfile.history_summary` 在模型中存在，但 `_profile_to_json` 未序列化它，`_profile_from_row` 中始终赋值为 `None`。`consolidate_memory` 将面试后洞察写入 `last_interview_insights`，但 `history_summary` 这条"给 Agent 看的格式化文本"只在运行时按需构建（`get_candidate_history`），不会持久化。

**根因**  
`memory_module._profile_to_json` 未包含 `history_summary`；`MemoryModule.get_candidate` 返回的 profile 中 `history_summary=None`，需调用方显式调用 `get_candidate_history` 并手动赋值。

**影响范围**  
- `src/storage/memory_module.py`
- `src/agents/orchestrator.py`（`create_session` 已手动赋值，但逻辑散落）

**解决方案**  
`MemoryModule.get_candidate` 在返回 `CandidateProfile` 时，同时查询并附加 `history_summary`；或在 `orchestrator.create_session` 现有逻辑中保持，确保文档注释明确此调用模式，以防后续遗漏

---

#### P2-6：SQLite 外键级联删除未声明（新发现）

**现象**  
删除候选人时，`Interview`、`ConversationRound`、`EvalReport`、`TokenUsage` 等关联记录不会自动清理，需要应用层手动级联删除，容易遗漏。

**根因**  
`database.py` 的 DDL 中，`Interview(candidate_id)` FK 声明未带 `ON DELETE CASCADE`；`ConversationRound`、`EvalReport` 等表同样缺少级联声明。SQLite 虽启用了 `PRAGMA foreign_keys = ON`，但 CASCADE 需在 DDL 中显式声明。

**影响范围**  
- `src/storage/database.py`：表结构 DDL

**解决方案**  
`MemoryModule.delete_candidate` 在应用层按顺序删除关联数据（简单可靠），无需改 DDL（避免 schema 迁移复杂度）

---

---

## 二、解决方案汇总

| 编号 | 涉及文件 | 改动说明 |
|------|---------|---------|
| P0-1 | `src/tools/resume_parser.py` | 新增 `read_resume_markdown` 工具函数 |
| P0-1 | `src/main.py` | 注册 `read_resume_markdown`；更新 interview/resume config 的 `tool_names` |
| P0-1 | `src/framework/prompt_builder.py` | `_build_fixed_zone` 追加简历路径提示 |
| P0-1 | `src/agents/prompts.py` | 说明可用 `read_resume_markdown` 工具查阅完整简历 |
| P0-2 | `src/agents/resume_agent.py` | 工具名 `parse_resume_pdf` → `parse_resume` |
| P0-3 | `src/models/candidate.py` | 添加 `years_of_experience`、`current_position` 字段 |
| P0-3 | `src/agents/resume_agent.py` | `_update_candidate_from_data` 映射新字段；user 消息字段列表更新 |
| P0-3 | `src/agents/prompts.py` | `RESUME_AGENT_SYSTEM_PROMPT` 要求提取新字段 |
| P0-3 | `src/storage/memory_module.py` | `_profile_to_json` / `_profile_from_row` 支持新字段 |
| P0-3 | `src/framework/prompt_builder.py` | `_build_fixed_zone` 展示工作年限和当前职位 |
| P1-1 | `src/web/routes.py` | `upload_resume` 添加同名查重，返回 409 |
| P1-1 | `src/web/ui.py` | 捕获 409，弹出覆盖确认弹窗 |
| P1-2 | `src/storage/repositories.py` | `CandidateRepository.delete` |
| P1-2 | `src/storage/memory_module.py` | `MemoryModule.delete_candidate`（级联删除） |
| P1-2 | `src/web/routes.py` | `DELETE /api/candidates/{candidate_id}` |
| P1-2 | `src/web/ui.py` | 候选人列表删除按钮 |
| P1-3 | `src/web/ui.py` | `_on_candidate_select(None)` 清空 state |
| P2-1 | `src/web/ui.py` | 新增左侧候选人列表面板；候选人详情 Tab |
| P2-2 | `src/web/ui.py` | 上传成功后 `candidate_sel.set_value(cid)` |
| P2-3 | `src/web/ui.py` | 历史面试 Tab 展示评价报告 |
| P2-4 | `src/web/ui.py` | 布局重构：左侧候选人管理面板 |
| P2-5 | `src/storage/memory_module.py` | 文档注释明确 `history_summary` 使用模式 |
| P2-6 | `src/storage/memory_module.py` | `delete_candidate` 应用层级联清理 |

---

## 三、优先级建议

| 优先级 | 问题 | 理由 |
|--------|------|------|
| **P0** | P0-2 工具名不一致 | 简历解析可能完全失败，是所有功能的前置依赖 |
| **P0** | P0-3 结构化字段缺失 | 基础数据模型，影响上下文注入和详情展示 |
| **P0** | P0-1 上下文注入不完整 | 核心功能缺陷，面试期间 Agent 无法感知完整候选人信息 |
| **P1** | P1-2 缺少删除功能 | 数据管理基础能力，影响候选人生命周期管理 |
| **P1** | P1-1 导入无去重 | 数据一致性问题，生产使用必须解决 |
| **P1** | P1-3 状态清空 bug | 逻辑错误，可能导致误操作 |
| **P2** | P2-1/P2-4 候选人详情+列表 UX | 用户体验核心功能，但不阻塞基本流程 |
| **P2** | P2-2 上传后自动选中 | 小交互优化 |
| **P2** | P2-3 历史报告联动 | 数据可见性改善 |
| **P2** | P2-5/P2-6 存储层完善 | 内部实现改善，不影响当前功能 |

---

## 四、实施顺序建议

考虑模块依赖关系，推荐按以下顺序实施：

```
Phase 2（数据模型）→ Phase 3（工具层）→ Phase 4（存储+API）→ Phase 5（UI 重构）
```

**详细步骤：**

1. **扩展 `CandidateProfile` 模型**（P0-3 基础）
   - `src/models/candidate.py`：添加新字段
   - `src/storage/memory_module.py`：更新序列化/反序列化
   - 验证：现有数据库记录仍可正常读取（新字段默认 `None`）

2. **更新 ResumeAgent**（P0-2 + P0-3）
   - `src/agents/resume_agent.py`：修复工具名；添加新字段映射
   - `src/agents/prompts.py`：更新 system prompt

3. **添加 `read_resume_markdown` 工具**（P0-1）
   - `src/tools/resume_parser.py`：实现工具函数
   - `src/main.py`：注册工具，更新 Agent config
   - `src/framework/prompt_builder.py`：输出简历路径
   - `src/agents/prompts.py`：说明工具可用

4. **存储层删除能力**（P1-2）
   - `src/storage/repositories.py`：`delete` 方法
   - `src/storage/memory_module.py`：`delete_candidate` 级联删除

5. **API 层：删除端点 + 去重逻辑**（P1-1 + P1-2）
   - `src/web/routes.py`：DELETE 端点 + 上传去重

6. **UI 全面重构**（P1-1 + P1-3 + P2-1 ~ P2-4）
   - `src/web/ui.py`：左侧候选人面板、详情视图、去重弹窗、状态修复
