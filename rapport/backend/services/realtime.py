"""gpt-realtime WebSocket bridge.

Connects server-side to either the team's Azure resource
(AZURE_REALTIME_ENDPOINT + AZURE_REALTIME_API_KEY + AZURE_REALTIME_DEPLOYMENT)
or standard OpenAI (OPENAI_API_KEY, model gpt-realtime). Uses the GA wire
format; the relay in call_loop tolerates both GA and beta event names."""
import asyncio
import json
import logging
import os
from urllib.parse import urlsplit

import websockets

log = logging.getLogger("realtime")

# The agent hangs up itself when the call reaches a natural end, recording a
# structured outcome. (Adapted from the root prototype's END_CALL_TOOL.)
END_CALL_TOOL = {
    "type": "function",
    "name": "end_call",
    "description": (
        "End the phone call. Call this only AFTER speaking a closing line, when the "
        "deal is closed, a clear next step is agreed, or the prospect wants to stop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": [
                    "deal_closed",
                    "next_step_agreed",
                    "declined",
                    "customer_ended",
                    "other",
                ],
                "description": "How the call ended.",
            },
            "summary": {
                "type": "string",
                "description": "One short sentence summarizing the outcome.",
            },
        },
        "required": ["outcome"],
    },
}


def _target():
    """(url, headers) for whichever credentials exist, else (None, None)."""
    az_key = os.getenv("AZURE_REALTIME_API_KEY", "").strip()
    az_ep = os.getenv("AZURE_REALTIME_ENDPOINT", "").strip()
    if az_key and az_ep and "PUT_YOUR" not in az_key:
        p = urlsplit(az_ep)
        deployment = os.getenv("AZURE_REALTIME_DEPLOYMENT", "gpt-realtime-2").strip()
        url = f"wss://{p.netloc}/openai/v1/realtime?model={deployment}"
        return url, {"api-key": az_key}

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        model = os.getenv("REALTIME_MODEL", "gpt-realtime")
        return f"wss://api.openai.com/v1/realtime?model={model}", {"Authorization": f"Bearer {key}"}

    return None, None


async def _connect(url, headers):
    # websockets >= 13 renamed extra_headers -> additional_headers
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=16 * 1024 * 1024)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, max_size=16 * 1024 * 1024)


class RealtimeSession:
    """One gpt-realtime conversation. on_event receives every server event."""

    def __init__(self, instructions: str, on_event):
        self.instructions = instructions
        self.on_event = on_event
        self.ws = None
        self.connected = False
        self._recv_task = None

    @staticmethod
    def available() -> bool:
        return _target()[0] is not None

    async def connect(self):
        url, headers = _target()
        if not url:
            raise RuntimeError("no realtime credentials configured (.env)")
        self.ws = await _connect(url, headers)
        self.connected = True
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": self.instructions,
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "transcription": {
                                "model": os.getenv("INPUT_TRANSCRIPTION_MODEL", "whisper-1")
                            },
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": int(
                                    os.getenv("VAD_SILENCE_MS", "600")
                                ),
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "voice": os.getenv("AGENT_VOICE", "marin"),
                        },
                    },
                    "tools": [END_CALL_TOOL],
                    "tool_choice": "auto",
                },
            }
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        log.info("realtime connected: %s", url.split("?")[0])

    async def _recv_loop(self):
        try:
            async for raw in self.ws:
                try:
                    ev = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                try:
                    await self.on_event(ev)
                except Exception as e:
                    log.exception("on_event failed for %s: %s", ev.get("type"), e)
        except Exception as e:
            log.warning("realtime recv loop ended: %s", e)
        finally:
            self.connected = False
            try:
                await self.on_event({"type": "_closed"})
            except Exception:
                pass

    async def send_audio(self, b64: str):
        await self._send({"type": "input_audio_buffer.append", "audio": b64})

    async def add_system_note(self, text: str):
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )

    async def create_response(self):
        await self._send({"type": "response.create"})

    async def _send(self, obj):
        if not (self.ws and self.connected):
            return
        try:
            await self.ws.send(json.dumps(obj))
        except Exception as e:
            log.warning("realtime send failed: %s", e)
            self.connected = False

    async def close(self):
        self.connected = False
        if self._recv_task:
            self._recv_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
