"""AirSim RPC codec regression tests.

AirSim transfers float images (the depth channel) as MessagePack lists of
floats. The legacy ``msgpack-python`` 0.5.x distribution ships without a C
extension in this environment, so a 256x144 depth frame costs ~85 ms in
pure-Python pack/unpack. ``msgpack`` 1.1.2's C codec costs ~2 ms.

Measured end-to-end against the same AirSim instance, alternating the two
codecs: depth request 280 ms -> 138 ms, while the scene request (bytes, not a
float list) stayed at ~86 ms.

The two distributions both install a ``msgpack/`` package directory, so having
both in the environment is not a performance question -- it is whichever one
was installed last. These tests fail loudly instead of letting the slow codec
come back silently.
"""

from __future__ import annotations

import importlib.metadata as metadata

import msgpack


def test_msgpack_uses_c_implementation():
    assert msgpack.Packer.__module__ == "msgpack._cmsgpack", (
        "msgpack is running in pure-Python mode; AirSim float images will be "
        "about 2x slower. See vendor/msgpack_rpc_python/README.md."
    )


def test_legacy_msgpack_python_is_absent():
    installed = {dist.metadata["Name"].lower() for dist in metadata.distributions()}
    assert "msgpack-python" not in installed, (
        "msgpack-python 0.5.x shadows msgpack's `msgpack/` package directory; "
        "it must not be installed alongside msgpack>=1.1.2."
    )
