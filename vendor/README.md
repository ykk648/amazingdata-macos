# Vendor wheels

Place the two official wheels in this directory before building:

- `tgw-*.whl`
- `AmazingData-*.whl`

Run `./scripts/bootstrap.sh /path/to/tgw.whl /path/to/AmazingData.whl` to copy
them safely. Wheel files are ignored by Git and must not be redistributed
without permission from their owner.

The initial compatibility target was tested with:

- `tgw-1.0.9.2-py3-none-any.whl`
  - SHA-256: `cbc30194e2d3923c87e5d40ce469b79575758001f9d5f7481d46c29c9667e21d`
- `AmazingData-1.1.9-cp314-none-any.whl`
  - SHA-256: `d9a5d12f20523f865f5cf017d134862bc985e01f1dcb0333c36f1876328006fa`
