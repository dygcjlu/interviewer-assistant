# 科大讯飞实时语音转写接入方案

## 1. 背景与目标

当前项目使用百度实时 ASR 进行语音转写，但从实际转写记录来看，识别准确率不稳定，大量句子出现无法理解的噪声文本（参见 `conversations/interview_agent_991982a1-...` 中的典型片段）。

**目标**：接入科大讯飞「实时语音转写大模型」（RTASR-LLM）作为第二 STT 引擎，通过环境变量切换，便于后续对比两家服务的识别效果。

---

## 2. 讯飞 API 要点

文档地址：https://www.xfyun.cn/doc/spark/asr_llm/rtasr_llm.html

| 项目 | 百度（现有） | 讯飞（新增） |
|------|-----------|-----------|
| 协议 | WSS | WSS |
| 鉴权方式 | WS 握手后发 START 帧（含 appid/appkey） | URL 参数中携带 HMAC-SHA1 签名（握手前完成鉴权） |
| 凭据字段 | `BAIDU_APP_ID`, `BAIDU_API_KEY` | `XUNFEI_APP_ID`, `XUNFEI_ACCESS_KEY_ID`, `XUNFEI_ACCESS_KEY_SECRET` |
| 音频格式 | PCM 16k/16bit | PCM 16k/16bit（一致） |
| 推荐发包粒度 | 5120 字节 / 次（约 160ms） | 1280 字节 / 40ms |
| 结束信令 | `{"type": "FINISH"}` | `{"end": true, "sessionId": "<uuid>"}` |
| 中间结果标识 | `msg.type == "MID_TEXT"` | `data.cn.st.type == "1"` |
| 最终结果标识 | `msg.type == "FIN_TEXT"` | `data.cn.st.type == "0"` |
| 文本提取路径 | `msg["result"]` | `data.cn.st.rt[*].ws[*].cw[*].w` 拼接 |

### 2.1 签名生成算法

```
1. 收集所有 URL 参数（不含 signature）:
   appId, accessKeyId, uuid, utc, audio_encode, lang, samplerate

2. 按参数名升序排序，每个 key/value 分别 URL-encode，
   拼接为 "key=value&key=value&..." 得到 baseString

3. HMAC-SHA1(key=accessKeySecret, msg=baseString)

4. Base64 编码 → signature（需 URL-encode 后放入请求 URL）
```

示例 URL：
```
wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1
  ?accessKeyId=xxx&appId=yyy&uuid=zzz&utc=2025-09-04T15%3A38%3A07%2B0800
  &audio_encode=pcm_s16le&lang=autodialect&samplerate=16000&signature=xxx
```

### 2.2 响应结构解析

```json
{
  "msg_type": "result",
  "data": {
    "seg_id": 0,
    "ls": false,
    "cn": {
      "st": {
        "type": "0",
        "bg": 930, "ed": 2590,
        "rt": [
          { "ws": [ { "cw": [ { "w": "你好", "wp": "n" } ] } ] }
        ]
      }
    }
  }
}
```

文本提取：遍历 `data.cn.st.rt[*].ws[*].cw[*].w`，按顺序拼接，忽略 `wp == "g"`（分段标识）的词条。

最终结果：`data.cn.st.type == "0"`；`data.ls == true` 表示整个会话的最后一帧。

---

## 3. 对现有架构的影响分析

### 3.1 现有结构

```
src/audio/protocol.py          # STTEngine Protocol（接口）
src/audio/baidu_stt.py         # BaiduRealtimeSTT（实现类）
src/audio/manager.py           # AudioManager（注入 candidate_stt / interviewer_stt）
src/main.py                    # lifespan() 中按 platform 实例化 STT 引擎
src/config.py                  # Settings（BAIDU_APP_ID 等配置项）
```

`STTEngine` Protocol 已定义好以下接口，讯飞实现无需改动调用方：

