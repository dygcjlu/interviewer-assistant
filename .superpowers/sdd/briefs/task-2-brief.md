# 任务 2：扩展 EvalReport 数据模型

**目标**：为 `EvalReport` 添加 `candidate_id` 和 `question_coverage` 字段，为后续 bug 修复打基础。

**步骤**：

1. 修改 `src/models/evaluation.py`，在 `EvalReport` 类添加两个字段：
   ```python
   @dataclass
   class EvalReport:
       interview_id: str
       candidate_id: str = ""  # 新增：候选人 ID
       overall_rating: str = ""
       strengths: list[str] = field(default_factory=list)
       weaknesses: list[str] = field(default_factory=list)
       recommendation: str = ""
       summary: str = ""
       question_coverage: str = ""  # 新增：问题覆盖率统计，格式 "已覆盖 4/7"
       generated_at: str = ""
   ```

2. 修改 `src/storage/memory_module.py` 的 `get_eval_report` 方法，兼容旧数据：
   ```python
   async def get_eval_report(self, interview_id: str) -> Optional[EvalReport]:
       # ... 现有读取逻辑 ...
       if data:
           # 兼容旧数据缺 candidate_id 和 question_coverage
           if "candidate_id" not in data:
               data["candidate_id"] = ""
           if "question_coverage" not in data:
               data["question_coverage"] = ""
           return EvalReport(**data)
       return None
   ```

3. 运行相关单元测试验证数据模型变更：
   ```bash
   python -m pytest tests/unit/ -k "EvalReport" -v
   ```

4. 提交变更：
   ```bash
   git add src/models/evaluation.py src/storage/memory_module.py
   git commit -m "feat: add candidate_id and question_coverage to EvalReport"
   ```

**验收标准**：
- ✅ `EvalReport` 包含 `candidate_id` 和 `question_coverage` 字段
- ✅ `get_eval_report` 兼容旧数据（缺字段时返回默认值）
- ✅ 相关测试通过
