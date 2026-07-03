#!/bin/sh
set -e

DATA_DIR=/home/janus/.janus

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R janus:janus "$DATA_DIR"
    exec gosu janus "$@"
fi

exec "$@"
