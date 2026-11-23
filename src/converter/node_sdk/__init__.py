"""Public node interface; importing the SDK never imports GI or another plugin."""

from converter.node_sdk.worker import NodeWorker, WorkerContext

__all__ = ["NodeWorker", "WorkerContext"]
