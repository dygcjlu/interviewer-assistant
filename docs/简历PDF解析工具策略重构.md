# 简历 PDF 解析工具策略重构

## 背景

当前使用 `pymupdf4llm.to_markdown()` 将简历 PDF 转成 Markdown，对多栏布局的还原质量较差：
节标题（如「工作经历」）与下属公司名被合并为同一行，多家公司内容混入单个文本块，层级完全丢失。

本方案引入 **Strategy 模式**，支持三种可配置的 PDF 解析工具，通过 `.env` 一行配置切换。

---

## 三种解析工具概览

| 工具 | 标识符 | 原理 | 依赖 | 精度 |
|---|---|---|---|---|
| pymupdf（当前） | `pymupdf` | 字体启发式规则提取 | 无需额外配置 | 低（多栏简历约 60%） |
| Qwen-VL | `qwen_vl` | 多模态 LLM 视觉理解，逐页图片识别 | 复用已有 DashScope key | 高（95%+） |
| MinerU Cloud API | `mineru` | 专业文档解析引擎（VLM+OCR 双引擎） | 免 Token 或申请 Token | 高（86–95%+） |

---

## 架构设计

### Strategy 模式

```
parse_resume_pdf(file_path)
        │
        ▼
  get_parser(PDF_PARSER)          ← 工厂函数，读取配置
        │
        ├── "pymupdf"  → PymupdfParser
        ├── "qwen_vl"  → QwenVLParser
        └── "mineru"   → MineruParser
                │
                ▼
        parser.extract(file_path) → str (Markdown)
```

所有解析器实现同一接口：

```python
class BasePDFParser(ABC):
    @abstractmethod
    async def extract(self, file_path: str) -> str:
        """从 PDF 提取 Markdown 文本。"""
```

### 文件结构

```
src/tools/
├── parse_resume_pdf.py          # 入口，工厂函数 + 工具注册（保持不变）
└── pdf_parsers/
    ├── __init__.py
    ├── base.py                  # BasePDFParser 抽象类
    ├── pymupdf_parser.py        # 现有逻辑迁移（pymupdf4llm）
    ├── qwen_vl_parser.py        # Qwen-VL 多模态解析
    └── mineru_parser.py         # MinerU Cloud API
```

---

## 配置变更

### `.env` 新增项

```dotenv
# PDF 解析引擎：pymupdf | qwen_vl | mineru
PDF_PARSER=pymupdf

# Qwen-VL 解析（PDF_PARSER=qwen_vl 时有效）
# 复用已有 QWEN_API_KEY（DashScope key）
QWEN_VL_MODEL=qwen-vl-max

# MinerU Cloud API（PDF_PARSER=mineru 时有效）
# 留空 → 使用免 Token 的 Agent 轻量 API（IP 限频，≤10MB，≤20页）
# 填写 → 使用精准 API（需在 mineru.net 申请，≤200MB，≤200页，vlm 模型精度更高）
MINERU_API_TOKEN=
MINERU_MODEL_VERSION=vlm         # pipeline | vlm（仅精准 API 有效）
```

### `src/config.py` 新增字段

```python
# PDF 解析引擎
PDF_PARSER: str = "pymupdf"       # pymupdf | qwen_vl | mineru

# Qwen-VL 解析配置
QWEN_VL_MODEL: str = "qwen-vl-max"

# MinerU Cloud API 配置
MINERU_API_TOKEN: str = ""
MINERU_MODEL_VERSION: str = "vlm"
```

---

## 各解析器实现细节

### 1. PymupdfParser（现有逻辑）

无需改动逻辑，仅做代码迁移。

```python
class PymupdfParser(BasePDFParser):
    async def extract(self, file_path: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: pymupdf4llm.to_markdown(file_path)
        )
```

---

### 2. QwenVLParser（Qwen-VL 多模态）

**调用流程：**

```
PDF 文件
    │
    ▼
pymupdf 逐页渲染为 PNG（复用 pymupdf 依赖，无需新增）
    │
    ├── Page 1 PNG ──┐
    ├── Page 2 PNG ──┤──→ 合并为单次 multimodal 请求（或逐页）
    └── Page N PNG ──┘
                     │
                     ▼
            DashScope multimodal API
            模型: QWEN_VL_MODEL
            Prompt: "请将图片中的文档内容完整转换为 Markdown 格式，
                    保留标题层级、列表、段落结构，不要遗漏任何内容。"
                     │
                     ▼
                Markdown 文本
```

**API 调用（DashScope，复用已有 QWEN_API_KEY）：**

```python
# 使用 OpenAI 兼容接口
client = openai.AsyncOpenAI(
    api_key=settings.QWEN_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# 每页图片 base64 编码后作为 image_url 传入
response = await client.chat.completions.create(
    model=settings.QWEN_VL_MODEL,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": "请将图片中的文档内容完整转换为 Markdown 格式..."}
        ]
    }]
)
```

**注意事项：**
- 简历通常 1–3 页，每页一次请求，结果按页拼接
- PNG 渲染 DPI 建议 150（清晰度与 token 消耗平衡）
- 需要新增依赖：`pymupdf`（用于渲染，`pymupdf4llm` 已间接依赖，可直接 `import pymupdf`）

