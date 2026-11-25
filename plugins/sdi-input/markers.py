"""Measure physical flash/click markers on the capture pipeline's common clock."""

from collections import deque
from struct import unpack_from

from converter.node_sdk import DISCONT


MATCH_NS = 250_000_000
REFRACTORY_NS = 250_000_000


class Markers:
    """Match isolated white flashes with loud pulses on embedded PCM channel one."""

    def __init__(self, video, audio, stride):
        """Set canonical sample offsets and initialize per-generation statistics."""
        self.width, self.height = video["width"], video["height"]
        self.stride, self.format = stride, video["format"]
        self.sample_bytes = 2 if audio["format"] == "S16LE" else 4
        self.sample_stride = self.sample_bytes * audio["channels"]
        self.sample_rate = audio["sample_rate"]
        self.threshold = 1 << (self.sample_bytes * 8 - 3)
        self.unpack_fmt = "<h" if self.sample_bytes == 2 else "<i"
        self.events = {"video": deque(maxlen=4), "audio": deque(maxlen=4)}
        self.last_click = -REFRACTORY_NS
        self.bright = None
        self.metrics = {"av_pairs": 0, "av_sum_ns": 0, "av_min_ns": None, "av_max_ns": None, "av_last_ns": None}

    def observe(self, kind, frame):
        """Extract one marker edge and pair it without comparing independent clocks."""
        if self.format not in {"UYVY", "v210"}:
            return
        if frame.flags & DISCONT:
            for queue in self.events.values():
                queue.clear()
            self.bright = None
            self.last_click = frame.pts
        if kind == "video":
            bright = self.video_is_bright(frame.data)
            marker = frame.pts if bright and self.bright is False else None
            self.bright = bright
        else:
            marker = self.audio_click(frame)
        if marker is not None:
            self.events[kind].append(marker)
            self.match()

    def video_is_bright(self, data):
        """Read the center luma of a packed UYVY or v210 frame."""
        x, y = self.width // 2, self.height // 2
        row = self.stride * y
        if self.format == "UYVY":
            return data[row + x * 2 + 1] >= 128
        group, pixel = divmod(x, 6)
        word, component = divmod(2 * pixel + 1, 3)
        packed = unpack_from("<I", data, row + group * 16 + word * 4)[0]
        return ((packed >> (10 * component)) & 1023) >= 512

    def audio_click(self, frame):
        """Date a threshold crossing at PCM sample precision and suppress repeats."""
        for offset in range(0, len(frame.data), self.sample_stride):
            pts = frame.pts + (offset // self.sample_stride) * 1_000_000_000 // self.sample_rate
            if pts - self.last_click < REFRACTORY_NS:
                continue
            if abs(unpack_from(self.unpack_fmt, frame.data, offset)[0]) >= self.threshold:
                self.last_click = pts
                return pts
        return None

    def match(self):
        """Keep signed audio-minus-video offset statistics for unambiguous pairs."""
        video, audio = self.events["video"], self.events["audio"]
        while video and audio:
            offset = audio[0] - video[0]
            if abs(offset) > MATCH_NS:
                (video if offset > 0 else audio).popleft()
                continue
            video.popleft()
            audio.popleft()
            metrics = self.metrics
            metrics["av_pairs"] += 1
            metrics["av_sum_ns"] += offset
            metrics["av_last_ns"] = offset
            metrics["av_min_ns"] = offset if metrics["av_min_ns"] is None else min(offset, metrics["av_min_ns"])
            metrics["av_max_ns"] = offset if metrics["av_max_ns"] is None else max(offset, metrics["av_max_ns"])
