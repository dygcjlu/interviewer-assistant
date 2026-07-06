"""顶层 conftest — 注册 pytest 标记，提供跨层共享 fixture。"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: 纯逻辑单元测试，无 I/O")
    config.addinivalue_line("markers", "integration: API 契约集成测试，Mock LLM")
    config.addinivalue_line("markers", "e2e: 端到端测试，真实服务器 + 真实 LLM")
    config.addinivalue_line(
        "markers",
        "windows_only: 依赖 Windows WASAPI/pyaudiowpatch，只在 windows runner 运行",
    )


@pytest.fixture
def sample_pdf(tmp_path):
    """生成最小合法 PDF 文件（1 字节 header trick 不够，用 reportlab 或写入最小结构）。"""
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n"
        b"2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n"
        b"3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
        b"0000000058 00000 n\n0000000115 00000 n\n"
        b"trailer\n<</Size 4 /Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
    )
    pdf_file = tmp_path / "张三.pdf"
    pdf_file.write_bytes(pdf_content)
    return pdf_file
