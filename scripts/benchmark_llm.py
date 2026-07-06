#!/usr/bin/env python3
"""LLM 延迟基准测试脚本。

对多个提供商、模型、思考模式配置执行流式调用，对比 TTFT / 总延迟 / tokens-per-sec。

用法：
    python scripts/benchmark_llm.py [--filter KEYWORD] [--skip-thinking]
                                    [--runs N] [--output-csv] [--timeout SEC]

环境变量：
    LLM_API_KEY       百炼 DashScope API Key（所有百炼模型共用）
    DEEPSEEK_API_KEY  DeepSeek 官方 API Key（api.deepseek.com）
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import time
from dataclasses import dataclass
from typing import Any

import openai
from dotenv import load_dotenv

load_dotenv()

try:
    from rich import box as rich_box
    from rich.console import Console
    from rich.table import Table

    _RICH = True
except ImportError:
    _RICH = False

_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEEPSEEK_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class ThinkingConfig:
    """单个思考模式的请求参数配置。"""

    extra_body: dict | None = None
    suppress_temperature: bool = False
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ModelConfig:
    """单个被测模型的完整配置。"""

    label: str
    model: str
    base_url: str
    api_key_env: str
    no_think: ThinkingConfig
    with_think: ThinkingConfig | None


@dataclass
class BenchmarkResult:
    """单次测试结果。"""

    label: str
    thinking: bool
    ttft_ms: float | None = None
    total_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_sec: float | None = None
    error: str | None = None


# 非思考模式的默认参数（所有模型共用）
NO_THINK = ThinkingConfig(
    extra_body=None, suppress_temperature=False, reasoning_effort=None
)

# 百炼 enable_thinking 格式（Qwen / 百炼 DeepSeek / 万擎 / Kimi）
_BAILIAN_THINK = ThinkingConfig(
    extra_body={"enable_thinking": True},
    suppress_temperature=False,
    reasoning_effort=None,
)

# DeepSeek 官方思考模式格式
_DS_OFFICIAL_THINK = ThinkingConfig(
    extra_body={"thinking": {"type": "enabled"}},
    suppress_temperature=True,
    reasoning_effort="high",
)


MODEL_CONFIGS: list[ModelConfig] = [
    # ── 百炼 Qwen 官方 ─────────────────────────────────────────────────────────
    ModelConfig(
        "qwen3.7-plus",
        "qwen3.7-plus",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "qwen3.7-plus+think",
        "qwen3.7-plus",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "qwen3.7-max",
        "qwen3.7-max",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "qwen3.7-max+think",
        "qwen3.7-max",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    # ── 百炼 DeepSeek 自营 ────────────────────────────────────────────────────
    ModelConfig(
        "ds-v4-pro (百炼)",
        "deepseek-v4-pro",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "ds-v4-pro+think (百炼)",
        "deepseek-v4-pro",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "ds-v4-flash (百炼)",
        "deepseek-v4-flash",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "ds-v4-flash+think (百炼)",
        "deepseek-v4-flash",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    # ── 百炼 万擎 DeepSeek ───────────────────────────────────────────────────
    ModelConfig(
        "vanchin-v4-pro",
        "vanchin/deepseek-v4-pro",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "vanchin-v4-pro+think",
        "vanchin/deepseek-v4-pro",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    # ── 百炼 Kimi ────────────────────────────────────────────────────────────
    ModelConfig(
        "kimi-k2.6",
        "kimi/kimi-k2.6",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    ModelConfig(
        "kimi-k2.6+think",
        "kimi/kimi-k2.6",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        _BAILIAN_THINK,
    ),
    # ── 百炼 MiniMax ─────────────────────────────────────────────────────────
    ModelConfig(
        "minimax-m3",
        "MiniMax/MiniMax-M3",
        _DASHSCOPE_URL,
        "LLM_API_KEY",
        NO_THINK,
        None,
    ),
    # ── 百炼 GLM ─────────────────────────────────────────────────────────────
    ModelConfig(
        "glm-5.1", "ZHIPU/GLM-5.1", _DASHSCOPE_URL, "LLM_API_KEY", NO_THINK, None
    ),
    # ── DeepSeek 官方 ────────────────────────────────────────────────────────
    ModelConfig(
        "ds-v4-pro (官方)",
        "deepseek-v4-pro",
        _DEEPSEEK_URL,
        "DEEPSEEK_API_KEY",
        NO_THINK,
        _DS_OFFICIAL_THINK,
    ),
    ModelConfig(
        "ds-v4-pro+think (官方)",
        "deepseek-v4-pro",
        _DEEPSEEK_URL,
        "DEEPSEEK_API_KEY",
        NO_THINK,
        _DS_OFFICIAL_THINK,
    ),
    ModelConfig(
        "ds-v4-flash (官方)",
        "deepseek-v4-flash",
        _DEEPSEEK_URL,
        "DEEPSEEK_API_KEY",
        NO_THINK,
        _DS_OFFICIAL_THINK,
    ),
    ModelConfig(
        "ds-v4-flash+think (官方)",
        "deepseek-v4-flash",
        _DEEPSEEK_URL,
        "DEEPSEEK_API_KEY",
        NO_THINK,
        _DS_OFFICIAL_THINK,
    ),
]


def build_request_kwargs(
    config: ModelConfig,
    thinking: ThinkingConfig,
    temperature: float,
    timeout: float,
) -> dict[str, Any]:
    """根据 ModelConfig 和 ThinkingConfig 构建 openai SDK 请求参数（不含 messages）。"""
    kwargs: dict[str, Any] = {
        "model": config.model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
    }
    if not thinking.suppress_temperature:
        kwargs["temperature"] = temperature
    if thinking.reasoning_effort:
        kwargs["reasoning_effort"] = thinking.reasoning_effort
    if thinking.extra_body:
        kwargs["extra_body"] = thinking.extra_body
    return kwargs


def filter_configs(
    configs: list[ModelConfig],
    filter_str: str | None,
    skip_thinking: bool,
) -> list[ModelConfig]:
    """按过滤条件筛选要运行的配置列表。"""
    result = list(configs)
    if filter_str:
        result = [c for c in result if filter_str.lower() in c.label.lower()]
    if skip_thinking:
        result = [c for c in result if "+think" not in c.label]
    return result


BENCHMARK_PROMPT = """\
[系统提示]
你是一名专业技术面试官助手。当前正在进行一场技术面试，候选人正在介绍其在医疗影像设备（CBCT 三维重建）领域的工程经验。

