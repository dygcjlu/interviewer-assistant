---
comet_change: split-llm-vl-config
role: technical-design
canonical_spec: openspec
---

# Design Doc: 文本 LLM 与多模态（VL）LLM 配置拆分

## 背景

项目中存在两类 LLM 使用场景：
1. **文本 LLM**：聊天、追问建议、评价报告（DeepSeek/Qwen 等）
2. **多模态 VL LLM**：PDF 简历逐页识别（Qwen-VL 等）

两者共用同一套 `LLM_*` 配置变量。切换文本 LLM 到 DeepSeek 后，`QwenVLParser` 仍用 DeepSeek 的 key 和 base_url 访问阿里云 Qwen-VL 接口，导致认证失败。

## 技术方案

### 新增配置变量（`src/config.py`）

```python
# 多模态 VL LLM 独立配置（可选；空字符串表示跟随主 LLM）
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
    # 三级回退：VL_LLM_MODEL → QWEN_VL_MODEL（兼容旧配置）→ LLM_MODEL
    return self.VL_LLM_MODEL or self.QWEN_VL_MODEL or self.LLM_MODEL
```

`@property` 在 pydantic v2 `BaseSettings` 上作为普通 Python 描述符工作，不影响 model field 解析，无兼容风险。

### 修改 QwenVLParser（`src/tools/pdf_parsers/qwen_vl_parser.py`）

将 `extract()` 中 3 处配置读取改为新属性：

```python
# Before
client = openai.AsyncOpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
# ...
return await self._extract_page(client, settings.QWEN_VL_MODEL, b64)

# After
client = openai.AsyncOpenAI(api_key=settings.effective_vl_api_key, base_url=settings.effective_vl_base_url)
# ...
return await self._extract_page(client, settings.effective_vl_model, b64)
```

### 配置示例（`.env`）

DeepSeek 文本 + Qwen-VL 多模态的典型配置：

```ini
# 文本 LLM
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-deepseek-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro

# 多模态 VL LLM（PDF 解析）— 与文本 LLM 不同提供商时填写
VL_LLM_API_KEY=sk-aliyun-xxx
VL_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VL_LLM_MODEL=qwen3-vl-plus
```

同一提供商（Qwen 文本 + Qwen VL）时，`VL_LLM_*` 保持空，自动复用 `LLM_*`。

## 回退逻辑

```
effective_vl_api_key:   VL_LLM_API_KEY → LLM_API_KEY
effective_vl_base_url:  VL_LLM_BASE_URL → LLM_BASE_URL
effective_vl_model:     VL_LLM_MODEL → QWEN_VL_MODEL → LLM_MODEL
```

`QWEN_VL_MODEL` 保留不删除，作为中间回退兼容旧配置。

## 测试策略

单元测试验证三种场景：
1. **VL 全空**：`effective_vl_*` 均回退到 `LLM_*`
2. **VL 全填**：`effective_vl_*` 均使用 `VL_LLM_*`
3. **仅填 QWEN_VL_MODEL**：`effective_vl_model` 回退到 `QWEN_VL_MODEL`（旧配置兼容）

## 文件变更清单

| 文件 | 变更 | 行数估算 |
|---|---|---|
| `src/config.py` | +3 字段，+3 @property | +15 行 |
| `src/tools/pdf_parsers/qwen_vl_parser.py` | 替换 3 处属性读取 | ±3 行 |
| `.env` | 注释更新，新增 VL 示例块 | +15 行 |
| `docs/arc/llm-providers.md` | 新增 VL 配置章节 | +30 行 |
| `tests/test_config_vl.py` | 新建单元测试 | +40 行 |
