"""Play one raw A/V contract as SDI picture and embedded PCM on DeckLink."""

import asyncio
from dataclasses import replace
from fractions import Fraction
import re

from converter.node_sdk import ConfigError, DISCONT, NodeWorker
from converter.node_sdk.gst_pipeline import check_bus, decklink_id, link_chain, make, play, set_enum
from converter.node_sdk.gst_shm import describe_caps, gst, to_gst_buffer, video_flag_map


PIXEL_FORMATS = {"UYVY": "8bit-yuv", "v210": "10bit-yuv"}
FRACTIONAL_RATES = {"2398": Fraction(24000, 1001), "2997": Fraction(30000, 1001),
                    "4795": Fraction(48000, 1001), "5994": Fraction(60000, 1001),
                    "9590": Fraction(96000, 1001), "11988": Fraction(120000, 1001)}
SD_RATES = {"ntsc": Fraction(30000, 1001), "ntsc2398": Fraction(24000, 1001),
            "ntsc-p": Fraction(30000, 1001), "pal": Fraction(25), "pal-p": Fraction(25)}
MAX_START_SKEW = 250_000_000


def mode_rate(mode):
    """Translate the DeckLink mode nickname into its exact picture cadence."""
    base = mode.removesuffix("-widescreen")
    if base in SD_RATES:
        return SD_RATES[base]
    match = re.fullmatch(r"[0-9]+(?:dci)?([pi])([0-9]+)", base)
    if not match:
        raise ConfigError("unsupported_format", "The DeckLink mode has no known picture cadence.")
    cadence = FRACTIONAL_RATES.get(match[2], Fraction(int(match[2])))
    return cadence / 2 if match[1] == "i" else cadence