你的任务是：根据候选人刚刚的回答，生成 3 条高质量的追问建议，帮助面试官深入挖掘候选人的技术深度和问题解决能力。

每条追问建议需要：
1. 针对候选人回答中的具体技术点或潜在知识盲点
2. 能区分候选人是否真正理解底层原理（而非只会套用框架）
3. 简洁明了，一句话问题

格式要求：
- 直接列出 3 条追问，不需要其他解释
- 每条以数字序号开头

[面试历史]

面试官：请介绍一下你在 CBCT 三维重建项目中负责的核心工作。

候选人：我主要负责图像重建流水线的性能优化。我们用的是 FDK 算法做三维重建，原始实现大概要 3 秒，我把它优化到了 1 秒以内。优化的思路主要是两块：一是把反投影计算从 CPU 移到 GPU，用 CUDA 写了一个自定义 kernel；二是把图像预处理（滤波、去噪）和重建计算流水线化，减少等待时间。

面试官：你提到用了 CUDA，那你们的重建 kernel 是怎么组织线程的？

候选人：每个线程处理重建体积里的一个 voxel，线程块用的 8×8×8 的三维结构。主要挑战是内存访问模式，投影数据的随机访问非常多，我们用了 texture memory 来缓存投影图，利用 GPU 的缓存机制把随机访问的延迟降下来。另外对 voxel 的遍历顺序做了调整，尽量让相邻线程访问连续的内存地址，提升 coalesced memory access 的比例。

面试官：图像采集的帧率是多少？整个流水线的实时性是怎么保证的？

候选人：我们的 flat panel detector 采集帧率是 30fps，也就是每 33 毫秒一帧。原来的处理流水线是串行的：采集完一帧，预处理，写缓冲区，等下一帧。我把它改成了并行的：采集第 N 帧的时候，GPU 在处理第 N-1 帧。原来每帧处理时间 20ms，串行时总时间是 33+20=53ms，并行之后 GPU 处理被隐藏在采集时间里，实际延迟降到了 33ms，相当于吞吐量提升了接近 40%。

