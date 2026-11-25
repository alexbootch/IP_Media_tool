"""Capture the negotiated SDI picture and embedded PCM from one DeckLink port."""

import asyncio
from dataclasses import replace

from converter.node_sdk import ConfigError, DISCONT, NodeWorker
from converter.node_sdk.gst_pipeline import check_bus, decklink_id, link_chain, make, play, set_enum
from converter.node_sdk.gst_shm import describe_caps, from_gst_buffer, gst, validate_caps, video_flag_map
from converter.node_sdk.shm import MAX_BYTES, ring_size
from .markers import Markers


VIDEO_FORMATS = {"UYVY", "v210", "ARGB", "BGRA", "r210"}
AUDIO_SLOT_BYTES = 256 * 1024
SIGNAL_TIMEOUT = 0.75


class Worker(NodeWorker):
    """Publish a new pair of SHM rings whenever the SDI signal or format changes."""

    async def start(self):
        """Start both capture branches on the same live GStreamer pipeline."""
        self.task = self.generation = None
        self.markers = None
        self.Gst = gst()
        self.flags = video_flag_map()
        from gi.repository import GstVideo

        self.GstVideo = GstVideo
        self.caps = {}
        self.frames = {}
        self.last_sample = {}
        self.last_timing = {}
        self.discontinuous = set()
        self.metrics = {"video_frames": 0, "audio_buffers": 0, "shm_drops": 0,
                        "capture_discontinuities": 0, "signal_losses": 0}
        self.pipeline = self.Gst.Pipeline.new("sdi-capture")
        persistent_id = decklink_id(self.context, "input")
        settings = self.context.config["settings"]
        self.measure_av = settings.get("measure_av", False)
        video = make(self.Gst, "decklinkvideosrc", persistent_id=persistent_id,
                     drop_no_signal_frames=True, output_stream_time=False)
        set_enum(video, "connection", "sdi")
        set_enum(video, "mode", "auto")
        set_enum(video, "video-format", "auto")
        audio = make(self.Gst, "decklinkaudiosrc", persistent_id=persistent_id)
        set_enum(audio, "connection", "embedded")
        set_enum(audio, "channels", str(settings["audio_channels"]))
        # Preserve the captured PCM width selected before negotiation.
        audio_caps = make(self.Gst, "capsfilter", caps=self.Gst.Caps.from_string(
            "audio/x-raw,format=" + settings.get("audio_format", "S32LE")))
        self.sinks = {
            "video": make(self.Gst, "appsink", sync=False, max_buffers=4, drop=True, wait_on_eos=False),
            "audio": make(self.Gst, "appsink", sync=False, max_buffers=16, drop=True, wait_on_eos=False),
        }
        link_chain(self.pipeline, video, self.sinks["video"])
        link_chain(self.pipeline, audio, audio_caps, self.sinks["audio"])
        play(self.Gst, self.pipeline)
        await self.context.set_state("waiting_signal")
        self.task = asyncio.create_task(self.consume())

    async def consume(self):
        """Drain a bounded number of samples per tick and withdraw stale contracts."""
        loop = asyncio.get_running_loop()
        while True:
            check_bus(self.Gst, self.pipeline)
            now = loop.time()
            if self.generation and any(now - self.last_sample[endpoint] > SIGNAL_TIMEOUT for endpoint in self.sinks):
                self.metrics["signal_losses"] += 1
                await self.reset()
            # Service PCM on every video poll, even during a burst or renegotiation.
            for _ in range(4):
                received = False
                for endpoint, sink in self.sinks.items():
                    sample = sink.emit("try-pull-sample", 0)
                    if sample is not None:
                        await self.ingest(endpoint, sample, now)
                        received = True
                if not received:
                    break
            await asyncio.sleep(0.002)

    async def ingest(self, endpoint, sample, now):
        """Normalize one sample and establish a generation from a coherent A/V pair."""
        buffer = sample.get_buffer()
        if buffer.has_flags(self.Gst.BufferFlags.GAP):
            return
        caps = sample.get_caps()
        previous = self.caps.get(endpoint)
        if previous is not None and not previous.is_equal(caps):
            await self.reset()
        self.caps[endpoint] = caps
        self.last_sample[endpoint] = now
        payload, flags = None, 0
        if endpoint == "video":
            if previous is None or not previous.is_equal(caps):
                self.video_info = self.GstVideo.VideoInfo.new_from_caps(caps)
                if self.video_info is None or caps.get_structure(0).get_string("format") not in VIDEO_FORMATS:
                    raise ConfigError("unsupported_format", "Unsupported packed DeckLink capture format.")
            payload = self.video_payload(buffer)
            native = int(buffer.get_flags())
            flags = sum(shared for bit, shared in self.flags if native & bit)
        frame = from_gst_buffer(self.Gst, buffer, sample.get_segment(), video_flags=flags, payload=payload)
        previous_timing = self.last_timing.get(endpoint)
        if previous_timing is not None:
            previous_pts, previous_duration = previous_timing
            if frame.pts < previous_pts:
                await self.reset()
                return
            tolerance = max(2_000_000, min(10_000_000, previous_duration // 2))
            if frame.flags & DISCONT or frame.pts > previous_pts + previous_duration + tolerance:
                self.metrics["capture_discontinuities"] += 1
                frame = replace(frame, flags=frame.flags | DISCONT)
        self.last_timing[endpoint] = (frame.pts, frame.duration)
        if self.generation is not None:
            self.write(endpoint, frame)
            return
        self.frames[endpoint] = frame
        if len(self.frames) != len(self.sinks):
            return
        if max(self.last_sample.values()) - min(self.last_sample.values()) > SIGNAL_TIMEOUT:
            return
        if abs(self.frames["video"].pts - self.frames["audio"].pts) > 250_000_000:
            return
        await self.open_generation()

    def video_payload(self, buffer):
        """Strip device-specific row padding only when the packed layout differs."""
        info = self.video_info
        meta = self.GstVideo.buffer_get_video_meta(buffer)
        if meta is None:
            if buffer.get_size() != info.size:
                raise ConfigError("unsupported_format", "DeckLink frame has no canonical layout or stride metadata.")
            return None
        stride, offset = meta.stride[0], meta.offset[0]
        target_stride = info.stride[0]
        if stride == target_stride and offset == 0 and buffer.get_size() == info.size:
            return None
        # v210 packs six pixels in 16 bytes and a canonical row can include padding.
        name = info.finfo.name
        row_bytes = ((info.width + 5) // 6) * 16 if name == "v210" else info.width * (2 if name == "UYVY" else 4)
        if (meta.n_planes != 1 or offset < 0 or min(stride, target_stride) < row_bytes
                or offset + stride * (info.height - 1) + row_bytes > buffer.get_size()
                or target_stride * info.height > info.size):
            raise ConfigError("unsupported_format", "Unsupported DeckLink frame stride.")
        image = bytearray(info.size)
        with buffer.map(self.Gst.MapFlags.READ) as mapping:
            for row in range(info.height):
                start, target = offset + row * stride, row * target_stride
                image[target:target + row_bytes] = mapping.data[start:start + row_bytes]
        return bytes(image)

    async def open_generation(self):
        """Write the first pair before advertising the new, bounded SHM allocation."""
        video = describe_caps(self.caps["video"], "video", video=True)
        audio = describe_caps(self.caps["audio"], "audio", video=False)
        size = validate_caps(video, [audio])["video"][1]
        audio_dimensions = (16, AUDIO_SLOT_BYTES)
        video_slots = min(8, (MAX_BYTES - ring_size(*audio_dimensions) - 64) // (size + 64))
        self.generation = self.context.create_shm_generation(
            "out", video=video, audio=[audio], buffers={"video": (video_slots, size), "audio": audio_dimensions})
        for endpoint, frame in self.frames.items():
            self.write(endpoint, frame)
        self.frames.clear()
        if self.measure_av:
            self.markers = Markers(video, audio, self.video_info.stride[0])
        await self.generation.publish()
        self.metrics.update(generation=self.generation.generation, video_caps=video["caps"], audio_caps=audio["caps"])
        await self.context.set_state("running")

    def write(self, endpoint, frame):
        """Count nonblocking SHM drops and mark the next committed sample discontinuous."""
        if endpoint in self.discontinuous:
            frame = replace(frame, flags=frame.flags | DISCONT)
        if self.generation.write(endpoint, frame):
            self.discontinuous.discard(endpoint)
            counter = "video_frames" if endpoint == "video" else "audio_buffers"
            self.metrics[counter] += 1
            if self.markers is not None:
                self.markers.observe(endpoint, frame)
        else:
            self.discontinuous.add(endpoint)
            self.metrics["shm_drops"] += 1

    async def reset(self):
        """Revoke the current generation and discard cached samples from the old signal."""
        if self.generation is not None:
            await self.generation.close()
            self.generation = None
        self.caps.clear()
        self.frames.clear()
        self.last_sample.clear()
        self.last_timing.clear()
        self.markers = None
        self.discontinuous = set(self.sinks)
        for key in ("generation", "video_caps", "audio_caps"):
            self.metrics.pop(key, None)
        if self.context.status_snapshot()["state"] != "waiting_signal":
            await self.context.set_state("waiting_signal")

    async def get_status(self):
        """Report current source caps, progression and bounded-buffer losses."""
        if getattr(self, "task", None) is not None and self.task.done():
            self.task.result()
        status = await super().get_status()
        status["metrics"] = dict(getattr(self, "metrics", {}))
        if getattr(self, "markers", None) is not None:
            status["metrics"].update(self.markers.metrics)
        return status

    async def stop(self):
        """Stop capture before retiring only this instance's resources."""
        if getattr(self, "task", None) is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if getattr(self, "pipeline", None) is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None
        if getattr(self, "generation", None) is not None:
            await self.generation.close()
            self.generation = None
