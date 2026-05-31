# 数据存储

本文档说明文件系统存储方案、MemoryModule 职责划分、录音目录规则以及数据恢复逻辑。

> **存储后端**：系统已从 SQLite 迁移至**基于文件系统的纯文本存储**。所有候选人档案、面试记录、评价报告均以 Markdown + YAML frontmatter 形式存储在 `candidates/` 目录下，无需数据库依赖。

---

## 目录结构总览

```
candidates/
├── index.md                          # 全局候选人目录（YAML frontmatter + Markdown 表格）
└── {candidate_id}/                   # 每个候选人一个 UUID 子目录
    ├── profile.md                    # 候选人档案（YAML frontmatter + 简历 Markdown 正文）
    ├── brief.md                      # 面试简报（Markdown，由 ResumeAgent 生成，复用于多次面试）
    ├── resume.pdf                    # 原始 PDF（上传时复制至此）
    └── interviews/
        ├── index.md                  # 本候选人的面试历史摘要
        └── {interview_id}/           # 每次面试一个 UUID 子目录
            ├── session.json          # 会话元数据（阶段、时间、录音路径）
            ├── transcript.md         # 完整对话记录（YAML frontmatter + 各轮发言）
            └── eval_report.md        # 评价报告（YAML frontmatter + Markdown 正文）
```

根目录由 `.env` 中 `CANDIDATES_DIR` 配置（默认 `candidates`）。

---

## 文件格式说明

### `candidates/index.md`

全局候选人目录，YAML frontmatter 包含所有候选人的结构化列表，供快速搜索。

```yaml
---
candidates:
  - id: 93ba2cdd-a8f6-...
    name: 张三
    created_at: "2026-05-20"
    latest_interview: "2026-05-21"
---
# 候选人目录
| 候选人 | ID | 创建时间 | 最近面试 |
```

---

### `candidates/{id}/profile.md`

YAML frontmatter 存储候选人结构化信息，Markdown 正文为完整简历内容（用于 Agent 读取）。

**frontmatter 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 候选人 UUID |
| `name` | string | 姓名 |
| `created_at` | string | 创建时间（ISO） |
| `resume_pdf` | string | 原始 PDF 相对路径 |
| `email` | string? | 邮箱（可选） |
| `phone` | string? | 电话（可选） |
| `age` | int? | 年龄（可选） |
| `current_position` | string? | 当前职位（可选） |
| `years_of_experience` | int? | 工作年限（可选） |
| `skills` | list[string]? | 技能列表（可选） |

正文为 ResumeAgent 解析出的 Markdown 格式简历全文，由 `MemoryModule.get_resume_markdown()` 单独读取，供 Agent 调用 `file_read` 或直接注入 prompt。

---

### `candidates/{id}/interviews/index.md`

候选人面试历史汇总，YAML frontmatter 列出所有面试条目（含评分和关键结论）。

**frontmatter 中每个 interview 条目**：

| 字段 | 说明 |
|---|---|
| `interview_id` | 面试 UUID |
| `start_time` | 开始时间（ISO） |
| `end_time` | 结束时间（ISO，可为 null） |
| `stage` | 最终阶段（`interviewing` / `completed`） |
| `trigger_mode` | `auto` / `manual` |
| `overall_score` | 综合评分（可为 null） |
| `recommendation` | 推荐结论（可为 null） |
| `key_findings` | 关键结论摘要 |

---

### `candidates/{id}/interviews/{id}/session.json`

会话元数据，JSON 格式：

```json
{
  "interview_id": "uuid",
  "candidate_id": "uuid",
  "start_time": "2026-05-20T10:00:00",
  "end_time": "2026-05-20T11:30:00",
  "stage": "completed",
  "trigger_mode": "auto",
  "recording_candidate_path": "recordings/uuid/full_candidate.wav",
  "recording_interviewer_path": "recordings/uuid/full_interviewer.wav",
  "context_summary": "..."
}
```

---

### `candidates/{id}/brief.md`

面试简报，纯 Markdown 格式，由 ResumeAgent 生成。同一候选人的多次面试共用同一份简报（覆盖写入）。

结构：候选人概况 → 项目考察（最多 3 个，含背景判断和追问角度）→ 技能考察（3-5 项）。

---

### `candidates/{id}/interviews/{id}/transcript.md`

YAML frontmatter 存储面试元数据，正文按轮次格式化面试官和候选人发言。

```markdown
---
interview_id: uuid
candidate_id: uuid
start_time: "2026-05-20T10:00:00"
end_time: "2026-05-20T11:30:00"
rounds: 5
---
# 面试记录 · 张三 · 2026-05-20

## Round 1 · 10:05

**面试官：** 请介绍一下你最近的项目经历

**候选人：** 我最近参与了...

**追问建议：** 项目中遇到的最大技术挑战是什么？

---
```

---

### `candidates/{id}/interviews/{id}/eval_report.md`

YAML frontmatter 存储评价报告结构化数据，正文为 Markdown 格式详细评价文字。

**frontmatter 字段**：

| 字段 | 说明 |
|---|---|
| `interview_id` | 关联面试 UUID |
| `overall_score` | 综合评分（float） |
| `recommendation` | `strong_hire \| hire \| weak_hire \| no_hire` |
| `generated_at` | 生成时间（ISO） |
| `strengths` | 优势列表 |
| `weaknesses` | 不足列表 |
| `dimensions` | 维度评分列表（`dimension`/`score`/`comment`） |

---

## MemoryModule 职责划分

**文件**：`src/storage/memory_module.py`

