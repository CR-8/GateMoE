#!/usr/bin/env bash
# GateMoE one-command installer for a cloud VM (Ubuntu 22.04 / 24.04, Debian 12 / 13; x86_64 or arm64).
#
#   curl -fsSL https://raw.githubusercontent.com/CR-8/GateMoE/main/scripts/install_cloud.sh | sudo bash
#
# Needs >= 8 GB RAM (t3.large is the minimum, t3.xlarge / m7i.xlarge recommended) and ~22 GB free disk.
# Re-running is safe: finished steps are skipped, downloads resume.
#
# Options (environment variables, e.g. `curl ... | sudo LANGS="kn hi" bash`):
#   GATEMOE_REF=main            git branch/tag to install
#   GATEMOE_HOME=/opt/gatemoe   install directory (code, venv, data, models)
#   GATEMOE_PASSWORD=...        web login password (default: generated and printed at the end)
#   PORT=8000                   web port
#   LANGS="kn"                  extra lesson languages to fetch ZIMs for (en always); Kannada/Tamil/
#                               Gujarati/Punjabi voices are exported from Meta MMS (+2.5 GB temporarily)
#   ZIMS="..."                  override the offline-knowledge list entirely (Kiwix name prefixes)
#   SKIP_MODELS=1               install software only (no model / ZIM downloads)
#   FORCE=1                     skip the RAM / disk checks
set -euo pipefail

REPO_URL="${GATEMOE_REPO:-https://github.com/CR-8/GateMoE.git}"
REF="${GATEMOE_REF:-main}"
HOME_DIR="${GATEMOE_HOME:-/opt/gatemoe}"
DATA="$HOME_DIR/data"
SRC="$HOME_DIR/GateMoE"
VENV="$HOME_DIR/venv"
PORT="${PORT:-8000}"
LANGS="${LANGS:-}"
LLAMA_COMMIT="${LLAMA_COMMIT:-5e03bdd8700948b9c41c54dd1b00f28a2aebc03f}"   # first commit with Clef support
HF_VERSION="${HF_VERSION:-0.8.137}"
GSAP_VERSION="${GSAP_VERSION:-3.15.0}"
PLAYWRIGHT_VERSION="${PLAYWRIGHT_VERSION:-1.56}"
SVC_USER=gatemoe
export DEBIAN_FRONTEND=noninteractive
# re-runs: the checkouts belong to the service user, so root's git would refuse them ("dubious
# ownership"); trust them for this script's git commands only
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'

log()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
cloud_meta() {  # $1 = ip | hostname. AWS IMDSv2, then Google Cloud; prints nothing elsewhere
  local tok
  if tok="$(curl -sf -m 2 --noproxy '*' -X PUT http://169.254.169.254/latest/api/token \
            -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null)"; then
    curl -sf -m 2 --noproxy '*' -H "X-aws-ec2-metadata-token: $tok" \
      "http://169.254.169.254/latest/meta-data/$([ "$1" = ip ] && echo public-ipv4 || echo public-hostname)" 2>/dev/null || true
  elif [ "$1" = ip ]; then
    curl -sf -m 2 --noproxy '*' -H 'Metadata-Flavor: Google' \
      http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true
  fi
}
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root: curl ... | sudo bash"
command -v apt-get >/dev/null || die "this installer supports Ubuntu / Debian (apt) only"
ARCH="$(uname -m)"
case "$ARCH" in x86_64|aarch64) ;; *) die "unsupported CPU architecture $ARCH (x86_64 or aarch64 only)";; esac

# ------------------------------------------------------------------ 0. can this machine run it?
MEM_GB=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
NPROC=$(nproc)
if [ "${FORCE:-0}" != 1 ] && awk "BEGIN{exit !($MEM_GB < 7.0)}"; then
  die "this machine has ${MEM_GB} GB RAM. GateMoE needs >= 8 GB: the Clef-flash router alone uses ~6.6 GB
       while it decides. On AWS: stop the instance, Actions > Instance settings > Change instance type
       (t3.large = 8 GB minimum, t3.xlarge / m7i.xlarge = 16 GB recommended), start it, re-run this script."
fi
mkdir -p "$HOME_DIR"
FREE_GB=$(df -BG --output=avail "$HOME_DIR" | tail -1 | tr -dc 0-9)
if [ "${FORCE:-0}" != 1 ] && [ "${SKIP_MODELS:-0}" != 1 ] && [ "$FREE_GB" -lt 18 ] && [ ! -s "$DATA/models/Cloudflare_clef-flash-Q4_0.gguf" ]; then
  die "only ${FREE_GB} GB free under $HOME_DIR; models + software need ~20 GB (grow the EBS volume)"
