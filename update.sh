#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

case "${1:-}" in
  "")
    exec python3 collect_projects.py
    ;;
  --push)
    exec python3 collect_projects.py --push
    ;;
  --commit)
    exec python3 collect_projects.py --sync
    ;;
  *)
    echo "用法：$0 [--commit|--push]" >&2
    exit 2
    ;;
esac
