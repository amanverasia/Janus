#!/bin/sh
set -e

DATA_DIR=/home/janus/.janus

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # On a warm start the data dir (backups, SQLite DB, logs) is already owned
    # by janus, so only do the expensive recursive chown when the top-level
    # directory itself is not owned by janus (e.g. a freshly mounted volume).
    # The tree's contents are written by the janus process, so the top-level
    # owner is a reliable gate against sweeping ~1 GiB on every boot.
    if [ "$(stat -c '%u:%g' "$DATA_DIR")" != "1000:1000" ]; then
        chown -R janus:janus "$DATA_DIR"
    fi
    exec gosu janus "$@"
fi

exec "$@"
