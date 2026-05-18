# 修改方案：P1 实时音频与 STT + P2 候选人选择器 UI

**日期：** 2026-05-18  
**基于：** `docs/gap-analysis.md` 缺口分析  
**涉及问题：** M1（WASAPI 采集）、M2（百度实时 STT）、M3（候选人选择器 UI）

---

## P1 — 实时音频与 STT

### 背景与现状

| 文件 | 现状 |
|---|---|
| `src/audio/wasapi.py` | 直接 re-export `MockAudioCapturer`，无实际采集 |
| `src/audio/baidu_stt.py` | 继承 `MockSTTEngine`，`connect/send_audio/receive` 全为空操作 |
| `src/audio/mock.py` | 每 20ms 产出 320 字节静音帧；STT 永不产出片段 |
| `src/main.py:124-134` | Windows 下导入上述两个 stub，实际行为与非 Windows Mock 完全一致 |

架构链路完整（`AudioManager → AudioStreamBridge → STTEngine → TranscriptionManager → WS`），只需替换两个叶子实现即可接通全链路。

### 依赖

```
sounddevice>=0.4.6        # WASAPI loopback/mic 采集（含预编译二进制）
websockets>=12.0          # 已在项目中使用，百度 ASR WS 复用
```

百度 API 配置（添加到 `.env`）：

```
BAIDU_APP_ID=
BAIDU_API_KEY=
BAIDU_SECRET_KEY=
```

### 子任务 1：实现 `WasapiCapturer`

**目标文件：** `src/audio/wasapi.py`

完整替换当前内容：

```python
"""真实 WASAPI 音频采集器（Windows-only）。

使用 sounddevice 库：
  - loopback 设备 → 采集扬声器输出（候选人声音）→ source="candidate"
  - 默认麦克风    → 采集面试官声音         → source="interviewer"

两路音频帧均推入 AudioStreamBridge。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import sounddevice as sd

from .protocol import AudioFrame

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_DTYPE = "int16"
_BLOCKSIZE = 320          # 20ms @ 16kHz × 1ch × 2 bytes = 320 bytes


class WasapiCapturer:
    """双声道 WASAPI 音频采集器。

    - 候选人（loopback）：采集扬声器回放，source="candidate"
    - 面试官（mic）：采集麦克风输入，source="interviewer"
    """

    def __init__(self) -> None:
        self._callback: Callable[[AudioFrame], None] | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loopback_stream: sd.RawInputStream | None = None
        self._mic_stream: sd.RawInputStream | None = None

    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None:
        self._callback = callback

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True

        loopback_device = self._find_loopback_device()
        mic_device = self._find_mic_device()

        self._loopback_stream = sd.RawInputStream(
            samplerate=_SAMPLE_RATE,
            blocksize=_BLOCKSIZE,
            device=loopback_device,
            channels=_CHANNELS,
            dtype=_DTYPE,
            callback=self._make_sd_callback("candidate"),
        )
        self._mic_stream = sd.RawInputStream(
            samplerate=_SAMPLE_RATE,
            blocksize=_BLOCKSIZE,
            device=mic_device,
            channels=_CHANNELS,
            dtype=_DTYPE,
            callback=self._make_sd_callback("interviewer"),
        )

        self._loopback_stream.start()
        self._mic_stream.start()
        logger.info(
            "WasapiCapturer: started loopback_device=%s mic_device=%s",
            loopback_device,
            mic_device,
        )

    async def stop(self) -> None:
        self._running = False
        for stream in (self._loopback_stream, self._mic_stream):
            if stream is not None:
                stream.stop()
                stream.close()
        self._loopback_stream = None
        self._mic_stream = None
        logger.info("WasapiCapturer: stopped")

    # ── internals ──────────────────────────────────────────────────────────

    def _make_sd_callback(self, source: str):
        """返回 sounddevice 回调，在采集线程调用，通过 run_coroutine_threadsafe 传回主 loop。"""
        def _cb(indata: bytes, frames: int, time_info, status) -> None:
            if status:
                logger.debug("WasapiCapturer [%s] status: %s", source, status)
            if not self._running or self._callback is None:
                return
            frame = AudioFrame(
                data=bytes(indata),
                source=source,
                timestamp=time.monotonic(),
            )
            self._callback(frame)          # 由 AudioManager 的 _sync_frame_callback 包装为线程安全
        return _cb

    @staticmethod
    def _find_loopback_device() -> int | None:
        """查找 WASAPI loopback 设备（扬声器回放）。找不到时返回 None（sounddevice 使用默认设备）。"""
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                name: str = dev.get("name", "").lower()
                if dev.get("max_input_channels", 0) > 0 and (
                    "loopback" in name or "立体声混音" in name or "stereo mix" in name
                ):
                    logger.info("WasapiCapturer: loopback device [%d] %s", i, dev["name"])
                    return i
        except Exception:
            logger.exception("WasapiCapturer: loopback device query failed")
        logger.warning("WasapiCapturer: no loopback device found, using default input")
        return None

    @staticmethod
    def _find_mic_device() -> int | None:
        """返回系统默认麦克风（None = sounddevice 默认输入）。"""
        return None


__all__ = ["WasapiCapturer"]
```

