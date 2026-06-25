---
comet_change: benchmark-llm-latency
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

# LLM 延迟基准测试脚本设计

## 背景

追问建议（InterviewAgent）当前使用 `qwen3.7-plus` 单次 LLM 调用耗时 25s+，需要一个基准测试脚本对多平台、多模型、多参数配置进行客观的延迟对比，支撑模型选型决策。

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 核心数据结构

```python
@dataclass(frozen=True)
class ThinkingConfig:
    extra_body: dict | None          # 附加到请求 body 的额外参数（如 enable_thinking）
    suppress_temperature: bool       # 是否禁止传 temperature（DeepSeek 官方思考模式）
    reasoning_effort: str | None     # "high" | "max" | None

@dataclass(frozen=True)
class ModelConfig:
    label: str                       # 报告显示名（如 "qwen3.7-plus+think"）
    model: str                       # API model ID
    base_url: str                    # 平台 endpoint
    api_key_env: str                 # 读取 API Key 的环境变量名
    no_think: ThinkingConfig         # 非思考模式参数
    with_think: ThinkingConfig | None  # 思考模式参数（None=不支持/未配置）

@dataclass
class BenchmarkResult:
    label: str
    thinking: bool
    ttft_ms: float | None            # 流式首 token 延迟；调用失败时为 None
    total_ms: float | None           # 总延迟
    prompt_tokens: int
    completion_tokens: int
    tokens_per_sec: float | None
    error: str | None                # 异常信息；成功时为 None
```

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 测试矩阵（18 个配置）

**百炼 DashScope**（`LLM_API_KEY`，base_url = `https://dashscope.aliyuncs.com/compatible-mode/v1`）：

| 标签 | Model ID | 思考参数 |
|------|----------|---------|
| qwen3.7-plus | `qwen3.7-plus` | 无 |
| qwen3.7-plus+think | `qwen3.7-plus` | `extra_body={"enable_thinking":True}` |
| qwen3.7-max | `qwen3.7-max` | 无 |
| qwen3.7-max+think | `qwen3.7-max` | `extra_body={"enable_thinking":True}` |
| ds-v4-pro (百炼) | `deepseek-v4-pro` | 无 |
| ds-v4-pro+think (百炼) | `deepseek-v4-pro` | `extra_body={"enable_thinking":True}` |
| ds-v4-flash (百炼) | `deepseek-v4-flash` | 无 |
| ds-v4-flash+think (百炼) | `deepseek-v4-flash` | `extra_body={"enable_thinking":True}` |
| vanchin-v4-pro | `vanchin/deepseek-v4-pro` | 无 |
| vanchin-v4-pro+think | `vanchin/deepseek-v4-pro` | `extra_body={"enable_thinking":True}` |
| kimi-k2.6 | `kimi/kimi-k2.6` | 无 |
| kimi-k2.6+think | `kimi/kimi-k2.6` | `extra_body={"enable_thinking":True}`（失败则记录错误） |
| minimax-m3 | `MiniMax/MiniMax-M3` | 无 |
| glm-5.1 | `ZHIPU/GLM-5.1` | 无 |

**DeepSeek 官方**（`DEEPSEEK_API_KEY`，base_url = `https://api.deepseek.com`）：

| 标签 | Model ID | 思考参数 |
|------|----------|---------|
| ds-v4-pro (官方) | `deepseek-v4-pro` | 无 |
| ds-v4-pro+think (官方) | `deepseek-v4-pro` | `extra_body={"thinking":{"type":"enabled"}}` + 不传 temperature + `reasoning_effort="high"` |
| ds-v4-flash (官方) | `deepseek-v4-flash` | 无 |
| ds-v4-flash+think (官方) | `deepseek-v4-flash` | `extra_body={"thinking":{"type":"enabled"}}` + 不传 temperature + `reasoning_effort="high"` |

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 核心实现逻辑

### 流式调用与计时

