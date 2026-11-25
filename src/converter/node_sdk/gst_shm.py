"""GStreamer boundary for canonical raw buffers and normalized TIME segments."""

import re

from converter.config.validation import ConfigError
from converter.node_sdk.shm import (
    DISCONT, GAP, INTERLACED, ONE_FIELD, REPEAT_FIRST_FIELD, TOP_FIELD_FIRST, MediaFrame,
)


def gst():
    """Import GI only in a worker that uses negotiated media."""
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    return Gst


def describe_caps(caps, endpoint_id, *, video):
    """Describe fixed caps without substituting defaults for absent signal metadata."""
    if caps.get_size() != 1 or not caps.is_fixed():
        raise ConfigError("unsupported_format", "Expected fixed caps from a negotiated sample.")
    structure = caps.get_structure(0)
    description = {
        "stream_id": endpoint_id, "endpoint_id": endpoint_id,
        "caps": caps.to_string(), "format": structure.get_string("format"),
    }
    if video:
        description.update(
            width=structure.get_int("width")[1], height=structure.get_int("height")[1],
            framerate=_fraction(structure, "framerate", "fps"),
            pixel_aspect_ratio=_fraction(structure, "pixel-aspect-ratio", "par"),
            interlace_mode=structure.get_string("interlace-mode"),
            field_order=structure.get_string("field-order"),
            colorimetry=structure.get_string("colorimetry"),
            hdr_metadata=None,
        )
    else:
        mask = re.search(r"(?:,\s*)channel-mask=\(bitmask\)(0x[0-9a-fA-F]+)", description["caps"])
        description.update(
            sample_rate=structure.get_int("rate")[1], channels=structure.get_int("channels")[1],
            layout=structure.get_string("layout"), channel_labels=None,
            channel_mask=mask[1] if mask else None,
        )
    return description


def _fraction(structure, field, prefix):
    """Keep the exact numerator and denominator when caps supply a positive fraction."""
    present, numerator, denominator = structure.get_fraction(field)
    return {f"{prefix}_num": numerator, f"{prefix}_den": denominator} if present and numerator > 0 else None


def video_flag_map():
    """Map GStreamer field flags to their dedicated SHM bits."""
    import gi

    gi.require_version("GstVideo", "1.0")
    from gi.repository import GstVideo

    flags = GstVideo.VideoBufferFlags
    return (
        (int(flags.INTERLACED), INTERLACED), (int(flags.TFF), TOP_FIELD_FIRST),
        (int(flags.RFF), REPEAT_FIRST_FIELD), (int(flags.ONEFIELD), ONE_FIELD),
    )


