# E2E 发现问题 · 修复实施计划

> 制定日期：2026-07-07
> 依据：`docs/e2e-review-findings.md`（当日端到端浏览器测试发现的 22 项问题）
> 验证策略：每批次完成后运行单元+集成测试防回归；全部完成后重启服务做完整浏览器 E2E 回归，逐项验证修复效果。

## 批次一：功能 Bug（P0/P1）

| # | 问题 | 修复方案 | 涉及文件 |
|---|---|---|---|
| 1 | A-1 对比勾选不可用 | `cb.on("update:model-value", ...)` 改为 `ui.checkbox(on_change=...)`，回调读 `e.value`（ValueChangeEventArguments） | `src/web/ui.py` |
| 2 | A-2 删除候选人不可用 | ① `_confirm_delete_dialog` 增加 `parent` 参数，`with parent:` 创建弹窗（对齐 `_confirm_dedup_dialog` 的已验证做法）；② 删除按钮改 `on("click.stop", ...)` 阻止冒泡选中 | `src/web/ui.py` |
| 3 | A-4 问题 Tab 不同步 | `_render_candidate_list` / `_on_candidate_select_inner` 链路透传 `qs_col`，候选人选中时拉取 `/api/interview/questions` 并渲染 | `src/web/ui.py` |
| 4 | B-1 聊天气泡不渲染 Markdown | Agent 侧气泡（`_bubble` sent=False 与 `_chat_stream` 流式气泡）改用 `ui.markdown` | `src/web/ui.py` |

## 批次二：体验类（P1/P2）

| # | 问题 | 修复方案 |
|---|---|---|
| 5 | A-5 会话候选人与 UI 不同步 | `_sync_candidate_panel` 无 `state["candidate_id"]` 时回退读取 `/api/session/current` 的 candidate_id；同步后刷新列表高亮 |
| 6 | A-6 按钮可用态未联动 | 新增 `_refresh_stage_buttons()`：空闲/已完成时禁用"结束面试"，面试中禁用"开始面试"；stage 变化处统一调用 |
| 7 | B-2 空状态缺失 | 聊天区加欢迎引导卡片；转写/问题/报告 Tab 初始占位文案 |
| 8 | B-5 建议枚举英文裸露 | `_render_report` 增加 recommendation 中文映射（hire→建议录用等）+ 颜色徽章 |
| 9 | B-6 建议文本未清洗 | 新增纯函数 `_clean_suggestion_text`（去首尾引号/空白），suggestion_delta/final 均过清洗；配单元测试 |
| 10 | A-3 覆盖检测无效 | `check-coverage` 判定 prompt 放宽（主题实质讨论即算覆盖）；判定结果（覆盖 ID 列表）写日志便于观测 |

## 批次三：打磨类（P2/P3）

| # | 问题 | 修复方案 |
|---|---|---|
| 11 | B-3 顶栏无品牌 | 顶栏加产品名"面试助手"+图标 |
| 12 | B-4 面板 Markdown 字号失衡 | 全局 CSS 缩放右侧面板内 markdown 标题字号 |
| 13 | B-7 列表无搜索 | 候选人列表头部加过滤输入框（前端过滤） |
| 14 | C-1 明确指令仍反问 | MainAgent Layer1 prompt 增加"面试官已明确要求生成时直接执行，不再追问" |
| 15 | C-3 上传后无提示 | 上传完成气泡补充"点击「解析简历」后才会创建候选人档案"说明 |
| 16 | D-2 LLM 日志过量 | INFO 级 `messages_body` 截断从 1000 收紧至 200 字（DEBUG 全量仍写 llm.log） |
| 17 | D-3 控制台日志乱码 | `setup_logging` 中 stdout/stderr `reconfigure(encoding="utf-8")` |
| 18 | D-4 韩语注释 | `ui.py:404` 注释翻译为中文 |
| 19 | D-1 端口冲突日志误导 | `main.py` ready 日志措辞调整；`start-dev.ps1` 健康检查校验端口归属 PID |
| 20 | C-2 Mock 数据不自洽 | `data/mock_script.json` 候选人自述改为与演示简历一致（李明哲/Java/订单系统） |

## 不在本轮修复（记录原因）

- B-3 的整体视觉重设计（配色系统/组件库替换）——工作量大，独立排期
- B-7 列表排序/分页——当前数据量下过滤已够用
- A-3 的触发链路重构（`_auto_check_coverage` 挂载点）——先验证 prompt 放宽的效果再决定

## 验证结果（2026-07-07 回归通过）

浏览器 E2E 回归全部通过：对比勾选 → 弹窗对比表格 ✓；删除弹窗出现且不误选中行 ✓（实际删除了重复的 Wei Chen 档案验证全链路）；选中候选人后「问题」Tab 立即显示 10 条清单 ✓；聊天气泡 Markdown 表格/加粗/标题正常渲染 ✓；空闲时「结束面试」禁用、面试中「开始面试」禁用、结束后恢复 ✓；欢迎卡片与各 Tab 空状态 ✓；报告页「建议录用」中文徽章 ✓；mock 面试覆盖进度 0/10 → 2/10（自动判定生效，日志可观测）✓；搜索框过滤/清空 ✓。单元+集成测试 562 个全绿，ruff 通过。

回归中额外发现并修复三个问题：
1. 问题清单勾选框同样存在 `e.value` 事件参数 bug（与 A-1 同源），一并改为 `on_change`；
2. 候选人列表滚动容器（Quasar scroll-area）内容宽度默认按子元素撑开，导致行尾删除按钮被撑出可视区无法点击——新增 `.candidate-scroll .q-scrollarea__content { width:100% }` 修复；
3. 后台任务中调用 `ui.notify`（删除成功提示、对比失败提示）同样触发 slot stack 为空异常——统一包在父容器 slot 内。

## 验证标准

1. 每批次后 `pytest tests/unit tests/integration` 全绿；
2. 浏览器 E2E 回归清单：
   - 勾选 2 名候选人 → 出现"已选 2 人/横向对比"→ 弹出对比表格
   - 点删除 → 出现确认弹窗 → 确认后候选人从列表与 API 消失；且点删除不再误选中候选人
   - 选中已生成问题清单的候选人 → 「问题」Tab 立即显示清单
   - Agent 聊天回复 Markdown 正常渲染（加粗/标题/表格）
   - 空闲时"结束面试"禁用；面试中"开始面试"禁用
   - 首次打开有欢迎引导；各 Tab 空状态有文案
   - 报告页显示"建议录用"中文徽章
   - 面试数轮后覆盖进度 > 0
