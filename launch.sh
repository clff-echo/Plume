#!/bin/sh
# The complete app tree, including Spotifyd, shares a single memory budget.
set -eu
PLUME_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$PLUME_DIR${PYTHONPATH:+:$PYTHONPATH}"
export GSK_RENDERER=cairo
export PYTHONDONTWRITEBYTECODE=1
export MALLOC_ARENA_MAX=2
if [ "${1:-}" = "--no-cap" ]; then
    shift
    export PLUME_CAPPED=0
    exec python3 -m plume.app "$@"
fi
if command -v systemd-run >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    exec systemd-run --user --scope --quiet --collect \
      --unit="plume-$$" \
      --property=MemoryAccounting=yes \
      --property=MemoryHigh=250000000 \
      --property=MemoryMax=300000000 \
      --property=MemorySwapMax=0 \
      env PLUME_CAPPED=1 python3 -m plume.app "$@"
fi
printf '%s\n' 'Plume : le plafond strict de 300 Mo nécessite systemd utilisateur et cgroups v2.' >&2
printf '%s\n' 'Pour tester sans plafond strict : ./launch.sh --no-cap' >&2
exit 1
