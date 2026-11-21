#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
for python in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$python" >/dev/null && "$python" -c 'import sys; sys.exit(sys.version_info < (3,10))'; then
        exec "$python" launcher.py "$@"
    fi
done
echo "Install Python 3.10 or later, then open this launcher again."
exit 1
