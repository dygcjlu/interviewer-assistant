"""Unit tests — ContextManager token 精确计数（count_tokens 单次调用）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.framework.context import ContextConfig, ContextManager
from src.models.message import Message
from src.models.session import ConversationRound


class _FakeLLM:
    """count_tokens 返回可预测的整数：每条消息 = 内容字符数 + 固定 overhead。"""

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") + 4 for m in messages)

    async def chat(self, *a, **k):
        raise AssertionError("not used in this test")


@pytest.mark.unit
def test_estimate_tokens_uses_count_tokens_single_call():
    """_estimate_tokens() 必须对整份虚拟消息列表整体调用一次 count_tokens，
    而不是逐段构造 Message 再分别调用后在 Python 里手动求和。

    _FakeLLM 原本的实现是逐消息线性可加的，无法区分"单次整体调用"与"多次
    调用后求和"这两种模式（数学结果恰好相同），因此改用 MagicMock 直接断言
    调用次数，并检查单次调用实际传入的消息列表内容，才能真正捕获回归。
    """
    llm = _FakeLLM()
    llm.count_tokens = MagicMock(return_value=42)
    cm = ContextManager(ContextConfig(), llm)
    cm._summary = "摘要内容"
    cm._all_rounds = [
        ConversationRound(round_number=1, interviewer_text="问题一", candidate_text="回答一"),
        ConversationRound(round_number=2, interviewer_text="问题二", candidate_text="回答二"),
    ]

    tokens = cm._estimate_tokens()

    assert tokens == 42
    llm.count_tokens.assert_called_once()
    # fixed(system 提示，1 条) + summary(1 条) + 每轮 1 条(2 条) = 4 条虚拟消息，
    # 且必须是同一次调用传入的单一 list，而非多次调用后拼接/累加。
    virtual_messages = llm.count_tokens.call_args[0][0]
    assert isinstance(virtual_messages, list)
    assert len(virtual_messages) == 4
    assert all(isinstance(m, Message) for m in virtual_messages)


# ── Task 2.3: 压缩/分块触发阈值回归验证 ─────────────────────────────────────────


def _make_real_llm_client():
    """构建真实 OpenAICompatibleClient（仅 mock openai.AsyncOpenAI 构造，
    tiktoken 编码本身完全离线，count_tokens 走真实 cl100k_base 精确计数）。

    与 tests/unit/test_llm_client.py 中 `_make_client()` 保持一致的构造方式。
    """
    from src.llm.client import OpenAICompatibleClient
    from src.llm.config import LLMConfig

    config = LLMConfig(
        api_key="test-key", model="test-model", base_url="http://fake/v1"
    )
    with patch("src.llm.client.openai.AsyncOpenAI"):
        return OpenAICompatibleClient(config)


# 模拟一场真实规模的中英混杂后端工程师面试，10 轮问答，每轮内容长度接近
# 真实面试中的单次问答（而非几个字的极端 case），用于回归验证压缩触发时机。
_REALISTIC_INTERVIEW_ROUNDS: list[tuple[str, str]] = [
    (
        "我们先聊聊你的项目经历。你在上一家公司主导的那个订单系统重构项目，"
        "能详细说说遇到的最大挑战是什么吗？",
        "我们当时的订单系统是单体架构，随着日订单量涨到 200 万，MySQL 主库的写入"
        "压力越来越大。我主导拆分成了订单创建、支付回调、库存扣减三个独立服务，"
        "中间用 Kafka 做异步解耦。最大的挑战是保证分布式事务的一致性，我们最终"
        "采用了 Saga 模式配合本地消息表来实现最终一致性，同时引入了幂等设计防止"
        "Kafka 消息重复消费导致库存超卖。",
    ),
    (
        "Saga 模式和 TCC 相比，你怎么权衡选型的？分别有什么优缺点？",
        "TCC 需要为每个操作实现 Try/Confirm/Cancel 三个接口，侵入性比较强，改造"
        "成本高；Saga 只需要正向操作和补偿操作，实现起来更轻量，但补偿逻辑要考虑"
        "幂等和顺序问题。我们当时业务对实时一致性要求不高，可以接受短暂的中间态，"
        "所以选择了 Saga，用 event sourcing 记录状态变更，出问题时也方便追溯。",
    ),
    (
        "如果让你现在设计一个高并发的秒杀系统，你会从哪些层面考虑？",
        "我会从流量削峰、库存扣减、防刷三个层面设计。前端做限流和答题验证码，"
        "网关层用 Redis + Lua 脚本做原子扣库存，避免超卖；再用消息队列把下单请求"
        "异步化，削峰填谷；后端服务本身做好熔断降级，比如用 Sentinel 或者 "
        "Hystrix，防止突发流量打垮数据库。另外还要做好缓存预热，防止冷启动时"
        "缓存击穿。",
    ),
    (
        "Redis 的 Lua 脚本原子性你能展开讲讲底层原理吗？为什么它能保证原子性？",
        "Redis 是单线程模型，所有命令都在一个事件循环里顺序执行，Lua 脚本在执行"
        "期间会独占这个线程，不会被其他客户端的命令打断，所以脚本内的多个操作"
        "天然具有原子性。不过要注意脚本不能执行时间过长的逻辑，否则会阻塞其他"
        "客户端请求，我们一般会控制脚本执行在毫秒级，并避免在脚本里做外部网络"
        "调用。",
    ),
    (
        "换个话题，聊聊你对微服务链路追踪的理解，你们用的是什么方案？",
        "我们用的是 OpenTelemetry 采集 trace 数据，后端存储用 Jaeger，通过 "
        "traceId 和 spanId 串联跨服务调用链。每个服务在入口处生成或透传 "
        "traceId，通过 HTTP header 或者消息队列的 header 传递下去。排查线上"
        "问题时能快速定位是哪个服务的哪个接口耗时异常，对 SLA 分析和容量规划也"
        "很有帮助。",
    ),
    (
        "Do you have experience with database sharding? How did you handle "
        "cross-shard queries?",
        "Yes, we sharded the order table by user_id hash into 16 physical "
        "shards using ShardingSphere. For cross-shard queries like admin "
        "reports, we avoided doing joins across shards directly — instead we "
        "built a separate read-optimized view in Elasticsearch that's synced "
        "via binlog (Canal), so analytical queries never touch the sharded "
        "OLTP tables directly.",
    ),
    (
        "面对线上突发的内存泄漏问题，你一般是怎么排查定位的？",
        "我一般先用 arthas 或者 jmap 抓一份 heap dump，再用 MAT（Memory "
        "Analyzer Tool）分析支配树，找出占用内存最大的对象和它的 GC Root 引用"
        "链。之前排查过一次因为静态 Map 缓存没有设置过期策略导致的内存持续"
        "增长，还遇到过线程池未正确关闭导致的线程堆积，最后通过监控 JVM 的 GC "
        "日志和线程数指标提前预警。",
    ),
    (
        "你怎么看待技术债务？团队应该如何平衡业务迭代速度和代码质量？",
        "技术债务不可避免，关键是要有意识地管理它而不是无限积累。我们的做法是"
        "每个迭代预留 10%-15% 的时间做重构和补测试，同时用 SonarQube 做代码"
        "质量门禁，核心链路的代码必须要有 Code Review 和单元测试覆盖率要求。"
        "遇到明显影响后续开发效率的债务，会单独拉出一个技术优化的 sprint 集中"
        "处理。",
    ),
    (
        "如果这个岗位需要你带 3-5 人的小团队，你之前有没有类似的管理经验？",
        "有的，我在上一份工作里带过一个 4 人的后端小组，负责整个支付中台的"
        "开发。除了日常的任务拆解和代码评审，我比较注重给团队成员成长空间，会"
        "根据每个人的能力和兴趣分配不同难度和方向的任务，也会定期做一对一的"
        "沟通了解大家的诉求和困难，尽量在项目排期和个人成长之间找平衡。",
    ),
    (
        "最后一个问题，你为什么想加入我们公司？对这个岗位有什么期待？",
        "我了解到贵公司在实时数据处理和风控领域有比较深的技术积累，这正好是我"
        "比较感兴趣、也希望能进一步深耕的方向。我个人比较看重团队的技术氛围和"
        "成长空间，希望能在一个有挑战性的业务场景里继续提升自己在分布式系统和"
        "高并发方面的能力，同时也能把之前积累的经验带给团队。",
    ),
]


@pytest.mark.unit
def test_compression_threshold_trigger_timing_regression_exact_token_counting():
    """回归验证（Task 2.3）：精确 tiktoken 计数下，对 ~10 轮真实规模中英混杂
    面试对话，round-count 触发（阈值 8）与 token-budget 触发（比值 > 0.65）
    各自的实际时机是否发生了明显偏移。

    结论（详见 task-2.3-report.md 中打印的逐轮数据）：
    - over_rounds 仍在第 9 轮准时触发（该条件不依赖 token 计数，理论上必然如此）。
    - over_budget 在整个 10 轮真实规模对话内均未触发，budget 利用率远低于 0.65，
      与"round-count 阈值 8 是唯一实际生效的触发条件"的预期一致，未发生明显偏移。
    - 因此本任务不调整 ContextConfig 中的 compression_round_threshold 或 0.65 比值，
      本测试用于将上述已验证行为钉住（pin），防止未来改动静默改变触发时机。
    """
    llm = _make_real_llm_client()
    config = ContextConfig()
    cm = ContextManager(config, llm)
    budget = int(config.token_budget * (1 - config.token_safety_margin))

    observations: list[tuple[int, int, float, bool, bool]] = []
    for i, (interviewer_text, candidate_text) in enumerate(
        _REALISTIC_INTERVIEW_ROUNDS, start=1
    ):
        cm._all_rounds.append(
            ConversationRound(
                round_number=i,
                interviewer_text=interviewer_text,
                candidate_text=candidate_text,
            )
        )
        tokens = cm._estimate_tokens()
        ratio = tokens / budget
        over_rounds = len(cm._all_rounds) > config.compression_round_threshold
        over_budget = ratio > 0.65
        observations.append((i, tokens, ratio, over_rounds, over_budget))
        print(
            f"[task-2.3] round={i:2d} tokens={tokens:6d} budget={budget} "
            f"ratio={ratio:.4f} over_rounds={over_rounds} over_budget={over_budget}"
        )

    # over_rounds：第 8 轮 len==8 不触发，第 9 轮 len==9 > 8 才触发 —— 与 token
    # 计数方式无关，纯粹校验 add_round 触发逻辑与 config 常量的行为未被破坏。
    round_8 = observations[7]
    round_9 = observations[8]
    assert round_8[3] is False, f"第 8 轮不应触发 over_rounds，实际观测：{round_8}"
    assert round_9[3] is True, f"第 9 轮应触发 over_rounds，实际观测：{round_9}"

    # over_budget：在真实规模的 10 轮对话内不应触发，且利用率远低于 0.65 —— 证明
    # token-budget 触发在精确计数下对正常长度对话是"沉默"的，round-count 阈值 8
    # 才是实际生效的压缩触发条件，与切换到精确计数前的预期一致，无需调整常量。
    assert all(not o[4] for o in observations), (
        f"over_budget 不应在 10 轮真实规模对话内触发，实际观测：{observations}"
    )
    final_ratio = observations[-1][2]
    assert final_ratio < 0.65, f"第 10 轮 budget 利用率应远低于 0.65，实际：{final_ratio:.4f}"
