# Demo 录制 Checklist

## 启动（Mock 音频，无需真实麦克风/ASR）
```powershell
$env:MOCK_AUDIO = "true"
.venv\Scripts\python -m src.main
# 打开 http://127.0.0.1:8000
```

## 操作脚本（建议录制顺序）
1. 上传候选人 PDF 简历 → 点击「解析简历」
2. 等待 Agent 呈现候选人概况与风险信号
3. 与 Agent 对话补充岗位关注点 → 触发「生成面试简报」
4. 开始面试（选择 auto 触发模式）
5. Mock 音频驱动双声道转写 → 观察实时转写与 AI 追问建议逐段弹出
6. 结束面试 → 生成评价报告 → 导出 PDF

## 录制注意
- 提前准备脱敏简历样本
- 分辨率 ≥ 1280×720，字体缩放适中
- 输出 GIF/截图放入 docs/assets/（见 Task 1.12）
