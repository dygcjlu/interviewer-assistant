"""E2E 层 conftest — 启动真实服务器（8001 端口），等待 /api/health 就绪。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

E2E_PORT = 8001
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"
_STARTUP_TIMEOUT_SEC = 60
_POLL_INTERVAL_SEC = 1.0

# E2E 测试用 PDF 简历：从系统 TEMP 目录获取，路径由环境变量 E2E_RESUME_PDF 指定
# 若未指定，在 conftest 中自动生成一个最小 PDF
_DEFAULT_RESUME_PATH = Path(os.environ.get("TEMP", "/tmp")) / "e2e_test_resume.pdf"


def _write_minimal_pdf(path: Path) -> None:
    """写入可被 PDF 解析器识别的最小结构 PDF。"""
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n"
        b"2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n"
        b"3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>>>>\nendobj\n"
        b"4 0 obj\n<</Length 44>>\nstream\nBT /F1 12 Tf 100 700 Td"
        b" (E2E Test Resume) Tj ET\nendstream\nendobj\n"
        b"5 0 obj\n<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f\n0000000009 00000 n\n"
        b"0000000058 00000 n\n0000000115 00000 n\n"
        b"0000000266 00000 n\n0000000360 00000 n\n"
        b"trailer\n<</Size 6 /Root 1 0 R>>\nstartxref\n441\n%%EOF\n"
    )
    path.write_bytes(content)


@pytest.fixture(scope="session")
def e2e_resume_pdf() -> Path:
    """返回 E2E 测试用的 PDF 文件路径（TEMP 目录下）。"""
    pdf_path = Path(os.environ.get("E2E_RESUME_PDF", str(_DEFAULT_RESUME_PATH)))
    if not pdf_path.exists():
        _write_minimal_pdf(pdf_path)
    return pdf_path


@pytest.fixture(scope="session")
def live_server():
    """
    启动真实服务器进程（端口 8001），等待 /api/health 返回 200。

    使用 .env 里配置的真实 API key（QWEN_API_KEY 等）。
    额外通过环境变量覆盖端口和 audio mock。
    """
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PORT"] = str(E2E_PORT)
    env["MOCK_AUDIO"] = "true"

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        cwd=str(project_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # 等待服务就绪
    deadline = time.time() + _STARTUP_TIMEOUT_SEC
    last_error = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{E2E_BASE}/api/health", timeout=3.0)
            if r.status_code == 200:
                break
        except Exception as exc:
            last_error = exc
        time.sleep(_POLL_INTERVAL_SEC)
    else:
        proc.terminate()
        proc.wait(timeout=5)
        raise RuntimeError(
            f"E2E 服务器在 {_STARTUP_TIMEOUT_SEC}s 内未就绪。"
            f"最后错误：{last_error}"
        )

    yield E2E_BASE

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
