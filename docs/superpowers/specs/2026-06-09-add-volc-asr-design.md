---
comet_change: add-volc-asr
role: technical-design
canonical_spec: openspec
---

# Design Doc: 火山引擎大模型实时语音识别（Volc ASR）

## 背景

项目现有 STT 引擎（百度、讯飞）通过 `STTEngine` Protocol 接入 `AudioManager`，双声道各一个独立 WebSocket 连接。火山引擎豆包大模型实时 ASR 使用自定义 WebSocket 二进制协议，需新增独立引擎实现，通过 `STT_ENGINE=volc` 切换。

OpenSpec 能力规格：`openspec/changes/add-volc-asr/specs/volc-asr/spec.md`

## 技术方案

### 模块结构

```
src/audio/volc_stt.py          ← 新增，~250 行
  ├── 协议常量 & 帧编解码（纯函数，可单测）
  │     _build_full_client_request(payload_json: bytes) -> bytes
  │     _build_audio_frame(audio: bytes, seq: int, is_last: bool) -> bytes
  │     _parse_server_frame(data: bytes) -> ParsedFrame | None
  └── VolcRealtimeSTT 类（镜像 BaiduRealtimeSTT 结构）
        connect / send_audio / receive / close
        _recv_loop / _reconnect / _queue_iter

src/config.py                   ← +3 字段
src/main.py                     ← +elif volc 分支
tests/unit/test_volc_stt.py     ← 编解码 + 行为测试
```

编解码函数放在模块顶层（非类方法），与业务逻辑分离，便于单元测试直接调用。

### 连接与鉴权

**端点**：`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`（双向流式，文档：[大模型流式语音识别 API](https://www.volcengine.com/docs/6561/1354869)）

**鉴权策略（兼容新旧控制台）**：

| 条件 | HTTP 请求头 |
|------|------------|
| `VOLC_ACCESS_KEY` 非空 | `X-Api-App-Key`、`X-Api-Access-Key`、`X-Api-Resource-Id`、`X-Api-Connect-Id`(UUID) |
| `VOLC_ACCESS_KEY` 为空且 `VOLC_APP_KEY` 非空 | `X-Api-Key`、`X-Api-Resource-Id` |

**Full Client Request JSON**：

```json
{
  "user": {"uid": "interviewer-assistant-{channel}"},
  "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
  "request": {
    "model_name": "bigmodel",
    "enable_itn": true,
    "enable_punc": true,
    "show_utterances": true,
    "result_type": "single"
  }
}
```

### 二进制协议

所有整数字段大端序。Header 固定 4 字节：`[version|header_size, msg_type|flags, serialization|compression, reserved]`。

| 帧类型 | Header Byte1 | 后续字段 |
|--------|-------------|----------|
| Full Client Request | `0x10` (type=1, flags=0) | payload_size(4B BE) + JSON |
| Audio Only（普通） | `0x21` (type=2, flags=1) | seq(4B BE) + size(4B) + PCM |
| Audio Only（末包） | `0x23` (type=2, flags=3) | -seq(4B signed BE) + size(4B) + PCM |
| Server Response | `0x91` (type=9) | [seq(4B)] + size(4B) + payload |
| Error | `0xF0` (type=15) | err_code(4B) + err_size(4B) + msg UTF-8 |

**发送策略**：
- 客户端不压缩（compression=0x00）
- 音频分包 6400 字节（200ms × 16kHz × 2bytes），sequence 从 1 递增
- `close()` 发送末包（flags=0x23，sequence 取负值）

**接收策略**：
- 根据 header compression 位判断 payload 是否 Gzip 压缩（服务端可能返回压缩响应，即使客户端未压缩发送）
- compression=0x01 时用 `gzip.decompress()` 解压后再 JSON 解析

### 识别结果解析

```
_recv_loop 收到 Full Server Response
  → _parse_server_frame() 解压 + JSON
  → for utterance in result.get("utterances", []):
       text = utterance.get("text", "")
       if not text: continue
       is_final = utterance.get("definite", False)
       标点修正（_LEADING_PUNCT 缓冲，复用百度/讯飞模式）
       put TranscriptSegment(text, source=channel, is_final, timestamp=now)
```

`result_type=single` 下服务端只推送变化分句，直接 emit，不做 utterance 去重/diff。

### 配置项（`src/config.py`）

```python
VOLC_APP_KEY: str = ""        # 控制台 App Key / App ID（新版控制台即 X-Api-Key）
VOLC_ACCESS_KEY: str = ""     # 控制台 Access Token（旧版控制台）
VOLC_RESOURCE_ID: str = ""    # 如 volc.bigasr.sauc.duration 或 volc.seedasr.sauc.duration
```

`STT_ENGINE` 新增合法值 `volc`。

### 工厂接入（`src/main.py`）

```python
elif settings.STT_ENGINE == "volc":
    from src.audio.volc_stt import VolcRealtimeSTT
    candidate_stt = VolcRealtimeSTT(channel="candidate")
    interviewer_stt = VolcRealtimeSTT(channel="interviewer")
    logger.info("Audio: using VolcRealtimeSTT")
```

### 错误处理与重连

与 `BaiduRealtimeSTT` 对称：

- 凭据缺失 → WARNING + 静默降级，`receive()` 不产出 segment
- send 失败 / recv loop 退出 → `_connected=False`，清空 `_audio_buf`
- `send_audio()` 检测断连 → 后台 `_reconnect()`（1.5s 延迟，`_reconnecting` 防并发）
- 错误帧（type=0xF）→ ERROR 日志 + 断连
- `close()` → `_closed=True` 阻止重连，发末包后关闭 WS

## 测试策略

### 单元测试（不依赖网络）

| 测试 | 验证点 |
|------|--------|
| `test_build_full_client_request` | header 4 字节 + BE payload size + JSON 完整性 |
| `test_build_audio_frame_normal` | seq=1, flags=0x21, PCM 长度正确 |
| `test_build_audio_frame_last` | flags=0x23, 负 sequence |
| `test_parse_server_response` | 解析含 definite=true/false 的 utterances |
| `test_parse_gzip_response` | compression=0x01 时正确解压 |
| `test_parse_error_frame` | type=0xF 返回 error_code + msg |
| `test_connect_no_credentials` | 凭据空时不连、不产出 |
| `test_send_audio_buffering` | <6400B 不发，≥6400B 触发 |

### 集成验证（需真实凭据）

1. `.env` 配置 `STT_ENGINE=volc` + volc 凭据
2. 启动服务，开始面试
3. 验证实时字幕滚动（definite=false）和确定分句（definite=true）
4. 验证追问建议在候选人停顿后触发

## 关键取舍

| 决策 | 选择 | 放弃 |
|------|------|------|
| 接口模式 | bigmodel 双向流式 | nostream（延迟高）、async+二遍识别（复杂度高） |
| 客户端压缩 | 不压缩发送 | Gzip 发送（PCM 无收益） |
| 服务端解压 | 必须支持 | — |
| 热词/corpus | v1 不做 | 后续扩展 |
| 默认引擎 | 保持 baidu | 不自动切换 |

## 回滚

`.env` 中 `STT_ENGINE=xunfei` 或 `STT_ENGINE=baidu`，重启服务即可，零代码改动。