面试官：你提到三维重建用了 FDK 算法，能说一下它的基本原理吗？

候选人：FDK 算法是基于锥束 CT 的经典重建算法。基本流程是三步：首先对每一张投影图做加权滤波，这个滤波核是在频率域做的，类似于二维傅里叶变换的斜坡滤波，目的是补偿锥束扫描带来的伪影。然后是反投影，就是把滤波后的投影图投射回三维体积里，每个 voxel 从所有角度的投影里累加贡献值。最后做归一化。

面试官：你提到滤波核类似傅里叶变换，能详细说说这个滤波在空间域的实现吗？

候选人：嗯，主要是对投影图的每一行做一维滤波，用的是 Ram-Lak 滤波核，这个核在频率域是线性增长的，也就是|ω|函数。具体实现的时候用 FFT 来做卷积：先把投影行用 FFT 变换到频率域，然后乘以频率滤波核，再做 IFFT 变换回来。这样时间复杂度是 O(N log N)，比直接卷积的 O(N²) 快很多。

面试官：在投影数据的物理意义上，为什么反投影之前一定要做这个滤波？

候选人：这是因为如果直接做反投影（backprojection），重建出来的图像会有一个低频偏差，边缘会模糊，中间会过亮，这是 backprojection 算法本身的局限性。原因是从多角度累加投影时，低频信息被重复叠加了，高频细节（边缘）的贡献相对被淡化了。Ram-Lak 滤波核的作用就是在频率域做一个预校正，把低频成分压制，高频成分增强，让反投影之后的结果接近真实的 CT 值分布。从数学上说，这对应的是中心切片定理（Fourier Slice Theorem）在锥束 CT 场景的推广。

面试官：你们在整个系统中是如何做内存管理的？GPU 显存够用吗？

候选人：显存是个挑战。我们的体积大小通常是 512×512×256 的 float32，大概 256MB。投影数据如果一次性全加载是 720 张 1024×768 的图，大概 2GB，显然超了。我们做了分块处理：每次只把一组角度范围内的投影加载到显存，做完这批反投影之后再换下一批。同时用 CUDA stream 做异步传输，GPU 在做当前批次的反投影时，CPU 在预取下一批投影数据，这样 PCI-E 带宽和 GPU 计算的时间是重叠的。

面试官：嗯，能总结一下这个项目让你最有成就感的技术突破点吗？

候选人：最有成就感的是把 CBCT 重建时间从 3 秒降到 0.8 秒。核心突破点有两个：一是把反投影从 CPU 迁移到 GPU 并做了 texture memory 优化，这一步占了大部分的加速；二是整个流水线的并行化设计，包括采集-处理并行、CUDA stream 的异步数据传输，让各个硬件资源的利用率都接近满载。整体来说，从 3 秒降到 0.8 秒，性能提升接近 4 倍，并且在不增加任何硬件的前提下实现的。

[当前候选人回答]

面试官：刚才你提到用了傅里叶变换做滤波，但反投影本质上是对投影数据做积分，这两个操作在数学上是什么关系？Ram-Lak 核的设计依据是什么？

候选人：嗯……这个问题挺深的。傅里叶变换和反投影的联系，我理解是中心切片定理，就是说一个三维物体在某个角度的二维投影，做傅里叶变换之后，得到的结果对应这个物体三维傅里叶变换在该角度截面上的值。所以如果我们从所有角度采集投影并做傅里叶变换，理论上可以填充三维频率空间，然后做三维逆傅里叶变换就能还原物体。但实际上这样做有个问题，就是三维频率空间的采样不均匀，低频区域被过度采样了，所以需要用 Ram-Lak 核来做加权校正，压制过度采样的低频。这和我刚才说的频率域里|ω|函数是一致的。