```python
async def connect() -> None
async def send_audio(audio_data: bytes) -> None
def receive() -> AsyncIterator[TranscriptSegment]
async def close() -> None
```

### 3.2 变更范围（最小化）

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/audio/xunfei_stt.py` | **新增** | 讯飞 STT 实现类 |
| `src/config.py` | **修改** | 新增 4 个配置字段 |
| `src/main.py` | **修改** | 按 `STT_ENGINE` 选择实现 |
| `.env.example` | **修改** | 新增讯飞凭据示例 |
| `docs/arc/overview.md` | **修改** | 更新技术栈说明 |

`protocol.py`、`manager.py`、`transcription.py`、`stream.py` 均**不需要改动**。

---

## 4. 详细实现方案

### 4.1 新增 `src/audio/xunfei_stt.py`

整体结构与 `baidu_stt.py` 对齐，保持相同的 Pattern：

```python
class XunfeiRealtimeSTT:
    """讯飞实时语音转写大模型 WebSocket 客户端。
    
    每个实例对应一个声道（candidate 或 interviewer）。
    凭据缺失时 connect() 静默返回（与 BaiduRealtimeSTT 行为一致）。
    """
    
    _WSS_BASE = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
    _SEND_CHUNK_BYTES = 1280   # 官方建议：每 40ms 发送 1280 字节
    _RECONNECT_DELAY_SEC = 1.5
```

**关键实现点：**

#### 4.1.1 签名生成

```python
import hashlib, hmac, base64
from urllib.parse import urlencode, quote
from datetime import datetime, timezone, timedelta

def _build_url(self, session_uuid: str) -> str:
    tz = timezone(timedelta(hours=8))
    utc_str = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+0800")
    
    params = {
        "appId": self._app_id,
        "accessKeyId": self._access_key_id,
        "uuid": session_uuid,
        "utc": utc_str,
        "audio_encode": "pcm_s16le",
        "lang": "autodialect",
        "samplerate": "16000",
        "pd": "tech",   # 科技领域，提升技术词汇识别准确率
    }
    
    # 升序排序 → URL-encode 每个 key/value → 拼接 baseString
    sorted_keys = sorted(params.keys())
    base_string = "&".join(
        f"{quote(k, safe='')}={quote(str(params[k]), safe='')}"
        for k in sorted_keys
    )
    
    # HMAC-SHA1 → Base64
    mac = hmac.new(
        self._access_key_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    )
    signature = base64.b64encode(mac.digest()).decode()
    
    params["signature"] = signature
    return f"{self._WSS_BASE}?{urlencode(params)}"
```

> **注意**：`uuid` 字段每次 `connect()` 生成一个新的 `str(uuid4())`，避免触发错误码 `35030`（签名重复）。

#### 4.1.2 send_audio 发包节奏

讯飞要求每 40ms 发送 1280 字节，发包过快会报错。使用与百度相同的缓冲积累策略，`_SEND_CHUNK_BYTES = 1280`。

#### 4.1.3 结束信令

```python
await self._ws.send(json.dumps({"end": True, "sessionId": self._session_id}))
```

其中 `self._session_id` 在收到握手成功响应（`action == "started"`）时从 `msg["sid"]` 提取。

#### 4.1.4 响应解析

```python
def _extract_text(self, data: dict) -> tuple[str, bool]:
    """从讯飞响应 data 字段提取文本和 is_final。"""
    try:
        st = data["cn"]["st"]
        is_final = st.get("type") == "0"
        words = []
        for rt in st.get("rt", []):
            for ws in rt.get("ws", []):
                for cw in ws.get("cw", []):
                    w = cw.get("w", "")
                    wp = cw.get("wp", "n")
                    if wp != "g" and w:   # 忽略分段标识
                        words.append(w)
        return "".join(words), is_final
    except (KeyError, TypeError):
        return "", False
