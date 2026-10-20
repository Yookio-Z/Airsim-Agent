# msgpack-rpc-python (vendored)

Origin: `msgpack-rpc-python==0.4.1`
(https://github.com/msgpack-rpc/msgpack-rpc-python), Apache-2.0.

## Why this copy exists

Upstream `msgpack-rpc-python` declares `msgpack-python` as a dependency.
`msgpack-python` is the legacy distribution and resolves to **msgpack 0.5.x,
which has no C extension installed in this project's environment** — every
pack/unpack runs in pure Python.

That matters for AirSim because a float image (e.g. a 256x144 depth frame,
36,864 floats) is transferred as a MessagePack list of floats. Measured in this
repository:

| step (256x144 depth) | msgpack 0.5.6 (pure Python) | msgpack 1.1.2 (C extension) |
|---|---:|---:|
| pack | ~26 ms | ~1 ms |
| unpack | ~59 ms | ~1 ms |

End-to-end `simGetImages(DepthPlanar)` on the running AirSim instance:
**217 ms -> 122 ms** (median), no code change in AirSim or this project.

## The only modification

- `pyproject.toml` dependency changed from `msgpack-python` to `msgpack>=1.1.2`.
- **No Python source in `msgpackrpc/` was modified.**

This mirrors the fix shipped by AeroVLA
(`third_party/AeroVLA/msgpack-rpc-python-fix-msgpack-dep.zip`), except it keeps
the 0.4.1 code that AirSim 1.8.1 already works with (0.4.1 accepts the
`pack_encoding` / `unpack_encoding` arguments passed by AirSim's client).

## How the project uses it

Root `pyproject.toml`:

```toml
dependencies = [..., "msgpack==1.1.2", "msgpack-rpc-python==0.4.1"]

[tool.uv.sources]
msgpack-rpc-python = { path = "vendor/msgpack_rpc_python" }
```

Rollback: remove those two lines and the source entry, delete this directory,
then `uv lock && uv sync`.
