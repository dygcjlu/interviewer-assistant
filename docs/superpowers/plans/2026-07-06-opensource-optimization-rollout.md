---
change: opensource-optimization-rollout
design-doc: docs/superpowers/specs/2026-07-06-opensource-optimization-rollout-design.md
base-ref: 5494466abf92d32b5f602c6ccaaacec1d9f3bc10
---

# 开源化优化落地 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按四阶段顺序落地 11 项开源化优化（工程规范、Token 精确计数、追问真流式、候选人真实姓名去重、PDF 测试、关键路径测试补齐、`memory_module.py` 拆分、工具调用可视化），全部保持对外接口/行为兼容，并对 3 项前端可感知行为补充 `cursor-ide-browser` 端到端验证。

**Architecture:** 项目为 Python 3.12 单进程应用：FastAPI + NiceGUI 同进程，SQLite/文件存储，OpenAI 兼容 LLM（默认通义千问）。本轮改动集中在 `src/framework/context.py`、`src/agents/*`、`src/web/{routes,ui}.py`、`src/storage/memory_module.py`、`src/utils/pdf_export.py` 与 CI/工程配置，不引入新的运行时依赖（仅新增 dev 工具链）。

**Tech Stack:** Python 3.12、pytest + pytest-cov、ruff/black/isort、tiktoken（`count_tokens`）、reportlab（PDF 生成）、pymupdf（PDF 回读校验）、NiceGUI（`ui.expansion` 折叠卡片）、`cursor-ide-browser` MCP（端到端验证）。

## Global Constraints

- **阶段顺序（硬约束）**：阶段一（工程规范，含一次独立"格式化"提交）→ 阶段二（Token 计数 / 追问流式 / 候选人去重 / PDF 测试）→ 阶段三（测试补齐 → `memory_module.py` 拆分，拆分必须晚于阶段二去重改动）→ 阶段四（工具调用可视化）→ 收尾验证。
- **格式化与行为改动分离**：阶段一 Task 1.3 的 `ruff --fix + black + isort` 全量格式化必须是单独一次提交，之后才开始任何行为类改动。
- **不可变数据风格**：新建对象而非原地修改；函数签名加类型注解；`@dataclass(frozen=True)` 优先（遵循 `ecc-coding-style`）。
- **代码格式**：88 列、双引号、`isort profile=black`。
- **Token 计数唯一入口**：所有 token 估算统一走 `llm_client.count_tokens(messages: list[Message]) -> int`；`count_tokens` 对每条消息叠加 `_PER_MESSAGE_OVERHEAD_TOKENS=4` 并在末尾乘 `_TOKEN_SAFETY_MARGIN`。**关键约束**：`ContextManager` 内多段文本必须先拼装成"一份虚拟消息列表"再**整体调用一次** `count_tokens()`，禁止逐段各自包 `Message` 分别调用（否则 overhead 与安全余量重复叠加，计数虚高）。
- **`MemoryModule` 对外接口不变**：拆分为 `candidate_store.py`/`interview_store.py`/`eval_store.py` 后，`MemoryModule` 保留为 Facade，所有 public 方法签名与行为逐一保持一致。
- **测试门禁**：CI 用 `pytest tests/unit tests/integration --cov=src --cov-report=term-missing --cov-fail-under=60`；`tests/e2e`（依赖真实 LLM）不进 CI。
- **端到端浏览器验证**：追问流式、候选人去重三选一弹窗、工具调用可视化 3 项，使用 `cursor-ide-browser` MCP（遵循 `.cursor/rules/browser-testing.mdc`），作为对应任务的**验收步骤**手动/半自动执行并记录结果，不写成 CI 自动化用例。
- **运行环境**：项目自带 `.venv`（Python 3.12），Windows PowerShell；测试与 lint 均在 `.venv` 中执行。

---

## Phase 1 — 工程规范与低成本高收益项（阶段一）

> 本阶段先补齐工具链与 CI，再产出一次纯格式化提交，最后补文档/模板。行为逻辑零改动。

### Task 1.1: 新增 dev 依赖

**Files:**
- Modify: `requirements-dev.txt`

- [x] **Step 1: 追加四个 dev 依赖**

在 `requirements-dev.txt` 末尾追加（不固定次要版本，只锁大版本下限，随后 `pip install` 取最新）：

```
ruff>=0.6
black>=24.0
isort>=5.13
pytest-cov>=5.0
```

- [x] **Step 2: 安装并验证可用**

Run:
```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\ruff --version
.venv\Scripts\black --version
.venv\Scripts\isort --version
.venv\Scripts\python -m pytest --cov --version
```
Expected: 四条命令均打印版本号，无 `ModuleNotFoundError`。

- [x] **Step 3: Commit**

```bash
git add requirements-dev.txt
git commit -m "chore: add ruff/black/isort/pytest-cov dev dependencies"
```

### Task 1.2: 新建根目录 `pyproject.toml` 工具配置

**Files:**
- Create: `pyproject.toml`

**Interfaces:**
- Produces: 供 Task 1.3（格式化）、Task 1.4（CI lint）、Task 1.5（覆盖率门禁）共用的 ruff/black/isort/pytest/coverage 配置。

- [x] **Step 1: 写配置文件**

创建 `pyproject.toml`（若已存在则合并，勿覆盖已有 build 段）：

```toml
[tool.black]
line-length = 88
target-version = ["py312"]

[tool.isort]
profile = "black"
line_length = 88

[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B"]
ignore = ["E501"]  # 行宽交由 black 负责

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
markers = [
    "unit: 单元测试",
    "integration: 集成测试",
    "asyncio: 异步测试",
]

[tool.coverage.run]
source = ["src"]
branch = true
```

- [x] **Step 2: 验证配置被识别**

Run:
```powershell
.venv\Scripts\ruff check --no-fix src\config.py
```
Expected: 命令成功执行（可能报若干 lint 问题，但不报 "no configuration found" / 解析错误）。

- [x] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: configure ruff/black/isort/pytest/coverage in pyproject.toml"
```

### Task 1.3: 全量格式化（独立提交）

**Files:**
- Modify: 全仓库 `src/`、`tests/`（由工具自动改写）

- [x] **Step 1: 先跑一次基线测试，记录通过状态**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration -q
```
Expected: 记录当前通过/失败数（作为格式化后回归对比基线；`test_volc_stt` 的失败留待 Task 1.6 修复）。

- [x] **Step 2: 执行格式化三连**

Run:
```powershell
.venv\Scripts\ruff check --fix .
.venv\Scripts\black .
.venv\Scripts\isort .
```
Expected: 打印被修改的文件数。此步骤**只做格式化 / import 排序 / 安全自动修复**，不得手工掺入任何行为改动。

- [x] **Step 3: 复跑测试确认无回归**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration -q
```
Expected: 通过数与 Step 1 一致（格式化不改变行为）。

- [x] **Step 4: 单独提交格式化**

```bash
git add -A
git commit -m "style: apply ruff/black/isort formatting (no behavior change)"
```

### Task 1.4: CI 增加 lint 步骤

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: 在测试步骤前插入 ruff lint**

在 CI 的依赖安装步骤之后、测试步骤之前新增：

```yaml
      - name: Lint (ruff)
        run: ruff check .
```

- [ ] **Step 2: 本地模拟验证**

Run:
```powershell
.venv\Scripts\ruff check .
```
Expected: exit code 0（Task 1.3 已修复所有可修复项；若仍有告警则本步一并清零）。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add ruff lint step"
```

### Task 1.5: CI 测试步骤接入覆盖率门禁

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: 改写测试步骤命令**

将 CI 中现有 pytest 步骤命令改为：

```yaml
      - name: Test with coverage gate
        run: pytest tests/unit tests/integration --cov=src --cov-report=term-missing --cov-fail-under=60
```

- [ ] **Step 2: 本地确认当前覆盖率 ≥ 60%**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration --cov=src --cov-report=term-missing --cov-fail-under=60
```
Expected: 命令通过（若当前 < 60%，记录差距；本阶段仅接入门禁，后续阶段二/三补齐测试后再复核）。

> 若此刻覆盖率 < 60%，不要下调门槛：在 report 中记录，门禁达标由阶段三 Task 6.5 / 收尾 Task 9.1 保证。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: enforce coverage gate --cov-fail-under=60"
```

### Task 1.6: 修复 `test_volc_stt` 对本地 `.env` 的隐式依赖

**Files:**
- Modify: `tests/unit/test_volc_stt.py:215-224`

**Context:** `test_connect_silent_when_no_credentials` 断言"无凭证时 `connect()` 不建连"。但本地 `.env` 若配置了 `VOLC_APP_ID`/`VOLC_ACCESS_TOKEN`，`get_settings()` 会读到真实凭证导致断言失败。需用 `monkeypatch.setenv` 显式清空。

- [ ] **Step 1: 先复现失败（在有 .env 凭证的环境）**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_volc_stt.py::TestVolcRealtimeSTTCredentialCheck::test_connect_silent_when_no_credentials -v
```
Expected: 若本地 `.env` 含 VOLC 凭证则 FAIL（`mock_connect` 被调用）；否则先人工确认根因是 ambient env。

- [ ] **Step 2: 改造测试隔离环境变量**

将测试改为接收 `monkeypatch` 并清空相关变量（同时清 `get_settings` 缓存，避免单例已缓存旧配置）：

```python
    @pytest.mark.asyncio
    async def test_connect_silent_when_no_credentials(self, monkeypatch):
        """connect() returns without connecting when credentials are absent."""
        monkeypatch.setenv("VOLC_APP_ID", "")
        monkeypatch.setenv("VOLC_ACCESS_TOKEN", "")
        from src.config import get_settings
        get_settings.cache_clear()  # 若 get_settings 使用 lru_cache；否则删除本行

        from src.audio.volc_stt import VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")
        with patch("src.audio.volc_stt.ws_connect") as mock_connect:
            await stt.connect()
            mock_connect.assert_not_called()
        assert not stt._connected