**关键设计决策：**

- `sounddevice.RawInputStream` 的回调在独立采集线程运行。`_make_sd_callback` 直接调用 `self._callback`，而 `AudioManager._sync_frame_callback` 已封装了 `run_coroutine_threadsafe`，线程安全由上层保障，此处无需额外处理。
- Loopback 设备优先匹配 `立体声混音` / `Stereo Mix`，这是 Windows 标准回放采集名称。找不到时回退默认输入（可在 `.env` 中后续扩展为可配置设备 ID）。
- `blocksize=320`：与现有 Mock 完全一致（16kHz × 20ms × 2 bytes），下游 STT 无需适配。

---

### 子任务 2：实现 `BaiduRealtimeSTT`

**目标文件：** `src/audio/baidu_stt.py`

完整替换当前内容：

```python
"""百度实时语音识别（Realtime ASR）WebSocket 客户端。

协议参考：https://ai.baidu.com/ai-doc/SPEECH/Wkh86eoho
连接端点：wss://vop.baidu.com/realtime_asr
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime
from typing import AsyncIterator

from websockets.asyncio.client import connect as ws_connect

from ..config import get_settings
from .protocol import TranscriptSegment

logger = logging.getLogger(__name__)

_WSS_URL = "wss://vop.baidu.com/realtime_asr"
_SAMPLE_RATE = 16000
_FORMAT = "pcm"
_RECONNECT_DELAY_SEC = 2.0
_MAX_RECONNECT = 5


class BaiduRealtimeSTT:
    """百度实时 ASR WebSocket 客户端。

    每个实例对应一个声道（candidate 或 interviewer）。
    connect() 建立连接；send_audio() 推送 PCM 帧；receive() 异步迭代识别结果。
    """

    def __init__(self, channel: str = "candidate") -> None:
        self._channel = channel
        self._ws = None
        self._connected = False
        self._recv_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        settings = get_settings()
        self._app_id: str = getattr(settings, "BAIDU_APP_ID", "")
        self._api_key: str = getattr(settings, "BAIDU_API_KEY", "")
        self._secret_key: str = getattr(settings, "BAIDU_SECRET_KEY", "")

    # ── STTEngine protocol ──────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 WebSocket 连接并发送开始帧。"""
        if not all([self._app_id, self._api_key, self._secret_key]):
            logger.warning(
                "BaiduRealtimeSTT [%s]: BAIDU credentials not configured, using mock mode",
                self._channel,
            )
            return

        token = await self._get_access_token()
        if not token:
            logger.error("BaiduRealtimeSTT [%s]: failed to obtain access token", self._channel)
            return

        try:
            self._ws = await ws_connect(_WSS_URL)
            start_frame = {
                "type": "START",
                "data": {
                    "appid": int(self._app_id),
                    "appkey": self._api_key,
                    "dev_pid": 80001,       # 普通话，支持标点
                    "cuid": f"interviewer-assistant-{self._channel}",
                    "token": token,
                    "format": _FORMAT,
                    "sample": _SAMPLE_RATE,
                },
            }
            await self._ws.send(json.dumps(start_frame))
            self._connected = True
            # 启动后台接收 task
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info("BaiduRealtimeSTT [%s]: connected", self._channel)
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: connect failed", self._channel)
            self._ws = None

    async def send_audio(self, audio_data: bytes) -> None:
        """发送 PCM 音频帧到百度 ASR。"""
        if not self._connected or self._ws is None:
            return
        try:
            await self._ws.send(audio_data)
        except Exception:
            logger.debug("BaiduRealtimeSTT [%s]: send_audio error, reconnecting…", self._channel)
            self._connected = False

    def receive(self) -> AsyncIterator[TranscriptSegment]:
        """返回识别结果的异步迭代器（从内部队列消费）。"""
        return self._queue_iter()

    async def close(self) -> None:
        """发送结束帧，关闭连接。"""
        if self._ws is not None and self._connected:
            try:
                finish_frame = {"type": "FINISH"}
                await self._ws.send(json.dumps(finish_frame))
                await self._ws.close()
            except Exception:
                logger.debug("BaiduRealtimeSTT [%s]: close error (ignored)", self._channel)
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._ws = None
        logger.info("BaiduRealtimeSTT [%s]: closed", self._channel)

    # ── internals ──────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """后台 task：持续从 WS 接收百度 ASR 响应，解析后入队。"""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                err_no = msg.get("err_no", 0)
                if err_no != 0:
                    logger.warning(
                        "BaiduRealtimeSTT [%s]: err_no=%d msg=%s",
                        self._channel,
                        err_no,
                        msg.get("err_msg", ""),
                    )
                    continue
                result = msg.get("result", "")
                if not result:
                    continue
                is_final = msg.get("type") == "FIN_TEXT"
                segment = TranscriptSegment(
                    text=result,
                    source=self._channel,
                    is_final=is_final,
                    timestamp=datetime.now(),
                )
                await self._recv_queue.put(segment)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: recv loop error", self._channel)

    async def _queue_iter(self) -> AsyncIterator[TranscriptSegment]:
        """从内部队列异步产出 TranscriptSegment。"""
        while True:
            segment = await self._recv_queue.get()
            yield segment

    async def _get_access_token(self) -> str:
        """通过百度 OAuth2 API 获取 access_token。"""
        import httpx
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self._api_key,
            "client_secret": self._secret_key,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, params=params)
                r.raise_for_status()
                return r.json().get("access_token", "")
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: get_access_token failed", self._channel)
            return ""


__all__ = ["BaiduRealtimeSTT"]
```

