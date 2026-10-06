#!/usr/bin/env bash
# Download everything GateMoE needs to run offline (run while online; resumable).
#
#   DATA_DIR=/mnt/hdd/gatemoe ./scripts/download_models.sh            # models + voices + starter ZIMs
#   ZIMS="wikipedia_en_all_nopic wikipedia_kn_all_nopic" ./scripts/download_models.sh
#   SKIP_ZIMS=1 / SKIP_SPEECH=1 / SKIP_MMS=1 to skip parts
set -euo pipefail
DATA_DIR="${DATA_DIR:-/mnt/hdd/gatemoe}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODELS="$DATA_DIR/models"
mkdir -p "$MODELS" "$DATA_DIR/zim"

get() {  # url dest [sha256]
  local url="$1" dst="$2" sha="${3:-}"
  mkdir -p "$(dirname "$dst")"
  if [ -s "$dst" ] && { [ -z "$sha" ] || echo "$sha  $dst" | sha256sum -c --quiet - 2>/dev/null; }; then
    echo "ok   $dst"; return; fi
  echo "get  $url"
  curl -fL --retry 5 --retry-delay 5 -C - -o "$dst.part" "$url"
  mv "$dst.part" "$dst"
  if [ -n "$sha" ]; then echo "$sha  $dst" | sha256sum -c --quiet -; fi
}

# ---- router: Cloudflare Clef-flash (Apache-2.0), Q4_0 for Cortex-A76 (5.60 GB)
get "https://huggingface.co/bartowski/Cloudflare_clef-flash-GGUF/resolve/main/Cloudflare_clef-flash-Q4_0.gguf" \
    "$MODELS/Cloudflare_clef-flash-Q4_0.gguf"

# ---- generator: Qwen3.5-4B (Apache-2.0), Q4_0 (2.58 GB)
get "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_0.gguf" \
    "$MODELS/Qwen3.5-4B-Q4_0.gguf" 298fcb5fe7a77ccc79745ae24751560c5ac56874caff4bb39b1f2055bd72b8bb

# ---- voices: Supertonic 3 (31 languages), Piper (te/ml/mr/bn), sherpa zh, MMS export (kn/ta/gu/pa)
if [ "${SKIP_SPEECH:-0}" != 1 ]; then
  MMS_FLAG=(); [ "${SKIP_MMS:-0}" = 1 ] && MMS_FLAG=(--no-mms)
  MODELS="$MODELS/speech" bash "$HERE/fetch_speech_models.sh" "${MMS_FLAG[@]}"
fi

# ---- offline knowledge: Kiwix ZIM archives (latest file for each prefix)
# Full English Wikipedia without pictures is ~53 GB, with pictures ~127 GB - both fit on 1 TB.
ZIMS="${ZIMS:-wikipedia_en_physics_nopic wikipedia_en_chemistry_nopic wikipedia_en_mathematics_nopic \
wikipedia_en_computer_nopic wikipedia_en_medicine_nopic wikipedia_hi_all_nopic wikipedia_kn_all_nopic}"
if [ "${SKIP_ZIMS:-0}" != 1 ]; then
  for prefix in $ZIMS; do
    project="${prefix%%_*}"                                   # wikipedia, wikibooks, libretexts, ...
    listing="$(curl -fsSL "https://download.kiwix.org/zim/${project}/")" || { echo "skip $prefix (listing failed)"; continue; }
    file="$(printf '%s' "$listing" | grep -o "${prefix}_[0-9]\{4\}-[0-9]\{2\}\.zim" | sort -u | tail -1)"
    if [ -z "$file" ]; then echo "skip $prefix (not found)"; continue; fi
    get "https://download.kiwix.org/zim/${project}/${file}" "$DATA_DIR/zim/${file}"
  done
fi
du -sh "$MODELS" "$DATA_DIR/zim" 2>/dev/null || true
echo "done."
