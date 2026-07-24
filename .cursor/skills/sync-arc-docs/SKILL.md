---
name: sync-arc-docs
description: >-
  Syncs interviewer-assistant docs/arc architecture docs with current src/.
  Use when the user asks to update, revise, or sync docs/arc, architecture
  overview docs, or keep arc docs aligned after refactors.
disable-model-invocation: true
---

# Sync docs/arc with source

对照当前 `src/`（配置入口为 `src/config.py` + `.env`），修订 `docs/arc/` 下架构文档，使其与实现一致。本 skill 即完整工作说明，无需其它提示词文档。

## Announce

开始时简短说明：正在用 `sync-arc-docs` 同步 `docs/arc`。

## Goal

使 `docs/arc/` 描述与仓库实现一致：职责、主链路、关键类/路径、配置入口可读且可核对源码。文档保持简洁，只写核心逻辑。

## Before editing

1. 读 `CLAUDE.md`「架构文档」表，确认当前文档清单与职责划分
2. 确认范围：全量 / 指定文档 / 指定主题（未指定则默认**全量**）
3. 文档 ↔ 源码对照见 [doc-source-map.md](doc-source-map.md)

## Principles（强制）

1. **以源码为准**：文档与代码冲突时改文档；只写当前有效实现；删除过时/历史说明
2. **保持精简**：概述职责、主链路、关键类/路径、配置入口即可；避免大段粘贴代码与啰嗦叙述（与 `CLAUDE.md` 要求一致）
3. **口径统一**：跨文档共用同一套名称与分层（MainAgent / InterviewController / ResumeAgent / InterviewAgent / EvalAgent；存储为 `candidates/` 文件系统，非 SQLite；配置经 `get_settings()`）
4. **发现问题要报**：分析中发现冗余、死代码、逻辑错误、文档/代码双双过时等，单独列出（路径 + 现象 + 建议）；**不要擅自改业务源码**（除非用户明确要求）
5. **清单同步**：若增删 `docs/arc/` 文档或职责边界变化，同步更新 `CLAUDE.md`「架构文档」表

## Forbidden

- 为「补全」而臆造未在源码中存在的模块或行为
- 大段保留「旧实现 vs 新实现」对比文
- 未经确认修改 `src/` 业务逻辑
- 对本项目派发**并发 subagent**（本仓库禁止；同步在主会话内顺序完成）

## Workflow

### Wave 1 — 按文档修订（主会话顺序执行）

按 [doc-source-map.md](doc-source-map.md) 逐篇或按相关组合处理（建议组合见下表）。**不要**并行派发 subagent。

| 组合 | 文档 |
|------|------|
| A 总览与流程 | `overview.md`、`flows.md` |
| B Agent 与 API | `agents.md`、`api.md` |
| C 提示与记忆 | `prompt-assembly.md`、`context-memory.md` |
| D 存储与 LLM | `storage.md`、`llm-providers.md` |

**每篇流程**：读现有文档 → 读对应源码 → 标差异 → 直接改文档 → 自检（类名/路径/时序是否仍成立）。

### Wave 2 — 交叉一致

模块改完后：

1. 核对 `overview.md` 分层图与 `flows.md` 时序，与 `agents.md` / `api.md` / `storage.md` 无矛盾
2. 核对 `prompt-assembly.md` 与 `context-memory.md` 对 ContextManager / PromptBuilder / USER.md 的描述一致
3. 若文档清单或职责划分有变，更新 `CLAUDE.md`「架构文档」表

### Deliverables（必须输出）

1. **已修订文档列表**（每篇一句话说明改了什么）
2. **问题清单**（冗余 / 错误 / 风险；路径 + 现象 + 建议；可空）
3. **仍不确定、需人工确认的点**（可空）

可选：写入 `docs/superpowers/reports/YYYY-MM-DD-arc-docs-sync-report.md`。

## Partial sync

用户只点名部分文档时：只改这些文档；若触及主链路（启动组装、面试状态机、转写→追问、存储路径），再检查 `overview.md` / `flows.md` / `CLAUDE.md` 是否需改。

## Self-check

- [ ] 未改 `src/`（除非用户要求）
- [ ] 无臆造行为；无大段旧 vs 新对比
- [ ] 类名/路径在源码中可定位
- [ ] 跨文档口径一致（Agent 名称、存储后端、配置入口）
- [ ] `CLAUDE.md` 架构文档表与 `docs/arc/` 实际文件一致
- [ ] 三项交付齐全