**关键设计决策：**

- `dev_pid=80001`：百度普通话实时识别，支持自动标点。面试场景中文为主，后续可通过配置切换。
- Access token 每次 `connect()` 时获取。百度 token 有效期 30 天，实际面试时长不超过 2 小时，无需缓存刷新逻辑。
- 识别结果经内部 `asyncio.Queue` 解耦，`receive()` 返回的异步迭代器从队列消费，完全符合现有 `STTEngine` 协议，`AudioManager._stt_receive_loop` 无需修改。
- 凭据缺失时 `connect()` 提前返回（不抛异常），退化为无输出的"软静默"，服务仍可正常运行；可配合 Mock 环境继续开发其他功能。

---

### 子任务 3：更新 `config.py` 与 `.env.example`

**`src/config.py`** — 在 `Settings` 类中追加三个字段（紧跟 `LLM_MAX_RETRIES`）：

```python
# 百度 ASR（实时语音识别）
BAIDU_APP_ID: str = ""
BAIDU_API_KEY: str = ""
BAIDU_SECRET_KEY: str = ""
```

**`.env.example`**（若不存在则新建，与 `.env` 平行）：

```
# 百度语音识别（实时 ASR）
# 申请地址：https://console.bce.baidu.com/ai/#/ai/speech/overview/index
BAIDU_APP_ID=
BAIDU_API_KEY=
BAIDU_SECRET_KEY=
```