---

### 3. MineruParser（MinerU Cloud API）

MinerU 提供两套 API，根据 `MINERU_API_TOKEN` 是否配置自动切换：

#### 模式 A：Agent 轻量 API（无 Token，免费，IP 限频）

适合绝大多数简历场景（简历通常 ≤10MB，≤20 页）。

**调用流程：**

```
本地 PDF 文件
    │
    ▼
POST https://mineru.net/api/v1/agent/parse/file
  body: {"file_name": "xxx.pdf", "language": "ch"}
    │
    ▼
获取 task_id + OSS 签名上传 URL
    │
    ▼
PUT <file_url> （直接 PUT 文件到 OSS，无需 Authorization）
    │
    ▼
轮询 GET https://mineru.net/api/v1/agent/parse/{task_id}
  直到 state == "done"
    │
    ▼
GET data.markdown_url （CDN 链接，下载 Markdown 文本）
    │
    ▼
返回 Markdown 字符串
```

#### 模式 B：精准 API（需 Token，vlm 模型，更高精度）

配置了 `MINERU_API_TOKEN` 时使用。

**调用流程：**

```
本地 PDF 文件
    │
    ▼
POST https://mineru.net/api/v4/file-urls/batch
  header: Authorization: Bearer {MINERU_API_TOKEN}
  body: {"files": [{"name": "xxx.pdf"}], "model_version": "vlm"}
    │
    ▼
获取 batch_id + OSS 上传 URL
    │
    ▼
PUT <upload_url> （上传文件）
    │
    ▼
轮询 GET https://mineru.net/api/v4/extract-results/batch/{batch_id}
  直到所有文件 state == "done"
    │
    ▼
GET data.full_zip_url → 下载 zip → 解压 → 读取 full.md
    │
    ▼
返回 Markdown 字符串
```

**轮询策略（两种模式通用）：**

```python
POLL_INTERVAL = 3      # 初始间隔秒数
MAX_POLL_INTERVAL = 15 # 最大间隔（指数退避上限）
TIMEOUT = 300          # 超时秒数
```

---

## 入口改造：`parse_resume_pdf.py`

```python
async def parse_resume_pdf(file_path: str) -> str:
    """从 PDF 文件提取结构化 Markdown，返回 JSON 字符串。"""
    settings = get_settings()
    parser = get_pdf_parser(settings.PDF_PARSER)   # 工厂函数
    try:
        text = await parser.extract(file_path)
        return json.dumps({"text": text, "pages": None}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("parse_resume_pdf failed: %s", file_path)
        return json.dumps({"error": str(exc)})


def get_pdf_parser(parser_type: str) -> BasePDFParser:
    if parser_type == "qwen_vl":
        return QwenVLParser()
    elif parser_type == "mineru":
        return MineruParser()
    else:
        return PymupdfParser()
```

---

## 新增依赖

| 工具 | 新增依赖 | 说明 |
|---|---|---|
| pymupdf | 无 | 现有 `pymupdf4llm` 已包含 |
| qwen_vl | 无 | 复用现有 `openai` SDK（已在 requirements.txt）；`pymupdf` 用于 PDF→图片，同上 |
| mineru | `aiohttp` 或 `httpx`（异步 HTTP） | 用于上传文件、轮询状态、下载结果；如已有 `httpx` 则无需新增 |

---

## 工具对比与推荐

| 维度 | pymupdf | qwen_vl | mineru（Agent） | mineru（精准） |
|---|---|---|---|---|
| 配置难度 | 无需配置 | 复用现有 key | 无需 Token | 需申请 Token |
| 简历精度 | 低 | 高 | 中高 | 最高 |
| 速度 | 极快（本地） | 中（每页 1 次 API）| 慢（异步轮询 10–60s）| 慢（同上） |
| 成本 | 免费 | 按 token 计费 | 免费（IP 限频）| 按页计费 |
| 适合场景 | 调试/降级 | 日常使用推荐 | 无 key 场景 | 追求最高精度 |

**推荐默认值：`PDF_PARSER=qwen_vl`**，理由：
- 已有 DashScope key，零新增配置
- 简历 1–3 页，成本可忽略
- 精度显著优于 pymupdf，优于 MinerU Agent API

---

## 实现任务拆解

```
1. 创建 src/tools/pdf_parsers/ 目录结构
   → 验证：目录和 __init__.py 存在

2. 实现 base.py（BasePDFParser 抽象类）
   → 验证：可正常导入

3. 迁移现有逻辑到 pymupdf_parser.py
   → 验证：现有测试（上传简历）行为不变

4. 实现 qwen_vl_parser.py
   → 验证：PDF_PARSER=qwen_vl，上传王韬略简历，层级正确

5. 实现 mineru_parser.py（Agent 模式）
   → 验证：PDF_PARSER=mineru，上传简历，返回合法 Markdown

6. 改造 parse_resume_pdf.py（工厂函数）
   → 验证：切换三种配置，行为均符合预期

7. 更新 src/config.py 新增配置字段
   → 验证：.env 中设置各字段后 get_settings() 能正确读取

8. 更新 .env.example 或 .env（补充注释和新字段）
```
