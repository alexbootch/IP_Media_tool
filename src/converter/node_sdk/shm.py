"""Local mmap rings. Each reader has its own cursor; no reader can block a write."""

from dataclasses import dataclass
import fcntl
import mmap
import os
from pathlib import Path
import re
import stat
import struct

from converter.config.validation import ConfigError, canonical_id


HEADER = struct.Struct("<8sIIQ")
SLOT = struct.Struct("<QqqqII")
MAGIC = b"IPMSHM01"
MAX_BYTES = 128 * 1024 * 1024
MAX_FRAME = 32 * 1024 * 1024
MAX_SLOTS = 64
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
DISCONT = 1
GAP = 2
TOP_FIELD_FIRST = 4
REPEAT_FIRST_FIELD = 8
ONE_FIELD = 16
INTERLACED = 32
KNOWN_FLAGS = DISCONT | GAP | TOP_FIELD_FIRST | REPEAT_FIRST_FIELD | ONE_FIELD | INTERLACED


@dataclass(frozen=True, slots=True)
class MediaFrame:
    """A detached raw sample with producer running-time timestamps (ns)."""

    data: bytes
    pts: int
    duration: int
    dts: int = -1
    flags: int = 0
    sequence: int = 0
    skipped: int = 0


def ring_path(root, instance_id, generation, endpoint_id):
    """Derive a pathname from verified identities instead of trusting a producer path."""
    canonical_id(instance_id)
    canonical_id(generation)
    if not isinstance(endpoint_id, str) or not IDENTIFIER.fullmatch(endpoint_id):
        raise ConfigError("invalid_config", "Invalid SHM endpoint ID.")
    return Path(root) / instance_id / generation / (endpoint_id + ".ring")


def ring_size(slots, slot_bytes):
    """Bound storage and arithmetic before opening or allocating a mapping."""
    if type(slots) is not int or type(slot_bytes) is not int or not 2 <= slots <= MAX_SLOTS or not 1 <= slot_bytes <= MAX_FRAME:
        raise ConfigError("unsupported_format", "Invalid SHM ring dimensions.")
    size = HEADER.size + slots * (SLOT.size + slot_bytes)
    if size > MAX_BYTES:
        raise ConfigError("unsupported_format", "SHM ring exceeds the allocation limit.")
    return size


class Ring:
    """Single writer with independent read-only mappings and nonblocking flock."""

    def __init__(self, path, slots, slot_bytes, *, create=False):
        """Allocate backing pages up front; consumers verify the file and header."""
        self.path, self.slots, self.slot_bytes = Path(path), slots, slot_bytes
        self.size = ring_size(slots, slot_bytes)
        self.writable = create
        self.cursor = 0
        self.dropped = 0
        self.mapping = None
        self.fd = os.open(path, os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT | os.O_EXCL | os.O_RDWR if create else os.O_RDONLY), 0o600)
        try:
            if create:
                os.posix_fallocate(self.fd, 0, self.size)
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size != self.size or stat.S_IMODE(info.st_mode) != 0o600:
                raise ConfigError("transport_error", "SHM backing file has invalid ownership, permissions or size.")
            self.mapping = mmap.mmap(self.fd, self.size, access=mmap.ACCESS_WRITE if create else mmap.ACCESS_READ)
            if create:
                HEADER.pack_into(self.mapping, 0, MAGIC, slots, slot_bytes, 0)
            if HEADER.unpack_from(self.mapping)[:3] != (MAGIC, slots, slot_bytes):
                raise ConfigError("transport_error", "SHM header does not match the contract.")
        except BaseException:
            self.close()
            if create:
                self.path.unlink(missing_ok=True)
            raise

    def _offset(self, sequence):
        """Locate a slot within the validated fixed-size layout."""
        return HEADER.size + ((sequence - 1) % self.slots) * (SLOT.size + self.slot_bytes)

    def write(self, frame):
        """Commit one sample, dropping it immediately if any reader holds the lock."""
        if not self.writable or self.mapping is None:
            raise ValueError("SHM writer is closed or read-only")
        if not 0 < len(frame.data) <= self.slot_bytes or frame.pts < 0 or frame.duration <= 0 or frame.dts < -1 or frame.flags & ~KNOWN_FLAGS:
            raise ValueError("Invalid SHM frame")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.dropped += 1
            return False
        try:
            sequence = HEADER.unpack_from(self.mapping)[3] + 1
            offset = self._offset(sequence)
            start = offset + SLOT.size
            flags = frame.flags | (DISCONT if self.dropped else 0)
            self.mapping[start:start + len(frame.data)] = frame.data
            SLOT.pack_into(self.mapping, offset, sequence, frame.pts, frame.dts, frame.duration, flags, len(frame.data))
            HEADER.pack_into(self.mapping, 0, MAGIC, self.slots, self.slot_bytes, sequence)
            self.dropped = 0
            return True
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)

    def read(self):
        """Copy the next retained sample, marking gaps without moving other cursors."""
        if self.writable or self.mapping is None:
            raise ValueError("SHM reader is closed or writable")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        try:
            latest = HEADER.unpack_from(self.mapping)[3]
            if latest <= self.cursor:
                return None
            sequence = max(self.cursor + 1, latest - self.slots + 1)
            offset = self._offset(sequence)
            stored, pts, dts, duration, flags, length = SLOT.unpack_from(self.mapping, offset)
            if stored != sequence or not 0 < length <= self.slot_bytes or pts < 0 or dts < -1 or duration <= 0 or flags & ~KNOWN_FLAGS:
                raise ConfigError("transport_error", "Corrupted SHM frame header.")
            start = offset + SLOT.size
            skipped = sequence - self.cursor - 1
            result = MediaFrame(self.mapping[start:start + length], pts, duration, dts, flags | (DISCONT if skipped or not self.cursor else 0), sequence, skipped)
            self.cursor = sequence
            return result
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)

    def close(self):
        """Release the mapping and descriptor without waiting for another process."""
        if self.mapping is not None:
            self.mapping.close()
            self.mapping = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
