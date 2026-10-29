#!/bin/sh
# Creates config.toml and links dora onto your PATH. Run with `devbox run setup`, which first
# runs `uv sync` to build .venv.

set -o errexit -o nounset

here=$(cd "$(dirname "$0")" && pwd -P)

if [ ! -f "$here/config.toml" ]; then
    cp "$here/config.example.toml" "$here/config.toml"
    echo "✔ Created config.toml from config.example.toml. List the repositories to sync in it; git ignores it."
fi

"$here/.venv/bin/python" -m cli_kit.link "$here"
