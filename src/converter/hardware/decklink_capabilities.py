"""One startup scan and explicit refreshes; never periodically poll DeckLink."""

import json
import logging

from converter.hardware.encoder_capabilities import CACHE_SCHEMA, MAX_OUTPUT_BYTES, EncoderCapabilityManager


LOGGER = logging.getLogger("ip-media-tool.decklink-capabilities")


class DeckLinkCapabilityManager(EncoderCapabilityManager):
    # Reuse the bounded child process, single-flight lock, shutdown and atomic cache writer.
    probe_module = "converter.hardware.decklink.probe"
    probe_label = "DeckLink"
    items_key = "ports"
    max_items = 1024

    def __init__(self, cache_path):
        super().__init__(cache_path)
        self._state.pop("adapters")
        self._state.update(ports=[], boards=[], driver={}, scan_errors=[], query_status="pending", plugin_version=None)

    def _load_cache(self):
        try:
            if self.cache_path.stat().st_size > MAX_OUTPUT_BYTES:
                return None
            with self.cache_path.open(encoding="utf-8") as source:
                payload = json.load(source)
            state = payload.get("state")
            if payload.get("cache_schema") != CACHE_SCHEMA or not isinstance(state, dict):
                return None
            if not isinstance(state.get("ports"), list) or len(state["ports"]) > self.max_items or not isinstance(state.get("boards"), list) or not isinstance(state.get("driver"), dict):
                return None
            return state
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError):
            LOGGER.warning("DeckLink cache is unavailable or invalid; a fresh scan will run.", exc_info=True)
            return None

    def _run(self, force):
        try:
            if not force:
                cached = self._load_cache()
                if cached:
                    with self._lock:
                        self._state.update(cached, query_running=True, from_cache=True, stale=True, initialized=True, last_error=None)
            with self._lock:
                if self._state["initialized"]:
                    self._state["stale"] = True
            if self._stop.is_set():
                return
            # PCI/driver versions cannot reveal a profile/duplex change. Always ask
            # the provider once at startup; cached results are only a stale fallback.
            payload = self._probe()
            if self._stop.is_set():
                return
            if not isinstance(payload.get("boards"), list) or not isinstance(payload.get("driver"), dict):
                raise RuntimeError("DeckLink probe returned invalid board inventory")
            with self._lock:
                self._state.update({key: payload[key] for key in (
                    "ports", "boards", "driver", "scan_errors", "query_status", "plugin_version",
                    "gstreamer_version", "fingerprint", "queried_at",
                )})
                self._state.update(initialized=True, stale=False, from_cache=False, last_error=None)
            try:
                self._save_cache()
            except OSError:
                LOGGER.warning("DeckLink scan is available, but its cache could not be saved.", exc_info=True)
                with self._lock:
                    self._state["last_error"] = "Результат отримано, але запис кешу DeckLink недоступний."
        except Exception as error:
            if not self._stop.is_set():
                LOGGER.exception("DeckLink capability scan failed")
                with self._lock:
                    self._state.update(last_error=str(error)[:2000], stale=self._state["initialized"], initialized=True, query_status="failed")
        finally:
            with self._lock:
                self._state["query_running"] = False
