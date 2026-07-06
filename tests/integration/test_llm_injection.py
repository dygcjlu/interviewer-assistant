"""测试 LLM 客户端依赖注入是否正确生效。

RED phase: 这些测试应该失败，因为当前代码直接实例化 OpenAICompatibleClient，
绕过了 app.state.llm_client 注入，导致 mock 无法生效。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.llm.protocol import ChatResponse
from src.models.candidate import CandidateProfile


@pytest.mark.asyncio
async def test_check_question_coverage_uses_injected_llm(client, mock_llm, tmp_path):
    """测试 check_question_coverage 使用注入的 llm_client 而非直接实例化。

    RED: 当前会失败，因为 routes.py line 572 直接实例化 OpenAICompatibleClient
    """
    # 准备数据
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id="test-001", name="张三")
    await memory.save_candidate(candidate, "# 张三简历\n\nPython 开发")

    # 保存问题清单
    questions = [
        {
            "id": "q1",
            "question": "介绍项目经验",
            "focus": "项目",
            "covered": False,
            "covered_by": "",
        }
    ]
    memory.save_questions("test-001", questions)

    # 创建会话并添加对话轮次
    controller = client._transport.app.state.controller
    await controller.create_session("test-001")
    await controller.start_interview()
    session = await controller.get_session()

    from datetime import datetime

    from src.models.session import ConversationRound

    session.rounds.append(
        ConversationRound(
            round_number=1,
            interviewer_text="介绍一下你的项目经验",
            candidate_text="我做过电商系统",
            timestamp=datetime.now(),
        )
    )

    # 预设 mock 响应：LLM 判定 q1 已覆盖
    mock_llm.push_chat(
        ChatResponse(content='["q1"]', prompt_tokens=50, completion_tokens=10)
    )

    # 调用 API
    resp = await client.post(
        "/api/interview/questions/check-coverage",
        json={
            "candidate_id": "test-001",
            "round_text": "面试官：介绍一下你的项目经验\n候选人：我做过电商系统",
        },
    )

    assert resp.status_code == 200
    data = resp.json()

    # 如果使用了注入的 mock_llm，updated 应该包含 q1
    # 如果绕过注入直接实例化，会因为没有真实 API key 而失败或返回空
    assert "q1" in data["updated"], "mock_llm 应该被调用，q1 应被标记为已覆盖"


@pytest.mark.asyncio
async def test_compare_candidates_uses_injected_llm(client, mock_llm, tmp_path):
    """测试 compare_candidates 使用注入的 llm_client 而非直接实例化。

    RED: 当前会失败，因为 routes.py line 711 直接实例化 OpenAICompatibleClient
    """
    # 准备两名候选人及其评价报告文件（绕过 save_eval_report，直接写文件）
    memory = client._transport.app.state.memory_module
    c1 = CandidateProfile(id="c1", name="候选人A")
    c2 = CandidateProfile(id="c2", name="候选人B")
    await memory.save_candidate(c1, "# A 简历")
    await memory.save_candidate(c2, "# B 简历")

    # 手动创建评价报告目录结构
    import yaml

    c1_dir = Path(tmp_path) / "candidates" / "c1" / "interviews" / "int-1"
    c2_dir = Path(tmp_path) / "candidates" / "c2" / "interviews" / "int-2"
    c1_dir.mkdir(parents=True)
    c2_dir.mkdir(parents=True)

    report1_data = {
        "id": "rep-1",
        "interview_id": "int-1",
        "candidate_id": "c1",
        "overall_score": 85,
        "dimensions": [
            {"dimension": "技术能力", "score": 90, "comment": "算法强", "evidence": []}
        ],
        "strengths": ["算法强"],
        "weaknesses": ["沟通弱"],
        "recommendation": "推荐",
        "summary": "优秀候选人",
        "generated_at": "2024-01-01T00:00:00",
    }
    report2_data = {
        "id": "rep-2",
        "interview_id": "int-2",
        "candidate_id": "c2",
        "overall_score": 75,
        "dimensions": [
            {"dimension": "技术能力", "score": 70, "comment": "经验少", "evidence": []}
        ],
        "strengths": ["态度好"],
        "weaknesses": ["经验少"],
        "recommendation": "观察",
        "summary": "待观察候选人",
        "generated_at": "2024-01-01T00:00:00",
    }

    (c1_dir / "eval_report.md").write_text(
        f"---\n{yaml.dump(report1_data, allow_unicode=True)}---\n\n优秀候选人",
        encoding="utf-8",
    )
    (c2_dir / "eval_report.md").write_text(
        f"---\n{yaml.dump(report2_data, allow_unicode=True)}---\n\n待观察候选人",
        encoding="utf-8",
    )

    # 预设 mock 响应
    mock_summary = "候选人A综合能力更强，推荐优先录用。"
    mock_llm.push_chat(
        ChatResponse(content=mock_summary, prompt_tokens=100, completion_tokens=20)
    )

    # 调用 API (GET with query params)
    resp = await client.get("/api/candidates/compare", params={"ids": "c1,c2"})

    assert resp.status_code == 200
    data = resp.json()

    # 如果使用了注入的 mock_llm，llm_summary 应该是预设内容
    assert data["llm_summary"] == mock_summary, "mock_llm 应该被调用并返回预设摘要"


@pytest.mark.asyncio
async def test_dispatch_generate_questions_uses_injected_llm(
    client, mock_llm, tmp_path
):
    """测试 dispatch_to_agent._generate_questions_from_brief 使用注入的 llm_client。

    RED: 当前会失败，因为 dispatch_to_agent.py line 191 直接实例化 OpenAICompatibleClient
    """
    import asyncio

    # 准备候选人和会话
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id="test-002", name="李四")
    await memory.save_candidate(candidate, "# 李四简历\n\nJava 开发")

    controller = client._transport.app.state.controller
    await controller.create_session("test-002")

    # 预设 mock 响应：返回结构化问题
    questions_json = json.dumps(
        [
            {"question": "介绍 Java 项目", "focus": "项目经验"},
            {"question": "并发编程经验", "focus": "技术深度"},
        ]
    )
    mock_llm.push_chat(
        ChatResponse(content=questions_json, prompt_tokens=80, completion_tokens=30)
    )

    # 手动触发 _generate_questions_from_brief
    from src.tools.dispatch_to_agent import _generate_questions_from_brief

    brief_text = "候选人 Java 背景，需考察项目经验和并发编程能力"
    await _generate_questions_from_brief("test-002", brief_text)

    # 等待异步任务完成
    await asyncio.sleep(0.1)

    # 检查问题是否保存
    questions = memory.get_questions("test-002")

    # 如果使用了注入的 mock_llm，应该有 2 个问题
    assert len(questions) == 2, "mock_llm 应该被调用，生成 2 个问题"
    assert any("Java" in q["question"] for q in questions), "问题内容应来自 mock 响应"
