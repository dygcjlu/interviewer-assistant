# Agent 驱动开发方案

> 使用 **Claude Code Agent Teams** 实现多模块并行自主开发。

## 背景认知

"单个 Agent 负责一个大任务 → 上下文爆炸 → 质量崩塌"是已知失败模式。Cursor 研究团队在用多 Agent 系统自主开发浏览器引擎时验证了这一点，并给出了解法：**分层角色 + 新鲜上下文 + 结果回传**。

---

## Claude Code Agent Teams

### 版本要求

Claude Code v2.1.32+，需开启实验性功能：

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

或在 `~/.claude/settings.json` 中配置：

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

### 核心机制

| 特性 | 说明 |
|---|---|
| 每个 Teammate 独立上下文窗口 | 天然隔离，不互相污染 |
| 共享任务列表 + 文件锁防竞争 | 支持并行认领任务 |
| Teammate 可以直接互发消息 | 前端 Agent 可通知 API Agent 接口变更 |
| Lead 协调，Teammate 各自闭环 | 多 Agent，每次全新上下文 |

### Teammate 分工设计

```
Lead（协调者，只做规划和任务分发，不写代码）
├── teammate: infra-storage      ← SQLite / aiosqlite CRUD，参考 memory-and-storage.md
├── teammate: llm-client         ← LLMClient OpenAI 兼容封装，参考 llm-client.md
├── teammate: audio-stt          ← AudioCapture + STT + TranscriptionManager，参考 audio-and-stt.md
│                                   ⚠️ WASAPI 仅限 Windows；Linux 开发阶段必须用 MockAudioCapturer
├── teammate: agent-framework    ← Orchestrator + 三个 Agent，参考 agent-orchestrator.md
├── teammate: context-prompt     ← ContextManager + PromptBuilder，参考 context-and-prompt.md
├── teammate: web-layer          ← FastAPI routes + WebSocket，参考 web-layer.md
└── teammate: frontend           ← Vue 3 SPA，参考 web-layer.md（前端部分）
```

**分工原则**：按依赖关系分阶段启动，底层先行：

```
第零步（人工完成，不交给 Agent）：提交 src/models/ 下所有共享数据结构定义
第一批（无依赖）：infra-storage、llm-client
第二批（依赖第一批）：audio-stt、agent-framework
第三批（依赖前两批）：context-prompt、web-layer
第四批（依赖 web-layer API 稳定）：frontend
```

**每批完成后，Lead 执行一次层间隔离审查**（不写代码，只读代码）：
- 有无跨层直接调用（如 Agent 层直接导入 SQLite）
- 有无绕过 Protocol 抽象的硬编码实现
- 有无违反"单进程 asyncio"约束（如 `threading.Thread`）

> 原因：Anthropic 研究发现 Agent 评估自身输出时倾向于夸赞质量；独立的 Lead review 比 Agent 自检更可靠。

### CLAUDE.md 分层结构

Claude Code 按需懒加载子目录的 CLAUDE.md，每个 Teammate 只获取自己目录的上下文。

```
项目根/
├── CLAUDE.md                    ← 全局规则（技术栈、层间隔离约定，基于 AGENTS.md 精炼）
├── src/
│   ├── CLAUDE.md                ← 后端总规：asyncio 单进程、不引入 LangGraph 等
│   ├── agents/
│   │   └── CLAUDE.md            ← 指向 docs/arc/agent-orchestrator.md 关键约束
│   ├── audio/
│   │   └── CLAUDE.md            ← 指向 docs/arc/audio-and-stt.md 关键约束
│   ├── framework/
│   │   └── CLAUDE.md            ← 指向 docs/arc/context-and-prompt.md 关键约束
│   ├── llm/
│   │   └── CLAUDE.md            ← 指向 docs/arc/llm-client.md 关键约束
│   ├── storage/
│   │   └── CLAUDE.md            ← 指向 docs/arc/memory-and-storage.md 关键约束
│   └── web/
│       └── CLAUDE.md            ← 指向 docs/arc/web-layer.md 关键约束
└── frontend/
    └── CLAUDE.md                ← Vue3 规则 + 指向 docs/arc/web-layer.md 前端部分
```

**CLAUDE.md 写法原则**（保持 200 行以内）：
- 写规则，不写 README；每条规则可以改写成"绝不做 X"的形式
- 在文件头声明"本模块负责什么，不负责什么"
- 列出与其他模块的接口约定（数据类型、函数签名）
- 列出禁止事项（如"不引入多进程"、"不绕过 Orchestrator 直接跨 Agent 通信"）