```

> 实现注意：先用 Grep 确认 `get_settings` 是否为 `functools.lru_cache`；若不是缓存单例，删除 `cache_clear()` 行；若 `VolcRealtimeSTT` 在 `__init__` 时读取凭证，确保 `monkeypatch.setenv` 在实例化之前生效（如上）。

- [ ] **Step 3: 复跑测试确认通过（并在有凭证的环境下再跑一次）**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_volc_stt.py -v
```
Expected: 全部 PASS，且在本地 `.env` 含 VOLC 凭证时同样 PASS。

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_volc_stt.py
git commit -m "test: isolate volc stt credential test from local .env"
```

### Task 1.7: 核对并更新 `docs/todo/` 6 个待办文档

**Files:**
- Modify: `docs/todo/01-readme-demo.md`、`docs/todo/02-report-export-pdf.md`、`docs/todo/03-structured-interview-mode.md`、`docs/todo/04-candidate-comparison.md`、`docs/todo/05-ci-complete.md`、`docs/todo/06-observability.md`

- [ ] **Step 1: 逐文件核对实际代码进度**

对每个文件：用 Grep/Read 核对其列出的 TODO 项是否已在代码中实现，勾选已完成项（`- [ ]` → `- [x]`），对未完成项补一行"实际进度"说明。**重点核对 `03-structured-interview-mode.md`**（结构化面试模式涉及 `save_questions`/`get_questions`/`update_question_coverage` 已存在，需确认覆盖到哪一步）。

- [ ] **Step 2: Commit**

```bash
git add docs/todo/
git commit -m "docs: sync docs/todo status with actual implementation progress"
```

### Task 1.8: 新建 `CHANGELOG.md`

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: 按 Keep a Changelog 格式创建**

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 工程规范：接入 ruff/black/isort/pytest-cov 与 CI 覆盖率门禁（60% 起步）
- Agent 工具调用可视化：新增 `tool_result` SSE 事件与可展开折叠卡片
- Issue/PR 模板、Demo 录制 checklist

### Changed
- 追问建议改为真流式逐 token 输出
- 候选人去重改为简历解析后按真实姓名判定（三选一交互）
- 上下文/评价 token 预算改用 tiktoken 精确计数

### Fixed
- `test_volc_stt` 对本地 `.env` 环境变量的隐式依赖
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md (Keep a Changelog format)"
```

### Task 1.9: 新建 Issue 模板

**Files:**
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`

- [ ] **Step 1: 写 bug_report.md**

```markdown
---
name: Bug 报告
about: 报告一个可复现的问题
title: "[Bug] "
labels: bug
---

## 问题描述

## 复现步骤
1.
2.

## 期望行为

## 实际行为

## 环境
- OS:
- Python 版本:
- 关键配置（PDF_PARSER / ASR 后端 / MOCK_AUDIO 等）:

## 日志片段（logs/app.log）
```

- [ ] **Step 2: 写 feature_request.md**

```markdown
---
name: 功能建议
about: 提出一个新功能或改进
title: "[Feature] "
labels: enhancement
---

## 需求背景

## 建议方案

## 备选方案

## 补充说明
```

- [ ] **Step 3: Commit**

```bash
git add .github/ISSUE_TEMPLATE/
git commit -m "docs: add issue templates (bug_report, feature_request)"
```

### Task 1.10: 新建 PR 模板

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 1: 写模板**

```markdown
## 变更说明

## 关联 Issue / Change

## 变更类型
- [ ] feat
- [ ] fix
- [ ] refactor
- [ ] docs / test / chore

## 自测清单
- [ ] `ruff check .` 通过
- [ ] `pytest tests/unit tests/integration --cov=src --cov-fail-under=60` 通过
- [ ] 涉及前端行为已用 cursor-ide-browser 验证（如适用）
- [ ] 已同步更新 docs/arc/ 相关文档（如适用）
```

- [ ] **Step 2: Commit**

```bash
git add .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs: add pull request template"
```

### Task 1.11: 新建 Demo 录制 checklist

**Files:**
- Create: `docs/demo-recording-checklist.md`

- [ ] **Step 1: 写 checklist（含 MOCK 启动步骤 + 完整操作脚本）**

```markdown
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
6. 停止面试 → 生成评价报告 → 导出 PDF

## 录制注意
- 提前准备脱敏简历样本
- 分辨率 ≥ 1280×720，字体缩放适中
- 输出 GIF/截图放入 docs/assets/（见 Task 1.12）
```

- [ ] **Step 2: Commit**

```bash
git add docs/demo-recording-checklist.md
git commit -m "docs: add demo recording checklist"
```

### Task 1.12: `docs/assets/` 占位与 README 预留位

**Files:**
- Create: `docs/assets/.gitkeep`
- Create: `docs/assets/README.md`
- Modify: `README.md`

- [ ] **Step 1: 创建 assets 目录说明**

`docs/assets/README.md`：
```markdown
# Demo 素材目录

存放 README 引用的截图与 GIF（如 `demo.gif`、`upload.png`）。
录制流程见 `docs/demo-recording-checklist.md`。
```
并创建空的 `docs/assets/.gitkeep`。

- [ ] **Step 2: 在 README 预留嵌入位置**

在 `README.md` 顶部功能介绍区插入占位（先 Read `README.md` 定位合适锚点）：
```markdown
<!-- Demo 演示（录制后替换）-->
<!-- ![Demo](docs/assets/demo.gif) -->
```

- [ ] **Step 3: Commit**

```bash
git add docs/assets/ README.md
git commit -m "docs: add docs/assets placeholder and README demo slot"
```

---

## Phase 2 — Token 精确计数（阶段二）

### Task 2.1: `context.py` 全量改用 `count_tokens()`（虚拟消息列表）

**Files:**
- Modify: `src/framework/context.py:133-150`（`token_usage`）、`:171-177`（`_estimate_tokens`）、`:199-238`（`_compress_async` 的 tail 边界与压缩可行性估算）
- Test: `tests/unit/test_context.py`

**Interfaces:**
- Consumes: `LLMClient.count_tokens(messages: list[Message]) -> int`（`src/llm/protocol.py:72`），`Message(role=..., content=...)`（`src/models/message.py`）。
- Produces: `ContextManager._estimate_tokens() -> int`（语义不变，实现改为精确计数）；`token_usage` 属性分区数值（`fixed_zone_tokens`/`summary_zone_tokens`/`window_zone_tokens`）。

**关键实现约束（务必遵守）：** 不要对 summary 和每一轮各自 `count_tokens()`。要把它们拼成**一份虚拟消息列表**（summary 作为 1 条 system 消息 + 每轮拼成 1 条 user 消息），整体只调用一次 `count_tokens()`。这样 `_PER_MESSAGE_OVERHEAD_TOKENS` 与安全余量只按虚拟消息条数叠加一次，符合 design doc D 约束。

- [ ] **Step 1: 写失败测试（精确计数 > 空内容基线，且分区求和自洽）**

在 `tests/unit/test_context.py` 新增：

```python
import pytest
from src.framework.context import ContextConfig, ContextManager
from src.models.session import ConversationRound


class _FakeLLM:
    """count_tokens 返回可预测的整数：每条消息 = 内容字符数 + 固定 overhead。"""
    def count_tokens(self, messages):
        return sum(len(m.content or "") + 4 for m in messages)

    async def chat(self, *a, **k):
        raise AssertionError("not used in this test")


@pytest.mark.unit
def test_estimate_tokens_uses_count_tokens_single_call():
    cm = ContextManager(ContextConfig(), _FakeLLM())
    cm._summary = "摘要内容"
    cm._all_rounds = [
        ConversationRound(round_number=1, interviewer_text="问题一", candidate_text="回答一"),
        ConversationRound(round_number=2, interviewer_text="问题二", candidate_text="回答二"),
    ]
    tokens = cm._estimate_tokens()
    # fixed(system 提示，1 条) + summary(1 条) + 每轮 1 条(2 条) = 4 条虚拟消息
    # 断言其为正且与虚拟消息列表整体计数一致（overhead 只叠加 4 次，不是逐段叠加）
    assert tokens > 0
    assert isinstance(tokens, int)
```

- [ ] **Step 2: 运行确认失败**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_context.py::test_estimate_tokens_uses_count_tokens_single_call -v
```
Expected: 初始 FAIL 或 通过但数值仍来自旧 `//3` 估算（记录旧值以对比）。

- [ ] **Step 3: 引入虚拟消息列表构造 + 改写 `_estimate_tokens`**

新增私有辅助并改写 `_estimate_tokens`：

```python
    _FIXED_ZONE_SYSTEM_TEXT = "[固定区占位]" * 120  # 约等于原 1500 token 的固定区估算

    def _build_virtual_messages(self) -> list["Message"]:
        """把 summary + 各轮拼成一份虚拟消息列表，供 count_tokens 一次性计数。"""
        msgs: list[Message] = [
            Message(role="system", content=self._FIXED_ZONE_SYSTEM_TEXT),
        ]
        if self._summary:
            msgs.append(Message(role="system", content=self._summary))
        for r in self._all_rounds:
            msgs.append(
                Message(
                    role="user",
                    content=f"{r.interviewer_text}\n{r.candidate_text}",
                )
            )
        return msgs

    def _estimate_tokens(self) -> int:
        return self._llm_client.count_tokens(self._build_virtual_messages())
```

> 说明：固定区（原硬编码 1500）改为一个稳定占位文本参与统一计数即可；若希望保留精确 1500，可改为 `1500 + count_tokens(summary+rounds 虚拟列表)`，但须与 `token_usage` 分区口径一致。实现时二选一并在 report 记录选择。

- [ ] **Step 4: 改写 `token_usage` 属性分区口径**

`token_usage` 的三个分区改用 `count_tokens` 单独度量（每个分区各构造一份该分区的虚拟消息列表并各调一次；分区间相互独立，不违反"多段文本单次调用"约束，因为约束针对的是"同一份预算的多段"）：

```python
    @property
    def token_usage(self) -> TokenUsageInfo:
        summary_msgs = (
            [Message(role="system", content=self._summary)] if self._summary else []
        )
        window_msgs = [
            Message(role="user", content=f"{r.interviewer_text}\n{r.candidate_text}")
            for r in self._all_rounds
        ]
        fixed_tokens = self._llm_client.count_tokens(
            [Message(role="system", content=self._FIXED_ZONE_SYSTEM_TEXT)]
        )
        summary_tokens = self._llm_client.count_tokens(summary_msgs) if summary_msgs else 0
        window_tokens = self._llm_client.count_tokens(window_msgs) if window_msgs else 0
        total = fixed_tokens + summary_tokens + window_tokens
        budget = int(self._config.token_budget * (1.0 - self._config.token_safety_margin))
        return TokenUsageInfo(
            total_used=total,
            budget=budget,
            fixed_zone_tokens=fixed_tokens,
            summary_zone_tokens=summary_tokens,
            window_zone_tokens=window_tokens,
            is_compressing=self._is_compressing,
            utilization=min(1.0, total / budget) if budget > 0 else 0.0,
        )
```

