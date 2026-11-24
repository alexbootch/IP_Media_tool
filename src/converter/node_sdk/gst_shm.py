"""GStreamer boundary for canonical raw buffers and normalized TIME segments."""

from converter.config.validation import ConfigError
from converter.node_sdk.shm import DISCONT, GAP, MediaFrame


def gst():
    """Import GI only in a worker that uses negotiated media."""
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    return Gst


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


def from_gst_buffer(Gst, buffer, segment, *, video_flags=0):
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
    return MediaFrame(bytes(buffer.extract_dup(0, buffer.get_size())), pts, buffer.duration, -1 if dts == Gst.CLOCK_TIME_NONE else dts, flags)


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