`MemoryModule` 是唯一对外暴露的存储接口，所有读写操作均通过原子写入（`mkstemp + os.replace`）保证文件安全，避免写入中途崩溃损坏数据。

### 候选人 CRUD

| 方法 | 说明 |
|---|---|
| `save_candidate(profile, resume_markdown)` | 写 `profile.md`（frontmatter + 正文），更新 `candidates/index.md` |
| `get_candidate(candidate_id)` | 读 `profile.md` frontmatter，返回 `CandidateProfile` |
| `get_resume_markdown(candidate_id)` | 读 `profile.md` 正文（frontmatter 之后的 Markdown） |
| `get_candidate_by_name(name)` | 从 index.md 按姓名精确查找 |
| `search_candidates(keyword, limit, offset)` | 从 index.md 按姓名模糊搜索 |
| `delete_candidate(candidate_id)` | 递归删除候选人目录，更新 index.md |

### 面试生命周期

| 方法 | 触发时机 | 说明 |
|---|---|---|
| `start_interview(session)` | `dispatch_to_agent` brief_done 时 | 写 `session.json`（stage=interviewing） |
| `finish_interview(session)` | `close_session()` 时 | 写 `transcript.md`，更新 `session.json`（end_time / stage），更新两级 index |
| `get_interview_detail(interview_id, candidate_id?)` | 历史查询 | 读 `session.json` + 解析 `transcript.md` + 读 `eval_report.md` |

### 面试简报

| 方法 | 说明 |
|---|---|
| `save_brief(candidate_id, content)` | 原子写入 `candidates/{id}/brief.md` |
| `get_brief(candidate_id)` | 读取 `brief.md`，不存在时返回空字符串 |

### 候选人历史记忆

| 方法 | 说明 |
|---|---|
| `get_candidate_history(candidate_id, limit=3)` | 读 `interviews/index.md`，返回最近 N 次面试摘要 + 格式化文字 |

返回 `CandidateHistory(past_interviews, history_summary)`，`history_summary` 字段直接注入到 `session.candidate.history_summary`，供 PromptBuilder Layer 4 使用。

### 评价报告

| 方法 | 说明 |
|---|---|
| `save_eval_report(report)` | 写 `eval_report.md`，更新 `interviews/index.md` 中的评分和关键结论 |
| `get_eval_report(interview_id, candidate_id?)` | 读 `eval_report.md`，反序列化为 `EvalReport` |

---

## 短期记忆 vs 长期记忆

| 维度 | 短期记忆 | 长期记忆 |
|---|---|---|
| 载体 | `InterviewSession` 对象（内存） | 文件系统（`candidates/` 目录） |
| 范围 | 单次面试会话期间 | 跨会话、跨服务重启 |
| 管理方 | `InterviewController`（Agent 层） | `MemoryModule`（Storage 层） |
| 内容 | 实时对话轮次、面试简报（interview_brief）、当前阶段 | 候选人档案（profile.md）、面试简报（brief.md）、历史面试（transcript/eval） |
| 生命周期 | 随 `create_session()` 创建，`close_session()` 清空 | 永久保存，支持查询和删除 |

---

## 录音文件命名规则与目录结构

录音根目录由 `.env` 中 `RECORDINGS_DIR` 配置（默认 `recordings/`）。

```
recordings/
└── {session_id}/                           # 每次面试对应一个 UUID 子目录
    ├── full_candidate.wav                  # 候选人音频完整录音
    ├── full_interviewer.wav                # 面试官音频完整录音
    └── rounds/
        ├── round_001_candidate.wav         # 第 1 轮候选人录音
        ├── round_001_interviewer.wav       # 第 1 轮面试官录音
        ├── round_002_candidate.wav
        ├── round_002_interviewer.wav
        └── ...
```

**命名规则**：

- 目录名：`{session_id}` = `InterviewSession.id`（UUID）
- 完整录音：`full_{source}.wav`，`source` 为 `candidate` 或 `interviewer`
- 轮次录音：`round_{round_number:03d}_{source}.wav`（三位零填充序号）

**写入时机**：

- `AudioRecorder.mark_round_boundary(round_number)` 在 `finalize_round()` 时调用，触发对应轮次录音文件的边界标记和写入
- 完整录音在面试结束（`AudioManager.stop()`）时写入
- 录音路径写回 `session.metadata.recording_candidate_path / recording_interviewer_path`，由 `finish_interview()` 持久化到 `session.json`

---

## 索引重建

`MemoryModule.rebuild_index()` 可从目录结构重建 `candidates/index.md` 和各 `interviews/index.md`，用于数据修复或手动编辑文件后的一致性恢复。

---

## 候选人数据恢复逻辑（`GET /resume/profile`）

```
GET /api/resume/profile?candidate_id=xxx

1. memory.get_candidate(candidate_id)
   ↓ 读 candidates/{id}/profile.md frontmatter → CandidateProfile
   ↓ 若不存在 → 404 not_found

2. session = controller.get_session()
   若 session 非空 且 session.candidate.id == candidate_id
   且 session.interview_brief 非空
   → 直接返回内存中的 session.interview_brief

3. 否则：
   memory.get_brief(candidate_id)
   ↓ 读 candidates/{id}/brief.md → str（不存在时返回空字符串）

4. memory.get_resume_markdown(candidate_id)
   ↓ 读 profile.md 正文

最终返回：
{
  "candidate_id": "...",
  "profile": <CandidateProfile>,
  "brief": "<内存 interview_brief 或 brief.md 内容>",
  "resume_markdown": "<profile.md 正文>"
}
```
