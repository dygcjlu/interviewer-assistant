# 共享数据结构定义

本文档定义所有跨模块共享的核心数据结构，是各模块间的**数据契约**。各模块接口方法签名中引用的类型均在此定义。

> 对应代码位置：`src/models/` 目录

---

## 1. 面试阶段枚举

```python
class InterviewStage(str, Enum):
    """面试会话的生命周期阶段"""
    IDLE = "idle"                        # 空闲，无活跃会话
    RESUME_ANALYSIS = "resume_analysis"  # 简历分析阶段
    INTERVIEWING = "interviewing"        # 面试进行中
    EVALUATING = "evaluating"            # 评价生成中
    COMPLETED = "completed"              # 面试已完成
```

使用方：Orchestrator 状态机、WebSocket `status` 消息、前端路由守卫。

---

## 2. 面试会话

### InterviewSession

所有 Agent 共享的核心数据容器（运行时单例，不持久化，面试结束后归档到 SQLite）：

```python
@dataclass
class InterviewSession:
    id: str
    candidate: CandidateProfile           # 候选人画像（简历解析结果）
    question_plan: list[InterviewQuestion] # 面试题目清单
    rounds: list[ConversationRound]       # 对话轮次记录（实时积累）
    stage: InterviewStage                  # 当前阶段
    context_summary: str                   # 摘要区内容（ContextManager 维护）
    covered_dimensions: set[str]           # 已覆盖的考察维度
    working_notes: str                     # 候选人关键表现标注（亮点/短板，压缩时保留）
    metadata: SessionMetadata
```

### ConversationRound

```python
@dataclass
class ConversationRound:
    round_number: int
    interviewer_text: str                  # 面试官发言（STT 转写 / 手动输入）
    candidate_text: str                    # 候选人回答（STT 转写）
    llm_suggestion: str | None = None      # LLM 生成的追问建议
    interviewer_audio_path: str | None = None
    candidate_audio_path: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
```

### SessionMetadata

```python
@dataclass
class SessionMetadata:
    candidate_id: str
    start_time: datetime
    end_time: datetime | None = None
    trigger_mode: str = "auto"             # "auto" | "manual"
    total_rounds: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
```

---

## 3. 候选人画像

由简历解析工具（`ResumeParser`）输出，贯穿整个面试生命周期。

### CandidateProfile

```python
@dataclass
class CandidateProfile:
    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    education: list[Education] = field(default_factory=list)
    work_experience: list[WorkExperience] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    projects: list[ProjectExperience] = field(default_factory=list)
    resume_text: str = ""                  # PyMuPDF 提取的原始文本
    resume_summary: str = ""               # LLM 生成的结构化摘要（注入 prompt 固定区）
    history_summary: str | None = None     # 历史面试摘要（MemoryModule 注入，prompt 第 4 层）
```

### Education

```python
@dataclass
class Education:
    school: str
    degree: str                            # "本科" | "硕士" | "博士" 等
    major: str
    start_year: int | None = None
    end_year: int | None = None
```

### WorkExperience

```python
@dataclass
class WorkExperience:
    company: str
    title: str                             # 职位
    duration: str                          # "2022.03 - 2024.06"
    description: str
```

### ProjectExperience

```python
@dataclass
class ProjectExperience:
    name: str
    role: str
    tech_stack: list[str]
    description: str
    highlights: list[str]                  # 关键成果
```

---

## 4. 面试题目

### InterviewQuestion

```python
@dataclass
class InterviewQuestion:
    id: int
    dimension: str                         # 考察维度（"系统设计" | "算法" | "项目经验" | ...）
    question: str                          # 题目文本
    follow_ups: list[str]                  # 预设追问点
    difficulty: str = "medium"             # "easy" | "medium" | "hard"
    source: str = "auto"                   # "auto"（LLM 生成）| "manual"（面试官添加）
    is_covered: bool = False               # 面试中是否已使用
```

---

## 5. 评价报告

### EvalReport