---

### 子任务 4：安装依赖

```powershell
pip install "sounddevice>=0.4.6"
```

若使用 `requirements.txt`，追加：

```
sounddevice>=0.4.6
```

---

### P1 验证步骤

1. 在 Windows 设备上，打开控制面板 → 录音 → 启用"立体声混音"（Stereo Mix），或确认扬声器具有 loopback 输入。
2. 在 `.env` 中填写 `BAIDU_APP_ID / BAIDU_API_KEY / BAIDU_SECRET_KEY`。
3. 启动服务，上传简历，开始面试。
4. 用扬声器播放一段语音，观察"转写"Tab 出现候选人识别文本。
5. 对麦克风说话，观察面试官识别文本出现。
6. **降级测试**：清空 `.env` 中的百度凭据，重启后服务应正常启动，转写 Tab 无输出但不报错。

---

## P2 — 候选人选择器 UI

### 背景与现状

- `GET /api/candidates` 和 `GET /api/candidates/{id}/history` 已在 `routes.py` 实现。
- `ui.py` 完全未调用上述接口，每次面试必须重新上传 PDF。
- `state["candidate_id"]` 在上传后通过 `_handle_upload` 设置；选择历史候选人后需走相同路径。

### 修改文件：`src/web/ui.py`

**改动共 3 处，均属追加/小改，不触碰现有逻辑。**

---

#### 改动 1：扩充页面状态

在 `index()` 函数的 `state` 字典初始化（约第 66 行）中，追加一个键：

```python
state: dict[str, Any] = {
    "candidate_id": None,
    "candidate_name": "—",
    "stage": "idle",
    "round_count": 0,
    "suggestion_label": None,
    "suggestion_text": "",
    "suggestion_card": None,
    "agent_history": [],
    "candidates": [],          # ← 新增：缓存候选人列表供下拉框使用
}
```

---

#### 改动 2：在底部工具栏添加候选人选择控件

在 `# Bottom input row` 区块（约第 152 行）的上传按钮之前插入候选人选择下拉框：

**原代码（第 153-165 行附近）：**

```python
        with ui.row().classes(
            "w-full items-end px-4 py-2 bg-white border-t gap-2 flex-shrink-0"
        ):
            user_in = ui.textarea(placeholder="输入指令或问题…").props(
                "autogrow rows=1 outlined dense"
            ).classes("flex-1")
            ui.upload(
                label="",
                on_upload=lambda e: asyncio.create_task(
                    _handle_upload(e, chat_col, chat_scroll, q_col, state)
                ),
                auto_upload=True,
            ).props("accept=.pdf flat dense").tooltip("上传简历 PDF")
            send_btn = ui.button(icon="send").props("flat dense color=primary")
```

**替换为：**

```python
        with ui.row().classes(
            "w-full items-end px-4 py-2 bg-white border-t gap-2 flex-shrink-0"
        ):
            user_in = ui.textarea(placeholder="输入指令或问题…").props(
                "autogrow rows=1 outlined dense"
            ).classes("flex-1")
            candidate_sel = ui.select(
                options={},
                label="选择候选人",
                clearable=True,
            ).props("dense outlined").classes("w-40").tooltip("从历史候选人中选择")
            ui.upload(
                label="",
                on_upload=lambda e: asyncio.create_task(
                    _handle_upload(e, chat_col, chat_scroll, q_col, state)
                ),
                auto_upload=True,
            ).props("accept=.pdf flat dense").tooltip("上传简历 PDF")
            send_btn = ui.button(icon="send").props("flat dense color=primary")
```

---

