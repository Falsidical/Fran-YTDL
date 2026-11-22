#!/usr/bin/env bash
# Build a standalone Linux bundle in dist/ytdlp-gui/ and a .tar.gz.
# Optional: FFMPEG=/path/to/ffmpeg ./packaging/build_linux.sh  (bundles ffmpeg into bin/)
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -e '.[dev]'
python3 -m PyInstaller --noconfirm --distpath dist --workpath build packaging/ytdlp-gui.spec
if [[ -n "${FFMPEG:-}" ]]; then
  mkdir -p dist/ytdlp-gui/bin && cp "$FFMPEG" dist/ytdlp-gui/bin/ffmpeg
  [[ -x "$(dirname "$FFMPEG")/ffprobe" ]] && cp "$(dirname "$FFMPEG")/ffprobe" dist/ytdlp-gui/bin/
fi
tar -C dist -czf dist/ytdlp-gui-linux-x86_64.tar.gz ytdlp-gui
echo "Built dist/ytdlp-gui/ytdlp-gui and dist/ytdlp-gui-linux-x86_64.tar.gz"
