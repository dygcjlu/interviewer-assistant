# #2 面试报告导出 PDF

## 目标

面试结束生成评价报告后，支持一键导出为 PDF，浏览器直接下载，实现功能完整闭环。

## 范围

- 新增 `GET /api/interview/{id}/report/export` 端点
- 将 EvalReport（维度评分、优劣势、推荐结论、总结）渲染为 PDF
- 前端评价报告区域增加"导出 PDF"按钮，点击触发浏览器下载
- PDF 内容仅包含 EvalReport，不含转写记录或面试简报

## 技术选型备选

- `weasyprint`：HTML → PDF，样式灵活，适合富文本排版
- `reportlab`：纯代码生成 PDF，无需 HTML 模板，依赖更轻

## 验收条件

- [ ] `GET /api/interview/{id}/report/export` 返回 `Content-Type: application/pdf`，浏览器触发下载
- [ ] PDF 包含所有 EvalReport 字段：各维度评分与评语、证据引用、优势、劣势、推荐结论、总结、生成时间
- [ ] PDF 中文字符正常显示，不乱码
- [ ] 报告不存在时返回 404
- [ ] 前端评价报告区域有"导出 PDF"按钮，点击后浏览器弹出保存对话框