class Worker(NodeWorker):
    """Use one pipeline clock and one SHM-to-playout timestamp transform for both streams."""

    async def start(self):
        """Hold the reserved device identity and wait for the configured input contract."""
        self.pipeline = self.reader = self.task = None
        self.Gst = gst()
        self.flags = video_flag_map()
        self.persistent_id = decklink_id(self.context, "output")
        self.settings = self.context.config["settings"]
        self.preroll = self.settings.get("playout_delay_ms", 100) * self.Gst.MSECOND
        self.metrics = {"video_buffers": 0, "audio_buffers": 0, "late_drops": 0,
                        "shm_skipped": 0, "startup_drops": 0}
        await self.context.set_state("waiting_source")

    async def on_contract(self, local_port_id, contract):
        """Tear down both branches at a generation boundary before binding a new reader."""
        await self.stop()
        for key in ("generation", "video_caps", "audio_caps"):
            self.metrics.pop(key, None)
        if self.context.status_snapshot()["state"] != "waiting_source":
            await self.context.set_state("waiting_source")
        if contract is not None:
            self.reader = self.context.open_shm(contract)
            self.metrics["generation"] = contract["transport_generation"]
            self.task = asyncio.create_task(self.consume(contract))

    def target_caps(self, sink):
        """Query dimensions, PAR, scan and pixel format from the configured GStreamer sink."""
        Gst = self.Gst
        mode = self.settings["mode"]
        set_enum(sink, "mode", mode)
        set_enum(sink, "video-format", PIXEL_FORMATS[self.settings.get("video_format", "UYVY")])
        rate = mode_rate(mode)
        cadence = Gst.Caps.from_string(f"video/x-raw,framerate={rate.numerator}/{rate.denominator}")
        target = sink.get_static_pad("sink").query_caps(cadence)
        if target.get_size() != 1 or not target.is_fixed():
            raise ConfigError("unsupported_format", "The DeckLink sink did not expose fixed output caps.")
        return target

    def video_chain(self, source, target_caps):
        """Convert only video, preserving same-mode interlaced frames and field flags."""
        Gst = self.Gst
        target = describe_caps(target_caps, "video", video=True)
        source_scan, target_scan = source["interlace_mode"], target["interlace_mode"]
        if source_scan not in {None, "progressive", "interleaved"}:
            raise ConfigError("unsupported_format", "SDI output requires progressive or interleaved raw frames.")
        same_raster = all(source[key] == target[key] for key in (
            "width", "height", "framerate", "pixel_aspect_ratio", "interlace_mode"))
        # The DeckLink mode's field order is implicit in sink caps.
        top_first = not self.settings["mode"].startswith("ntsc")
        order = "top-field-first" if top_first else "bottom-field-first"
        same_order = source["field_order"] in {None, order}
        convert = make(Gst, "videoconvert")
        final = make(Gst, "capsfilter", caps=target_caps)
        if same_raster and same_order:
            return [convert, final]
        elements = []
        if source_scan == "interleaved":
            deinterlace = make(Gst, "deinterlace")
            set_enum(deinterlace, "fields", "all")
            # The deinterlacer processes planar 10-bit data but cannot unpack v210.
            if source["format"] == "v210":
                elements.extend([make(Gst, "videoconvert"), make(
                    Gst, "capsfilter", caps=Gst.Caps.from_string("video/x-raw,format=I422_10LE"))])
            elements.append(deinterlace)
        elements.extend([convert, make(Gst, "videoscale", add_borders=True), make(Gst, "videorate")])
        if target_scan == "interleaved":
            rate = target["framerate"]
            par = target["pixel_aspect_ratio"]
            fields = Fraction(2 * rate["fps_num"], rate["fps_den"])
            progressive = Gst.Caps.from_string(
                f"video/x-raw,width={target['width']},height={target['height']},"
                f"framerate={fields.numerator}/{fields.denominator},interlace-mode=progressive,"
                f"pixel-aspect-ratio={par['par_num']}/{par['par_den']},format={target['format']}")
            interlace = make(Gst, "interlace", top_field_first=top_first)
            set_enum(interlace, "field-pattern", "1:1")
            elements.extend([make(Gst, "capsfilter", caps=progressive), interlace])
        elements.append(final)
        return elements

    def build_pipeline(self, contract):
        """Validate embedded PCM without a resampler or mixer, then assemble both branches."""
        Gst = self.Gst
        video, tracks = contract["video"], contract["audio"]
        if video is None or len(tracks) != 1:
            raise ConfigError("unsupported_format", "SDI output requires video and one interleaved PCM bank.")
        audio = tracks[0]
        if (audio["channels"] not in {2, 8, 16} or audio["sample_rate"] != 48000
                or audio["format"] not in {"S16LE", "S32LE"} or audio["layout"] != "interleaved"):
            raise ConfigError("unsupported_format", "DeckLink requires 48 kHz interleaved PCM with 2, 8 or 16 channels.")
        self.pipeline = Gst.Pipeline.new("sdi-playout")
        # The DeckLink clock stands still until both sinks enable hardware playback.
        # Its sinks map the shared pipeline clock to hardware as playback starts.
        self.pipeline.use_clock(Gst.SystemClock.obtain())
        video_sink = make(Gst, "decklinkvideosink", persistent_id=self.persistent_id, **{"async": False})
        audio_sink = make(Gst, "decklinkaudiosink", persistent_id=self.persistent_id, **{"async": False})
        # The live appsrc branches need the pipeline clock before submitting their first PTS.
        target = self.target_caps(video_sink)
        sources = {}
        for kind, description, sink, conversion in (
            ("video", video, video_sink, self.video_chain(video, target)),
            ("audio", audio, audio_sink, []),
        ):
            maximum = 8 if kind == "video" else 16
            app = make(Gst, "appsrc", caps=Gst.Caps.from_string(description["caps"]),
                       is_live=True, format=Gst.Format.TIME, do_timestamp=False, block=False,
                       max_bytes=0, max_buffers=maximum)
            link_chain(self.pipeline, app, *conversion, sink)
            sources[kind] = (description["endpoint_id"], app, maximum)
        self.sinks = (video_sink, audio_sink)
        play(Gst, self.pipeline)
        return sources

    def running_time(self):
        """Read the consumer running time once both GStreamer clock values exist."""
        clock = self.pipeline.get_clock()
        base = self.pipeline.get_base_time()
        if clock is None or base in {0, self.Gst.CLOCK_TIME_NONE}:
            return None
        return max(0, clock.get_time() - base)

    async def consume(self, contract):
        """Read both endpoints fairly and retain one play-out epoch for their relative PTS."""
        Gst = self.Gst
        sources = self.build_pipeline(contract)
        pending, last_pts, submitted = {}, {}, {}
        discontinuous = set(sources)
        origin = anchor = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while True:
            check_bus(Gst, self.pipeline)
            now = self.running_time()
            for kind, (endpoint, app, maximum) in sources.items():
                if kind not in pending and app.get_property("current-level-buffers") < maximum:
                    frame = self.reader.read(endpoint, newest=kind not in submitted)
                    if frame is not None:
                        counter = "shm_skipped" if kind in submitted else "startup_drops"
                        self.metrics[counter] += frame.skipped
                        pending[kind] = frame
            if origin is None and len(pending) == len(sources) and now is not None:
                first = min(pending, key=lambda kind: pending[kind].pts)
                minimum = pending[first].pts
                if max(frame.pts for frame in pending.values()) - minimum > MAX_START_SKEW:
                    pending.pop(first)
                    self.metrics["startup_drops"] += 1
                else:
                    origin, anchor = minimum, now + self.preroll
            if origin is not None and now is not None:
                for kind, frame in list(pending.items()):
                    # Pull history at line rate once caught up, without queuing far-future frames.
                    scheduled = anchor + frame.pts - origin
                    if scheduled > now + self.preroll + 75 * Gst.MSECOND:
                        continue
                    pending.pop(kind)
                    if scheduled < now - 10 * Gst.MSECOND or frame.pts < origin or frame.pts <= last_pts.get(kind, -1):
                        self.metrics["late_drops"] += 1
                        discontinuous.add(kind)
                        continue
                    if kind in discontinuous:
                        frame = replace(frame, flags=frame.flags | DISCONT)
                    app = sources[kind][1]
                    self.push(kind, app, frame, origin, anchor)
                    last_pts[kind] = frame.pts
                    discontinuous.discard(kind)
                    submitted[kind] = loop.time()
            # appsrc accepts buffers before the asynchronous sink caps negotiation finishes.
            video_caps, audio_caps = (sink.get_static_pad("sink").get_current_caps() for sink in self.sinks)
            ready = len(submitted) == len(sources) and video_caps is not None and audio_caps is not None
            if ready and self.context.status_snapshot()["state"] != "running":
                self.metrics.update(video_caps=video_caps.to_string(), audio_caps=audio_caps.to_string())
                await self.context.set_state("running")
            elif not ready and loop.time() > deadline:
                raise ConfigError("timeout", "SDI playback did not receive negotiated audio and video.")
            elif ready and loop.time() - min(submitted.values()) > 2:
                raise ConfigError("timeout", "SDI playback could not maintain its shared A/V timeline.")
            await asyncio.sleep(0.002)

    def push(self, kind, app, frame, origin, anchor):
        """Translate both timestamps by the same offset and submit one canonical sample."""
        Gst = self.Gst
        native = sum(bit for bit, shared in self.flags if frame.flags & shared) if kind == "video" else 0
        buffer = to_gst_buffer(Gst, frame, base=origin, video_flags=native)
        buffer.pts += anchor
        if buffer.dts != Gst.CLOCK_TIME_NONE:
            buffer.dts += anchor
        if app.emit("push-buffer", buffer) != Gst.FlowReturn.OK:
            raise ConfigError("pipeline_error", "DeckLink appsrc stopped accepting frames.")
        self.metrics[kind + "_buffers"] += 1

    async def get_status(self):
        """Report negotiated playback settings and loss counters through the SDK heartbeat."""
        if getattr(self, "task", None) is not None and self.task.done():
            self.task.result()
        status = await super().get_status()
        status["metrics"] = dict(getattr(self, "metrics", {}))
        return status

    async def stop(self):
        """Cancel the reader task before releasing its pipeline and independent cursors."""
        if getattr(self, "task", None) is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        try:
            if getattr(self, "pipeline", None) is not None:
                self.pipeline.set_state(self.Gst.State.NULL)
                self.pipeline = None
        finally:
            if getattr(self, "reader", None) is not None:
                self.reader.close()
                self.reader = None
