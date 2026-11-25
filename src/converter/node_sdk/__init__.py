"""Public node interface; importing the SDK never imports GI or another plugin."""

from converter.config.validation import ConfigError
from converter.node_sdk.worker import NodeWorker, WorkerContext
from converter.node_sdk.shm import MediaFrame, DISCONT

__all__ = ["ConfigError", "NodeWorker", "WorkerContext", "MediaFrame", "DISCONT"]