```python
async def run_streaming_benchmark(
    config: ModelConfig,
    prompt: str,
    use_thinking: bool,
    timeout: float = 60.0,
    temperature: float = 0.1,
) -> BenchmarkResult:
    thinking = config.with_think if use_thinking else config.no_think
    api_key = os.environ.get(config.api_key_env, "")

    client = openai.AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    kwargs = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if not thinking.suppress_temperature:
        kwargs["temperature"] = temperature
    if thinking.reasoning_effort:
        kwargs["reasoning_effort"] = thinking.reasoning_effort
    if thinking.extra_body:
        kwargs["extra_body"] = thinking.extra_body

    start = time.perf_counter()
    ttft = None
    prompt_tokens = completion_tokens = 0
    content_len = 0

    try:
        stream = await client.chat.completions.create(**kwargs, timeout=timeout)
        async for chunk in stream:
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens or 0
                completion_tokens = chunk.usage.completion_tokens or 0
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                content_len += len(delta)
                if ttft is None:
                    ttft = (time.perf_counter() - start) * 1000

        total_ms = (time.perf_counter() - start) * 1000
        tokens_per_sec = (completion_tokens / total_ms * 1000) if total_ms > 0 else 0

        return BenchmarkResult(
            label=config.label,
            thinking=use_thinking,
            ttft_ms=ttft,
            total_ms=total_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_sec=tokens_per_sec,
            error=None,
        )
    except Exception as exc:
        return BenchmarkResult(
            label=config.label, thinking=use_thinking,
            ttft_ms=None, total_ms=None,
            prompt_tokens=0, completion_tokens=0, tokens_per_sec=None,
            error=str(exc)[:120],
        )
```

### 参数注入策略（思考模式）

| 平台 | extra_body | suppress_temperature | reasoning_effort |
|------|------------|---------------------|-----------------|
| 百炼 Qwen / DeepSeek / 万擎 / Kimi | `{"enable_thinking": True}` | False | None |
| DeepSeek 官方（思考） | `{"thinking": {"type": "enabled"}}` | True | "high" |

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 并发策略：完全串行

所有 18 个配置按顺序依次执行。选择串行的原因：
1. 脚本为一次性分析工具，5-30 分钟可接受
2. 避免触发速率限制（尤其是 DeepSeek 官方 API）
3. 支持"即时打印当前结果"的观察体验

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## Prompt 构造

Prompt 内嵌于脚本，构成：
- **系统提示**（~1KB）：面试官助手角色 + 追问生成规则
- **面试历史**（~3.5KB）：8 轮真实风格的面试对话（技术面 CBCT 重建算法主题）
- **当前回答**（~0.5KB）：候选人对并行优化的详细解释
- **任务指令**（~0.3KB）：生成 3 条追问建议

总计约 5.3KB，接近真实追问场景。

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 输出设计

### 即时输出（每个模型完成后）
```
[  1/18] qwen3.7-plus          | TTFT:  1.2s | Total:  8.4s | 45 tok/s | OK
[  2/18] qwen3.7-plus+think    | TTFT:  2.1s | Total: 23.1s | 18 tok/s | OK
```

### 汇总表格（全部完成后，按 total_ms 升序）
```
┌─────────────────────────────┬─────────┬──────────┬──────────┬───────────┬──────────────────┐
│ Label                       │ Think   │ TTFT (s) │ Total(s) │ Tok/s     │ Status           │
├─────────────────────────────┼─────────┼──────────┼──────────┼───────────┼──────────────────┤
│ ds-v4-flash (百炼)          │ ✗       │     0.8  │     3.2  │    120.5  │ OK               │
│ qwen3.7-plus                │ ✗       │     1.2  │     8.4  │     45.1  │ OK               │
...
│ kimi-k2.6+think             │ ✓       │    None  │    None  │     None  │ Error: 400 ...   │
└─────────────────────────────┴─────────┴──────────┴──────────┴───────────┴──────────────────┘
```

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## CLI 参数

```
python scripts/benchmark_llm.py
  [--filter <keyword>]   # 只跑 label 包含关键字的配置（大小写不敏感）
  [--skip-thinking]      # 跳过所有思考模式变体
  [--runs N]             # 每个配置重复 N 次取平均（默认 1）
  [--output-csv]         # 输出 scripts/benchmark_results.csv
  [--timeout SEC]        # 单次超时（默认 60）
```

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 错误处理

- 任何 openai SDK 异常（超时、认证、网络、API 错误）均被捕获
- 错误记录到 `BenchmarkResult.error`（截断至 120 字符）
- 不中断后续测试
- 终端输出中错误行以红色显示

archived-with: 2026-06-09-benchmark-llm-latency
status: final
---

## 文件清单

| 文件 | 说明 |
|------|------|
| `scripts/benchmark_llm.py` | 主脚本（约 300 行） |
| `scripts/benchmark_results.csv` | 运行产物（纳入 .gitignore） |