fi
echo "machine: $ARCH, ${NPROC} vCPU, ${MEM_GB} GB RAM, ${FREE_GB} GB free disk"

# ------------------------------------------------------------------ 1. system packages
log "System packages"
apt-get update -q
apt-get install -y -q --no-install-recommends build-essential cmake git curl ca-certificates bzip2 xz-utils \
  pkg-config python3 python3-venv python3-dev libcairo2-dev libpango1.0-dev libssl-dev ffmpeg graphviz \
  fonts-noto-core fonts-noto-cjk util-linux openssl
# swap as a safety net on 8-12 GB machines (the router peaks close to 7 GB)
if awk "BEGIN{exit !($MEM_GB < 12)}" && [ -z "$(swapon --show --noheadings)" ] && [ ! -f /swapfile ]; then
  log "2 GB swap file (safety net for 8 GB machines)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ------------------------------------------------------------------ 2. Python (>= 3.11)
PY=python3
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  log "Python 3.12 via uv (system Python is older than 3.11)"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" uv python install 3.12
  PY="$(UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" uv python find 3.12)"
fi

# ------------------------------------------------------------------ 3. Node.js 22 (HyperFrames)
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
  log "Node.js 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -q nodejs
fi

# ------------------------------------------------------------------ 4. GateMoE source
log "GateMoE source ($REF)"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q --depth 1 origin "$REF"
  git -C "$SRC" checkout -q -B deploy FETCH_HEAD
else
  git clone -q --depth 1 --branch "$REF" "$REPO_URL" "$SRC"
fi
mkdir -p "$DATA"/{models,zim,jobs,cache,hyperframes}

# ------------------------------------------------------------------ 5. llama.cpp (llama-server with Clef support)
LLAMA="$DATA/llama.cpp"
if [ ! -x "$LLAMA/build/bin/llama-server" ]; then
  log "llama.cpp $LLAMA_COMMIT (native build for this CPU, ~5-15 min)"
  [ -d "$LLAMA/.git" ] || { mkdir -p "$LLAMA" && git -C "$LLAMA" init -q && git -C "$LLAMA" remote add origin https://github.com/ggml-org/llama.cpp; }
  git -C "$LLAMA" fetch -q --depth 1 origin "$LLAMA_COMMIT"
  git -C "$LLAMA" checkout -q FETCH_HEAD
  cmake -S "$LLAMA" -B "$LLAMA/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_BUILD_SERVER=ON \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF >/dev/null
  cmake --build "$LLAMA/build" --config Release -j "$NPROC" --target llama-server llama-bench >/dev/null
fi

# ------------------------------------------------------------------ 6. Python environment
log "Python environment"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip wheel
"$VENV/bin/pip" install -q -e "$SRC[video]" -r "$SRC/scripts/requirements-speech.txt" selectolax

# ------------------------------------------------------------------ 7. HyperFrames + a headless Chromium
log "HyperFrames $HF_VERSION + headless Chromium"
cd "$DATA/hyperframes"
[ -f package.json ] || npm init -y >/dev/null
npm install --no-fund --no-audit --loglevel=error "hyperframes@${HF_VERSION}" "gsap@${GSAP_VERSION}"
HYPERFRAMES_NO_TELEMETRY=1 npx --no-install hyperframes telemetry disable >/dev/null 2>&1 || true
cd "$HOME_DIR"
export PLAYWRIGHT_BROWSERS_PATH="$HOME_DIR/browsers"     # Chromium builds for x86_64 and arm64
npx -y "playwright@${PLAYWRIGHT_VERSION}" install --with-deps chromium-headless-shell >/dev/null \
  || npx -y "playwright@${PLAYWRIGHT_VERSION}" install --with-deps chromium >/dev/null
CHROME="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f \( -name headless_shell -o -name chrome-headless-shell -o -name chrome \) \
          -perm -u+x 2>/dev/null | sort | head -1)"
[ -n "$CHROME" ] || warn "no headless Chromium found - videos will fall back to Manim only"
cd "$HOME_DIR"

# ------------------------------------------------------------------ 8. configuration
log "Configuration"
CONF="$DATA/gatemoe.yaml"
if [ ! -f "$CONF" ]; then
  cat > "$CONF" <<EOF
# GateMoE settings for this VM (merged over gatemoe/config/default.yaml). Edit, then:
#   sudo systemctl restart gatemoe
paths:
  data_dir: $DATA
  chrome: ${CHROME:-/usr/bin/chromium}
hardware:
  threads: $NPROC
server:
  port: $PORT
EOF
fi
ENV_FILE=/etc/gatemoe.env
if [ ! -f "$ENV_FILE" ] || [ -n "${GATEMOE_PASSWORD:-}" ]; then
  PASS="${GATEMOE_PASSWORD:-$(openssl rand -base64 15 | tr -d '/+=' | cut -c1-16)}"
  case "$PASS" in *"'"*|*$'\n'*) die "GATEMOE_PASSWORD must not contain quotes or newlines";; esac
  # names the browser may use for this server (IP addresses are always allowed)
  PUB_DNS="$(cloud_meta hostname)"
  [[ "$PUB_DNS" =~ ^[A-Za-z0-9.-]+$ ]] || PUB_DNS=""
  umask 077
  cat > "$ENV_FILE" <<EOF