def validate_caps(video, audio):
    """Compare fixed raw caps and derive canonical video sizes and PCM alignment."""
    Gst = gst()
    import gi

    gi.require_version("GstVideo", "1.0")
    gi.require_version("GstAudio", "1.0")
    from gi.repository import GstAudio, GstVideo

    layouts = {}
    streams = ([(video, "video/x-raw")] if video else []) + [(item, "audio/x-raw") for item in audio]
    for stream, kind in streams:
        caps = Gst.Caps.from_string(stream["caps"])
        if caps is None or caps.get_size() != 1 or not caps.is_fixed():
            raise ConfigError("unsupported_format", "SHM requires one fixed raw caps structure per stream.")
        features = caps.get_features(0)
        if features.is_any() or any(features.get_nth(index) not in {"memory:SystemMemory", "format:Interlaced"} for index in range(features.get_size())):
            raise ConfigError("unsupported_format", "SHM v1 requires linear system-memory caps.")
        structure = caps.get_structure(0)
        if structure.get_name() != kind or structure.get_string("format") != stream["format"]:
            raise ConfigError("unsupported_format", "SHM caps media type or format differs from the contract.")
        integers = {"width": "width", "height": "height"} if kind == "video/x-raw" else {"rate": "sample_rate", "channels": "channels"}
        for field, key in integers.items():
            present, value = structure.get_int(field)
            if not present or value != stream[key]:
                raise ConfigError("unsupported_format", f"SHM caps and contract disagree on {field}.")
        if kind == "audio/x-raw":
            if structure.get_string("layout") != "interleaved" or stream["layout"] != "interleaved":
                raise ConfigError("unsupported_format", "SHM v1 audio requires interleaved samples; channel order is preserved.")
            mask = stream["channel_mask"]
            if mask is not None and (not structure.has_field("channel-mask") or not caps.can_intersect(Gst.Caps.from_string(f"audio/x-raw,channel-mask=(bitmask){mask}"))):
                raise ConfigError("unsupported_format", "Audio channel mask differs from caps.")
            info = GstAudio.AudioInfo.new_from_caps(caps)
            if info is None or info.bpf <= 0:
                raise ConfigError("unsupported_format", "Invalid PCM frame layout.")
            layouts[stream["endpoint_id"]] = (False, info.bpf)
            continue
        for field, key in (("interlace-mode", "interlace_mode"), ("field-order", "field_order"), ("colorimetry", "colorimetry")):
            if structure.get_string(field) != stream[key]:
                raise ConfigError("unsupported_format", f"SHM caps and contract disagree on {field}.")
        for field, key, prefix in (("framerate", "framerate", "fps"), ("pixel-aspect-ratio", "pixel_aspect_ratio", "par")):
            present, numerator, denominator = structure.get_fraction(field)
            fraction = stream[key]
            if fraction is None:
                if present and numerator:
                    raise ConfigError("unsupported_format", f"SHM contract omits the negotiated {field}.")
            elif not present or numerator * fraction[f"{prefix}_den"] != denominator * fraction[f"{prefix}_num"]:
                raise ConfigError("unsupported_format", f"SHM caps and contract disagree on {field}.")
        info = GstVideo.VideoInfo.new_from_caps(caps)
        if info is None or info.size <= 0:
            raise ConfigError("unsupported_format", "Invalid canonical video frame layout.")
        layouts[stream["endpoint_id"]] = (True, info.size)
    return layouts


def from_gst_buffer(Gst, buffer, segment, *, video_flags=0, payload=None):
    """Copy a canonical CPU buffer and convert timestamps through its TIME segment."""
    if segment.format != Gst.Format.TIME or segment.rate != 1.0 or segment.applied_rate != 1.0:
        raise ConfigError("unsupported_format", "SHM v1 requires a forward rate-1 TIME segment.")
    pts = segment.to_running_time(Gst.Format.TIME, buffer.pts) if buffer.pts != Gst.CLOCK_TIME_NONE else Gst.CLOCK_TIME_NONE
    dts = segment.to_running_time(Gst.Format.TIME, buffer.dts) if buffer.dts != Gst.CLOCK_TIME_NONE else Gst.CLOCK_TIME_NONE
    if pts == Gst.CLOCK_TIME_NONE or buffer.duration in {0, Gst.CLOCK_TIME_NONE}:
        raise ConfigError("unsupported_format", "SHM needs a running-time PTS and positive duration.")
    flags = video_flags
    if buffer.has_flags(Gst.BufferFlags.DISCONT):
        flags |= DISCONT
    if buffer.has_flags(Gst.BufferFlags.GAP):
        flags |= GAP
    data = bytes(buffer.extract_dup(0, buffer.get_size())) if payload is None else payload
    return MediaFrame(data, pts, buffer.duration, -1 if dts == Gst.CLOCK_TIME_NONE else dts, flags)


def to_gst_buffer(Gst, frame, *, base=0, video_flags=0):
    """Rebase onto a consumer timeline using the same origin for every stream."""
    if frame.pts < base:
        raise ValueError("Frame predates the consumer timeline origin")
    buffer = Gst.Buffer.new_wrapped(frame.data)
    buffer.pts = frame.pts - base
    buffer.dts = frame.dts - base if frame.dts >= base else Gst.CLOCK_TIME_NONE
    buffer.duration = frame.duration
    flags = video_flags
    if frame.flags & DISCONT:
        flags |= int(Gst.BufferFlags.DISCONT)
    if frame.flags & GAP:
        flags |= int(Gst.BufferFlags.GAP)
    if flags:
        buffer.set_flags(Gst.BufferFlags(flags))
    return buffer
