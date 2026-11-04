import asyncio
import base64
import json
import unittest
from unittest.mock import patch

from recognition_compare import (
    CODE_FORMAT_RULES, ComparisonBridge, DEFAULT_PROMPTS, FORMAT_TOOL, Lane, Pending, REALTIME_MODEL,
    asr_configuration, catalog, realtime_configuration,
)
from provider_io import SERVICE, checked_instructions, LiveSettings, ProviderFailure, VoiceLiveSettings, cancel_task
from fixtures import FakeProvider, FakeBrowser, until, RtcHttp, SDP
from speech_display import format_spoken_codes, native_rows


class RawVoiceProvider(FakeProvider):
    reject_model = None
    formatted_result = None

    async def send(self, raw):
        await super().send(raw)
        event = json.loads(raw)
        if event["type"] == "session.update":
            self.incoming.get_nowait()
            if event["session"]["input_audio_transcription"]["model"] == self.reject_model:
                self.incoming.put_nowait({"type": "error", "error": {"message": "Private provider body"}})
            else:
                self.incoming.put_nowait({
                    "type": "session.updated", "session": {**event["session"], "model": SERVICE["model"]},
                })
        elif event["type"] == "response.create":
            response = {"id": "format_" + event["response"]["metadata"]["comparison_row"],
                        "metadata": event["response"]["metadata"]}
            self.incoming.put_nowait({"type": "response.created", "response": response})
            raw = event["response"]["input"][0]["content"][0]["text"]
            self.incoming.put_nowait({"type": "response.done", "response": {
                **response, "status": "completed", "output": [{
                    "type": "function_call", "name": FORMAT_TOOL["name"],
                    "arguments": json.dumps({"text": (
                        format_spoken_codes(raw) if self.formatted_result is None else self.formatted_result
                    )}),
                }],
            }})


class NativeRealtimeProvider(FakeProvider):
    audio = base64.b64encode(b"\x01\x02" * 2400).decode()
    extra_output = ()
    reject = False
    result = None
    auto_respond = True
    forced_transcription = None

    def __init__(self):
        super().__init__()
        self.incoming.put_nowait({"type": "session.created", "session": {"model": REALTIME_MODEL}})

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.update":
            if self.reject:
                self.incoming.put_nowait({"type": "error", "error": {"message": "Private provider body"}})
            else:
                session = {**event["session"], "model": REALTIME_MODEL}
                if self.forced_transcription:
                    session["input_audio_transcription"] = {"model": self.forced_transcription}
                self.incoming.put_nowait({"type": "session.updated", "session": session})
        elif event["type"] == "conversation.item.retrieve":
            self.incoming.put_nowait({"type": "conversation.item.retrieved", "item": {
                "id": event["item_id"], "type": "message", "role": "user",
                "content": [{"type": "input_audio", "audio": self.audio}],
            }})
        elif event["type"] == "response.create" and self.auto_respond:
            response = {"id": "response_" + event["response"]["metadata"]["input_item"],
                        "metadata": event["response"]["metadata"]}
            self.incoming.put_nowait({"type": "response.created", "response": response})
            self.incoming.put_nowait({"type": "response.done", "response": {
                **response, "status": "completed", "output": [*self.extra_output, {
                    "type": "function_call", "name": "report_recognition", "arguments": json.dumps(
                        self.result if self.result is not None else {"heard_text": "C I 六十三", "formatted_text": "CI63"}
                    ),
                }],
            }})

    def utterance(self, item):
        for kind in ("speech_started", "speech_stopped", "committed"):
            self.incoming.put_nowait({"type": "input_audio_buffer." + kind, "item_id": item})


ComparisonHttp = RtcHttp


class ComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.browser = FakeBrowser()
        self.voice = []
        self.live = FakeProvider()
        self.realtime = NativeRealtimeProvider()
        self.realtime_urls = []

        async def voice_connector(_url, _token):
            provider = RawVoiceProvider()
            self.voice.append(provider)
            return provider

        async def live_connector(_url, _token):
            return self.live

        async def realtime_connector(url, _token):
            self.realtime_urls.append(url)
            return self.realtime

        async def token():
            return "private-test-token"

        self.bridge = ComparisonBridge(
            self.browser, VoiceLiveSettings("https://example.cognitiveservices.azure.com"),
            LiveSettings("https://example.openai.azure.com"), token,
            voice_connector=voice_connector, live_connector=live_connector,
            realtime_connector=realtime_connector,
        )
        self.http = ComparisonHttp(self.live)
        self.override = patch("recognition_compare.aiohttp.ClientSession", return_value=self.http)
        self.override.start()
        self.addCleanup(self.override.stop)
        self.running = None

    async def asyncTearDown(self):
        if self.running and not self.running.done():
            self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
        if self.running:
            await asyncio.wait_for(self.running, 10)
        RawVoiceProvider.reject_model = None
        NativeRealtimeProvider.reject = False
        NativeRealtimeProvider.forced_transcription = None

    async def start(self):
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.start", "consent": True, "sdp": SDP}))
        await until(lambda: self.bridge.lanes["live"].provider is not None)
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.live.connected"}))
        await until(lambda: all(lane.status in {"ready", "failed"} for lane in self.bridge.lanes.values()))

    def test_no_airline_hints_responses_or_business_workflow(self):
        session = asr_configuration()["session"]
        self.assertEqual(session["tools"], [FORMAT_TOOL])
        self.assertEqual(session["modalities"], ["text"])
        self.assertFalse(session["turn_detection"]["create_response"])
        self.assertNotIn("phrase_list", session["input_audio_transcription"])
        self.assertNotIn("input_audio_echo_cancellation", session)
        native = realtime_configuration()["session"]
        self.assertEqual(native["modalities"], ["text"])
        self.assertIsNone(native["input_audio_transcription"])
        self.assertFalse(native["turn_detection"]["create_response"])
        configuration = catalog(self.bridge.voice, self.bridge.live)
        self.assertFalse(configuration["business_workflow"])
        self.assertEqual(configuration["phrase_count"], 0)
        self.assertEqual(configuration["lanes"][2]["kind"], "native_fragments")

    def test_all_default_prompts_share_code_extraction_and_repetition_examples(self):
        for lane, prompt in DEFAULT_PROMPTS.items():
            with self.subTest(lane=lane):
                self.assertEqual(checked_instructions(prompt), prompt)
                self.assertIn(CODE_FORMAT_RULES, prompt)
                self.assertIn("三個零一個八七個五 => 00085555555", prompt)
                self.assertIn("3個01個87個5 => 00085555555", prompt)
                self.assertIn("double zero, triple five, eight => 005558", prompt)
                self.assertIn("只能含 ASCII 大寫 A-Z 與半形數字 0-9", prompt)
        self.assertIn("heard_text 記錄你聽到的完整文字", DEFAULT_PROMPTS["realtime"])
        self.assertNotIn("其他敘述維持原文", DEFAULT_PROMPTS["realtime"])
        self.assertNotIn("不確定的原文保留", DEFAULT_PROMPTS["speech"])

    async def test_same_pcm_reaches_both_asr_lanes_without_autonomous_reply_or_mcp(self):
        await self.start()
        audio = base64.b64encode(b"\xc4\x09" * 960).decode()
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.audio", "audio": audio}))
        await until(lambda: all(lane.frames == 1 for lane in self.bridge.lanes.values() if lane.id != "live"))
        for provider in [*self.voice, self.realtime]:
            self.assertEqual(provider.sent[-1], {"type": "input_audio_buffer.append", "audio": audio})
            self.assertFalse(any(event["type"] == "response.create" for event in provider.sent))
        self.assertNotIn("private-test-token", json.dumps(self.browser.events))
        self.assertNotIn("sess_test", json.dumps(self.browser.events))
        self.assertFalse(hasattr(self.bridge, "flow"))

    async def test_native_realtime_rejects_an_independent_transcription_model(self):
        self.assertEqual(asr_configuration()["session"]["input_audio_transcription"]["language"], "zh-TW")
        NativeRealtimeProvider.forced_transcription = "whisper-1"
        await self.start()
        self.assertEqual(self.bridge.lanes["realtime"].status, "failed")
        self.assertEqual(self.bridge.lanes["speech"].status, "ready")
        self.assertEqual(self.realtime_urls, [
            "wss://example.cognitiveservices.azure.com/voice-live/realtime?api-version=2026-07-15&model=gpt-realtime-2.1",
        ])

    async def test_luna_processing_is_stateless_separate_and_correlated_to_each_raw_row(self):
        await self.start()
        for lane in (self.bridge.lanes["speech"],):
            for row, raw in ((2, "C I 六十三"), (1, "x q v r nine p")):
                lane.pending[str(row)] = Pending(row, committed=True, text=raw)
                await self.bridge.finish(lane, str(row))
        await until(lambda: len([e for e in self.browser.events if e["type"] == "compare.processed" and e["status"] == "done"]) == 2)
        for lane in ("speech",):
            final = [e for e in self.browser.events if e["type"] == "compare.processed" and e["status"] == "done" and e["lane"] == lane]
            self.assertEqual([(e["row"], e["text"]) for e in final], [(2, "CI63"), (1, "XQVR9P")])
            raw = [e["text"] for e in self.browser.events if e["type"] == "compare.transcript" and e["lane"] == lane]
            self.assertEqual(raw, ["C I 六十三", "x q v r nine p"])
        requests = [event["response"] for event in self.voice[0].sent if event["type"] == "response.create"]
        self.assertEqual(len(requests), 2)
        for body in requests:
            self.assertEqual(body["conversation"], "none")
            self.assertEqual(body["modalities"], ["text"])
            self.assertEqual(body["tools"], [FORMAT_TOOL])
            self.assertEqual(body["tool_choice"]["name"], FORMAT_TOOL["name"])
            self.assertNotIn("previous_response_id", body)
            self.assertIn(body["input"][0]["content"][0]["text"], ("C I 六十三", "x q v r nine p"))
        self.assertFalse(any(url.endswith("/responses") for url, _ in self.http.requests))

    async def test_native_generated_fragments_are_separate_and_never_reformatted_by_luna(self):
        await self.start()
        for kind, text in (("session.input_transcript.delta", "C I 六十三"), ("session.output_transcript.delta", "CI63")):
            self.live.incoming.put_nowait({"type": kind, "delta": text, "start_ms": 100, "end_ms": 200})
        await until(lambda: any(e["type"] == "compare.native.processed" for e in self.browser.events))
        raw = next(e for e in self.browser.events if e["type"] == "compare.fragment")
        processed = next(e for e in self.browser.events if e["type"] == "compare.native.processed")
        self.assertEqual(raw["rows"][0]["raw"], "C I 六十三")
        self.assertEqual(processed["rows"][0]["raw"], "CI63")
        self.assertFalse(any(url.endswith("/responses") for url, _ in self.http.requests))

    async def test_empty_transcript_is_not_sent_to_luna(self):
        await self.start()
        lane = self.bridge.lanes["speech"]
        lane.pending["empty"] = Pending(1, committed=True, text="")
        await self.bridge.finish(lane, "empty")
        self.assertTrue(any(e.get("status") == "skipped" for e in self.browser.events))
        self.assertEqual(lane.process_calls, 0)
        self.assertFalse(any(url.endswith("/responses") for url, _ in self.http.requests))

    async def test_processing_failure_preserves_raw_and_does_not_stop_asr_or_other_lane(self):
        async def fail(_raw):
            raise ProviderFailure("postprocess_failed")
        self.bridge.postprocessor = fail
        await self.start()
        lane = self.bridge.lanes["speech"]
        lane.pending["failed"] = Pending(1, committed=True, text="C I 六十三")
        await self.bridge.finish(lane, "failed")
        await until(lambda: any(e["type"] == "compare.processed" and e["status"] == "failed" for e in self.browser.events))
        self.assertEqual(lane.status, "ready")
        self.assertEqual(self.bridge.lanes["realtime"].status, "ready")
        self.assertEqual(next(e["text"] for e in self.browser.events if e["type"] == "compare.transcript"), "C I 六十三")
        self.bridge.postprocessor = None
        lane.pending["next"] = Pending(2, committed=True, text="C I 六十三")
        await self.bridge.finish(lane, "next")
        await until(lambda: any(e.get("status") == "done" and e.get("row") == 2 for e in self.browser.events))

    async def test_processing_limits_are_visible_without_replacing_raw_text(self):
        for calls, characters, turns in ((60, 0, 1), (0, 24000, 1), (0, 0, 9)):
            lane = Lane("speech", process_calls=calls, process_characters=characters)
            for row in range(1, turns + 1):
                lane.pending[str(row)] = Pending(row, committed=True, text="C I 六十三")
                await self.bridge.finish(lane, str(row))
            self.assertEqual(self.browser.events[-1]["status"], "failed")
            self.assertIn("上限", self.browser.events[-1]["message"])
            self.assertLessEqual(lane.processing.qsize(), 8)

    async def test_stop_cancels_processing_and_marks_active_and_queued_rows(self):
        processing = asyncio.Event()

        async def blocked(_raw):
            processing.set()
            await asyncio.Event().wait()

        self.bridge.postprocessor = blocked
        await self.start()
        lane = self.bridge.lanes["speech"]
        for row in (1, 2):
            lane.pending[str(row)] = Pending(row, committed=True, text="C I 六十三")
            await self.bridge.finish(lane, str(row))
        await processing.wait()
        with patch("recognition_compare.PROCESS_DRAIN_SECONDS", .01):
            self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
            await asyncio.wait_for(self.running, 5)
        cancelled = [e["row"] for e in self.browser.events if e["type"] == "compare.processed" and e["status"] == "cancelled"]
        self.assertEqual(cancelled, [1, 2])
        self.assertTrue(all(task.done() for task in self.bridge.process_tasks))
        self.assertTrue(lane.processing.empty())

    async def test_incomplete_refused_or_malformed_structured_responses_are_not_success(self):
        for document in (
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"type": "function_call"}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]},
            {"status": "completed", "output": [None]},
            {"status": "completed", "output": None},
        ):
            lane = Lane("speech", response_id="format-test")
            lane.format_metadata = {"comparison_row": "1"}
            lane.format_result = asyncio.get_running_loop().create_future()
            with self.subTest(document=document), self.assertRaises(ProviderFailure):
                self.bridge.format_response(lane, {"type": "response.done", "response": {
                    **document, "id": lane.response_id, "metadata": lane.format_metadata,
                }}, FORMAT_TOOL)
            lane.format_result.cancel()

    async def test_independent_interleaved_asr_finals_keep_own_rows_and_raw_text(self):
        await self.start()
        for provider in self.voice:
            for item in ("one", "two"):
                provider.incoming.put_nowait({"type": "input_audio_buffer.speech_started", "item_id": item})
            provider.incoming.put_nowait({"type": "input_audio_buffer.committed", "item_id": "two"})
            provider.incoming.put_nowait({
                "type": "conversation.item.input_audio_transcription.completed", "item_id": "two",
                "content_index": 0, "transcript": "第二句简体。",
            })
            provider.incoming.put_nowait({
                "type": "conversation.item.input_audio_transcription.completed", "item_id": "one",
                "content_index": 0, "transcript": "My number is xqvr9p.",
            })
            provider.incoming.put_nowait({"type": "input_audio_buffer.committed", "item_id": "one"})
        await until(lambda: len([event for event in self.browser.events if event["type"] == "compare.transcript"]) == 2)
        for lane in ("speech",):
            events = [event for event in self.browser.events if event.get("lane") == lane and event["type"] == "compare.transcript"]
            self.assertEqual([(event["row"], event["text"]) for event in events],
                             [(2, "第二句简体。"), (1, "My number is xqvr9p.")])
            self.assertTrue(all(event["final"] for event in events))
            self.assertEqual(self.bridge.lanes[lane].pending, {})

    async def test_one_provider_rejection_is_visible_and_other_lanes_continue(self):
        NativeRealtimeProvider.reject = True
        await self.start()
        self.assertEqual(self.bridge.lanes["realtime"].status, "failed")
        self.assertEqual(self.bridge.lanes["speech"].status, "ready")
        self.assertEqual(self.bridge.lanes["live"].status, "ready")
        self.assertNotIn("Private provider body", json.dumps(self.browser.events))
        audio = base64.b64encode(b"\0" * 1920).decode()
        await self.bridge.audio(audio)
        await until(lambda: self.bridge.lanes["speech"].frames == 1)
        self.assertEqual(self.bridge.lanes["realtime"].frames, 0)

    async def test_native_fragments_are_not_replaced_by_asr_and_close_collects_usage(self):
        await self.start()
        self.live.incoming.put_nowait({
            "type": "session.input_transcript.delta", "delta": "原生文字", "start_ms": 100, "end_ms": 200,
        })
        await until(lambda: any(event["type"] == "compare.fragment" for event in self.browser.events))
        self.assertFalse(any(event["type"] == "compare.transcript" for event in self.browser.events))
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
        await asyncio.wait_for(self.running, 10)
        self.assertTrue(self.browser.events[-1]["native_usage_final"])
        self.assertTrue(self.live.closed)
        self.assertTrue(all(provider.closed for provider in self.voice))
        self.assertTrue(all(lane.provider is None for lane in self.bridge.lanes.values()))

    async def test_invalid_audio_and_unconsented_start_are_rejected(self):
        for payload in ("!", "", base64.b64encode(b"\0").decode(), base64.b64encode(b"\0" * 4802).decode()):
            with self.subTest(payload_length=len(payload)), self.assertRaises(ValueError):
                await self.bridge.audio(payload)
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.start", "consent": False, "sdp": SDP}))
        await self.running
        self.assertTrue(any(event.get("code") == "consent_required" for event in self.browser.events))
        self.assertEqual(self.voice, [])

    async def test_failed_transcription_is_explicit_not_fabricated_text(self):
        await self.start()
        provider = self.voice[0]
        provider.incoming.put_nowait({"type": "input_audio_buffer.speech_started", "item_id": "failed"})
        provider.incoming.put_nowait({
            "type": "conversation.item.input_audio_transcription.failed", "item_id": "failed", "content_index": 0,
        })
        await until(lambda: any(event.get("code") == "transcription_failed" for event in self.browser.events))
        self.assertFalse(any(event["type"] == "compare.transcript" for event in self.browser.events))
        self.assertEqual(self.bridge.lanes["speech"].status, "ready")

    async def test_queue_pressure_fails_only_lagging_lane(self):
        self.bridge.lanes["speech"].status = "ready"
        encoded = base64.b64encode(b"\0" * 1920).decode()
        for _ in range(9):
            await self.bridge.audio(encoded)
        self.assertEqual(self.bridge.lanes["speech"].status, "failed")
        self.assertTrue(any(event.get("code") == "audio_backpressure" for event in self.browser.events))

    async def test_stop_before_start_creates_no_upstream_session(self):
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
        await self.running
        self.assertEqual(self.voice, [])
        self.assertEqual(self.live.sent, [])
        self.assertEqual(self.browser.events[-1]["type"], "compare.ended")
        self.assertFalse(any(event["type"] == "compare.error" for event in self.browser.events))

    async def test_oversized_start_is_rejected_before_opening_any_provider(self):
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(" " * (96 * 1024 + 1))
        await self.running
        self.assertTrue(any(event.get("code") == "invalid_command" for event in self.browser.events))
        self.assertEqual(self.voice, [])
        self.assertEqual(self.live.sent, [])

    async def test_stop_cancels_native_startup_without_waiting_for_creation(self):
        creation = asyncio.Event()
        cancelled = asyncio.Event()

        async def creating(*_args):
            creation.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch("recognition_compare.create_live_rtc", creating):
            self.running = asyncio.create_task(self.bridge.run())
            self.browser.incoming.put_nowait(json.dumps({"type": "compare.start", "consent": True, "sdp": SDP}))
            await asyncio.wait_for(creation.wait(), 1)
            self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
            await asyncio.wait_for(self.running, 2)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(any(event["type"] == "compare.error" for event in self.browser.events))
        self.assertTrue(all(lane.queue.empty() for lane in self.bridge.lanes.values()))
        self.assertTrue(all(provider.closed for provider in self.voice))

    async def test_failed_reader_does_not_rethrow_during_cleanup_or_leak_providers(self):
        await self.start()
        self.realtime.incoming.put_nowait({"type": "response.created", "response": {}})
        await until(lambda: self.bridge.lanes["realtime"].status == "failed")
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
        await asyncio.wait_for(self.running, 5)
        self.assertTrue(all(provider.closed for provider in self.voice))
        self.assertTrue(self.live.closed)
        self.assertTrue(all(lane.provider is None for lane in self.bridge.lanes.values()))
        self.assertEqual(self.browser.events[-1]["type"], "compare.ended")

    async def test_stop_cancels_asr_startup_without_waiting_for_connection(self):
        creation = asyncio.Event()
        cancelled = []

        async def creating(_url, _token):
            creation.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        self.bridge.voice_connector = creating
        self.bridge.realtime_connector = creating
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.start", "consent": True, "sdp": SDP}))
        await asyncio.wait_for(creation.wait(), 1)
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.stop"}))
        await asyncio.wait_for(self.running, 2)
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(self.voice, [])
        self.assertEqual(self.browser.events[-1]["type"], "compare.ended")

    async def test_native_three_audio_items_generate_correlated_stateless_results_without_luna(self):
        await self.start()
        for index in range(3):
            self.realtime.utterance(str(index))
        await until(lambda: len([event for event in self.browser.events
                                if event["type"] == "compare.processed" and event.get("status") == "done"]) == 3)
        requests = [event["response"] for event in self.realtime.sent if event["type"] == "response.create"]
        self.assertEqual(len(requests), 3)
        self.assertEqual([body["input"] for body in requests], [[{
            "type": "message", "role": "user", "content": [{"type": "input_audio", "audio": self.realtime.audio}],
        }]] * 3)
        self.assertTrue(all(body["conversation"] == "none" for body in requests))
        self.assertFalse(any(url.endswith("/responses") for url, _ in self.http.requests))
        self.assertEqual(sum(event["type"] == "conversation.item.delete" for event in self.realtime.sent), 3)

    async def test_native_auxiliary_assistant_message_keeps_the_single_structured_result(self):
        self.realtime.extra_output = ({"type": "message", "role": "assistant", "content": [
            {"type": "text", "text": "Ancillary generated text is not the recognition result."},
        ]},)
        await self.start()
        self.realtime.utterance("with-message")
        await until(lambda: any(event["type"] == "compare.processed" and event.get("status") == "done"
                                for event in self.browser.events))
        result = next(event for event in self.browser.events if event["type"] == "compare.processed"
                      and event.get("status") == "done")
        self.assertEqual(result["text"], "CI63")
        self.assertFalse(any(url.endswith("/responses") for url, _ in self.http.requests))

    async def test_native_auxiliary_refusal_is_not_accepted_as_a_success(self):
        self.realtime.extra_output = ({"type": "message", "role": "assistant", "content": [
            {"type": "refusal", "refusal": "No"},
        ]},)
        await self.start()
        self.realtime.utterance("refused")
        await until(lambda: self.bridge.lanes["realtime"].status == "failed")
        self.assertFalse(any(event["type"] == "compare.processed" and event.get("status") == "done"
                             for event in self.browser.events))

    async def test_native_retrieved_audio_is_validated_before_inference(self):
        self.realtime.audio = "not-base64"
        await self.start()
        self.realtime.utterance("bad-audio")
        await until(lambda: self.bridge.lanes["realtime"].status == "failed")
        self.assertFalse(any(event["type"] == "response.create" for event in self.realtime.sent))
        self.assertEqual(self.bridge.lanes["speech"].status, "ready")

    async def test_empty_native_output_is_not_guessed_or_sent_to_luna(self):
        self.realtime.result = {"heard_text": "", "formatted_text": ""}
        await self.start()
        self.realtime.utterance("empty")
        await until(lambda: any(event.get("status") == "skipped" for event in self.browser.events))
        self.assertFalse(any(url.endswith("/responses") for url, _ in self.http.requests))
        self.assertFalse(any(event.get("status") == "done" for event in self.browser.events))

    async def test_speech_without_a_code_preserves_raw_text_and_marks_processing_skipped(self):
        await self.start()
        self.voice[0].formatted_result = ""
        lane = self.bridge.lanes["speech"]
        lane.pending["greeting"] = Pending(1, committed=True, text="你好，今天心情很好")
        await self.bridge.finish(lane, "greeting")
        await until(lambda: any(event.get("status") == "skipped" for event in self.browser.events))
        raw = next(event for event in self.browser.events if event["type"] == "compare.transcript" and event.get("final"))
        self.assertEqual(raw["text"], "你好，今天心情很好")
        result = next(event for event in self.browser.events if event.get("status") == "skipped")
        self.assertEqual(result["text"], "")
        self.assertIn("沒有可明確擷取的代碼", result["message"])
        self.assertEqual(lane.status, "ready")

    async def test_native_heard_text_without_a_code_is_not_a_provider_failure(self):
        self.realtime.result = {"heard_text": "你好，今天心情很好", "formatted_text": ""}
        await self.start()
        self.realtime.utterance("no-code")
        await until(lambda: any(event.get("status") == "skipped" for event in self.browser.events))
        raw = next(event for event in self.browser.events if event["type"] == "compare.transcript" and event.get("final"))
        self.assertEqual(raw["text"], "你好，今天心情很好")
        result = next(event for event in self.browser.events if event.get("status") == "skipped")
        self.assertEqual(result["text"], "")
        self.assertIn("沒有可明確擷取的代碼", result["message"])
        await until(lambda: any(event["type"] == "conversation.item.delete" for event in self.realtime.sent))
        self.assertEqual(self.bridge.lanes["realtime"].status, "ready")

    async def test_invalid_native_structured_output_fails_only_native_lane(self):
        self.realtime.result = {"heard_text": "", "formatted_text": "INVENTED123"}
        await self.start()
        self.realtime.utterance("bad")
        await until(lambda: self.bridge.lanes["realtime"].status == "failed")
        self.assertEqual(self.bridge.lanes["speech"].status, "ready")
        self.assertFalse(any(event.get("status") == "done" for event in self.browser.events))

    async def test_custom_prompt_is_validated_frozen_and_applied_to_each_model(self):
        prompts = {key: value + "\nTEST_" + key for key, value in DEFAULT_PROMPTS.items()}
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(json.dumps({
            "type": "compare.start", "consent": True, "sdp": SDP, "instructions": prompts,
        }))
        await until(lambda: self.bridge.lanes["live"].provider is not None)
        self.browser.incoming.put_nowait(json.dumps({"type": "compare.live.connected"}))
        await until(lambda: self.bridge.lanes["realtime"].status == "ready" and self.bridge.lanes["speech"].status == "ready")
        native = next(event["session"] for event in self.realtime.sent if event["type"] == "session.update")
        self.assertIn("TEST_realtime", native["instructions"])
        await self.bridge.process_text("C I 六十三", 1)
        body = next(event["response"] for event in self.voice[0].sent if event["type"] == "response.create")
        self.assertIn("TEST_speech", body["instructions"])
        receipt = next(event for event in self.browser.events if event["type"] == "compare.instructions")
        self.assertEqual(set(receipt["fingerprints"]), set(DEFAULT_PROMPTS))

    async def test_oversized_prompt_starts_no_provider(self):
        self.running = asyncio.create_task(self.bridge.run())
        self.browser.incoming.put_nowait(json.dumps({
            "type": "compare.start", "consent": True, "sdp": SDP,
            "instructions": {**DEFAULT_PROMPTS, "speech": "x" * 4001},
        }))
        await self.running
        self.assertTrue(any(event.get("code") == "invalid_agent_instructions" for event in self.browser.events))
        self.assertEqual(self.voice, [])
        self.assertEqual(self.realtime.sent, [])


class DisplayFormattingTests(unittest.TestCase):
    def test_number_and_code_display_does_not_invent_or_repair_letters(self):
        for raw, expected in (
            ("XQV R九P", "XQVR9P"), ("A, B, C, E, 二, 三, 四, 五, 六, 七", "ABCE234567"),
            ("CI 六十三", "CI63"), ("CI sixty three", "CI63"), ("十二", "12"), ("二十", "20"),
            ("零零一二三", "00123"), ("x q v r nine p", "XQVR9P"), ("你好，今天下午三點", "你好，今天下午3點"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(format_spoken_codes(raw), expected)

    def test_cardinal_split_fragments_and_out_of_order_rows(self):
        rows = native_rows([
            {"delta": "三", "start_ms": 2200, "end_ms": 2400},
            {"delta": "XQVR九P", "start_ms": 0, "end_ms": 200},
            {"delta": "CI 六十", "start_ms": 2000, "end_ms": 2200},
        ])
        self.assertEqual([row["text"] for row in rows], ["XQVR9P", "CI63"])
        self.assertEqual(rows[1]["raw"], "CI 六十三")