```

#### 4.1.5 错误处理

| 行为 | 触发条件 |
|------|---------|
| 记录 warning 并继续 | `code` 非零但连接未断开（如单次识别超时） |
| 标记断连并重连 | WS 连接被服务端关闭，或收到致命错误码（35001 鉴权失败、35002 用量不足、37008 引擎异常断连） |
| 静默跳过（不重连） | 37009（已收到最终帧，正常会话结束） |

### 4.2 修改 `src/config.py`

新增以下字段：

```python
# 讯飞实时语音转写大模型
XUNFEI_APP_ID: str = ""
XUNFEI_ACCESS_KEY_ID: str = ""
XUNFEI_ACCESS_KEY_SECRET: str = ""

# STT 引擎选择：baidu | xunfei（仅在 Windows + 非 Mock 模式下生效）
STT_ENGINE: str = "baidu"
```

### 4.3 修改 `src/main.py`

将以下代码段：

```python
# 现有代码
if sys.platform == "win32":
    from src.audio.wasapi import WasapiCapturer
    from src.audio.baidu_stt import BaiduRealtimeSTT
    capturer = WasapiCapturer()
    candidate_stt = BaiduRealtimeSTT(channel="candidate")
    interviewer_stt = BaiduRealtimeSTT(channel="interviewer")
```

替换为：

```python
if sys.platform == "win32":
    from src.audio.wasapi import WasapiCapturer
    capturer = WasapiCapturer()
    
    if settings.STT_ENGINE == "xunfei":
        from src.audio.xunfei_stt import XunfeiRealtimeSTT
        candidate_stt = XunfeiRealtimeSTT(channel="candidate")
        interviewer_stt = XunfeiRealtimeSTT(channel="interviewer")
        logger.info("Audio: using XunfeiRealtimeSTT")
    else:
        from src.audio.baidu_stt import BaiduRealtimeSTT
        candidate_stt = BaiduRealtimeSTT(channel="candidate")
        interviewer_stt = BaiduRealtimeSTT(channel="interviewer")
        logger.info("Audio: using BaiduRealtimeSTT")
```

### 4.4 修改 `.env.example`

```ini
# 科大讯飞实时语音转写大模型
# 申请地址：https://console.xfyun.cn/services/new_rta
XUNFEI_APP_ID=
XUNFEI_ACCESS_KEY_ID=
XUNFEI_ACCESS_KEY_SECRET=

# STT 引擎选择：baidu（默认）| xunfei
STT_ENGINE=baidu
```

---

## 5. 实现顺序

```
1. 新增 src/audio/xunfei_stt.py        → 验证：单元测试签名生成正确
2. 修改 src/config.py                  → 验证：Settings 正确加载新字段
3. 修改 src/main.py                    → 验证：STT_ENGINE=xunfei 时正确实例化
4. 修改 .env.example                   → 补充注释
5. 修改 docs/arc/overview.md           → 更新技术栈说明
```

---

## 6. 已完成实现

上述方案已全部落地：
- `src/audio/xunfei_stt.py`：讯飞 STT 实现类，`pd` 硬编码为 `com`（企业领域）
- `src/config.py`：新增 `XUNFEI_APP_ID`、`XUNFEI_ACCESS_KEY_ID`、`XUNFEI_ACCESS_KEY_SECRET`、`STT_ENGINE` 字段
- `src/main.py`：按 `STT_ENGINE` 环境变量选择 STT 实现
- `.env.example`：新增讯飞凭据示例和 `STT_ENGINE` 说明

---

## 7. 不在本次范围内

- 讯飞「声纹分离」功能（`role_type=2`）：当前架构已通过独立麦克风采集实现声道分离，无需 ASR 层再做说话人分离。
- 热词配置：需在讯飞控制台手动上传，超出代码范围。
- 同时运行两家 STT 做实时对比：成本翻倍且架构变复杂，当前通过切换 `STT_ENGINE` 分场景对比即可。
