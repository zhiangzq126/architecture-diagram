#!/bin/sh
# Read-only bootstrap: usable even when the Python environment module cannot run.
# This script deliberately does not invoke installers or inspect other runtimes.
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if version=$("$candidate" -I -B -S -c 'import sys; print(".".join(map(str, sys.version_info[:3]))); sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)' 2>/dev/null); then
            printf 'ready: %s %s (Python >=3.9)\n' "$candidate" "$version"
            printf '%s\n' 'Next: use environment.doctor(backend) for read-only dependency checks; no installation was performed.'
            exit 0
        fi
        printf 'blocked: %s is unusable or older than Python 3.9 (%s)\n' "$candidate" "$version" >&2
    fi
done
printf '%s\n' 'blocked: Python >=3.9 was not found on PATH.' >&2
printf '%s\n' 'Install Python from python.org or an already-installed OS package manager, then rerun this script.' >&2
printf '%s\n' 'Suggested commands (manual review only): Homebrew: brew install python; Debian/Ubuntu: ask an administrator for python3 and python3-venv.' >&2
printf '%s\n' 'Nothing was installed; no package manager, sudo, network download, or environment configuration change was attempted.' >&2
exit 1
