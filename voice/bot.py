"""WhyLine voice agent: talk to the live market, and hear anomalies as they land.

Free stack on one Groq key (Pipecat v1):
    mic -> SmallWebRTC -> Groq Whisper (STT) -> Groq gpt-oss-20b + WhyLine tools
        -> Groq Orpheus (TTS) -> speaker

The bot runs in its own process and reaches the live market through the
backend's /api/tools routes (backend/tools.py), the same tools the text chat
and MCP server use. It also listens to the backend's /ws feed and speaks a
short alert when a live anomaly's attribution (scope + "why") arrives.

    python bot.py        # backend must be running on :8000
    # then open the dashboard -> Ask -> Voice (or http://localhost:7860 for Pipecat's UI)

Reads GROQ_API_KEY (and optional VOICE_GROQ_MODEL / GROQ_TTS_VOICE) from
backend/.env so the key lives in one place.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import websockets
from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.groq.tts import GroqTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")

BACKEND = os.getenv("WHYLINE_URL", "http://localhost:8000").rstrip("/")
# Separate model from the backend chat (gpt-oss-120b): Groq's free-tier
# limits are per model, so talking never eats the chat's tokens/minute.
MODEL = os.getenv("VOICE_GROQ_MODEL") or "openai/gpt-oss-20b"
VOICE = os.getenv("GROQ_TTS_VOICE") or "autumn"
ALERT_GAP = float(os.getenv("VOICE_ALERT_GAP", "20"))  # min seconds between spoken alerts

VOICE_RULES = (
    " You are speaking aloud: one to three short sentences, no lists or symbols, "
    "say 'percent' instead of '%', and round prices sensibly."
)

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def load_tools(http: httpx.AsyncClient) -> tuple[list[FunctionSchema], str]:
    """WhyLine's tool schemas + persona, each tool forwarded to the backend."""
    data = (await http.get(f"{BACKEND}/api/tools")).raise_for_status().json()

    async def forward(params: FunctionCallParams) -> None:
        try:
            resp = await http.post(f"{BACKEND}/api/tools/{params.function_name}", json=params.arguments)
            result = resp.json()
        except httpx.HTTPError as exc:
            result = {"error": f"WhyLine backend unreachable ({type(exc).__name__})"}
        await params.result_callback(result)

    schemas = [
        FunctionSchema(
            name=fn["name"],
            description=fn["description"],
            properties=fn["parameters"].get("properties", {}),
            required=fn["parameters"].get("required", []),
            handler=forward,
        )
        for fn in (t["function"] for t in data["tools"])
    ]
    return schemas, data["system_prompt"]


def alert_text(anomaly: dict, attribution: dict) -> str:
    """One spoken sentence or two for a live anomaly."""
    coin = anomaly["symbol"].removesuffix("USDT")
    verb = "jumped" if anomaly["direction"] == "up" else "dropped"
    text = f"Heads up: {coin} {verb} {abs(anomaly['pct_change']):.2f} percent in one minute."
    market = attribution.get("market")
    if attribution.get("summary"):
        text += " " + attribution["summary"]
    elif market:
        text += " It looks market-wide." if market["scope"] == "market_wide" else f" It looks specific to {coin}."
    return text


async def announce_anomalies(worker: PipelineWorker) -> None:
    """Speak each live anomaly once its final attribution arrives on /ws."""
    ws_url = "ws" + BACKEND.removeprefix("http") + "/ws"
    live: dict[str, dict] = {}
    last_spoken = 0.0
    async for ws in websockets.connect(ws_url):  # reconnects with backoff
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "anomaly" and msg["anomaly"].get("live"):
                    live[msg["anomaly"]["id"]] = msg["anomaly"]
                elif msg["type"] == "attribution" and msg["attribution"]["status"] != "pending":
                    anomaly = live.pop(msg["anomaly_id"], None)
                    # ponytail: drops alerts inside the gap (replay mode fires many); queue them if missed alerts matter
                    if anomaly and time.monotonic() - last_spoken >= ALERT_GAP:
                        last_spoken = time.monotonic()
                        await worker.queue_frames([TTSSpeakFrame(alert_text(anomaly, msg["attribution"]))])
        except websockets.ConnectionClosed:
            continue


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    key = os.environ["GROQ_API_KEY"]
    http = httpx.AsyncClient(timeout=10.0)
    tools, persona = await load_tools(http)

    stt = GroqSTTService(api_key=key)
    llm = GroqLLMService(
        api_key=key,
        settings=GroqLLMService.Settings(
            model=MODEL,
            system_instruction=persona + VOICE_RULES,
            reasoning_effort="low" if MODEL.startswith("openai/gpt-oss") else None,
        ),
    )
    tts = GroqTTSService(api_key=key, settings=GroqTTSService.Settings(voice=VOICE))

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        await tts.queue_frame(TTSSpeakFrame("Let me check."))

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline(
        [transport.input(), stt, user_aggregator, llm, tts, transport.output(), assistant_aggregator]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    alerts: asyncio.Task | None = None

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal alerts
        logger.info("Voice client connected")
        context.add_message(
            {"role": "developer", "content": "Greet the user in one short sentence and offer to explain recent anomalies."}
        )
        await worker.queue_frames([LLMRunFrame()])
        alerts = asyncio.create_task(announce_anomalies(worker))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Voice client disconnected")
        if alerts:
            alerts.cancel()
        await runner.cancel()

    try:
        await runner.run()
    finally:
        await http.aclose()


async def bot(runner_args: RunnerArguments):
    """Pipecat runner entry point."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


def preflight() -> None:
    """Fail at startup, not mid-conversation, on the two setup gaps seen in practice."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY missing: add it to backend/.env")
    resp = httpx.post(
        "https://api.groq.com/openai/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "canopylabs/orpheus-v1-english", "voice": VOICE, "input": "ok", "response_format": "wav"},
        timeout=20.0,
    )
    if resp.status_code == 400 and "terms" in resp.text:
        raise SystemExit(
            "Groq's Orpheus voice needs a one-time terms acceptance (free). Open\n"
            "  https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english\n"
            "accept, then run bot.py again."
        )
    resp.raise_for_status()


if __name__ == "__main__":
    from pipecat.runner.run import main

    preflight()
    main()
