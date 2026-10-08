#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

# Build the development profile into ./build and run it from there, no root needed.
#   scripts/run.sh            the real library of the .Devel build ($XDG_DATA_HOME/bookcase-devel)
#   scripts/run.sh --demo     the invented library in build/demo (scripts/demo_library.py, made
#                             when missing), never the real one
set -euo pipefail
cd "$(dirname "$0")/.."

prefix="$PWD/build/install"
if [ ! -f build/build.ninja ]; then
  meson setup build --prefix="$prefix" -Dprofile=development
elif ! meson introspect build --buildoptions | python3 -c '
import json, sys
options = {option["name"]: option["value"] for option in json.load(sys.stdin)}
sys.exit(options.get("prefix") != sys.argv[1] or options.get("profile") != "development")
' "$prefix"; then
  meson configure build -Dprefix="$prefix" -Dprofile=development
fi
meson install -C build --quiet
for argument in "$@"; do
  if [ "$argument" = --demo ] && [ ! -f build/demo/library.sqlite ]; then
    python3 scripts/demo_library.py --data-dir build/demo
  fi
done

export GSETTINGS_SCHEMA_DIR="$prefix/share/glib-2.0/schemas"
export XDG_DATA_DIRS="$prefix/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
exec "$prefix/bin/bookcase" "$@"
