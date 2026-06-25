# 简历亮点功能增强建议

> 记录待讨论的功能增强点，按优先级排列。每个点确认后再单独展开设计。

---

## 一、让项目"可展示"（最高优先级）

### 1. README + Demo
- 添加清晰的 README：架构图（复用 `docs/arc/overview.md` 的 mermaid）、功能截图或 GIF 录屏、一键启动说明
- 目标：面试官扫 GitHub 2 分钟内能看懂项目是什么、能做什么

### 2. 面试报告导出（PDF/Markdown）
- 新增 `GET /api/interview/{id}/report/export` 端点
- 把 `transcript.md` + `EvalReport` 渲染成可下载的 PDF（候选库：`weasyprint` / `reportlab`）
- 意义：功能完整闭环，用户真实会用的能力

---

## 二、技术亮点补强（中优先级）

### 3. 跨平台音频支持（WebRTC 方案）
- 当前限制：Windows-only WASAPI
- 方案：浏览器麦克风（WebRTC getUserMedia）采集音频，通过 WebSocket 传 PCM 流到后端
- 意义：解除平台限制，提升开源传播力，技术含量足够讲

### 4. 结构化面试模式
- 面试简报生成后，同时生成有序问题清单（含预期考察点）
- UI 显示当前问题进度，标记哪些已覆盖、哪些未问到
- 意义：产品功能更完整，体现对面试场景的深度理解

### 5. 多候选人横向对比
- 新增 `GET /api/candidates/compare?ids=a,b,c` 接口
- LLM 基于多份 EvalReport 生成横向对比摘要（各自优劣势、岗位匹配度）
- 实现成本低（主要是 prompt 设计 + 聚合接口），业务价值直接

---

## 三、工程规范信号（加分项）

### 6. CI 完整化
- 确保 CI 跑 `pytest tests/unit tests/integration`（当前 CI 分支状态待确认）
- README 加测试通过 badge
- 意义：工程规范信号，面试官看代码时会注意

### 7. 可观测性（结构化日志 + metrics）
- 项目已有 `src/utils/metrics.py`，在此基础上暴露关键指标：LLM 延迟、ASR 延迟、追问触发次数
- 通过 `GET /metrics` 端点输出（Prometheus 格式或简单 JSON）
- 意义：生产意识信号

---

## 优先顺序汇总

| 优先级 | 改动 | 预估工作量 | 简历价值 |
|---|---|---|---|
| 🔴 必做 | README + 截图/GIF | 半天 | 极高 |
| 🔴 必做 | 报告导出 PDF | 1天 | 高（功能闭环） |
| 🟡 推荐 | 结构化面试模式 | 2-3天 | 高（场景深度） |
| 🟡 推荐 | 多候选人横向对比 | 1天 | 高（业务价值） |
| 🟢 加分 | CI 跑测试 + badge | 半天 | 中（工程信号） |
| 🟢 加分 | WebRTC 跨平台音频 | 3-5天 | 高（技术含量） |
