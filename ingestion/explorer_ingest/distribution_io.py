"""Bounded transport decoding and durable writes on a pinned external disk."""

import gzip
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def decoded(path, encoding):
    with Path(path).open("rb") as raw:
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                yield stream
        elif encoding == "raw":
            yield raw
        else:
            raise ValueError("Unsupported artifact encoding")


def transfer(incoming, outgoing, storage, expected_bytes, expected_sha256=None):
    measured = 0
    digest = hashlib.sha256()
    while block := incoming.read(1024 * 1024):
        measured += len(block)
        if measured > expected_bytes:
            raise ValueError("Artifact exceeds its decoded size bound")
        storage.check(len(block))
        outgoing.write(block)
        digest.update(block)
    if measured != expected_bytes or (expected_sha256 and digest.hexdigest() != expected_sha256):
        raise ValueError("Artifact content size/checksum mismatch")
    return digest.hexdigest()


def durable(stream):
    stream.flush()
    os.fsync(stream.fileno())
