# /review-next

处理 `docs/review-plan.md` 中下一个待检模块。

## 执行说明

读取 `.cursor/skills/review-all/SKILL.md`，按其定义的完整流程执行**单个模块**的 review：

1. 从 `docs/review-plan.md` 选取优先级最高的 `⏳ 待检` 模块
2. 深度分析（代码质量 + 测试缺口 + 安全 + 性能）
3. 展示报告，等待确认
4. 执行修复 + 补充测试
5. 更新台账状态

完成后停止，等待下一次 `/review-next` 指令。
