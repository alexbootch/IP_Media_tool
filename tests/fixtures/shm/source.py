"""Linux acceptance source: synchronized raw video plus two PCM channels."""

import asyncio

from converter.node_sdk import NodeWorker
from converter.node_sdk.gst_shm import from_gst_buffer, gst


VIDEO = {"stream_id": "v", "endpoint_id": "v", "caps": "video/x-raw,format=RGBx,width=16,height=16,framerate=25/1,pixel-aspect-ratio=1/1,interlace-mode=progressive", "width": 16, "height": 16, "format": "RGBx", "framerate": {"fps_num": 25, "fps_den": 1}, "pixel_aspect_ratio": {"par_num": 1, "par_den": 1}, "interlace_mode": "progressive", "field_order": None, "colorimetry": None, "hdr_metadata": None}
AUDIO = {"stream_id": "a", "endpoint_id": "a", "caps": "audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved,channel-mask=(bitmask)0x0000000000000003", "format": "S16LE", "sample_rate": 48000, "channels": 2, "layout": "interleaved", "channel_labels": ["left", "right"], "channel_mask": "0x3"}
PERIOD = 40_000_000
SEGMENT_START = 1_000_000_000


class Worker(NodeWorker):
    """Publish two rings from one running-time domain."""

    async def start(self):
        """Register fixed caps, publish the rings and start the synthetic clock."""
        self.count = 0
        self.gst = gst()
        self.segment = self.gst.Segment.new()
        self.segment.init(self.gst.Format.TIME)
        self.segment.start = SEGMENT_START
        self.generation = self.context.create_shm_generation("out", video=VIDEO, audio=[AUDIO], buffers={"v": (4, 1024), "a": (4, 7680)})
        await self.generation.publish()
        await self.context.set_state("running")
        self.task = asyncio.create_task(self.produce())

    async def produce(self):
        """Vary the payload at each 40 ms boundary so readers can detect corruption."""
        start = asyncio.get_running_loop().time()
        while True:
            n = self.count
            marker = (n % 100) + 1
            self.write_sample("v", bytes((marker, 0, 0, 0)) * 256, n)
            self.write_sample("a", bytes((marker, 0, 255 - marker, 0)) * 1920, n)
            self.count += 1
            await asyncio.sleep(max(0, start + self.count * 0.04 - asyncio.get_running_loop().time()))

    def write_sample(self, endpoint, data, n):
        """Pass a GStreamer buffer with nonzero segment start through the SDK bridge."""
        buffer = self.gst.Buffer.new_wrapped(data)
        buffer.pts = SEGMENT_START + n * PERIOD
        buffer.duration = PERIOD
        self.generation.write(endpoint, from_gst_buffer(self.gst, buffer, self.segment))

    async def get_status(self):
        """Report progression and the generation ID without opening another ring."""
        status = await super().get_status()
        if hasattr(self, "generation"):
            status["metrics"] = {"frames": self.count, "generation": self.generation.generation}
        return status

    async def stop(self):
        """Stop production before releasing this worker's generation."""
        if hasattr(self, "task"):
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if hasattr(self, "generation"):
            await self.generation.close()
