"""Small GStreamer helpers shared by workers that bridge hardware and SHM."""

from converter.node_sdk.worker import WorkerDiagnostic


def make(Gst, factory, **properties):
    """Create an element and set its properties before any pipeline transition."""
    element = Gst.ElementFactory.make(factory, None)
    if element is None:
        raise WorkerDiagnostic("dependency_unavailable", f"GStreamer element {factory} is unavailable.")
    for name, value in properties.items():
        element.set_property(name.replace("_", "-"), value)
    return element


def set_enum(element, name, nick):
    """Set a GStreamer enum by validated nick, including nicks with punctuation."""
    from gi.repository import Gst

    if element.find_property(name) is None:
        raise WorkerDiagnostic("dependency_unavailable", f"GStreamer element lacks property {name}.")
    Gst.util_set_object_arg(element, name, nick)
    if element.get_property(name).value_nick != nick:
        raise WorkerDiagnostic("unsupported_format", f"GStreamer rejected the value of {name}.")


def link_chain(pipeline, *elements):
    """Add and link one branch without letting GStreamer silently skip a link."""
    for element in elements:
        pipeline.add(element)
    for upstream, downstream in zip(elements, elements[1:]):
        if not upstream.link(downstream):
            raise WorkerDiagnostic("unsupported_format", f"Cannot link {upstream.get_factory().get_name()} to {downstream.get_factory().get_name()}.")


def play(Gst, pipeline):
    """Request live playback or fail immediately on a rejected state change."""
    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        check_bus(Gst, pipeline)
        raise WorkerDiagnostic("pipeline_error", "GStreamer could not switch the media pipeline to PLAYING.")


def check_bus(Gst, pipeline):
    """Handle latency updates and surface terminal messages without logging debug paths."""
    mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.LATENCY
    bus = pipeline.get_bus()
    while (message := bus.pop_filtered(mask)) is not None:
        if message.type == Gst.MessageType.LATENCY:
            pipeline.recalculate_latency()
        elif message.type == Gst.MessageType.ERROR:
            error, _ = message.parse_error()
            factory = message.src.get_factory() if isinstance(message.src, Gst.Element) else None
            name = factory.get_name() if factory is not None else "pipeline"
            raise WorkerDiagnostic("pipeline_error", f"GStreamer {name}: {error.domain}/{error.code}.")
        else:
            raise WorkerDiagnostic("pipeline_error", "GStreamer ended the live media stream.")


def decklink_id(context, direction):
    """Resolve the numeric GStreamer identity from this worker's DeckLink lease."""
    for lease in context.leases:
        if lease["provider"] == "decklink" and lease["direction"] == direction:
            return int(lease["resource_id"], 10)
    raise WorkerDiagnostic("hardware_unavailable", "No DeckLink lease was assigned to this worker.")