[任务]
根据候选人最后这段回答，生成 3 条追问建议，帮助面试官进一步验证候选人对 CT 重建理论的真实掌握深度。
"""


async def run_streaming_benchmark(
    config: ModelConfig,
    prompt: str,
    timeout: float = 90.0,
    temperature: float = 0.1,
) -> BenchmarkResult:
    """对单个 ModelConfig 执行流式调用，返回延迟指标。"""
    use_thinking = "+think" in config.label
    thinking_cfg = config.with_think if use_thinking else config.no_think

    if use_thinking and config.with_think is None:
        return BenchmarkResult(
            label=config.label,
            thinking=True,
            error="thinking mode not configured for this model",
        )

    api_key = os.environ.get(config.api_key_env, "")
    if not api_key:
        return BenchmarkResult(
            label=config.label,
            thinking=use_thinking,
            error=f"missing env var: {config.api_key_env}",
        )

    client = openai.AsyncOpenAI(api_key=api_key, base_url=config.base_url)
    kwargs = build_request_kwargs(
        config, thinking_cfg, temperature=temperature, timeout=timeout
    )
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    kwargs["messages"] = [
        {"role": "system", "content": f"[timestamp: {ts}]"},
        {"role": "user", "content": prompt},
    ]

    start = time.perf_counter()
    ttft: float | None = None
    prompt_tokens = completion_tokens = 0

    try:
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens or 0
                completion_tokens = chunk.usage.completion_tokens or 0
            if not chunk.choices:
                continue
            delta_content = chunk.choices[0].delta.content or ""
            if delta_content and ttft is None:
                ttft = (time.perf_counter() - start) * 1000

        total_ms = (time.perf_counter() - start) * 1000
        tps = (
            (completion_tokens / total_ms * 1000)
            if total_ms > 0 and completion_tokens
            else None
        )

        return BenchmarkResult(
            label=config.label,
            thinking=use_thinking,
            ttft_ms=ttft,
            total_ms=total_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_sec=tps,
        )

    except Exception as exc:
        return BenchmarkResult(
            label=config.label,
            thinking=use_thinking,
            error=str(exc)[:120],
        )


def print_live_result(idx: int, total: int, result: BenchmarkResult) -> None:
    """即时打印单条测试结果（ASCII-safe，兼容 Windows 控制台）。"""
    think_mark = "Y" if result.thinking else "N"
    if result.error:
        status = f"ERROR: {result.error[:60]}"
        line = f"[{idx:3d}/{total}] {result.label:<32} | think={think_mark} | {status}"
    else:
        ttft_s = (
            f"{result.ttft_ms / 1000:.2f}s" if result.ttft_ms is not None else "  N/A "
        )
        total_s = (
            f"{result.total_ms / 1000:.2f}s"
            if result.total_ms is not None
            else "  N/A "
        )
        tps = (
            f"{result.tokens_per_sec:.1f}"
            if result.tokens_per_sec is not None
            else "N/A"
        )
        line = (
            f"[{idx:3d}/{total}] {result.label:<32} | think={think_mark} "
            f"| TTFT={ttft_s:>7} | Total={total_s:>8} | {tps:>7} tok/s | OK"
        )
    print(line, flush=True)


def print_summary_table(results: list[BenchmarkResult]) -> None:
    """打印按总延迟排序的汇总表格。"""
    sorted_results = sorted(
        results,
        key=lambda r: (
            r.error is not None,
            r.total_ms if r.total_ms is not None else float("inf"),
        ),
    )

    if _RICH:
        console = Console()
        table = Table(
            box=rich_box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
            title="LLM Latency Benchmark",
        )
        table.add_column("Label", style="white", min_width=32)
        table.add_column("Think", justify="center", width=6)
        table.add_column("TTFT (s)", justify="right", width=9)
        table.add_column("Total (s)", justify="right", width=10)
        table.add_column("Completion", justify="right", width=11)
        table.add_column("Tok/s", justify="right", width=8)
        table.add_column("Status", min_width=20)

        for r in sorted_results:
            think = "✓" if r.thinking else "✗"
            ttft = f"{r.ttft_ms / 1000:.2f}" if r.ttft_ms is not None else "-"
            total = f"{r.total_ms / 1000:.2f}" if r.total_ms is not None else "-"
            compl = str(r.completion_tokens) if r.completion_tokens else "-"
            tps = f"{r.tokens_per_sec:.1f}" if r.tokens_per_sec is not None else "-"
            status_str = "OK" if not r.error else f"ERR: {r.error[:25]}"
            row_style = "red" if r.error else ""
            table.add_row(
                r.label, think, ttft, total, compl, tps, status_str, style=row_style
            )

        console.print()
        console.print(table)
    else:
        hdr = f"{'Label':<32} {'Think':>6} {'TTFT(s)':>9} {'Total(s)':>10} {'Tok/s':>8}  Status"
        sep = "-" * len(hdr)
        print(f"\n{'LLM Latency Benchmark':^{len(hdr)}}")
        print(sep)
        print(hdr)
        print(sep)
        for r in sorted_results:
            think = "Y" if r.thinking else "N"
            ttft = f"{r.ttft_ms / 1000:.2f}" if r.ttft_ms is not None else "     -"
            total = f"{r.total_ms / 1000:.2f}" if r.total_ms is not None else "     -"
            tps = (
                f"{r.tokens_per_sec:.1f}" if r.tokens_per_sec is not None else "     -"
            )
            status_str = "OK" if not r.error else f"ERR: {r.error[:30]}"
            print(
                f"{r.label:<32} {think:>6} {ttft:>9} {total:>10} {tps:>8}  {status_str}"
            )


def write_csv(results: list[BenchmarkResult], path: str) -> None:
    """将结果写入 CSV 文件。"""
    fieldnames = [
        "label",
        "thinking",
        "ttft_ms",
        "total_ms",
        "prompt_tokens",
        "completion_tokens",
        "tokens_per_sec",
        "error",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "label": r.label,
                    "thinking": r.thinking,
                    "ttft_ms": f"{r.ttft_ms:.1f}" if r.ttft_ms is not None else "",
                    "total_ms": f"{r.total_ms:.1f}" if r.total_ms is not None else "",
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "tokens_per_sec": (
                        f"{r.tokens_per_sec:.1f}"
                        if r.tokens_per_sec is not None
                        else ""
                    ),
                    "error": r.error or "",
                }
            )
    print(f"\nCSV written to: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM 延迟基准测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "环境变量：\n"
            "  LLM_API_KEY       百炼 DashScope API Key\n"
            "  DEEPSEEK_API_KEY  DeepSeek 官方 API Key\n"
        ),
    )
    parser.add_argument(
        "--filter",
        metavar="KEYWORD",
        help="只跑 label 包含关键字的配置（大小写不敏感）",
    )
    parser.add_argument(
        "--skip-thinking", action="store_true", help="跳过所有 +think 变体"
    )
    parser.add_argument(
        "--runs", type=int, default=1, metavar="N", help="每个配置重复 N 次（默认 1）"
    )
    parser.add_argument(
        "--output-csv", action="store_true", help="输出 scripts/benchmark_results.csv"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        metavar="SEC",
        help="单次调用超时秒数（默认 90）",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.1, help="LLM temperature（默认 0.1）"
    )
    return parser.parse_args()


async def _run_all(
    configs: list[ModelConfig],
    prompt: str,
    runs: int,
    timeout: float,
    temperature: float,
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    total = len(configs) * runs
    run_idx = 0
    for run_n in range(runs):
        if runs > 1:
            print(f"\n=== Run {run_n + 1}/{runs} ===")
        for cfg in configs:
            run_idx += 1
            result = await run_streaming_benchmark(
                cfg, prompt, timeout=timeout, temperature=temperature
            )
            print_live_result(run_idx, total, result)
            results.append(result)
    return results


def main() -> None:
    args = parse_args()
    configs = filter_configs(
        MODEL_CONFIGS, filter_str=args.filter, skip_thinking=args.skip_thinking
    )

    if not configs:
        print("No configs match the given filter. Exiting.")
        return

    prompt_size = len(BENCHMARK_PROMPT.encode("utf-8"))
    print("\nLLM Latency Benchmark")
    print(
        f"Configs: {len(configs)} x {args.runs} runs = {len(configs) * args.runs} calls"
    )
    print(
        f"Prompt size: {prompt_size:,} bytes | Timeout: {args.timeout}s | Temperature: {args.temperature}"
    )
    print("-" * 65)

    results = asyncio.run(
        _run_all(configs, BENCHMARK_PROMPT, args.runs, args.timeout, args.temperature)
    )

    print_summary_table(results)

    if args.output_csv:
        csv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "benchmark_results.csv"
        )
        write_csv(results, csv_path)


if __name__ == "__main__":
    main()
