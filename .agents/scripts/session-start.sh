#!/usr/bin/env bash
set -e

if command -v nomos >/dev/null 2>&1; then
    exec nomos hook session-start
elif [ -n "${GOBIN:-}" ] && [ -x "${GOBIN}/nomos" ]; then
    exec "${GOBIN}/nomos" hook session-start
elif [ -x "${HOME}/.nomos/bin/nomos" ]; then
    exec "${HOME}/.nomos/bin/nomos" hook session-start
elif [ -x "${HOME}/go/bin/nomos" ]; then
    exec "${HOME}/go/bin/nomos" hook session-start
elif [ -x "${HOME}/.local/bin/nomos" ]; then
    exec "${HOME}/.local/bin/nomos" hook session-start
fi

echo "{}"
