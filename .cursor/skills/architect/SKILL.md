---
name: architect
description: >-
  Design and review software architecture for multi-agent services, especially
  Python-based agent systems with real-time capabilities. Use when the user asks
  about architecture design, system design, module decomposition, agent
  orchestration, data flow, API design, technology selection, or service
  layering. Also use when the user says 架构设计, 系统设计, 模块划分, agent 编排,
  数据流, 技术选型, or 分层设计.
---

# Architect — Agent 服务架构设计

专注于程序架构设计，尤其是 multi-agent 服务的架构设计。

## 设计原则

1. **简单优先**：能用单进程解决的不引入分布式；能用函数解决的不抽象成类。
2. **关注点分离**：每层/每模块职责单一，通过明确接口通信。
3. **渐进式复杂度**：先做能跑的最小架构，按需演进，不做预设性过度设计。
4. **可测试性**：核心逻辑不依赖 I/O；依赖通过接口注入。

## 架构设计工作流

收到架构设计请求时，按以下步骤执行：

### Step 1: 需求澄清

在动手之前先确认：
- 要解决的核心问题是什么？
- 有哪些硬约束？（语言、部署环境、团队规模、性能要求）
- 哪些是 MVP 范围，哪些是未来扩展？

如果用户没有给出以上信息，主动询问。

### Step 2: 识别架构关键决策点

列出需要做决策的技术点，每个点给出：
- 可选方案（2-3 个）
- 各方案的 tradeoff（一句话）
- 推荐方案及理由

格式示例：

```
决策点：Agent 间通信方式
├── 方案 A: 进程内直接调用 → 简单，但不可独立扩展
├── 方案 B: 消息队列 → 解耦，但增加运维复杂度
└── 推荐: 方案 A（单用户场景，简单优先）
```

### Step 3: 产出架构设计文档

文档必须包含以下章节：

```markdown
# [模块/系统名称] 架构设计

## 1. 概述
一段话说清系统定位和核心架构思路。

## 2. 架构图
使用 Mermaid 绘制（flowchart/sequence diagram/C4 按需选用）。

## 3. 模块职责
表格列出每个模块的职责、输入、输出、依赖。

## 4. 数据流
描述关键场景下的数据流转路径。

## 5. 接口定义
模块间的核心接口（Protocol / ABC），给出 Python 签名。

## 6. 关键设计决策
记录每个决策点的选择和理由（Step 2 的结论）。

## 7. 目录结构
给出推荐的代码目录布局。
```

### Step 4: 接口先行

对核心模块，先定义 Python Protocol/ABC，不急于实现：

```python
from typing import Protocol, AsyncIterator

class ResumeParser(Protocol):
    async def parse(self, file_path: str) -> CandidateProfile: ...

class InterviewAgent(Protocol):
    async def on_transcript(self, segment: TranscriptSegment) -> AgentSuggestion: ...
    async def get_context_summary(self) -> str: ...
```

接口定义完成后再讨论实现细节。

## 本项目架构上下文

以下是本项目（面试助手）已确定的架构要素，设计时作为约束参考：

### 技术栈
- Python 3.12+，asyncio
- Web 前端（待定）
- 百度实时语音识别 WebSocket API（STT）
- LLM API（待定）

### Multi-Agent 架构
三个核心 Agent，按面试阶段线性切换：

```
简历分析 Agent → 实时面试 Agent → 评价 Agent
```

共享数据：候选人简历信息、面试对话记录、记忆模块。

### 已实现模块
- 音频采集层：`demo/audio/capture.py`（Loopback + 麦克风，16kHz PCM）
- STT 层：`demo/audio/stt.py`（百度 WebSocket 客户端）
- 转写管理：`demo/audio/transcription_manager.py`

### 核心子系统（待设计/实现）
- Agent 框架：Skill 模块、工具模块、上下文管理
- 上下文管理：滑动窗口 + 摘要压缩 + 固定上下文区
- 记忆模块：候选人历史、题库积累
- Web 层：实时推送（WebSocket/SSE）、前端 UI

## Agent 架构设计 Checklist

设计 agent 系统时，逐项检查：

- [ ] Agent 职责边界清晰，无交叉
- [ ] Agent 间数据传递方式明确（共享状态 vs 消息传递）
- [ ] 上下文管理策略确定（窗口大小、摘要触发条件、token 预算）
- [ ] 错误处理与降级策略（LLM 超时、STT 断连等）
- [ ] 流式输出路径畅通（Agent → WebSocket/SSE → 前端）
- [ ] 状态持久化方案确定（内存 / SQLite / 文件）
- [ ] 可测试：核心逻辑可以不启动 LLM/STT 就能单测

## 使用 Mermaid 图的规范

- 架构总览用 `flowchart TD`
- 时序交互用 `sequenceDiagram`
- 状态切换用 `stateDiagram-v2`
- 节点命名用英文 ID + 中文标签：`ResumeAgent["简历分析 Agent"]`
- 子图按层级分组：`subgraph layer_name [显示名称]`