```python
@dataclass
class EvalReport:
    id: str
    interview_id: str
    dimensions: list[DimensionScore]       # 分维度评分
    overall_score: float                   # 综合评分（1-10）
    strengths: list[str]                   # 优势列表
    weaknesses: list[str]                  # 不足列表
    recommendation: str                    # "strong_hire" | "hire" | "weak_hire" | "no_hire"
    summary: str                           # 综合文字评价
    generated_at: datetime
```

### DimensionScore

```python
@dataclass
class DimensionScore:
    dimension: str                         # 考察维度名称
    score: float                           # 1-10
    comment: str                           # 该维度评价说明
    evidence: list[str]                    # 支撑证据（候选人原话引用）
```

---

## 6. LLM 消息

对齐 OpenAI Chat Completion API 消息格式（所有 OpenAI 兼容模型通用）。

### Message

```python
@dataclass
class Message:
    role: str                              # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCallInfo] | None = None   # assistant 消息中的工具调用
    tool_call_id: str | None = None        # role="tool" 时关联的调用 ID
```

### ToolCallInfo

```python
@dataclass
class ToolCallInfo:
    id: str
    type: str = "function"
    function: FunctionCallInfo
```

### FunctionCallInfo

```python
@dataclass
class FunctionCallInfo:
    name: str                              # 工具名称
    arguments: str                         # JSON 字符串形式的参数
```

**数据流向**：`PromptBuilder.build()` → `list[Message]` → `LLMClient.chat()` / `chat_stream()`

---

## 7. Token 用量统计

### TokenUsageInfo

```python
@dataclass
class TokenUsageInfo:
    total_used: int                        # 当前上下文总 token 数
    budget: int                            # token 预算上限
    fixed_zone_tokens: int                 # 固定区占用
    summary_zone_tokens: int               # 摘要区占用
    window_zone_tokens: int                # 滑动窗口占用
    is_compressing: bool                   # 是否正在后台压缩
    utilization: float                     # 预算使用率（0.0 - 1.0）
```

使用方：`ContextManager.token_usage` 属性返回此类型，前端通过 WebSocket `token_usage` 消息展示。

---

## 8. 录音结果

### RecordingResult

`AudioRecorder.stop_recording()` 的返回值：

```python
@dataclass
class RecordingResult:
    session_id: str
    full_candidate_path: str               # 候选人完整录音路径
    full_interviewer_path: str             # 面试官完整录音路径
    round_slices: list[RoundSlice]
    total_duration_sec: float
```

### RoundSlice

```python
@dataclass
class RoundSlice:
    round_number: int
    candidate_audio_path: str
    interviewer_audio_path: str
    start_time_sec: float
    end_time_sec: float
```

---

## 9. Skill 元数据

### SkillMeta

`SkillLoader.load_index()` 返回的索引项：

```python
@dataclass
class SkillMeta:
    name: str                              # Skill 标识名（目录名）
    description: str                       # 一句话描述（注入 prompt 索引层）
    trigger_hint: str                      # 使用时机提示
```

### SkillContent

`SkillLoader.load_skill()` 返回的完整内容：

```python
@dataclass
class SkillContent:
    meta: SkillMeta
    full_text: str                         # SKILL.md 完整 Markdown 内容
```

---

## 10. 类型归属与代码位置

| 类型 | 代码文件 |
|------|---------|
| InterviewStage, InterviewSession, ConversationRound, SessionMetadata | `src/models/session.py` |
| CandidateProfile, Education, WorkExperience, ProjectExperience | `src/models/candidate.py` |
| InterviewQuestion | `src/models/session.py` |
| EvalReport, DimensionScore | `src/models/evaluation.py` |
| Message, ToolCallInfo, FunctionCallInfo | `src/models/message.py` |
| TokenUsageInfo | `src/models/session.py` |
| RecordingResult, RoundSlice | `src/audio/recorder.py` |
| SkillMeta, SkillContent | `src/framework/skill.py` |