- [ ] **Step 5: 改写 `_compress_async` 里的 tail 边界与压缩可行性估算**

将 `_compress_async` 中两处基于 `//3` / `len(text)/1.5` 的估算改为 `count_tokens`：

- tail 边界（原 `:208-214`）：把每轮 `round_tokens = (len(iv)+len(cand))//3` 改为
  `round_tokens = self._llm_client.count_tokens([Message(role="user", content=f"{r.interviewer_text}\n{r.candidate_text}")])`
- 压缩请求可行性（原 `:229` 的 `estimated_tokens = int(len(conversation_text)/1.5)+2000`）改为
  `estimated_tokens = self._llm_client.count_tokens([Message(role="system", content=_COMPRESSION_SYSTEM_PROMPT), Message(role="user", content=conversation_text)])`

- [ ] **Step 6: 运行 context 全部测试**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_context.py -v
```
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/framework/context.py tests/unit/test_context.py
git commit -m "feat: use count_tokens for context token accounting (single virtual message list)"
```

### Task 2.2: `eval_agent.py` 的 `estimated_tokens` 改用 `count_tokens()`

**Files:**
- Modify: `src/agents/eval_agent.py:116-118`
- Test: `tests/unit/test_eval_agent.py`

**Context:** 现状 `estimated_tokens = len(full_text) + system_text_len`（纯字符数），用于选择 single-call 还是 chunked map-reduce 路径。

- [ ] **Step 1: 改写估算逻辑**

```python
        # 精确计数：把系统消息 + 完整对话文本拼成虚拟消息列表整体计数
        estimated_tokens = self._llm_client.count_tokens(
            base_messages + [Message(role="user", content=full_text)]
        )
```

> 实现注意：确认 `eval_agent.py` 顶部已 import `Message`（`from ..models.message import Message`），并确认 `self._llm_client` 属性名（若为 `self.llm_client` 则相应调整）。用 Grep 核对。

- [ ] **Step 2: 补/跑测试验证阈值分流仍正确**

在 `tests/unit/test_eval_agent.py` 增加：小对话走 single-call、超大对话走 chunked（mock `count_tokens` 返回可控值以覆盖 `<= _TOKEN_THRESHOLD` 与 `>` 两侧）。

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_eval_agent.py -v
```
Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add src/agents/eval_agent.py tests/unit/test_eval_agent.py
git commit -m "feat: use count_tokens for eval single/chunked path decision"
```

### Task 2.3: 压缩/分块触发阈值回归验证

**Files:**
- Test / 调查: `tests/unit/test_context.py`、（如需调整）`src/framework/context.py:36-43`（`ContextConfig` 常量）

**Context:** 精确计数后数值通常小于旧 `//3` 估算（尤其中文），`compression_round_threshold=8`、`over_budget>0.65` 等触发时机可能偏移。

- [ ] **Step 1: 构造中英混杂真实规模数据回归**

在 `tests/unit/test_context.py` 增加一个用例：注入 ~10 轮中英混杂对话，断言 `add_round` 在预期轮次触发压缩（观察 `_estimate_tokens` 相对 budget 的比值）。用真实 `OpenAICompatibleClient.count_tokens`（tiktoken 可用时）或贴近真实的 `_FakeLLM`。

- [ ] **Step 2: 运行并观测触发时机**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_context.py -k threshold -v
```
Expected: 明确压缩是否在预期轮次触发。

- [ ] **Step 3: 按需调整常量（如触发时机明显偏移）**

若触发明显偏早/偏晚，调整 `ContextConfig` 中 `compression_round_threshold` 或 `0.65` 比值，并在提交信息与 report 记录**调整原因与前后对比数值**；若无需调整，也在 report 明确记录"验证通过，无需调整"。

- [ ] **Step 4: Commit**

```bash
git add src/framework/context.py tests/unit/test_context.py
git commit -m "test: regression-verify compression thresholds under exact token count"
```

### Task 2.4: 补充中英混杂新旧估算差异单测

**Files:**
- Test: `tests/unit/test_context.py`

- [ ] **Step 1: 写用例覆盖"中文场景精确计数明显低于旧字符估算"**

断言：对同一段以中文为主的对话，`_estimate_tokens()`（新，tiktoken）返回值与旧 `//3` 口径存在方向性差异（中文通常更低），保证改造后行为可解释、可回归。

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_context.py -v
```
```bash
git add tests/unit/test_context.py
git commit -m "test: cover cn/en mixed token estimation difference"
```

---

## Phase 2 — 追问建议真流式输出（阶段二）

### Task 3.1 + 3.2: `generate_suggestion()` 改用 `chat_stream()` 并修正日志统计

**Files:**
- Modify: `src/agents/interview_agent.py:266-306`
- Test: `tests/unit/test_interview_agent.py`

**Interfaces:**
- Consumes: `LLMClient.chat_stream(messages, tools=None, ...) -> AsyncIterator[StreamChunk]`；`StreamChunk(delta: str, is_final: bool, accumulated_content: str, prompt_tokens: int|None, completion_tokens: int|None)`（`src/llm/protocol.py:22-29`）。
- Produces: `generate_suggestion(request_id) -> AsyncIterator[str]` 逐 token yield（现状一次性 yield 整段）。下游 `_on_trigger_fired._runner` 已按每次 yield 推送 `suggestion_delta` 事件（`interview_agent.py:370-411`），改为真流式后自然逐段展示。

**Context:** 现状 `response = await self.llm_client.chat(messages)` 后一次性 `yield reply_text`。改为 `chat_stream` 逐 `delta` yield，`prompt_tokens`/`completion_tokens` 从 `is_final` chunk 累计结果取。

- [ ] **Step 1: 写失败测试（多次 delta + 日志 token 来自流式结果）**

在 `tests/unit/test_interview_agent.py` 新增：

```python
import pytest
from src.llm.protocol import StreamChunk