#### 改动 3：添加候选人加载与选择逻辑

在 `# ── Interaction handlers ───` 区块（约第 168 行）之前，插入以下代码块（在底部工具栏 `with` 块结束之后、`async def _do_send()` 之前）：

```python
    # ── 候选人选择器 ────────────────────────────────────────────────────────────

    async def _load_candidates() -> None:
        """页面加载时拉取历史候选人列表，填充下拉框。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{_base_url}/api/candidates", params={"limit": 50})
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            logger.debug("load_candidates failed: %s", exc)
            return

        candidates = data.get("candidates", [])
        state["candidates"] = candidates
        # options dict: {candidate_id: "姓名 (技能预览)"}
        opts = {}
        for c in candidates:
            cid = c.get("id", "")
            name = c.get("name") or "—"
            skills = c.get("skills", [])
            preview = "、".join(skills[:3]) if skills else ""
            label = f"{name}  {preview}" if preview else name
            opts[cid] = label
        candidate_sel.set_options(opts)

    async def _on_candidate_select(cid: str | None) -> None:
        """候选人选中后：加载其 profile + 最新题目计划，跳过上传流程。"""
        if not cid:
            return

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_base_url}/api/resume/profile", params={"candidate_id": cid})
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            _error(chat_col, f"加载候选人信息失败：{exc}")
            await _scroll(chat_scroll)
            return

        profile = data.get("profile", {})
        questions = data.get("questions", [])

        state["candidate_id"] = cid
        state["candidate_name"] = profile.get("name") or "—"
        _refresh_bar(stage_badge, candidate_label, round_label, state)

        skills = ", ".join((profile.get("skills") or [])[:8])
        q_lines = "\n".join(
            f"  {i+1}. {q.get('question', '')}" for i, q in enumerate(questions[:10])
        )
        if len(questions) > 10:
            q_lines += f"\n  …共 {len(questions)} 道"
        reply = (
            f"已选择历史候选人：{profile.get('name', '—')}\n"
            f"技能：{skills or '—'}\n\n"
            + (f"面试题目（{len(questions)} 道）：\n{q_lines}" if questions else "暂无历史题目，可点击「开始面试」重新生成。")
        )
        _bubble(chat_col, reply, sent=False, name="Agent")
        if questions:
            _render_questions(q_col, questions, cid)
        await _scroll(chat_scroll)

    candidate_sel.on("update:model-value", lambda e: asyncio.create_task(_on_candidate_select(e.args)))
    asyncio.create_task(_load_candidates())
```

---

### P2 验证步骤

1. 启动服务，至少上传一次简历以创建历史候选人。
2. 刷新页面，观察底部工具栏出现"选择候选人"下拉框，且下拉选项已包含历史候选人姓名。
3. 从下拉框选择一位候选人：
   - 状态栏候选人名称更新。
   - 聊天区出现"已选择历史候选人"消息。
   - "题目"Tab 填充上次面试的题目列表。
4. 点击"开始面试"，确认面试正常启动（`stage → interviewing`）。
5. 同时验证原有上传流程：选择候选人后清空下拉框选择，上传新 PDF，流程正常。

---

## 变更文件汇总

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/audio/wasapi.py` | **全量替换** | 实现 sounddevice WASAPI 双流采集 |
| `src/audio/baidu_stt.py` | **全量替换** | 实现百度 ASR WS 客户端 |
| `src/config.py` | **追加** | 添加 `BAIDU_APP_ID/API_KEY/SECRET_KEY` 字段 |
| `.env.example` | **新建/追加** | 百度 ASR 配置示例 |
| `src/web/ui.py` | **3 处局部修改** | 添加候选人下拉框控件 + 加载/选择逻辑 |
| `requirements.txt` | **追加** | `sounddevice>=0.4.6` |

**不涉及改动的文件：**  
`AudioManager`、`AudioStreamBridge`、`TranscriptionManager`、`routes.py`、`memory_module.py`——架构链路无需变更。