### 自动测试与验证（Hooks）

在 `.claude/settings.json` 中配置：

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "cd /home/dengyg/agent/interviewer-assistant && python -m pyflakes src/ 2>&1 | tail -10"
          }
        ]
      }
    ],
    "TaskCompleted": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd /home/dengyg/agent/interviewer-assistant && python -m pytest tests/ -q && echo 'PASS' || exit 2"
          }
        ]
      }
    ],
    "TeammateIdle": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd /home/dengyg/agent/interviewer-assistant && python -m pytest tests/ -q && echo 'PASS' || exit 2"
          }
        ]
      }
    ]
  }
}
```

**三级验证策略**：

| 触发时机 | Hook 事件 | 检查内容 | 原因 |
|---|---|---|---|
| 每次文件写入后 | `PostToolUse(Edit\|Write)` | `pyflakes` 静态语法检查 | 快速（<1s），不会因模块未完成而误报 |
| Teammate 准备空闲时 | `TeammateIdle` | `pytest tests/<模块>/` | 中等粒度，强制自验证 |
| 任务标记完成时 | `TaskCompleted` | `pytest tests/ -q` 全量 | 最终门控，失败则退出码 2，强制继续修复 |

> ⚠️ 不要在 PostToolUse 跑全量 pytest：开发中的模块测试必然失败，Agent 会陷入"修一个错误 → 整体失败 → 循环"的死循环，浪费大量 token。

`TaskCompleted` / `TeammateIdle` hook 返回退出码 `2` 时，Teammate 被阻止标记完成，必须继续修复——即"强制自我验证"。

### 启动示例

```bash
claude

# 在 Claude Code 交互中输入：
# 创建一个 Agent Team，按照 docs/arc/ 下的架构文档开发面试助手项目。
# 分配 7 个 Teammate，分工如下：
# 1. infra-storage：负责 src/storage/ + src/models/，参考 docs/arc/memory-and-storage.md 和 docs/arc/data-models.md
# 2. llm-client：负责 src/llm/，参考 docs/arc/llm-client.md
# 3. audio-stt：负责 src/audio/，参考 docs/arc/audio-and-stt.md
# 4. agent-framework：负责 src/agents/，参考 docs/arc/agent-orchestrator.md
# 5. context-prompt：负责 src/framework/，参考 docs/arc/context-and-prompt.md 和 docs/arc/skill-and-tool.md
# 6. web-layer：负责 src/web/ + src/main.py，参考 docs/arc/web-layer.md
# 7. frontend：负责 frontend/，参考 docs/arc/web-layer.md 中的前端部分
#
# 每个 Teammate：
# - 必须为自己负责的模块编写测试，测试通过后才能标记任务完成
# - 完成时给 Lead 写交接报告（实现了什么、接口约定、待注意事项、未覆盖的边界）
# - 按依赖顺序分批启动：第一批 infra-storage + llm-client，完成后再启动下一批
```

### 会话中断恢复机制

Agent Teams 已知限制：`/resume` 无法恢复 in-process teammates；Lead 重启后 Teammate 全部失效。在长任务中会话中断是大概率事件。

**方案**：每个 Teammate 在完成每个子任务后，向 `progress/<模块名>.md` 追加一条进度记录。Lead 重启后可从这些文件重建状态，无需重新开发已完成的部分。

目录结构：

```
progress/
├── infra-storage.md
├── llm-client.md
├── audio-stt.md
├── agent-framework.md
├── context-prompt.md
├── web-layer.md
└── frontend.md
```

每条进度记录格式（Teammate 在各自 CLAUDE.md 中声明此义务）：

```markdown
## [时间戳] 子任务完成

