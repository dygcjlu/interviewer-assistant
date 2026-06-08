# Fix Review Findings �?任务清单

> 执行顺序：Critical �?High（按依赖关系）→ Medium（按影响面）�?文档更新
> 每个任务独立可执行，完成后打勾�?

---

## 第一批：Critical（必须立即修复）

- [x] **T01 [F4-5]** `memory_module.py`：`save_eval_report()` upsert �?�?`interviews/index.md` 中无匹配条目则主动插入，�?eval 字段
  - 影响文件：`src/storage/memory_module.py`
  - 验证：写�?eval 报告后，`interviews/index.md` 应有�?overall_score、recommendation、key_findings 的条�?

---

## 第二批：High（建议立即修复）

- [x] **T02 [F4-1]** `routes.py`：eval 路由改为 finally 确保 `close_session()` 执行
  - 影响文件：`src/web/routes.py`
  - 验证：eval 失败�?`transcript.md` 仍写入，session 从内存清�?

- [x] **T03 [F1-3]** `dispatch_to_agent.py`：`parse_done` 副作用末尾补�?`main_agent.set_candidate_context()`
  - 影响文件：`src/tools/dispatch_to_agent.py`
  - 验证：简历解析完成后，MainAgent 系统提示 Layer 3 包含完整 resume_content

- [x] **T04 [F1-1]** `dispatch_to_agent.py`：`save_candidate` 失败升级�?`user_facing` 错误，而非静默成功
  - 影响文件：`src/tools/dispatch_to_agent.py`
  - 验证：强�?save_candidate 失败时，工具返回�?user_facing 的错误结�?

- [x] **T05 [F1-4]** `dispatch_to_agent.py`：移�?`brief_done` 分支中的 `start_interview()` 调用（⚠�?先确�?UI 依赖，见 T05 说明�?
  - 影响文件：`src/tools/dispatch_to_agent.py`，`docs/arc/flows.md`
  - 前置确认：UI 是否依赖 `stage=interviewing` 决定显示逻辑

- [x] **T06 [F2-1]** `interview_controller.py` + `routes.py`：`start_interview` 前置条件改为 `stage == IDLE`
  - 影响文件：`src/agents/interview_controller.py`
  - 验证：EVALUATING/COMPLETED 状态下调用 start_interview 应返回错�?

- [x] **T07 [F2-2]** `audio/manager.py`：`AudioManager.start()` 添加失败回滚逻辑（清理已�?STT + 已建 task + 置空 TM�?
  - 影响文件：`src/audio/manager.py`
  - 验证：模�?start 中途失败，无资源泄漏，_transcription_manager �?None

---

## 第三批：Medium（按影响面排序）

- [x] **T08 [F3-3/F4-2]** 术语统一：全局替换"题目清单"�?面试简�?�? 处）
  - 影响文件：`src/agents/interview_agent.py`�? 处）、`src/agents/prompts.py`�? 处，�?prompt_builder 注释�?
  - 验证：`rg "题目清单" src/` 无结�?

- [x] **T09 [F5-4]** `main_agent.py`：`_trim_history` 截断后保�?tool call pair 完整性（跳过孤儿 tool 消息�?
  - 影响文件：`src/agents/main_agent.py`
  - 验证：截断后首条消息不为 `role=tool`

- [x] **T10 [F5-2]** `prompts.py`：`_LAYER1_ROLE` 补充 `manage_user_memory` 不应调用的场景说�?
  - 影响文件：`src/agents/prompts.py`
  - 验证：prompt 明确包含"候选人个体信息不保�?约束

- [x] **T11 [F5-3]** `prompts.py` �?`main_agent.py`：`_NUDGE_SYSTEM` 增加"仅关注面试官对岗�?偏好的表�?约束
  - 影响文件：`src/agents/main_agent.py` �?`src/agents/prompts.py`（确认常量位置）
  - 验证：nudge 系统提示包含候选人信息忽略约束

- [x] **T12 [F1-2]** `prompts.py`：ResumeAgent profile schema �?`age` 改为可选字段，�?无则省略"说明
  - 影响文件：`src/agents/prompts.py`
  - 验证：提示词�?age 字段不再标注必填

- [x] **T13 [F1-5a]** `routes.py`：`/candidates` 接口返回真实总数（不�?limit+offset 截断�?
  - 影响文件：`src/web/routes.py`
  - 验证：total 反映真实候选人数量

- [x] **T14 [F1-5b]** `routes.py`：候选人去重改为解析后按真实姓名判断（或改为后置警告 UI�?
  - 影响文件：`src/web/routes.py`（和/�?upload 逻辑�?
  - 前置确认：去重时序调整是否影响上传流程（简历解析是异步的）

- [x] **T15 [F2-3]** `interview_controller.py`：`create_session` �?`candidate_id` 不存在时明确 404，不创建空档�?
  - 影响文件：`src/agents/interview_controller.py`，`src/web/routes.py`
  - 验证：传入不存在�?candidate_id 返回 404

---

## 第四批：文档更新

- [x] **T16 [F5-5]** `docs/arc/agents.md`：明确记�?切换候选人不清空对话历�?为有意设计，说明理由
- [x] **T17 [F1-4]** `docs/arc/flows.md §2`：更新简报生成流程说明，移除"brief 后自动进�?interviewing"的误导性描�?
- [x] **T18 [F3-5]** `docs/feature-review-plan.md`（或 findings）：修正 ContextManager 压缩对评价影响的描述（仅影响实时建议上下文，EvalAgent 用完�?transcript�?

---

## 待讨论（在实�?T05/T14 前需与用户确认）

- **T05 前置**：UI �?`brief_done` 后是否依�?`stage=interviewing` 决定显示逻辑�?
- **T14 前置**：去重时序—简历解析是异步的，上传时无法立即取得真实姓名，如何处理�?
