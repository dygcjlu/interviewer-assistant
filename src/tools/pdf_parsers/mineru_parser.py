"""MineruParser — 使用 MinerU Cloud API 解析 PDF。

支持两种模式，根据 MINERU_API_TOKEN 是否配置自动切换：
- Agent 轻量 API（无 Token，免费，IP 限频）
- 精准 API（需 Token，vlm 模型，更高精度）
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import Path

import httpx

from src.config import Settings, get_settings

from .base import BasePDFParser

logger = logging.getLogger(__name__)

_AGENT_BASE = "https://mineru.net/api/v1"
_PRECISE_BASE = "https://mineru.net/api/v4"

POLL_INTERVAL = 3
MAX_POLL_INTERVAL = 15
TIMEOUT = 300


class MineruParser(BasePDFParser):
    async def extract(self, file_path: str) -> str:
        settings = get_settings()
        if settings.MINERU_API_TOKEN:
            return await self._precise(file_path, settings)
        else:
            return await self._agent(file_path)

    # ------------------------------------------------------------------
    # Agent 轻量 API（无 Token）
    # ------------------------------------------------------------------

    async def _agent(self, file_path: str) -> str:
        file_name = Path(file_path).name
        async with httpx.AsyncClient(timeout=60) as client:
            # 1. 申请任务
            resp = await client.post(
                f"{_AGENT_BASE}/agent/parse/file",
                json={"file_name": file_name, "language": "ch"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            task_id = data["task_id"]
            file_url = data["file_url"]

            # L1-4: 同步 read 会阻塞事件循环（1-10MB PDF 几十到几百 ms），移到线程池
            content = await asyncio.to_thread(Path(file_path).read_bytes)
            put_resp = await client.put(file_url, content=content)
            put_resp.raise_for_status()
            logger.info(
                "MineruParser[agent]: uploaded %s, task_id=%s", file_name, task_id
            )

            # 3. 轮询直到完成
            result_data = await self._poll_agent(client, task_id)

            # 4. 下载 Markdown
            md_url = result_data["markdown_url"]
            md_resp = await client.get(md_url)
            md_resp.raise_for_status()
            return md_resp.text

    async def _poll_agent(self, client: httpx.AsyncClient, task_id: str) -> dict:
        interval = POLL_INTERVAL
        elapsed = 0
        while elapsed < TIMEOUT:
            await asyncio.sleep(interval)
            elapsed += interval
            resp = await client.get(f"{_AGENT_BASE}/agent/parse/{task_id}")
            resp.raise_for_status()
            data = resp.json().get("data", {})
            state = data.get("state", "")
            logger.debug("MineruParser[agent]: task %s state=%s", task_id, state)
            if state == "done":
                return data
            if state in ("failed", "error"):
                raise RuntimeError(f"MinerU Agent task {task_id} failed: {data}")
            interval = min(interval * 2, MAX_POLL_INTERVAL)
        raise TimeoutError(f"MinerU Agent task {task_id} timed out after {TIMEOUT}s")

    # ------------------------------------------------------------------
    # 精准 API（需 Token）
    # ------------------------------------------------------------------

    async def _precise(self, file_path: str, settings: Settings) -> str:
        file_name = Path(file_path).name
        headers = {"Authorization": f"Bearer {settings.MINERU_API_TOKEN}"}

        async with httpx.AsyncClient(timeout=60) as client:
            # 1. 申请批次
            resp = await client.post(
                f"{_PRECISE_BASE}/file-urls/batch",
                headers=headers,
                json={
                    "files": [{"name": file_name}],
                    "model_version": settings.MINERU_MODEL_VERSION,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            batch_id = data["batch_id"]
            upload_url = data["files"][0]["url"]

            # L1-4: 同上，移到线程池避免阻塞事件循环
            content = await asyncio.to_thread(Path(file_path).read_bytes)
            put_resp = await client.put(upload_url, content=content)
            put_resp.raise_for_status()
            logger.info(
                "MineruParser[precise]: uploaded %s, batch_id=%s", file_name, batch_id
            )

            # 3. 轮询
            result_data = await self._poll_precise(client, headers, batch_id)

            # 4. 下载 zip，内存解压读取 full.md
            zip_url = result_data["full_zip_url"]
            zip_resp = await client.get(zip_url)
            zip_resp.raise_for_status()
            return self._extract_md_from_zip(zip_resp.content)

    async def _poll_precise(
        self, client: httpx.AsyncClient, headers: dict, batch_id: str
    ) -> dict:
        interval = POLL_INTERVAL
        elapsed = 0
        while elapsed < TIMEOUT:
            await asyncio.sleep(interval)
            elapsed += interval
            resp = await client.get(
                f"{_PRECISE_BASE}/extract-results/batch/{batch_id}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            files = data.get("files", [])
            logger.debug("MineruParser[precise]: batch %s files=%s", batch_id, files)
            if files and all(f.get("state") == "done" for f in files):
                return data
            if any(f.get("state") in ("failed", "error") for f in files):
                raise RuntimeError(f"MinerU precise batch {batch_id} failed: {data}")
            interval = min(interval * 2, MAX_POLL_INTERVAL)
        raise TimeoutError(
            f"MinerU precise batch {batch_id} timed out after {TIMEOUT}s"
        )

    @staticmethod
    def _extract_md_from_zip(zip_bytes: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            # 优先 full.md，降级到任意 .md 文件
            target = (
                "full.md"
                if "full.md" in names
                else next((n for n in names if n.endswith(".md")), None)
            )
            if target is None:
                raise FileNotFoundError(
                    f"No .md file found in MinerU zip. Files: {names}"
                )
            return zf.read(target).decode("utf-8")
