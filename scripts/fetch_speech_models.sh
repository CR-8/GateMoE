#!/usr/bin/env bash
# GateMoE speech models for Raspberry Pi 5 (run once, online; everything after is offline).
#   MODELS=/mnt/hdd/gatemoe/models/speech ./fetch_speech_models.sh [--no-mms] [--mms "kan tam guj pan"]
# Needs: curl, tar (bzip2), python3 (3.11 Bookworm or 3.13 Trixie), sha256sum.
# Code dir (this script's dir) must contain export_mms_onnx.py.
set -euo pipefail
MODELS="${MODELS:-$HOME/gatemoe-models/speech}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MMS_LANGS="kan tam guj pan"            # MMS-only languages; add tel ben mar mal ory asm if wanted
DO_MMS=1; ST_INT8=0
while [ $# -gt 0 ]; do case "$1" in --no-mms) DO_MMS=0;; --mms) MMS_LANGS="$2"; shift;; --st-int8) ST_INT8=1;; esac; shift; done

get() {  # url dest [sha256]
  local url="$1" dst="$2" sha="${3:-}"
  mkdir -p "$(dirname "$dst")"
  if [ -s "$dst" ] && { [ -z "$sha" ] || echo "$sha  $dst" | sha256sum -c --quiet - 2>/dev/null; }; then
    echo "ok   $dst"; return; fi
  echo "get  $url"
  curl -fL --retry 5 --retry-delay 3 -C - -o "$dst.part" "$url"
  mv "$dst.part" "$dst"
  if [ -n "$sha" ]; then echo "$sha  $dst" | sha256sum -c --quiet - ; fi
}

# ---------------------------------------------------------------- Supertonic 3
# Weights: BigScience OpenRAIL-M.  Revision pinned = supertonic PyPI 1.3.1 pin (== main on 2026-10-06).
ST_REV=724fb5abbf5502583fb520898d45929e62f02c0b
ST="https://huggingface.co/Supertone/supertonic-3/resolve/$ST_REV"
D="$MODELS/supertonic3"
get "$ST/onnx/duration_predictor.onnx" "$D/onnx/duration_predictor.onnx" c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db
get "$ST/onnx/text_encoder.onnx"       "$D/onnx/text_encoder.onnx"       c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff
get "$ST/onnx/vector_estimator.onnx"   "$D/onnx/vector_estimator.onnx"   883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c
get "$ST/onnx/vocoder.onnx"            "$D/onnx/vocoder.onnx"            085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba
get "$ST/onnx/tts.json"                "$D/onnx/tts.json"                42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09
get "$ST/onnx/unicode_indexer.json"    "$D/onnx/unicode_indexer.json"    9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f
get "$ST/LICENSE"                      "$D/LICENSE"
for v in F1 F2 F3 F4 F5 M1 M2 M3 M4 M5; do get "$ST/voice_styles/$v.json" "$D/voice_styles/$v.json"; done

# Optional: k2-fsa int8 vector_estimator + vocoder (128.8 MB tarball; same I/O; use variant="int8")
if [ "$ST_INT8" = 1 ] && [ ! -s "$D/onnx/vector_estimator.int8.onnx" ]; then
  n=sherpa-onnx-supertonic-3-tts-int8-2026-05-11
  get "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/$n.tar.bz2" "$MODELS/tmp/$n.tar.bz2"
  tar -C "$MODELS/tmp" -xjf "$MODELS/tmp/$n.tar.bz2" "$n/vector_estimator.int8.onnx" "$n/vocoder.int8.onnx"
  mv "$MODELS/tmp/$n/vector_estimator.int8.onnx" "$MODELS/tmp/$n/vocoder.int8.onnx" "$D/onnx/"
  rm -rf "$MODELS/tmp"
fi

# ------------------------------------------------------- Piper voices (piper-tts)
PV_REV=c10ece1aade47bb51c153c893d14e5bf8e5b7117
PV="https://huggingface.co/rhasspy/piper-voices/resolve/$PV_REV"
for p in te/te_IN/venkatesh/medium/te_IN-venkatesh-medium \
         ml/ml_IN/arjun/medium/ml_IN-arjun-medium \
         mr/mr_IN/google/medium/mr_IN-google-medium \
         bn/bn_BD/google/medium/bn_BD-google-medium; do
  n="$(basename "$p")"
  get "$PV/$p.onnx"      "$MODELS/piper/$n/$n.onnx"
  get "$PV/$p.onnx.json" "$MODELS/piper/$n/$n.onnx.json"
  get "$PV/$(dirname "$p")/MODEL_CARD" "$MODELS/piper/$n/MODEL_CARD"
done

# ------------------------------------------- Chinese (sherpa-onnx release tarball)
SH="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"
n=vits-piper-zh_CN-chaowen-medium
if [ ! -s "$MODELS/sherpa/$n/zh_CN-chaowen-medium.onnx" ]; then
  get "$SH/$n.tar.bz2" "$MODELS/sherpa/$n.tar.bz2"
  tar -C "$MODELS/sherpa" -xjf "$MODELS/sherpa/$n.tar.bz2" && rm "$MODELS/sherpa/$n.tar.bz2"
fi

# ------------------------------------- Meta MMS-TTS -> ONNX (one-time export)
# Weights CC-BY-NC-4.0.  Needs a throw-away venv with CPU torch (aarch64 PyPI wheel is CPU-only, ~450 MB).
declare -A MMS_REV=( [kan]=30e3c5d533e8c559c10bf0d25637fea51b95bd7c [tam]=e9cf59dae34f0f51e3b1842876a658e4516f9fe4
  [tel]=dea6807154acc01918581982dcd40a116882a14d [ben]=0da99de6074c8829121cdabfbdba423af18e8e56
  [mar]=7af4a6db1df2eb20042d24cc7c180a492df1cc13 [mal]=893b8c6442d6a630896d1d3ac0f429094ddfae82
  [guj]=b72e80a7eeca90b72e0af2e2d00b77a336ce242d [pan]=45d7962e8daba724f9ff251ee3198bdb47a5f498
  [ory]=581f221219b728fab4d53efb24e18134bd1a9e28 [asm]=c9472cc5e1ea965e4b57c147426ed826197d3ae2 )
if [ "$DO_MMS" = 1 ]; then
  need=0; for l in $MMS_LANGS; do [ -s "$MODELS/mms/$l/model.onnx" ] || need=1; done
  if [ "$need" = 1 ]; then
    EV="$(mktemp -d)/venv"; python3 -m venv "$EV"
    "$EV/bin/pip" install -q --upgrade pip
    if [ "$(uname -m)" = "x86_64" ]; then "$EV/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu
    else "$EV/bin/pip" install -q torch; fi
    "$EV/bin/pip" install -q transformers onnx onnxruntime
    export HF_HOME="$(dirname "$EV")/hf"
    for l in $MMS_LANGS; do
      [ -s "$MODELS/mms/$l/model.onnx" ] && { echo "ok   mms/$l"; continue; }
      "$EV/bin/python" "$HERE/export_mms_onnx.py" --lang "$l" --revision "${MMS_REV[$l]}" --out "$MODELS/mms/$l"
    done
    rm -rf "$(dirname "$EV")"
  fi
fi
du -sh "$MODELS"/* 2>/dev/null || true
echo "done: $MODELS"
