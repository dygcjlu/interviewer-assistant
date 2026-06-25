# 文本 LLM 与多模态（VL）LLM 配置拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立的 VL LLM 配置变量（`VL_LLM_*`），使 PDF 多模态解析与文本 LLM 可使用不同提供商、不同 API Key 和 Base URL，不设置时自动回退保持零破坏性。

**Architecture:** 在 `Settings`（pydantic BaseSettings）中新增 3 个可选字段和 3 个 `@property` 计算有效值；`QwenVLParser` 改用这些计算属性；`.env` 更新示例注释。回退优先级：`VL_LLM_*` → `QWEN_VL_MODEL`（仅 model）→ `LLM_*`。

**Tech Stack:** Python 3.12, pydantic-settings v2, pytest

---

## 文件结构

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/config.py` | 修改 | 新增 3 个字段 + 3 个 @property |
| `src/tools/pdf_parsers/qwen_vl_parser.py` | 修改 | 替换 3 处配置读取 |
| `.env` | 修改 | 注释更新 + VL 示例块 |
| `docs/arc/llm-providers.md` | 修改 | 新增 VL 独立配置章节 |
| `tests/unit/test_config_vl.py` | 新建 | Settings 回退逻辑单元测试 |

---

## Task 1：Settings 新增 VL 配置字段与 @property

**Files:**
- Modify: `src/config.py`
- Test: `tests/unit/test_config_vl.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_config_vl.py`（若 `tests/unit/` 不存在则先创建目录）：

```python
"""测试 Settings VL LLM 配置回退逻辑。"""
import pytest
from src.config import Settings


def test_effective_vl_api_key_uses_vl_when_set():
    s = Settings(
        LLM_API_KEY="text-key",
        VL_LLM_API_KEY="vl-key",
    )
    assert s.effective_vl_api_key == "vl-key"


def test_effective_vl_api_key_falls_back_to_llm():
    s = Settings(
        LLM_API_KEY="text-key",
        VL_LLM_API_KEY="",
    )
    assert s.effective_vl_api_key == "text-key"


def test_effective_vl_base_url_uses_vl_when_set():
    s = Settings(
        LLM_BASE_URL="https://text.api.com",
        VL_LLM_BASE_URL="https://vl.api.com",
    )
    assert s.effective_vl_base_url == "https://vl.api.com"


def test_effective_vl_base_url_falls_back_to_llm():
    s = Settings(
        LLM_BASE_URL="https://text.api.com",
        VL_LLM_BASE_URL="",
    )
    assert s.effective_vl_base_url == "https://text.api.com"


def test_effective_vl_model_uses_vl_model_first():
    s = Settings(
        LLM_MODEL="text-model",
        QWEN_VL_MODEL="qwen-vl-model",
        VL_LLM_MODEL="vl-model",
    )
    assert s.effective_vl_model == "vl-model"


def test_effective_vl_model_falls_back_to_qwen_vl_model():
    s = Settings(
        LLM_MODEL="text-model",
        QWEN_VL_MODEL="qwen-vl-model",
        VL_LLM_MODEL="",
    )
    assert s.effective_vl_model == "qwen-vl-model"


def test_effective_vl_model_falls_back_to_llm_model():
    s = Settings(
        LLM_MODEL="text-model",
        QWEN_VL_MODEL="",
        VL_LLM_MODEL="",
    )
    assert s.effective_vl_model == "text-model"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv\Scripts\python -m pytest tests/unit/test_config_vl.py -v
```

预期：`AttributeError` 或 `ValidationError`（`VL_LLM_API_KEY` 字段不存在）

- [ ] **Step 3: 实现 — 在 `src/config.py` 新增字段和 @property**

在 `Settings` 类的 `QWEN_VL_MODEL` 字段后面插入：

```python
    # 多模态 VL LLM 独立配置（可选；空字符串 = 跟随主 LLM）
    VL_LLM_API_KEY: str = ""
    VL_LLM_BASE_URL: str = ""
    VL_LLM_MODEL: str = ""

    @property
    def effective_vl_api_key(self) -> str:
        return self.VL_LLM_API_KEY or self.LLM_API_KEY

    @property
    def effective_vl_base_url(self) -> str:
        return self.VL_LLM_BASE_URL or self.LLM_BASE_URL

    @property
    def effective_vl_model(self) -> str:
        return self.VL_LLM_MODEL or self.QWEN_VL_MODEL or self.LLM_MODEL
```

- [ ] **Step 4: 运行测试，确认全部通过**

```bash
.venv\Scripts\python -m pytest tests/unit/test_config_vl.py -v
```

预期：7 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/config.py tests/unit/test_config_vl.py
git commit -m "feat: add VL_LLM_* config fields with fallback properties to Settings"
```

---

## Task 2：更新 QwenVLParser 使用新属性

**Files:**
- Modify: `src/tools/pdf_parsers/qwen_vl_parser.py`

- [ ] **Step 1: 修改 `extract()` 方法中的 3 处配置读取**

将 `extract()` 方法中：

```python
        client = openai.AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )
```

改为：

```python
        client = openai.AsyncOpenAI(
            api_key=settings.effective_vl_api_key,
            base_url=settings.effective_vl_base_url,
        )
```

