#!/usr/bin/env bash
# GateMoE installer for Raspberry Pi 5 (8 GB), Raspberry Pi OS 64-bit (Bookworm or Trixie).
# Run ONCE while online; afterwards everything runs offline.
#
#   DATA_DIR=/mnt/hdd/gatemoe ./scripts/install_pi.sh
#
# Then:  ./scripts/download_models.sh   (models, voices, Wikipedia/ZIMs)
#        gatemoe doctor && sudo systemctl enable --now gatemoe
set -euo pipefail
DATA_DIR="${DATA_DIR:-/mnt/hdd/gatemoe}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LLAMA_COMMIT="${LLAMA_COMMIT:-5e03bdd8700948b9c41c54dd1b00f28a2aebc03f}"   # first commit tested with Clef (2026-10-05)
HF_VERSION="${HF_VERSION:-0.8.137}"
GSAP_VERSION="${GSAP_VERSION:-3.15.0}"
NODE_MAJOR=22
log() { printf '\n== %s\n' "$*"; }

log "System packages"
sudo apt-get update
sudo apt-get install -y build-essential cmake git curl ca-certificates bzip2 pkg-config \
  python3 python3-venv python3-dev libcairo2-dev libpango1.0-dev ffmpeg \
  fonts-noto-core fonts-noto-cjk util-linux graphviz
# Browser for HyperFrames (package name differs between Debian releases)
sudo apt-get install -y chromium || sudo apt-get install -y chromium-browser
sudo apt-get install -y chromium-headless-shell 2>/dev/null || true   # faster BeginFrame capture when available

log "Node.js ${NODE_MAJOR} (HyperFrames needs >= 22; Debian ships older)"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt "$NODE_MAJOR" ]; then
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | sudo -E bash -
  sudo apt-get install -y nodejs
fi

log "Data directory ${DATA_DIR}"
sudo mkdir -p "$DATA_DIR"
sudo chown "$(id -u):$(id -g)" "$DATA_DIR"
mkdir -p "$DATA_DIR"/{models,zim,jobs,cache,hyperframes}

log "llama.cpp (llama-server with Clef /v1/systemone support)"
if [ ! -x "$DATA_DIR/llama.cpp/build/bin/llama-server" ]; then
  [ -d "$DATA_DIR/llama.cpp/.git" ] || git clone https://github.com/ggml-org/llama.cpp "$DATA_DIR/llama.cpp"
  git -C "$DATA_DIR/llama.cpp" fetch --depth 1 origin "$LLAMA_COMMIT" 2>/dev/null || git -C "$DATA_DIR/llama.cpp" fetch origin
  git -C "$DATA_DIR/llama.cpp" checkout "$LLAMA_COMMIT"
  # GGML_NATIVE=ON targets the Cortex-A76 (NEON + dotprod) -> Q4_0 weights get the fast 4x4 repack
  cmake -S "$DATA_DIR/llama.cpp" -B "$DATA_DIR/llama.cpp/build" -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  cmake --build "$DATA_DIR/llama.cpp/build" --config Release -j 4 --target llama-server llama-bench
fi

log "Python environment"
python3 -m venv "$DATA_DIR/venv"
"$DATA_DIR/venv/bin/pip" install --upgrade pip wheel
"$DATA_DIR/venv/bin/pip" install -e "$REPO[video]"
"$DATA_DIR/venv/bin/pip" install -r "$REPO/scripts/requirements-speech.txt" selectolax

log "HyperFrames ${HF_VERSION} + GSAP ${GSAP_VERSION} (local install, no telemetry)"
cd "$DATA_DIR/hyperframes"
[ -f package.json ] || npm init -y >/dev/null
npm install --no-fund --no-audit "hyperframes@${HF_VERSION}" "gsap@${GSAP_VERSION}"
HYPERFRAMES_NO_TELEMETRY=1 npx --no-install hyperframes telemetry disable || true

log "Config ${DATA_DIR}/gatemoe.yaml"
CHROME="$(command -v chromium-headless-shell || command -v chromium || command -v chromium-browser || echo /usr/bin/chromium)"
if [ ! -f "$DATA_DIR/gatemoe.yaml" ]; then
  cat > "$DATA_DIR/gatemoe.yaml" <<EOF
paths:
  data_dir: ${DATA_DIR}
  chrome: ${CHROME}
EOF
fi

log "systemd service"
sudo tee /etc/systemd/system/gatemoe.service >/dev/null <<EOF
[Unit]
Description=GateMoE offline tutor
After=network.target local-fs.target

[Service]
User=$(id -un)
Environment=GATEMOE_CONFIG=${DATA_DIR}/gatemoe.yaml
ExecStart=${DATA_DIR}/venv/bin/gatemoe serve
Restart=on-failure
Nice=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload

cat <<EOF

Installed. Next:
  1. DATA_DIR=${DATA_DIR} ${REPO}/scripts/download_models.sh
  2. GATEMOE_CONFIG=${DATA_DIR}/gatemoe.yaml ${DATA_DIR}/venv/bin/gatemoe doctor
  3. sudo systemctl enable --now gatemoe     # then open http://<pi-address>:8000 on your phone
Tips: power the USB HDD from a powered hub (the Pi gives USB only 600 mA without a 5 A supply);
      do not put swap on the HDD (use zram) - see docs/PI_SETUP.md.
EOF