**已完成**：UserRepository CRUD，测试通过
**关键文件**：src/storage/user_repo.py:UserRepository
**对外接口**：`create_user(profile: CandidateProfile) -> int`
**下一步**：等待 agent-framework 提供 InterviewSession schema
```

> 这解决了 Anthropic 研究中的核心问题："没有跨会话记忆导致长任务失败"。`progress/*.md` 就是本项目的 `claude-progress.txt`。

---

### Teammate 交接报告模板

要求每个 Teammate 完成时输出以下结构（可在各模块 CLAUDE.md 中声明）：

```markdown
## 交接报告 - [模块名]

### 已实现
- ...

### 对外接口（其他模块需要知道的）
- 类/函数签名
- 数据结构

### 测试覆盖
- 通过的测试文件列表

### 已知偏差或风险
- ...

### 未完成/需要其他 Teammate 配合的
- ...
```

---

## 开发前必须完成的准备工作

> "架构和指令比 Agent 能力更重要。Agent 会严格按指令执行，指令写差了结果就差。" — Cursor Research

### 0. 手动提交共享数据结构和 Protocol 定义（最高优先级）

**必须在启动任何 Agent 之前由人工完成并 git commit**，原因：
- `src/models/` 是所有 Teammate 的公共合同；infra-storage 和 agent-framework 同时需要它
- Protocol 定义（`AudioCapturer`、`STTEngine`、`LLMClient`）让第二批 Teammate 无需等待底层

具体内容：
- `src/models/session.py` — `InterviewSession` dataclass（含所有字段，不可为 Any）
- `src/models/candidate.py` — `CandidateProfile` dataclass
- `src/models/evaluation.py` — `EvalReport` dataclass
- `src/audio/protocol.py` — `AudioCapturer` / `STTEngine` Protocol
- `src/llm/protocol.py` — `LLMClient` Protocol
- `src/audio/mock.py` — `MockAudioCapturer` 实现（返回静音数据，用于 Linux 开发环境替代 WASAPI）

### 1. 精炼 CLAUDE.md 层级

基于 `AGENTS.md` 和 `docs/arc/` 各文档，为每个模块目录写专属 CLAUDE.md（200 行以内），重点写：
- 本模块的边界（负责什么、不负责什么）
- 禁止事项（有 "绝不" 字样的硬约束）
- 与相邻模块的接口约定
- **进度记录义务**：每完成一个子任务，向 `progress/<模块名>.md` 追加一条记录

每个模块 CLAUDE.md 还需声明平台兼容规则：`src/audio/CLAUDE.md` 中注明"绝不直接 import pyaudio 或 wasapi；开发阶段使用 MockAudioCapturer"。

### 2. 建立测试框架骨架

在 `tests/` 目录下按模块预建测试目录结构，每个模块至少有一个 `test_*.py` 占位文件，说明"什么通过算完成"。Agent 将以此作为完成标准。

```
tests/
├── conftest.py
├── test_llm/
├── test_audio/
├── test_agents/
├── test_framework/
├── test_storage/
└── test_web/
```

### 3. 建立进度目录骨架

创建 `progress/` 目录并为每个模块建立初始文件，这是会话中断恢复的基础：

```bash
mkdir -p progress
for m in infra-storage llm-client audio-stt agent-framework context-prompt web-layer frontend; do
  echo "# Progress: $m" > progress/$m.md
done
git add progress/ && git commit -m "init: progress tracking files"
```

### 4. 接口契约文档

为底层模块（LLMClient、STTEngine、AudioCapturer 等 Protocol 抽象）预先写好 Protocol 定义（已在步骤 0 中完成），让上层 Teammate 可以独立开发而不等待底层完成。

---

## 已知限制（Agent Teams 实验性阶段）

| 限制 | 影响 | 缓解措施 |
|---|---|---|
| `/resume` 无法恢复 in-process teammates | Lead 重启后 Teammate 全部失效 | `progress/*.md` 进度文件，重启后 Lead 从文件重建状态 |
| Teammate 不能嵌套（不能再 spawn 子 Team） | 单个 Teammate 无法并行化内部任务 | 任务拆分粒度要合理，单 Teammate 负责单一聚合模块 |
| Task 状态有时不自动更新 | 依赖任务可能被阻塞 | Lead 定期轮询并手动确认已完成任务的状态 |
| 关闭时 Teammate 需完成当前 tool call 才退出 | 强制关闭可能丢失进度 | 正常结束前等待所有 Teammate idle |
| Agent 倾向于夸赞自身输出（自评偏差） | 质量问题难被 Teammate 自检发现 | 每批次由 Lead 做独立层间隔离 review |

## 参考资料

- [Cursor: Towards self-driving codebases](https://cursor.com/blog/self-driving-codebases)
- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic: Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- [Claude Code: Agent Teams](https://code.claude.com/docs/en/agent-teams)
- [Claude Code: Custom Subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code: Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Hierarchical CLAUDE.md Best Practices](https://agentpatterns.ai/instructions/hierarchical-claude-md/)