将 `_run_one` 内部调用：

```python
                    return await self._extract_page(client, settings.QWEN_VL_MODEL, b64)
```

改为：

```python
                    return await self._extract_page(client, settings.effective_vl_model, b64)
```

- [ ] **Step 2: 验证无 linter 错误**

```bash
.venv\Scripts\python -m ruff check src/tools/pdf_parsers/qwen_vl_parser.py
```

预期：无输出（无错误）

- [ ] **Step 3: 运行已有测试套件确保无回归**

```bash
.venv\Scripts\python -m pytest tests/ -v --ignore=tests/e2e -x -q 2>&1 | head -50
```

预期：所有已有测试仍通过

- [ ] **Step 4: 提交**

```bash
git add src/tools/pdf_parsers/qwen_vl_parser.py
git commit -m "refactor: use effective_vl_* settings in QwenVLParser"
```

---

## Task 3：更新 `.env` 注释

**Files:**
- Modify: `.env`

- [ ] **Step 1: 在 PDF 解析配置块中更新注释**

找到 `.env` 中以下内容：

```ini
# Qwen-VL 解析（PDF_PARSER=qwen_vl 时有效，复用 LLM_API_KEY / LLM_BASE_URL）
QWEN_VL_MODEL=qwen3-vl-plus
```

改为：

```ini
# Qwen-VL 解析（PDF_PARSER=qwen_vl 时有效）
# 若文本 LLM 与 VL LLM 是同一提供商（如 Qwen），VL_LLM_* 可留空，自动复用 LLM_* 配置
# 若文本 LLM 与 VL LLM 是不同提供商（如 DeepSeek 文本 + Qwen VL），需填写以下三项：
# VL_LLM_API_KEY=sk-aliyun-xxx
# VL_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# VL_LLM_MODEL=qwen3-vl-plus
#
# 已弃用（保留兼容）：QWEN_VL_MODEL，建议迁移到 VL_LLM_MODEL
QWEN_VL_MODEL=qwen3-vl-plus
```

- [ ] **Step 2: 提交**

```bash
git add .env
git commit -m "docs: update .env to document VL_LLM_* config and deprecate QWEN_VL_MODEL"
```

---

## Task 4：更新 `docs/arc/llm-providers.md`

**Files:**
- Modify: `docs/arc/llm-providers.md`

- [ ] **Step 1: 新增多模态 LLM 独立配置章节**

在文档末尾（`## 各 Agent 的思考模式建议` 之后）追加：

```markdown
---

## 多模态（VL）LLM 独立配置

PDF 简历解析（`QwenVLParser`）使用多模态视觉语言模型，与文本 LLM 可以是不同提供商。

### 配置变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `VL_LLM_API_KEY` | VL 模型的 API Key | 空（回退到 `LLM_API_KEY`）|
| `VL_LLM_BASE_URL` | VL 模型的接入点 | 空（回退到 `LLM_BASE_URL`）|
| `VL_LLM_MODEL` | VL 模型名称 | 空（回退到 `QWEN_VL_MODEL`，再回退到 `LLM_MODEL`）|

### 回退规则

```
effective_vl_api_key:   VL_LLM_API_KEY → LLM_API_KEY
effective_vl_base_url:  VL_LLM_BASE_URL → LLM_BASE_URL
effective_vl_model:     VL_LLM_MODEL → QWEN_VL_MODEL → LLM_MODEL
```

### 典型场景

**同一提供商（Qwen 文本 + Qwen VL）** — `VL_LLM_*` 留空，自动复用：
```ini
LLM_PROVIDER=qwen
LLM_API_KEY=sk-aliyun-xxx
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.7-plus
QWEN_VL_MODEL=qwen3-vl-plus   # 或 VL_LLM_MODEL=qwen3-vl-plus
```

**不同提供商（DeepSeek 文本 + Qwen VL）** — 需填写 VL_LLM_*：
```ini
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-deepseek-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro

VL_LLM_API_KEY=sk-aliyun-xxx
VL_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VL_LLM_MODEL=qwen3-vl-plus
```
```

- [ ] **Step 2: 提交**

```bash
git add docs/arc/llm-providers.md
git commit -m "docs: document VL LLM independent config in llm-providers.md"
```

---

## Task 5：运行完整测试套件

- [ ] **Step 1: 运行所有单元测试**

```bash
.venv\Scripts\python -m pytest tests/ -v --ignore=tests/e2e -q 2>&1 | tail -20
```

预期：所有测试通过，无新失败

- [ ] **Step 2: 检查 linter**

```bash
.venv\Scripts\python -m ruff check src/config.py src/tools/pdf_parsers/qwen_vl_parser.py
```

预期：无输出

---

## 验收标准

1. 设置 `VL_LLM_API_KEY` 时，`QwenVLParser` 使用该 Key，而非 `LLM_API_KEY`
2. 不设置 `VL_LLM_*` 时，行为与改动前完全相同（向后兼容）
3. 三级回退 `VL_LLM_MODEL → QWEN_VL_MODEL → LLM_MODEL` 均有单元测试覆盖
4. 所有已有测试通过

---

*Base ref: 16e99a4394affa071fd60ce10d225366a7530fbe*
