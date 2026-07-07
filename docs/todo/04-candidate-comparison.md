# #4 多候选人横向对比

## 目标

支持在候选人列表页勾选多名候选人，由 LLM 基于各自 EvalReport 生成横向对比摘要，帮助面试官快速做出录用决策。

## 范围

- 候选人列表页支持多选（checkbox）
- 勾选 2 个及以上候选人后，出现"对比"按钮
- 新增 `GET /api/candidates/compare?ids=a,b,c` 端点，聚合多份 EvalReport 调用 LLM 生成对比摘要
- 对比结果在页面内直接展示：评分对比表格 + LLM 生成的文字总结（各自优劣势、岗位匹配度）

## 验收条件

- [x] 候选人列表页每行有 checkbox，支持勾选多个候选人
  - 实现：`src/web/ui.py` 约第 1303–1322 行，候选人列表每行渲染 `ui.checkbox`，勾选状态存入 `state["selected_for_compare"]`（set）。
- [x] 勾选 2 个及以上时，出现"横向对比"按钮；勾选少于 2 个时按钮禁用或隐藏
  - 实现：`_refresh_compare_bar()`（`src/web/ui.py` 约第 1243–1277 行）仅在 `len(sel) >= 2` 时渲染"横向对比"与"清除"按钮，否则不渲染（隐藏）。
- [x] `GET /api/candidates/compare?ids=a,b,c` 返回对比结果，任意 id 不存在或无 EvalReport 时返回明确错误信息
  - 实现：`src/web/routes.py` `compare_candidates()`（约第 766–886 行）：id 不存在返回 404（`{"code": "not_found", ...}`）；无 EvalReport 的候选人不报错，而是在返回结果的 `missing_report` 列表中明确列出（见下一条）。
- [x] 对比结果包含：各候选人维度评分对比表格、LLM 生成的文字总结（优劣势对比、岗位匹配度排序）
  - 实现：同上，返回体含 `score_table`（各候选人各维度评分）与 `llm_summary`（LLM 根据 prompt 生成排序/优劣势/岗位匹配度建议的文字总结）；前端表格与摘要渲染见 `src/web/ui.py` 约第 1213–1241 行。集成测试 `tests/integration/test_llm_injection.py::test_compare_candidates_uses_injected_llm` 覆盖该接口的基本流程。
- [x] 对比最多支持 5 名候选人（部分达标：后端 422 限制生效，超出时前端展示原始 httpx 异常文本而非后端友好文案「最多对比 5 名候选人」）
  - 实现：`compare_candidates()` 中 `len(id_list) > 5` 时返回 422 `{"code": "too_many", "message": "最多对比 5 名候选人"}`；前端 `_do_compare()`（`src/web/ui.py` 约第 1197–1212 行）未在选择阶段主动限制勾选数量，超出时以 `ui.notify(f"对比失败：{exc}", type="negative")` 提示。
- [x] 无 EvalReport 的候选人在对比中有明确提示（"暂无评价报告"）
  - 实现：`compare_candidates()` 收集 `missing_report` 列表，前端弹窗中渲染"以下候选人暂无评价报告：..."（`src/web/ui.py` 约第 1215–1219 行）。
