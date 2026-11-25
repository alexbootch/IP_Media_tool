"""Small GStreamer helpers shared by workers that bridge hardware and SHM."""

from converter.config.validation import ConfigError


def make(Gst, factory, **properties):
    """Create an element and set its properties before any pipeline transition."""
    element = Gst.ElementFactory.make(factory)
    if element is None:
        raise ConfigError("dependency_unavailable", f"GStreamer element {factory} is unavailable.")
    for name, value in properties.items():
        element.set_property(name.replace("_", "-"), value)
    return element


def set_enum(element, name, nick):
    """Set a GStreamer enum by validated nick, including nicks with punctuation."""
    choices = type(element.get_property(name)).__enum_values__.values()
    for choice in choices:
        if choice.value_nick == nick:
            element.set_property(name, choice)
            return
    raise ConfigError("unsupported_format", f"Unsupported GStreamer setting {name}: {nick}.")


def link_chain(pipeline, *elements):
    """Add and link one branch without letting GStreamer silently skip a link."""
    for element in elements:
        pipeline.add(element)
    for upstream, downstream in zip(elements, elements[1:]):
        if not upstream.link(downstream):
            raise ConfigError("unsupported_format", f"Cannot link {upstream.name} to {downstream.name}.")


def play(Gst, pipeline):
    """Request live playback or fail immediately on a rejected state change."""
    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        check_bus(Gst, pipeline)
        raise ConfigError("pipeline_error", "GStreamer could not start the media pipeline.")


def check_bus(Gst, pipeline):
    """Handle latency updates and surface terminal messages without logging debug paths."""
    mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.LATENCY
    bus = pipeline.get_bus()
    while (message := bus.pop_filtered(mask)) is not None:
        if message.type == Gst.MessageType.LATENCY:
            pipeline.recalculate_latency()
        elif message.type == Gst.MessageType.ERROR:
            raise ConfigError("pipeline_error", "A GStreamer element reported a pipeline failure.")
        else:
            raise ConfigError("pipeline_error", "GStreamer ended the live media stream.")


def decklink_id(context, direction):
    """Resolve the numeric GStreamer identity from this worker's DeckLink lease."""
    for lease in context.leases:
        if lease["provider"] == "decklink" and lease["direction"] == direction:
            return int(lease["resource_id"], 10)
    raise ConfigError("hardware_unavailable", "No DeckLink lease was assigned to this worker.")
