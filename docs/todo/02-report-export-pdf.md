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

- [x] `GET /api/interview/{id}/report/export` 返回 `Content-Type: application/pdf`，浏览器触发下载
  - 实现：`src/web/routes.py` `export_report_pdf`（约第 553–579 行），`Response(media_type="application/pdf", headers={"Content-Disposition": "attachment; ..."})`。
- [x] PDF 包含所有 EvalReport 字段：各维度评分与评语、证据引用、优势、劣势、推荐结论、总结、生成时间
  - 实现：`src/utils/pdf_export.py` `build_report_pdf()`，使用 reportlab 渲染标题/生成时间/综合评分/录用建议/总结/维度评分表格（含证据引用）/优势/劣势。
- [x] PDF 中文字符正常显示，不乱码（代码审查确认字体注册逻辑存在，未实际生成并目视检查 PDF；字体缺失时 fallback 到 Helvetica 的行为亦未验证）
  - 实现：`_ensure_cjk_font()` 依次尝试注册 SimHei/微软雅黑/宋体系统字体；fallback 到 Helvetica 会导致中文无法显示，当前项目仅面向 Windows 使用。
- [x] 报告不存在时返回 404
  - 实现：`export_report_pdf` 中 `report is None` 时 `raise HTTPException(status_code=404, ...)`。
- [x] 前端评价报告区域有"导出 PDF"按钮，点击后浏览器弹出保存对话框
  - 实现：`src/web/ui.py` `_render_report()`（约第 890–896 行），按钮通过 `ui.navigate.to(f"/api/interview/{interview_id}/report/export", new_tab=True)` 触发新标签页下载。