class _StreamLLM:
    def __init__(self):
        self.count_tokens_return = 10

    def count_tokens(self, messages):
        return self.count_tokens_return

    async def chat_stream(self, messages, tools=None, temperature=0.7, timeout_sec=None):
        for piece in ["建议", "：可", "追问项目细节"]:
            yield StreamChunk(delta=piece)
        yield StreamChunk(
            delta="", is_final=True, accumulated_content="建议：可追问项目细节",
            prompt_tokens=123, completion_tokens=7,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_suggestion_streams_multiple_deltas(interview_agent_with_session):
    agent = interview_agent_with_session
    agent.llm_client = _StreamLLM()
    tokens = [t async for t in agent.generate_suggestion(request_id=0)]
    assert len(tokens) >= 2  # 逐段而非一次性
    assert "".join(tokens) == "建议：可追问项目细节"
```

> `interview_agent_with_session` fixture：构造带激活 session 的 `InterviewAgent`（复用现有测试夹具或新建，注意 `on_activate` 需 session）。

- [ ] **Step 2: 运行确认失败**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_interview_agent.py::test_generate_suggestion_streams_multiple_deltas -v
```
Expected: FAIL（现状只 yield 一段）。

- [ ] **Step 3: 改写 `generate_suggestion` 的生成段（`interview_agent.py:266-306`）**

```python
        try:
            reply_text = ""
            prompt_tokens = 0
            completion_tokens = 0
            async for chunk in self.llm_client.chat_stream(messages):
                if chunk.delta:
                    reply_text += chunk.delta
                    yield chunk.delta
                if chunk.is_final:
                    prompt_tokens = chunk.prompt_tokens or 0
                    completion_tokens = chunk.completion_tokens or 0
                    if not reply_text and chunk.accumulated_content:
                        # 兜底：某些实现只在 final 给全量
                        reply_text = chunk.accumulated_content
                        yield chunk.accumulated_content

            reply_text = reply_text.strip()
            assistant_msg = Message(role="assistant", content=reply_text)
            if self._logger is not None:
                await self._logger.append([assistant_msg])

            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "suggestion_generated request_id=%d output_chars=%d "
                "prompt_tokens=%d completion_tokens=%d elapsed_ms=%.1f text=%s",
                request_id, len(reply_text), prompt_tokens, completion_tokens,
                elapsed_ms, truncate(reply_text),
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "InterviewAgent generate_suggestion cancelled request_id=%d elapsed_ms=%.1f",
                request_id, elapsed_ms,
            )
            raise
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "InterviewAgent generate_suggestion failed request_id=%d elapsed_ms=%.1f",
                request_id, elapsed_ms,
            )
```

> 注意：删除原 `response = await self.llm_client.chat(messages)` 及 `response.prompt_tokens`/`content` 相关行；保留 `_enforce_token_budget` 与之前的取消/日志逻辑。

- [ ] **Step 4: 运行确认通过**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_interview_agent.py -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/agents/interview_agent.py tests/unit/test_interview_agent.py
git commit -m "feat: stream follow-up suggestions token-by-token via chat_stream"
```

### Task 3.3 + 3.4: 流式场景下取消逻辑验证 + 取消/多 yield 单测

**Files:**
- Modify（如需）: `src/agents/interview_agent.py:213-222`（`generate_suggestion` 开头的取消上一次流）、`:76-89`（`cancel_current_stream`）
- Test: `tests/unit/test_interview_agent.py`

**Context:** `_current_stream_task` 取消路径原本针对非流式实现验证过；改流式后需确认取消不产生悬挂任务或状态不一致。

- [ ] **Step 1: 写取消场景测试**

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_suggestion_cancel_midstream(interview_agent_with_session):
    import asyncio
    agent = interview_agent_with_session

    class _SlowStreamLLM:
        def count_tokens(self, messages):
            return 10
        async def chat_stream(self, messages, tools=None, temperature=0.7, timeout_sec=None):
            for piece in ["一", "二", "三"]:
                await asyncio.sleep(0.05)
                yield StreamChunk(delta=piece)
            yield StreamChunk(delta="", is_final=True, accumulated_content="一二三",
                              prompt_tokens=1, completion_tokens=1)

    agent.llm_client = _SlowStreamLLM()

    async def _consume():
        async for _ in agent.generate_suggestion(request_id=0):
            pass

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.06)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # 取消后不应留下未完成的悬挂任务
    assert agent._current_stream_task is None or agent._current_stream_task.done()
```

- [ ] **Step 2: 运行**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_interview_agent.py -k cancel -v
```
Expected: PASS；若失败，检查 `generate_suggestion` 取消分支是否 `raise`（应保留 `raise`），以及 `_runner`（`:405`）对 `CancelledError` 的吞并是否正确。

- [ ] **Step 3: Commit**

```bash
git add src/agents/interview_agent.py tests/unit/test_interview_agent.py
git commit -m "test: verify suggestion stream cancellation has no dangling task"
```

### Task 3.5: 【端到端浏览器验证】追问流式展示 + 中途中止

**Files:**
- 验收记录（不进 CI）: 追加到本任务的执行 report

**Context:** 使用 `cursor-ide-browser` MCP，遵循 `.cursor/rules/browser-testing.mdc`。可在 `MOCK_AUDIO=true` 下启动并触发追问。

- [ ] **Step 1: 启动应用（Mock 音频）**

Run:
```powershell
$env:MOCK_AUDIO = "true"
.venv\Scripts\python -m src.main
```
Expected: `http://127.0.0.1:8000` 可访问。

- [ ] **Step 2: 浏览器端触发追问并观察逐段展示**

用 `cursor-ide-browser`：导航到应用 → 完成一次上传/解析/开始面试 → 手动触发一次追问建议（`/api/interview/suggest` 或界面按钮）→ 截图/快照确认「AI 追问建议」卡片文字**逐段增长**（而非一次性整段出现）。

- [ ] **Step 3: 验证中途中止行为**

在追问生成中途切换触发模式到 `manual`（触发 `cancel_current_stream`）→ 确认前端停止追加、无残留半截卡片状态异常。

- [ ] **Step 4: 记录验收结果**

将截图路径与结论写入本任务 report（通过/问题）。**不新增 CI 用例。**

---

## Phase 2 — 候选人去重改为按真实姓名（阶段二）

> 交互方案已确认：解析完成后检测到重名，弹「覆盖已有档案 / 保留两份独立档案 / 取消本次上传」三选一，与现有 409 提示模式一致。

### Task 4.1: 梳理并确定去重校验点后移的接入设计

**Files:**
- 调查: `src/web/routes.py:166-302`（`upload_resume`）、`src/web/ui.py:818-889`（上传流程）、`src/tools/dispatch_to_agent.py:113-160`（parse_done 分支，现有 `get_candidate_by_name(real_name)` 仅加 `duplicate_warning`）

**Context（已确认现状）：**
- 上传时：`upload_resume` 用 `_safe_stem(filename)` 调 `get_candidate_by_name(safe_stem)` → 409 `duplicate_candidate` → 前端 `_confirm_overwrite_dialog`（当前仅"覆盖/取消"两选）。
- 解析后：`dispatch_to_agent` parse_done 分支**已经**先 `save_candidate`（存为新档案），再 `get_candidate_by_name(real_name)`，命中仅追加 `duplicate_warning` 文案——没有真正的三选一，且是"先存后提示"。

**目标接入设计（本任务产出，后续 4.2-4.4 实现）：**
1. **移除**上传时的 `safe_stem` 去重（`routes.py:189-200`），上传只负责存 PDF。
2. 去重判定**后移**到 parse_done 拿到 `real_name` 之后，且**在 save_candidate 之前**判定（避免"先存后问"）。
3. 命中重名时**不立即持久化**：把解析出的 `CandidateProfile` + `resume_markdown` 暂存到一个"待决议 pending"结构（内存字典，key=pending_id），并通过聊天 SSE 向前端 yield 一个新事件 `{"type":"duplicate_candidate", "pending_id":..., "existing_candidate_id":..., "existing_candidate_name":..., "new_name":...}`。
4. 新增 REST 端点 `POST /api/resume/resolve-duplicate`，body `{pending_id, action}`（`action ∈ {overwrite, keep_both, cancel}`），执行对应持久化并清理 pending。

- [ ] **Step 1: Read 三处代码确认接入点，产出接入设计说明**

Read `routes.py`、`ui.py`、`dispatch_to_agent.py` 对应区间，确认上面 4 点的具体行号与数据可得性（`session.candidate` 是否已含 parse 后 profile；`resume_markdown` 从 `markdown_path` 读取），把最终接入方案（含 pending 存储位置、事件字段、端点签名）写入本任务 report。

> 若发现更简方案（例如复用 `overwrite` query 参数 + 前端在 parse 前先查重），在 report 说明并据此调整 4.2-4.4；默认采用上文"pending + resolve 端点 + SSE 事件"方案。

### Task 4.2: 后端——去重判定后移 + pending 暂存

**Files:**
- Modify: `src/web/routes.py:189-200`（删除上传期 safe_stem 去重）
- Modify: `src/tools/dispatch_to_agent.py:140-160`（parse_done：save 前判重，命中则暂存 pending 并标记结果）
- Create: `src/web/pending_uploads.py`（进程内 pending 暂存，单用户工具足够）
- Modify: `src/storage/memory_module.py`（如需新增 `overwrite_candidate` / 复用 `save_candidate` + `delete_candidate`）
- Test: `tests/integration/test_routes.py`、`tests/unit/test_dispatch_to_agent.py`

**Interfaces:**
- Produces:
  - `pending_uploads.PendingStore` 单例：`put(profile, resume_markdown, existing_id) -> str(pending_id)`、`pop(pending_id) -> PendingUpload | None`。
  - `dispatch_to_agent` parse_done 命中重名时在 `result` 写 `{"duplicate_pending": {"pending_id":..., "existing_candidate_id":..., "existing_candidate_name":..., "new_name":...}}`，且**不**调用 `save_candidate`。

- [ ] **Step 1: 写 PendingStore + 失败测试**

`src/web/pending_uploads.py`：

```python
"""解析后待决议的候选人去重暂存（单用户进程内）。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..models.candidate import CandidateProfile


@dataclass(frozen=True)
class PendingUpload:
    profile: CandidateProfile
    resume_markdown: str
    existing_candidate_id: str


class PendingStore:
    def __init__(self) -> None:
        self._items: dict[str, PendingUpload] = {}

    def put(self, profile: CandidateProfile, resume_markdown: str, existing_id: str) -> str:
        pending_id = f"pu-{uuid.uuid4().hex[:12]}"
        self._items[pending_id] = PendingUpload(profile, resume_markdown, existing_id)
        return pending_id

    def pop(self, pending_id: str) -> PendingUpload | None:
        return self._items.pop(pending_id, None)
```

测试 `tests/unit/test_pending_uploads.py`：put 后 pop 返回同一对象、二次 pop 返回 None。

- [ ] **Step 2: 删除上传期 safe_stem 去重**

删除 `routes.py:189-200`（`if not candidate_id and not overwrite: existing = ... raise 409 duplicate_candidate`）整段。上传响应字段保持不变。

- [ ] **Step 3: parse_done 改为 save 前判重 + 暂存 pending**

在 `dispatch_to_agent.py` parse_done 分支中，把当前"先 save 后 warning"改为"先判重"：

```python
        elif ctx.memory_module is not None:
            real_name = session.candidate.name
            existing = (
                await ctx.memory_module.get_candidate_by_name(real_name)
                if real_name else None
            )
            if existing is not None and existing.id != session.candidate.id:
                from ..web.pending_uploads import get_pending_store
                pending_id = get_pending_store().put(
                    session.candidate, resume_markdown, existing.id
                )
                result["duplicate_pending"] = {
                    "pending_id": pending_id,
                    "existing_candidate_id": existing.id,
                    "existing_candidate_name": existing.name,
                    "new_name": real_name,
                }
                # 命中重名：不落盘，交由前端三选一后经 resolve 端点决议
                return
            try:
                await ctx.memory_module.save_candidate(session.candidate, resume_markdown)
                session.candidate.resume_content = resume_markdown
            except Exception as exc:
                logger.exception("dispatch_to_agent: save_candidate failed")
                result["user_facing"] = f"候选人档案保存失败：{exc}。简历内容未持久化，请重试。"
                return
            if ctx.main_agent is not None:
                ctx.main_agent.set_candidate_context(
                    session.candidate, interview_brief=session.interview_brief
                )
```

> `get_pending_store()`：在 `pending_uploads.py` 提供模块级单例（`_store = PendingStore()` + `def get_pending_store(): return _store`）。

- [ ] **Step 4: MainAgent 将 `duplicate_pending` 透传为 SSE 事件**

`dispatch_to_agent` 是工具，结果通过工具结果 JSON 回到 MainAgent。需让前端拿到该事件：在 MainAgent 工具循环中，检测工具结果含 `duplicate_pending` 时 `yield {"type": "duplicate_candidate", **payload}`（与 Task 8 的 `tool_result` 事件推送位置相邻实现；本任务先接入 `duplicate_candidate` 事件）。用 Grep 定位 `main_agent.py:291`（`result_str = await self._tools.dispatch(...)`）后解析：

```python
                result_str = await self._tools.dispatch(tc.function.name, tc.function.arguments)
                # 去重待决议事件透传
                try:
                    _rj = json.loads(result_str)
                    if isinstance(_rj, dict) and _rj.get("duplicate_pending"):
                        yield {"type": "duplicate_candidate", **_rj["duplicate_pending"]}
                except Exception:
                    pass
```

- [ ] **Step 5: 运行相关测试**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_pending_uploads.py tests\unit\test_dispatch_to_agent.py tests\integration\test_routes.py -v
```
Expected: PASS（新增去重逻辑不破坏原有 parse_done 正常保存路径）。

- [ ] **Step 6: Commit**

```bash
git add src/web/pending_uploads.py src/web/routes.py src/tools/dispatch_to_agent.py src/agents/main_agent.py tests/unit/test_pending_uploads.py
git commit -m "feat: move candidate dedup to post-parse real-name check with pending store"
```

### Task 4.3: 后端——resolve 端点三分支处理逻辑

**Files:**
- Modify: `src/web/routes.py`（新增 `POST /api/resume/resolve-duplicate`）
- Modify: `src/storage/memory_module.py`（如需 `overwrite` 语义：复用 `save_candidate`——因其对同 id 会更新 index；"覆盖"= 用已存在 id 保存新数据）
- Test: `tests/integration/test_routes.py`

**Interfaces:**
- Produces: `POST /api/resume/resolve-duplicate` body `{"pending_id": str, "action": "overwrite"|"keep_both"|"cancel"}` →
  - `overwrite`：`profile.id = existing_candidate_id` 后 `save_candidate`（覆盖旧档案），返回 `{"candidate_id": existing_id, "action":"overwrite"}`。
  - `keep_both`：`profile.id = None`（强制生成新 id）后 `save_candidate`，返回 `{"candidate_id": <new_id>, "action":"keep_both"}`。
  - `cancel`：不写任何数据，返回 `{"action":"cancel"}`。
  - pending 不存在：404 `{"code":"pending_not_found"}`。

- [ ] **Step 1: 写失败集成测试（三分支各一 + pending 缺失）**

在 `tests/integration/test_routes.py` 新增：先构造一条 pending（可直接调用 `get_pending_store().put(...)`），再分别 POST 三种 action，断言：
- overwrite → 旧 id 的 profile 内容被更新，候选人总数不变。
- keep_both → 新增一个同名不同 id 候选人，总数 +1。
- cancel → 无新增、无修改。
- 不存在的 pending_id → 404。

- [ ] **Step 2: 实现端点**

```python
@router.post("/resume/resolve-duplicate")
async def resolve_duplicate(request: Request, body: ResolveDuplicateRequest):
    from ..web.pending_uploads import get_pending_store
    memory = _memory(request)
    pending = get_pending_store().pop(body.pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail={"code": "pending_not_found", "message": "待决议记录不存在或已处理"})

    if body.action == "cancel":
        return {"action": "cancel"}

    profile = pending.profile
    if body.action == "overwrite":
        profile.id = pending.existing_candidate_id
    elif body.action == "keep_both":
        profile.id = None  # save_candidate 会生成新 id
    else:
        raise HTTPException(status_code=400, detail={"code": "invalid_action", "message": f"未知 action: {body.action}"})

    new_id = await memory.save_candidate(profile, pending.resume_markdown)
    return {"action": body.action, "candidate_id": new_id}
```

`ResolveDuplicateRequest`（放到 routes 的 pydantic models 处）：
```python
class ResolveDuplicateRequest(BaseModel):
    pending_id: str
    action: str
```

> 实现注意：确认 `save_candidate` 对 `profile.id` 为空时会生成 `c-<uuid>` 且不与既有 id 冲突（见 `memory_module.py:329`），对已存在 id 会更新 index（`:347-352`）。

- [ ] **Step 3: 运行测试**

Run:
```powershell
.venv\Scripts\python -m pytest tests\integration\test_routes.py -k resolve_duplicate -v
```
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add src/web/routes.py tests/integration/test_routes.py
git commit -m "feat: add resolve-duplicate endpoint (overwrite/keep_both/cancel)"
```

### Task 4.4: 前端——三选一弹窗交互

**Files:**
- Modify: `src/web/ui.py:548-566`（`_chat_stream` 的 WS/SSE 事件处理，或 `_chat_stream` 内 SSE 分支 `:940-944`）、新增 `_confirm_dedup_dialog`
- 复用/参考: `src/web/ui.py:1018-1039`（现有 `_confirm_overwrite_dialog` 两选模式）

**Interfaces:**
- Consumes: SSE 事件 `{"type":"duplicate_candidate", "pending_id", "existing_candidate_name", "new_name", ...}`（Task 4.2 产出）。
- Produces: 用户选择后 `POST /api/resume/resolve-duplicate`（Task 4.3）。

- [ ] **Step 1: 新增三选一弹窗**

在 `ui.py` 新增（参考现有 `_confirm_overwrite_dialog` 的 future 模式）：

```python
async def _confirm_dedup_dialog(existing_name: str) -> str:
    """返回 'overwrite' | 'keep_both' | 'cancel'。"""
    done: asyncio.Future[str] = asyncio.get_event_loop().create_future()
    with ui.dialog() as dialog, ui.card().classes("p-4 gap-3"):
        ui.label(f"候选人「{existing_name}」已存在").classes("text-base font-semibold")
        ui.label("解析出的姓名与已有候选人重名，请选择处理方式：").classes("text-sm text-grey-7")

        def _choose(action: str):
            if not done.done():
                done.set_result(action)
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("取消本次上传", on_click=lambda: _choose("cancel")).props("flat dense")
            ui.button("保留两份独立档案", on_click=lambda: _choose("keep_both")).props("outline dense")
            ui.button("覆盖已有档案", on_click=lambda: _choose("overwrite")).props("unelevated dense color=negative")
    dialog.open()
    return await done
```

- [ ] **Step 2: 在 `_chat_stream` SSE 分支处理 `duplicate_candidate` 事件**

在 `_chat_stream`（`:940`）的 `chunk_type` 分支中新增：

```python
                    elif chunk_type == "duplicate_candidate":
                        action = await _confirm_dedup_dialog(
                            chunk.get("existing_candidate_name", chunk.get("new_name", ""))
                        )
                        try:
                            async with httpx.AsyncClient(timeout=60) as client:
                                r = await client.post(
                                    f"{_base_url}/api/resume/resolve-duplicate",
                                    json={"pending_id": chunk.get("pending_id", ""), "action": action},
                                )
                                r.raise_for_status()
                                resolved = r.json()
                        except Exception as exc:
                            _error(chat_col, f"去重处理失败：{exc}")
                            resolved = None
                        if resolved and resolved.get("action") == "cancel":
                            _bubble(chat_col, "已取消本次上传，未创建候选人档案。", sent=False, name="Agent")
                        elif resolved:
                            state["candidate_id"] = resolved.get("candidate_id", state.get("candidate_id"))
                            _bubble(chat_col, "候选人档案已保存。", sent=False, name="Agent")
                        await _scroll(chat_scroll)
```

- [ ] **Step 3: 移除/保留上传期两选弹窗**

因去重改到解析后，`_handle_upload` 中依赖 409 的 `_conflict` 分支（`ui.py:856-867`）不再触发（上传期去重已删除）。保留代码不删亦可（不会触发），但为避免死代码，建议在本任务同时移除 `_conflict` 处理分支并在 commit 说明"上传期去重已迁移到解析后"。旧 `_confirm_overwrite_dialog` 若无其它引用一并移除（Grep 确认无引用）。

- [ ] **Step 4: 前端逻辑无法单测的部分，用集成/端到端覆盖**

前端交互主要靠 Task 4.6 端到端验证；后端三分支已在 4.3 覆盖。此步仅做 `ruff check` + 应用能正常启动的冒烟。

Run:
```powershell
.venv\Scripts\ruff check src\web\ui.py
```
Expected: 无 lint 错误。

- [ ] **Step 5: Commit**

```bash
git add src/web/ui.py
git commit -m "feat: three-way dedup dialog after resume parse (overwrite/keep both/cancel)"
```

### Task 4.5: 补充去重识别场景单元/集成测试

**Files:**
- Test: `tests/unit/test_dispatch_to_agent.py`、`tests/integration/test_routes.py`

- [ ] **Step 1: 覆盖两种识别场景 + 三分支执行结果**

- 「不同文件名、同真实姓名」：上传 `zhangsan_v2.pdf`，解析出姓名"张三"，命中已有"张三" → 触发 `duplicate_pending`。
- 「同文件名、不同真实姓名」：不应误判为重复（解析姓名不同 → 正常 save，不触发 pending）。
- 三分支执行结果（overwrite/keep_both/cancel）落库效果（复用/扩展 Task 4.3 的断言）。

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_dispatch_to_agent.py tests\integration\test_routes.py -v
```
```bash
git add tests/
git commit -m "test: cover real-name dedup detection and three resolution branches"
```

### Task 4.6: 【端到端浏览器验证】同名候选人三选一弹窗

**Files:**
- 验收记录（不进 CI）: 本任务 report

- [ ] **Step 1: 准备两份不同文件名、同真实姓名的简历**

- [ ] **Step 2: 浏览器端复现**

用 `cursor-ide-browser`：先上传并解析第一份（创建"张三"）→ 再上传第二份（不同文件名，解析出"张三"）→ 确认三选一弹窗出现。分别验证：
- 「覆盖已有档案」→ 候选人列表仍是 1 个"张三"，档案内容为新数据。
- 「保留两份独立档案」→ 列表出现 2 个"张三"（不同 id）。
- 「取消本次上传」→ 不新增、不修改。

- [ ] **Step 3: 记录截图与结论到 report。不新增 CI 用例。**

---

## Phase 2 — PDF 导出中文渲染测试（阶段二）

### Task 5.1: `pdf_export.py` 生成→回读校验集成测试

**Files:**
- Test: `tests/integration/test_pdf_export.py`（新建）
- 被测: `src/utils/pdf_export.py`（`build_report_pdf(report, candidate_name="") -> bytes`，当前 0% 覆盖）

**Interfaces:**
- Consumes: `build_report_pdf`、`EvalReport`/`DimensionScore`（`src/models/evaluation.py`）、`pymupdf`（`import fitz`）。

**Context:** 生成用 reportlab（注册系统 CJK 字体，Windows 下 simhei/msyh/simsun）。回读用 pymupdf 提取文本断言中文正确、无乱码。**注意跨平台**：`_ensure_cjk_font` 在非 Windows 无 CJK 字体时回退 Helvetica，中文可能无法渲染；测试需对"字体不可用"做条件跳过或断言降级行为，避免在 CI（Linux）误报。

- [ ] **Step 1: 写测试**

```python
import pytest
from datetime import datetime

fitz = pytest.importorskip("fitz")  # pymupdf

from src.models.evaluation import DimensionScore, EvalReport
from src.utils.pdf_export import build_report_pdf, _ensure_cjk_font


def _sample_report() -> EvalReport:
    return EvalReport(
        id="er-test",
        interview_id="iv-test",
        dimensions=[DimensionScore(dimension="技术深度", score=8.0, comment="扎实", evidence=["答对分布式一致性"])],
        overall_score=8.0,
        strengths=["沟通清晰"],
        weaknesses=["系统设计经验不足"],
        recommendation="hire",
        summary="综合表现良好，推荐进入下一轮。",
        generated_at=datetime(2026, 7, 6, 10, 30),
        candidate_id="c-test",
    )


@pytest.mark.integration
def test_build_report_pdf_roundtrip_chinese():
    pdf_bytes = build_report_pdf(_sample_report(), candidate_name="张三")
    assert pdf_bytes[:4] == b"%PDF"

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    doc.close()

    has_cjk_font = _ensure_cjk_font() != "Helvetica"
    if has_cjk_font:
        assert "面试评价报告" in text
        assert "张三" in text
        assert "技术深度" in text
        # 无乱码：不应出现替换字符
        assert "\ufffd" not in text
    else:
        pytest.skip("无可用 CJK 字体（非 Windows CI），跳过中文断言")
```

- [ ] **Step 2: 运行**

Run:
```powershell
.venv\Scripts\python -m pytest tests\integration\test_pdf_export.py -v
```
Expected: 本地 Windows PASS（含中文断言）；无 CJK 字体环境 SKIP。

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_pdf_export.py
git commit -m "test: add pdf export chinese roundtrip integration test"
```

---

## Phase 3 — MainAgent + routes 测试补齐（阶段三）

> 阶段三先补测试，再拆分 `memory_module.py`（Task 7）。拆分依赖阶段二 Task 4.2 对 `memory_module` 的去重相关改动已完成并测试通过。

### Task 6.1: MainAgent 工具调用循环单测

**Files:**
- Test: `tests/unit/test_main_agent.py`
- 被测: `src/agents/main_agent.py:206-409`（`_handle_chat_locked` 工具循环）

**Interfaces:**
- Consumes: `MainAgent.handle_chat(user_message) -> AsyncIterator[str|dict]`；需 mock `OpenAICompatibleClient.chat_stream`/`chat`、`ToolRegistry.dispatch`/`get_schemas`。

- [ ] **Step 1: 写用例**

覆盖三条路径：
1. 纯文本无工具（`chat_stream` 只出 delta，`is_final` 无 tool_calls）→ yield 文字、无 dict 事件。
2. 单轮工具调用（首个 `chat_stream` final 带 tool_calls；随后 `chat` 无 tool_calls 返回文字）→ yield `{"type":"tool_call",...}` + 最终文字。
3. `user_facing` 错误短路（工具结果含 `{"user_facing": True, "message": ...}`）→ 直接 yield 该错误、跳过后续 LLM 自由发挥。

示例（路径 2 骨架）：
```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_chat_single_tool_call(main_agent, fake_llm, fake_tools):
    fake_llm.script_stream_final_tool_calls(name="dispatch_to_agent", arguments='{"agent":"resume","task":"解析"}')
    fake_tools.set_result("dispatch_to_agent", '{"type":"parse_done","markdown_path":"x.md"}')
    fake_llm.script_chat_text("已完成简历解析。")
    events = [c async for c in main_agent.handle_chat("解析这份简历")]
    tool_events = [e for e in events if isinstance(e, dict) and e.get("type") == "tool_call"]
    text = "".join(e for e in events if isinstance(e, str))
    assert len(tool_events) == 1 and tool_events[0]["name"] == "dispatch_to_agent"
    assert "已完成" in text
```

> `main_agent`/`fake_llm`/`fake_tools` fixtures：mock `_llm`（提供 `chat_stream`/`chat`）、`_tools`（`get_schemas` 返回 None 或 schema、`dispatch` 返回脚本化结果）、`_memory_module`、`_user_memory_store.render()` 返回 ""。

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py -k tool -v
```
```bash
git add tests/unit/test_main_agent.py
git commit -m "test: cover MainAgent tool-call loop paths"
```

### Task 6.2: `_trim_history` 边界（含孤儿 tool 消息）单测

**Files:**
- Test: `tests/unit/test_main_agent.py`
- 被测: `src/agents/main_agent.py:411-418`

- [ ] **Step 1: 写用例**

```python
@pytest.mark.unit
def test_trim_history_drops_leading_orphan_tool_messages(main_agent):
    from src.models.message import Message
    # 构造超过 _HISTORY_LIMIT(24) 且截断后开头为 role="tool" 的历史
    main_agent._history = (
        [Message(role="user", content=f"u{i}") for i in range(30)]
    )
    main_agent._history[6] = Message(role="tool", content="orphan", tool_call_id="x")
    main_agent._trim_history()
    assert len(main_agent._history) <= 24
    assert main_agent._history[0].role != "tool"  # 开头孤儿 tool 已被跳过
```

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py -k trim -v
```
```bash
git add tests/unit/test_main_agent.py
git commit -m "test: cover MainAgent _trim_history orphan tool boundary"
```

### Task 6.3: Memory Nudge 触发条件单测

**Files:**
- Test: `tests/unit/test_main_agent.py`
- 被测: `src/agents/main_agent.py:209-213, 260-264, 396-409`（`_NUDGE_INTERVAL=10` 计数、`_should_nudge`、`_background_memory_review` 触发）

- [ ] **Step 1: 写用例**

- 连续 `_NUDGE_INTERVAL` 轮对话后 `_should_nudge` 触发后台 review（mock `_background_memory_review` 断言被调度）。
- 若本轮 LLM 主动调用了 `manage_user_memory`（`tool_called_memory=True`），则重置计数、不重复触发 nudge。

> 用 monkeypatch 把 `_background_memory_review` 替换为记录调用次数的 AsyncMock；用 `asyncio` 让 `create_task` 可被观测。

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py -k nudge -v
```
```bash
git add tests/unit/test_main_agent.py
git commit -m "test: cover MainAgent memory nudge trigger conditions"
```

### Task 6.4: `routes.py` 剩余错误分支测试（404/409）

**Files:**
- Test: `tests/integration/test_routes.py`
- 被测: `src/web/routes.py`（404 `not_found`、409 `no_session`/`interview_in_progress`/`session_error` 等分支）

- [ ] **Step 1: 写用例**

覆盖：
- `GET /api/resume/profile?candidate_id=<不存在>` → 404 `not_found`。
- `POST /api/interview/stop` 无 session → 409 `no_session`。
- `POST /api/interview/suggest` 无 session → 409 `no_session`。
- `GET /api/interview/eval?interview_id=<不存在>` → 404 `not_found`。
- 面试进行中上传简历 → 409 `interview_in_progress`（构造 session.stage=INTERVIEWING）。

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\integration\test_routes.py -v
```
```bash
git add tests/integration/test_routes.py
git commit -m "test: cover routes 404/409 error branches"
```

### Task 6.5: 复核 `main_agent.py`/`routes.py` 覆盖率达 70%+

**Files:**
- 验证: 覆盖率报告

- [ ] **Step 1: 跑针对性覆盖率**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py tests\integration\test_routes.py --cov=src.agents.main_agent --cov=src.web.routes --cov-report=term-missing
```
Expected: `main_agent.py` 与 `routes.py` 覆盖率均 ≥ 70%；对 term-missing 报出的未覆盖关键行补测试直至达标。

- [ ] **Step 2: Commit（如补了测试）**

```bash
git add tests/
git commit -m "test: raise main_agent/routes coverage to 70%+"
```

---

## Phase 3 — 拆分 `memory_module.py`（阶段三，晚于阶段二去重改动）

> **顺序约束：** 必须在 Task 4.2（去重涉及 `get_candidate_by_name`/`save_candidate` 的改动）完成并稳定后再做，避免同文件并行改动。拆分为纯结构重构，**对外接口零变化**。

### Task 7.1: 建立拆分前回归基线

**Files:**
- 验证: 完整测试套件

- [ ] **Step 1: 跑全量单元 + 集成测试，记录通过基线**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration -q
```
Expected: 全绿。记录测试数与耗时，作为拆分后对比基线（同名快照）。

### Task 7.2: 新建 `candidate_store.py` 并迁移候选人相关方法

**Files:**
- Create: `src/storage/candidate_store.py`
- Modify: `src/storage/memory_module.py`（改为委托）
- Test: 复用现有 `tests/unit/test_memory_module.py` / 集成测试

**Interfaces:**
- Produces: `CandidateStore(root: Path)`，迁移：`save_candidate`、`get_candidate`、`get_resume_markdown`、`get_candidate_by_name`、`search_candidates`、`count_candidates`、`delete_candidate`、`get_candidate_history`、`save_brief`/`get_brief`、`save_questions`/`get_questions`/`update_question_coverage`、候选人 index 读写（`_read/_write_candidates_index`）、`_read_profile_meta`、路径工具（`_candidate_dir`/`_profile_path`/`_brief_path`/`_questions_path`）、`_profile_from_meta`/`_format_history_summary`。

**关键约束：** 方法签名、返回类型、异常行为逐一保持与原 `MemoryModule` 一致。多个 store 共享同一 `root` 与 index 文件路径规则；将 `_parse_frontmatter`/`_render_frontmatter`/`_parse_dt` 等纯函数放入一个共享模块 `src/storage/_store_common.py` 供三个 store 复用（DRY）。

- [ ] **Step 1: 抽取共享工具到 `_store_common.py`**

把 `_parse_frontmatter`、`_render_frontmatter`、`_parse_dt`、`_build_candidates_index`、`_build_profile_md`、`_build_interviews_index`、`_normalize_inline`、`_build_transcript_md`、`_build_eval_report_md`、`_profile_from_meta`、`_format_history_summary`、`_parse_transcript`、数据类（`InterviewSummary`/`CandidateHistory`/`RecordingPaths`/`InterviewDetail`）迁入 `_store_common.py`，`memory_module.py` 从中 re-export（保持外部 `from ...memory_module import InterviewDetail` 等历史 import 不破）。

- [ ] **Step 2: 迁移候选人方法到 `CandidateStore`**

`CandidateStore` 持有 `self._root`、`self._index_path`，实现上述方法（逻辑原样搬运）。

- [ ] **Step 3: `MemoryModule` 委托候选人方法**

`MemoryModule.__init__` 内实例化 `self._candidates = CandidateStore(self._root)` 等，候选人方法改为 `return await self._candidates.save_candidate(...)` 形式的一行委托。

- [ ] **Step 4: 跑测试确认无回归**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_memory_module.py tests\integration -q
```
Expected: 与基线一致全绿。

- [ ] **Step 5: Commit**

```bash
git add src/storage/candidate_store.py src/storage/_store_common.py src/storage/memory_module.py
git commit -m "refactor: extract CandidateStore from MemoryModule (facade unchanged)"
```

### Task 7.3: 新建 `interview_store.py` 并迁移面试生命周期 + WAL 方法

**Files:**
- Create: `src/storage/interview_store.py`
- Modify: `src/storage/memory_module.py`

**Interfaces:**
- Produces: `InterviewStore(root: Path)`，迁移：`start_interview`、`append_round`、`scan_orphan_wal`、`recover_interview_from_wal`、`discard_orphan_wal`、`finish_interview`、`get_interview_detail`、面试 index 读写（`_read/_write_interviews_index`）、相关路径工具（`_interviews_dir`/`_interviews_index_path`/`_interview_dir`/`_session_json_path`/`_transcript_path`/`_rounds_wal_path`）。

**依赖注意：** `recover_interview_from_wal` 内部调用 `self.finish_interview`（同 store 内），`finish_interview` 更新 `candidates/index.md` 的 `latest_interview`（跨到候选人 index）——将"更新 candidates index"通过对 `CandidateStore` 的一个窄方法（如 `touch_latest_interview(candidate_id, date_str)`）委托，避免 InterviewStore 直接写候选人 index（低耦合）。`_write_interviews_index` 需读 `_read_profile_meta` 拿候选人名——通过 `CandidateStore` 暴露 `read_profile_meta` 复用。

- [ ] **Step 1: 迁移方法 + 建立 store 间窄接口**

在 `CandidateStore` 暴露：`read_profile_meta(candidate_id) -> dict|None`、`touch_latest_interview(candidate_id, date_str) -> None`。`InterviewStore` 构造时接收 `candidate_store` 引用用于这两处协作。

- [ ] **Step 2: `MemoryModule` 委托面试方法**

- [ ] **Step 3: 跑测试（WAL 恢复/finish 是回归重点）**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_memory_module.py tests\integration -q -k "wal or interview or recover or finish"
```
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add src/storage/interview_store.py src/storage/memory_module.py src/storage/candidate_store.py
git commit -m "refactor: extract InterviewStore (lifecycle + WAL) from MemoryModule"
```

### Task 7.4: 新建 `eval_store.py` 并迁移评价报告持久化

**Files:**
- Create: `src/storage/eval_store.py`
- Modify: `src/storage/memory_module.py`

**Interfaces:**
- Produces: `EvalStore(root: Path, candidate_store, interview_store)`，迁移：`save_eval_report`、`get_eval_report`、`get_latest_eval_report`、`_find_candidate_for_interview`、`_eval_report_path`。

**依赖注意：** `save_eval_report` 会更新 `interviews/index.md` 中的评分/结论——通过 `interview_store` 的 index 读写方法协作；`get_latest_eval_report` 遍历 `interview_store._read_interviews_index`。`rebuild_index` 跨三者，留在 `MemoryModule`（Facade）里编排，分别调用三个 store 的重建能力（或作为 Facade 方法直接读目录，调用各 store 的 index 写方法）。

- [ ] **Step 1: 迁移方法**

- [ ] **Step 2: `MemoryModule` 委托评价方法；`rebuild_index` 留在 Facade 编排**

- [ ] **Step 3: 跑测试（eval orphan 兜底路径是回归重点）**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_memory_module.py tests\integration -q -k "eval or report or rebuild"
```
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add src/storage/eval_store.py src/storage/memory_module.py
git commit -m "refactor: extract EvalStore from MemoryModule (facade unchanged)"
```

### Task 7.5: 确认 `MemoryModule` Facade 接口零变化

**Files:**
- Modify（如需补委托）: `src/storage/memory_module.py`

- [ ] **Step 1: 逐一核对 public 方法仍存在且签名不变**

用 Grep 列出改造前 `MemoryModule` 所有 `async def`/`def`（非 `_` 前缀）方法名，逐一确认改造后仍在 Facade 上且参数/返回一致。差异清零。

- [ ] **Step 2: `ruff check` + import 兼容检查**

Run:
```powershell
.venv\Scripts\ruff check src\storage\
.venv\Scripts\python -c "from src.storage.memory_module import MemoryModule, InterviewDetail, RecordingPaths, CandidateHistory, InterviewSummary; print('ok')"
```
Expected: 打印 `ok`，无 ImportError（历史 import 路径保持可用）。

- [ ] **Step 3: Commit（如有补充）**

```bash
git add src/storage/memory_module.py
git commit -m "refactor: keep MemoryModule facade signatures byte-compatible"
```

### Task 7.6: 拆分后完整回归

**Files:**
- 验证: 完整测试套件

- [ ] **Step 1: 跑全量测试，与 Task 7.1 基线对比**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration -q
```
Expected: 通过数 ≥ 基线，无新增失败。

- [ ] **Step 2: 覆盖率复核不倒退**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration --cov=src --cov-report=term-missing --cov-fail-under=60
```
Expected: 通过门禁。

---

## Phase 4 — Agent 工具调用可视化（阶段四）

> 方案A（已确认）：后端新增 `tool_result` SSE 事件；前端把现有单行"药丸"升级为可展开折叠卡片，`tool_call` 建"进行中"卡、`tool_result` 按 `tool_call_id` 原地更新。

### Task 8.1: MainAgent 推送 `tool_result` 事件

**Files:**
- Modify: `src/agents/main_agent.py:282-296`（工具循环 for 内，`dispatch` 之后）
- Test: `tests/unit/test_main_agent.py`

**Interfaces:**
- Produces: 在现有 `{"type":"tool_call","name","args"}`（`:286-290`）之后、拿到 `result_str` 后，新增
  `yield {"type":"tool_result", "tool_call_id": tc.id, "name": tc.function.name, "result_summary": <摘要>, "success": <bool>}`。
- `success`：解析 `result_str` JSON，若含 `error`/`user_facing` 视为失败，否则成功；解析失败按成功但摘要取截断文本。
- `result_summary`：对 JSON 结果取关键字段（如 `type`/`warning`/`error`）拼简短中文摘要，非 JSON 取前 ~80 字。

- [ ] **Step 1: 写失败测试（成功/失败两种）**

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_result_event_success_and_failure(main_agent, fake_llm, fake_tools):
    # 成功
    fake_llm.script_stream_final_tool_calls(name="dispatch_to_agent", arguments="{}", tc_id="tc-1")
    fake_tools.set_result("dispatch_to_agent", '{"type":"parse_done"}')
    fake_llm.script_chat_text("done")
    events = [c async for c in main_agent.handle_chat("go")]
    tr = [e for e in events if isinstance(e, dict) and e.get("type") == "tool_result"]
    assert tr and tr[0]["tool_call_id"] == "tc-1" and tr[0]["success"] is True

    # 失败（工具返回 error）
    fake_llm.reset()
    fake_llm.script_stream_final_tool_calls(name="dispatch_to_agent", arguments="{}", tc_id="tc-2")
    fake_tools.set_result("dispatch_to_agent", '{"error":"解析失败","user_facing":true}')
    events2 = [c async for c in main_agent.handle_chat("go2")]
    tr2 = [e for e in events2 if isinstance(e, dict) and e.get("type") == "tool_result"]
    assert tr2 and tr2[0]["success"] is False
```

- [ ] **Step 2: 运行确认失败**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py -k tool_result -v
```
Expected: FAIL（尚无 tool_result 事件）。

- [ ] **Step 3: 实现（在 `main_agent.py` dispatch 之后）**

在 `result_str = await self._tools.dispatch(...)` 之后、`tool_msg` 之前插入：

```python
                success, summary = _summarize_tool_result(result_str)
                yield {
                    "type": "tool_result",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "result_summary": summary,
                    "success": success,
                }
```

并在模块顶部新增纯函数：

```python
def _summarize_tool_result(result_str: str) -> tuple[bool, str]:
    """从工具结果 JSON 生成 (success, 简短摘要)。"""
    if not result_str:
        return True, ""
    try:
        data = json.loads(result_str)
    except Exception:
        return True, result_str[:80]
    if isinstance(data, dict):
        if data.get("error") or data.get("user_facing"):
            return False, str(data.get("message") or data.get("error") or "执行失败")[:120]
        rtype = data.get("type", "")
        warning = data.get("warning")
        if warning:
            return True, f"{rtype} · {str(warning)[:80]}" if rtype else str(warning)[:100]
        return True, rtype or "完成"
    return True, str(data)[:80]
```

- [ ] **Step 4: 运行确认通过**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py -k tool_result -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/agents/main_agent.py tests/unit/test_main_agent.py
git commit -m "feat: emit tool_result SSE event from MainAgent tool loop"
```

### Task 8.2: `docs/arc/api.md` 补充 `tool_result` 事件说明

**Files:**
- Modify: `docs/arc/api.md`

- [ ] **Step 1: 在 SSE 事件类型表补一行**

Read `docs/arc/api.md` 找到 SSE/`tool_call` 事件表，追加 `tool_result` 行：字段 `tool_call_id`、`name`、`result_summary`、`success`，说明"工具执行完成后推送，前端按 `tool_call_id` 原地更新对应卡片"。同时补充 `duplicate_candidate` 事件（Task 4.2 新增）说明。

- [ ] **Step 2: Commit**

```bash
git add docs/arc/api.md
git commit -m "docs: document tool_result and duplicate_candidate SSE events"
```

### Task 8.3 + 8.4 + 8.5: 前端折叠卡片（`tool_call` 建卡 → `tool_result` 原地更新）

**Files:**
- Modify: `src/web/ui.py:940-944`（`_chat_stream` 新增 `tool_result` 分支）、`:978-992`（`_render_tool_call_row` 升级为折叠卡片并返回句柄）

**Interfaces:**
- Consumes: `{"type":"tool_call","name","args"}` 与新 `{"type":"tool_result","tool_call_id","name","result_summary","success"}`。
- **关联难点**：`tool_call` 事件当前**不带** `tool_call_id`（`main_agent.py:286-290` 只有 name/args）。为按 id 原地更新，需让 `tool_call` 事件也带 `tool_call_id`。**在 Task 8.1 同时给 `tool_call` 事件补 `"tool_call_id": tc.id`**（改 `main_agent.py:286-290`），否则前端无法关联。

- [ ] **Step 0（补齐 tool_call id，属 8.1 关联改动）**

把 `main_agent.py` 的 `tool_call` 事件改为：
```python
                yield {
                    "type": "tool_call",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "args": tc.function.arguments,
                }
```
并补一条单测断言 `tool_call` 事件含 `tool_call_id`。

- [ ] **Step 1: `_render_tool_call_row` 升级为折叠卡片并登记句柄**

改为创建 `ui.expansion`，返回可更新的内部 label/图标句柄，并存入 `_chat_stream` 本地字典 `tool_cards[tool_call_id]`：

```python
def _render_tool_call_card(col, tool_call_id: str, tool_name: str, args_str: str) -> dict:
    """渲染"进行中"折叠卡片，返回句柄用于后续 tool_result 原地更新。"""
    args_summary = _tool_args_summary(tool_name, args_str)
    with col:
        with ui.row().classes("w-full justify-center py-1") as row:
            exp = ui.expansion(f"⏳ {tool_name}").classes("w-full").style("max-width:85%")
            with exp:
                status = ui.label("执行中…").classes("text-xs text-grey-6")
                ui.label(args_summary or "(无参数摘要)").classes("text-xs text-grey-5 whitespace-pre-wrap")
    return {"row": row, "exp": exp, "status": status, "id": tool_call_id}


def _update_tool_call_card(handle: dict, success: bool, result_summary: str) -> None:
    icon = "✅" if success else "❌"
    handle["exp"].set_text(f"{icon} {handle_name(handle)}")
    handle["status"].set_text(result_summary or ("完成" if success else "失败"))
```

> 具体 NiceGUI API 以实际版本为准（`ui.expansion` 是否支持 `set_text`；若不支持则用内部 label 承载标题）。实现时先在应用里试跑确认渲染。

- [ ] **Step 2: `_chat_stream` 维护卡片字典并处理两类事件**

在 `_chat_stream` 顶部加 `tool_cards: dict[str, dict] = {}`，替换原 `tool_call` 分支并新增 `tool_result` 分支：

```python
                    if chunk_type == "tool_call":
                        tcid = chunk.get("tool_call_id", "")
                        handle = _render_tool_call_card(chat_col, tcid, chunk.get("name", ""), chunk.get("args", ""))
                        if tcid:
                            tool_cards[tcid] = handle
                        await _scroll(chat_scroll)

                    elif chunk_type == "tool_result":
                        tcid = chunk.get("tool_call_id", "")
                        handle = tool_cards.get(tcid)
                        if handle is not None:
                            _update_tool_call_card(handle, chunk.get("success", True), chunk.get("result_summary", ""))
                        else:
                            # 未找到对应 tool_call（容错）：新建一张已完成卡片
                            _render_tool_call_card(chat_col, tcid, chunk.get("name", ""), "")
                        await _scroll(chat_scroll)
```

- [ ] **Step 3: 多次工具调用顺序展示**

由于每个 `tool_call` 都用各自 `tool_call_id` 建独立卡片、`tool_result` 只更新对应卡片，多次调用天然按时间顺序呈现为多张独立卡片，无需合并。确认字典不复用 id 即可。

- [ ] **Step 4: lint + 冒烟**

Run:
```powershell
.venv\Scripts\ruff check src\web\ui.py
```
Expected: 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/web/ui.py src/agents/main_agent.py tests/unit/test_main_agent.py
git commit -m "feat: render tool call as expandable card updated in place by tool_result"
```

### Task 8.6: `tool_result` 字段正确性单测（成功/失败）

**Files:**
- Test: `tests/unit/test_main_agent.py`

- [ ] **Step 1: 强化断言**

在 Task 8.1 基础上补：`result_summary` 内容符合预期（成功取 `type`/`warning`，失败取 `message`/`error`）；`tool_call` 与 `tool_result` 的 `tool_call_id` 一致成对出现。

- [ ] **Step 2: 运行 + Commit**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit\test_main_agent.py -k "tool_result or tool_call" -v
```
```bash
git add tests/unit/test_main_agent.py
git commit -m "test: assert tool_result event field correctness (success/failure)"
```

### Task 8.7: 【端到端浏览器验证】工具调用卡片全过程

**Files:**
- 验收记录（不进 CI）: 本任务 report

- [ ] **Step 1: 启动并触发含工具调用的对话**

用 `cursor-ide-browser`（可 Mock LLM 或真实）：触发一次会调用 `dispatch_to_agent`（解析简历）或 `manage_user_memory`（保存岗位偏好）的对话。

- [ ] **Step 2: 观察卡片状态流转**

确认：卡片先显示"⏳ 执行中…" → 收到结果后原地变为"✅/❌ + 结果摘要"，可展开查看参数摘要；多次调用呈现多张独立卡片、按时间顺序。

- [ ] **Step 3: 验证失败场景**

构造一次会失败的工具调用（如解析不存在的 PDF），确认卡片显示❌与失败摘要。

- [ ] **Step 4: 记录截图与结论到 report。不新增 CI 用例。**

---

## Phase 5 — 收尾验证

### Task 9.1: 全量 lint + 测试 + 覆盖率门禁

**Files:**
- 验证: 全仓库

- [ ] **Step 1: lint**

Run:
```powershell
.venv\Scripts\ruff check .
```
Expected: exit 0。

- [ ] **Step 2: 测试 + 覆盖率门禁**

Run:
```powershell
.venv\Scripts\python -m pytest tests\unit tests\integration --cov=src --cov-report=term-missing --cov-fail-under=60
```
Expected: 全绿且覆盖率 ≥ 60%。

### Task 9.2: 同步更新 `docs/arc/` 架构文档

**Files:**
- Modify: `docs/arc/context-memory.md`（token 精确计数）、`docs/arc/flows.md`（追问真流式、去重后移流程）、`docs/arc/storage.md`（`memory_module` 拆分为三 store + Facade）、`docs/arc/api.md`（若 8.2 未完全覆盖则补齐 `tool_result`/`duplicate_candidate`/`resolve-duplicate` 端点）

- [ ] **Step 1: 逐篇更新受影响章节**

保持文档简洁（只描述核心逻辑）：
- `context-memory.md`：token 估算由字符数 `//3` 改为 tiktoken `count_tokens`（虚拟消息列表单次计数），压缩触发阈值验证结论。
- `flows.md`：追问建议时序改为 `chat_stream` 逐 token；简历上传→解析→重名三选一（overwrite/keep_both/cancel）的新时序。
- `storage.md`：`MemoryModule` 现为 Facade，委托 `CandidateStore`/`InterviewStore`/`EvalStore`；共享 `_store_common.py`。
- `api.md`：确认 `tool_result`、`duplicate_candidate` 事件与 `/api/resume/resolve-duplicate` 端点已记录。

- [ ] **Step 2: Commit**

```bash
git add docs/arc/
git commit -m "docs: sync arc docs for token count, streaming, dedup, store split"
```

### Task 9.3: 复核并更新 `docs/todo/` 最终状态

**Files:**
- Modify: `docs/todo/*.md`

- [ ] **Step 1: 勾选本轮完成项**

基于阶段二/三/四实际落地，更新 `docs/todo/` 中相关条目状态（尤其 CI/覆盖率、可观测性、结构化面试相关）。

- [ ] **Step 2: Commit**

```bash
git add docs/todo/
git commit -m "docs: finalize docs/todo status after rollout"
```

---

## Testing Strategy

- **单元测试**：Token 计数（`test_context.py`）、追问流式与取消（`test_interview_agent.py`）、MainAgent 工具循环/trim/nudge/tool_result（`test_main_agent.py`）、去重识别与摘要（`test_dispatch_to_agent.py`、`test_pending_uploads.py`）、STT 凭证隔离（`test_volc_stt.py`）。
- **集成测试**：routes 404/409 与 resolve-duplicate 三分支（`test_routes.py`）、PDF 生成→pymupdf 回读（`test_pdf_export.py`）、`memory_module` 拆分前后回归（`test_memory_module.py` 全量）。
- **端到端浏览器验证（不进 CI，仅记录）**：追问流式（Task 3.5）、去重三选一弹窗（Task 4.6）、工具调用卡片（Task 8.7），均用 `cursor-ide-browser` MCP。
- **门禁**：`ruff check .` + `pytest tests/unit tests/integration --cov=src --cov-fail-under=60`。

## Risks & Mitigations

- **tiktoken 精确计数导致压缩/分块阈值偏移**：Task 2.3 用中英混杂真实数据回归；偏移显著则调常量并在 report 记原因。
- **追问流式化后取消边界**：Task 3.3/3.4 专门覆盖中途取消无悬挂任务；Task 3.5 端到端手动触发中止。
- **去重与 `memory_module` 拆分耦合**：顺序上拆分（Phase 3 Task 7）晚于去重（Phase 2 Task 4）；拆分前后 Task 7.1/7.6 回归基线对比。
- **`tool_result` 前端关联复杂度**：事件携带 `tool_call_id`，前端按 id 原地更新（Task 8.0 补齐 `tool_call` 的 id）；容错分支处理找不到卡片的情况。
- **PDF 中文测试跨平台**：非 Windows 无 CJK 字体时 `pytest.importorskip`/条件 skip，避免 CI 误报。
- **去重三选一的前后端往返**：采用 pending 暂存 + `duplicate_candidate` SSE 事件 + `resolve-duplicate` 端点；Task 4.1 先确认接入点，若发现更简方案可替换。

## Success Criteria

- [ ] `ruff check .` 与覆盖率门禁 `--cov-fail-under=60` 在 CI 通过。
- [ ] 上下文/评价 token 估算全部走 `count_tokens`（虚拟消息列表单次计数），压缩阈值经回归验证。
- [ ] 追问建议逐 token 流式展示，中途中止无状态不一致（端到端已验证）。
- [ ] 候选人去重在解析后按真实姓名判定，三选一（覆盖/保留两份/取消）后端与前端均正确（端到端已验证）。
- [ ] `pdf_export.py` 有生成→回读中文校验测试；`main_agent.py`/`routes.py` 覆盖率 ≥ 70%。
- [ ] `MemoryModule` 拆分为三 store + Facade，对外接口零变化，全量回归无失败。
- [ ] 工具调用以折叠卡片可视化（`tool_call`→进行中，`tool_result`→原地更新，多次调用独立卡片），端到端已验证含失败场景。
- [ ] `docs/arc/`、`docs/todo/`、`CHANGELOG.md`、Issue/PR 模板、Demo checklist 均已更新/新增。

---

## Self-Review Notes

- **Spec coverage**：tasks.md 的 46 个任务（1.1–9.3）与本计划 Task 一一对应（Phase 1 = §1，Phase 2 = §2–5，Phase 3 = §6–7，Phase 4 = §8，Phase 5 = §9）。
- **顺序约束落地**：格式化独立提交（1.3）先于行为改动；`memory_module` 拆分（Phase 3 Task 7）晚于去重（Phase 2 Task 4）；工具可视化前端依赖 `tool_call_id`（Task 8.0）。
- **虚拟消息列表约束**：Task 2.1 显式要求 summary+各轮拼一份列表单次 `count_tokens`，避免 overhead 重复叠加。
- **端到端非 CI**：3 项浏览器验证（3.5/4.6/8.7）均标注"记录、不新增 CI 用例"。
- **待实现者确认点**：Task 4.1（去重接入点最终方案）、`get_settings` 是否 lru_cache（1.6）、NiceGUI `ui.expansion` 具体更新 API（8.3）——均在对应步骤标注需 Grep/试跑确认。