GATEMOE_CONFIG='$CONF'
GATEMOE_USER='gatemoe'
GATEMOE_PASSWORD='$PASS'
GATEMOE_ALLOWED_HOSTS='$PUB_DNS'
EOF
  umask 022
fi

# ------------------------------------------------------------------ 9. models, voices and offline knowledge
if [ "${SKIP_MODELS:-0}" != 1 ]; then
  log "Models, voices and offline knowledge (~10 GB, resumable)"
  ZIM_LIST="${ZIMS:-wikipedia_en_physics_nopic wikipedia_en_chemistry_nopic wikipedia_en_computer_nopic libretexts.org_en_k12 phet_en_all}"
  MMS=1
  for l in $LANGS; do
    [ -n "${ZIMS:-}" ] || ZIM_LIST="$ZIM_LIST wikipedia_${l}_all_nopic phet_${l}_all"
    case "$l" in kn|ta|gu|pa) MMS=0;; esac      # these voices need the one-time MMS export
  done
  DATA_DIR="$DATA" ZIMS="$ZIM_LIST" SKIP_MMS="$MMS" PATH="$VENV/bin:$PATH" bash "$SRC/scripts/download_models.sh"
fi

# ------------------------------------------------------------------ 10. service
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin "$SVC_USER"
chown -R "$SVC_USER:$SVC_USER" "$HOME_DIR"
chown root:"$SVC_USER" "$ENV_FILE" && chmod 640 "$ENV_FILE"
if [ -d /run/systemd/system ]; then
  log "systemd service"
  cat > /etc/systemd/system/gatemoe.service <<EOF
[Unit]
Description=GateMoE offline tutor
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
EnvironmentFile=$ENV_FILE
Environment=HOME=$HOME_DIR
ExecStart=$VENV/bin/gatemoe serve
Restart=on-failure
RestartSec=5
Nice=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q gatemoe
  systemctl restart gatemoe
else
  warn "systemd is not running (container?) - starting GateMoE in the background"
  pkill -f "$VENV/bin/gatemoe serve" 2>/dev/null || true
  su -s /bin/bash "$SVC_USER" -c "set -a; . $ENV_FILE; set +a; HOME=$HOME_DIR nohup $VENV/bin/gatemoe serve > $HOME_DIR/gatemoe.log 2>&1 &"
fi

log "Waiting for the web app"
for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null && break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null || die "the server did not start - see: journalctl -u gatemoe -n 50"
su -s /bin/bash "$SVC_USER" -c "set -a; . $ENV_FILE; set +a; $VENV/bin/gatemoe doctor" || true

# ------------------------------------------------------------------ done
PUB_IP="$(cloud_meta ip)"
[[ "$PUB_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || PUB_IP="<this-server-ip>"
PASS="$(sed -n "s/^GATEMOE_PASSWORD='\(.*\)'$/\1/p" "$ENV_FILE")"
cat <<EOF

========================================================================
 GateMoE is running.

 Open:      http://$PUB_IP:$PORT
 Login:     user "gatemoe", password "$PASS"
            (change it: sudo nano $ENV_FILE && sudo systemctl restart gatemoe)

 Open the port for your own IP only - AWS: security group inbound rule;
   Google Cloud: gcloud compute firewall-rules create gatemoe --allow=tcp:$PORT --source-ranges=<your-ip>/32
 Safer (no open port, encrypted):  ssh -L $PORT:localhost:$PORT <user>@$PUB_IP
   (Google Cloud: gcloud compute ssh <vm> -- -L $PORT:localhost:$PORT), then open http://localhost:$PORT

 Logs:      journalctl -u gatemoe -f        Settings: $CONF
 A lesson takes several minutes on CPU. Stop the instance when you are not using it.
========================================================================
EOF
