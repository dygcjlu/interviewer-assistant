# Skill 与工具系统

## 1. Skill 模块（SkillModule）

借鉴 hermes-agent 的 Skill 系统，采用文件系统目录结构，运行时动态加载。

### 1.1 目录结构

```
skills/
├── deep_dive/
│   └── SKILL.md
├── dimension_switch/
│   └── SKILL.md
├── behavioral_probe/
│   └── SKILL.md
└── resume_anchor/
    └── SKILL.md
```

### 1.2 SKILL.md 格式规范

```markdown
---
name: deep_dive
description: 技术细节深挖追问技巧
trigger_hint: 候选人回答较浅、使用了术语但未展开时使用
---

## 使用时机

当候选人给出了正确但浅层的答案，或使用了专业术语却未解释背后原理时，激活本 Skill。

## Prompt 指引

引导 Agent 追问：
- 要求候选人说明该技术的实现原理或底层机制
- 追问在实际项目中遇到的具体问题和解决方案
- 验证候选人是否真正在生产环境使用过该技术
```

### 1.3 内置 Skill 清单

| Skill | 用途 | 典型触发场景 |
|-------|------|------------|
| `deep_dive` | 技术细节深挖追问 | 候选人回答较浅，使用了术语但未展开 |
| `dimension_switch` | 考察维度切换时机判断 | 当前维度已充分考察，或候选人回答反复触达瓶颈 |
| `behavioral_probe` | 行为面试追问（STAR 结构验证） | 候选人描述项目经历时缺乏具体细节或结果数据 |
| `resume_anchor` | 以简历具体项目为锚点展开提问 | 需要绑定候选人真实经历，降低套答概率 |

### 1.4 加载机制

```python
class SkillLoader:
    def __init__(self, skills_dir: Path): ...

    def load_index(self) -> list[SkillMeta]:
        """扫描 skills/ 目录，读取每个 SKILL.md 的 frontmatter，返回索引列表"""
        ...

    def load_skill(self, name: str) -> SkillContent:
        """按需加载指定 Skill 的完整 SKILL.md 内容"""
        ...
```

#### 返回类型

```python
@dataclass
class SkillMeta:
    name: str                              # Skill 标识名（目录名）
    description: str                       # 一句话描述（注入 prompt 索引层）
    trigger_hint: str                      # 使用时机提示

@dataclass
class SkillContent:
    meta: SkillMeta
    full_text: str                         # SKILL.md 完整 Markdown 内容
```

> 完整定义见 [共享数据结构](./data-models.md)

system prompt 中只注入 Skill 索引（名称 + `description` 字段），Agent 决策需要某个 Skill 时再加载完整内容，避免所有 Skill 内容堆满 prompt。

### 1.5 Skill 作为 Tool 暴露（progressive disclosure）

参考 hermes-agent 的设计，Skill 通过两个 Tool 暴露给 LLM：

```python
@registry.register(description="列出当前可用的面试技巧索引（名称 + 一句话描述）")
async def skills_list() -> str:
    """返回当前 Agent 可用 Skill 的索引列表"""
    ...

@registry.register(description="加载指定面试技巧的完整内容")
async def skill_view(name: str) -> str:
    """返回指定 Skill 的 SKILL.md 完整内容"""
    ...
```

- system prompt 注入 Skill 索引（第 2 层），LLM 判断需要时调用 `skill_view` Tool 获取完整内容
- **使用范围**：仅 ResumeAgent（出题时参考追问技巧）和 EvalAgent（评价时参考考察维度标准）配置 Skill
- **InterviewAgent 不使用 Skill**：实时面试对延迟敏感，追问建议直接基于 system prompt 指令 + 上下文生成

---

## 2. ToolRegistry（工具注册与调度）

统一管理所有 Agent 可调用的工具，提供注册机制和调度管道。

### 2.1 ToolEntry 数据结构

```python
@dataclass
class ToolEntry:
    name: str                              # 工具名称（LLM function call 中的函数名）
    description: str                       # 工具描述（注入 prompt）
    parameters_schema: dict                # JSON Schema，描述入参
    fn: Callable[..., Awaitable[Any]]      # 异步执行函数
    pre_hook: Callable | None = None       # 执行前钩子（如参数校验、权限检查）
    post_hook: Callable | None = None      # 执行后钩子（如结果格式化、日志记录）
```

### 2.2 注册方式

```python
registry = ToolRegistry()

@registry.register(description="解析简历 PDF，提取候选人结构化信息")
async def parse_resume(file_path: str) -> dict:
    ...
```

### 2.3 调度管道

```
LLM 返回 function_call
    → ToolRegistry.dispatch(name, args)
        → 1. 参数校验（schema validate）
        → 2. pre_hook（若有）
        → 3. fn(**args) 执行工具
        → 4. post_hook（若有，记录轨迹/日志）
        → 5. 返回结果给 Agent
```

### 2.4 ToolRegistry 完整接口

```python
class ToolRegistry:
    """工具注册中心与调度器"""

    def register(self, description: str,
                 parameters_schema: dict | None = None) -> Callable:
        """装饰器 — 注册工具函数，自动提取函数签名生成 schema"""

    def get_tool(self, name: str) -> ToolEntry | None:
        """按名称查询已注册的工具"""

    def get_schemas(self, names: list[str] | None = None) -> list[ToolSchema]:
        """获取工具的 JSON Schema 列表（传入 LLM 的 tools 参数）
        names 为 None 时返回全部，否则按名称过滤"""

    async def dispatch(self, name: str, arguments: str) -> str:
        """调度执行工具
        参数:
          name — 工具名（LLM function call 返回的函数名）
          arguments — JSON 字符串形式的参数（LLM function call 返回的 arguments）
        返回: 工具执行结果的字符串表示（JSON 序列化后返回给 LLM）
        内部管道: schema 校验 → pre_hook → fn(**args) → post_hook → 序列化"""
```

`ToolSchema` 类型（对齐 OpenAI function calling 格式）定义见 [LLM Client](./llm-client.md)。

---

## 3. 设计决策

### 决策 9: Skill 加载策略

```
├── 方案 A: Skill 逻辑直接编写进代码，每个 Agent 预定义固定的 Skill 行为
├── 方案 B: 文件系统目录结构（skills/{name}/SKILL.md），运行时动态扫描加载
└── 选择: 方案 B
    理由: 文件系统方案将 Skill 的 prompt 内容与代码解耦，新增/修改 Skill 无需
         改动代码，只需编辑 SKILL.md。system prompt 只注入索引，按需加载完整
         内容，避免无关 Skill 占用 token 预算。
```
