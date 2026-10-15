#!/usr/bin/env bash
# A second GateMoE node on Google Cloud that GENERATES lessons, while the first VM keeps running
# a benchmark untouched. Run in Google Cloud Shell (the >_ icon at the top right of the console):
#
#   ZONE=us-central1-a PASSWORD=YourWebPassword1 bash gcp_generation_node.sh up
#   ZONE=us-central1-a bash gcp_generation_node.sh status     # install progress, address, knowledge pack
#   ZONE=us-central1-a bash gcp_generation_node.sh stop       # pause (only the disk is billed)
#   ZONE=us-central1-a bash gcp_generation_node.sh start
#   ZONE=us-central1-a bash gcp_generation_node.sh down       # delete the node and its disk
#
# The node installs GateMoE by itself (scripts/install_cloud.sh, about 30-60 minutes, nobody needs to
# watch), then adds a pack of subject archives from the live Kiwix catalogue (`gatemoe kdd fields`:
# Wikipedia topic selections, LibreTexts libraries, Stack Exchange sites of academic fields) in the
# background at low priority. Lessons can be made as soon as the web app answers.
#
# Options: NAME=gatemoe-gen  TYPE=e2-standard-4 (4 vCPU, 16 GB)  DISK_GB=150  REF=<git branch>
#          LANGS="hi"  FIELDS=50 (archives in the pack)  FIELDS_GB=60 (their disk budget)
# Cost (us-central1, list prices): e2-standard-4 about $0.13/h + 150 GB disk about $0.50/day,
# so roughly $3.70 per day while running; stopped, only the disk is billed.
# Free-trial accounts may run 8 vCPUs in total: 2 (a benchmark e2-standard-2) + 4 here fits.
set -euo pipefail
NAME="${NAME:-gatemoe-gen}"
ZONE="${ZONE:?set ZONE=<zone, e.g. the one of your first VM: us-central1-a>}"
TYPE="${TYPE:-e2-standard-4}"
DISK_GB="${DISK_GB:-150}"
REF="${REF:-ui-classroom-redesign}"
LANGS="${LANGS:-hi}"
FIELDS="${FIELDS:-50}"
FIELDS_GB="${FIELDS_GB:-60}"
TAG=gatemoe-gen
RAW="https://raw.githubusercontent.com/CR-8/GateMoE/$REF/scripts/install_cloud.sh"

ip_of() { gcloud compute instances describe "$NAME" --zone "$ZONE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)'; }

case "${1:-}" in
up)
  PASSWORD="${PASSWORD:?set PASSWORD=<web login password, letters and digits, 10+ characters>}"
  [[ "$PASSWORD" =~ ^[A-Za-z0-9]{10,64}$ ]] || { echo "PASSWORD: use 10-64 letters and digits"; exit 1; }
  [[ "$LANGS" =~ ^[a-z\ ]*$ && "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "bad LANGS or REF"; exit 1; }
  STARTUP="$(mktemp)"
  cat > "$STARTUP" <<EOF
#!/bin/bash
# runs at every boot: installs once, then (re)starts the subject-archive pack until it is complete
exec >>/var/log/gatemoe-node.log 2>&1
echo "== boot \$(date -u +%FT%TZ)"
if [ ! -f /opt/gatemoe/.node-ready ]; then
  curl -fsSL "$RAW" | GATEMOE_REF="$REF" GATEMOE_PASSWORD="$PASSWORD" LANGS="$LANGS" bash \
    && touch /opt/gatemoe/.node-ready || { echo "== install failed; it resumes at the next boot"; exit 1; }
fi
if [ ! -f /opt/gatemoe/.fields-done ]; then
  echo "== subject archives (gatemoe kdd fields --count $FIELDS --max-gb $FIELDS_GB)"
  su -s /bin/bash gatemoe -c 'set -a; . /etc/gatemoe.env; set +a; HOME=/opt/gatemoe nice -n 15 \
    /opt/gatemoe/venv/bin/gatemoe kdd fields --count $FIELDS --max-gb $FIELDS_GB --apply' \
    && touch /opt/gatemoe/.fields-done
fi
echo "== node ready \$(date -u +%FT%TZ)"
EOF
  gcloud compute firewall-rules describe gatemoe-gen-web >/dev/null 2>&1 || \
    gcloud compute firewall-rules create gatemoe-gen-web --allow tcp:8000 --target-tags "$TAG" \
      --source-ranges 0.0.0.0/0 --description "GateMoE generation node web app (password protected)"
  gcloud compute instances create "$NAME" --zone "$ZONE" --machine-type "$TYPE" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-balanced --tags "$TAG" \
    --metadata-from-file startup-script="$STARTUP"
  rm -f "$STARTUP"
  echo
  echo "Created $NAME. It installs itself now (30-60 min). Follow it with:"
  echo "  ZONE=$ZONE bash $0 status"
  echo "Then open http://$(ip_of):8000  (user: gatemoe, password: the one you chose)"
  ;;
status)
  echo "address: http://$(ip_of):8000   state: $(gcloud compute instances describe "$NAME" --zone "$ZONE" --format='value(status)')"
  gcloud compute ssh "$NAME" --zone "$ZONE" --command '
    echo "web app: $(systemctl is-active gatemoe 2>/dev/null || echo not-installed-yet)"
    echo "subject archives installed: $(ls /opt/gatemoe/data/zim/*.zim 2>/dev/null | wc -l)   disk: $(df -h / | awk "NR==2{print \$3\" used, \"\$4\" free\"}")"
    [ -f /opt/gatemoe/.fields-done ] && echo "knowledge pack: complete" || echo "knowledge pack: in progress (or not started yet)"
    echo "--- last lines of /var/log/gatemoe-node.log"; sudo tail -n 12 /var/log/gatemoe-node.log'
  ;;
stop)  gcloud compute instances stop "$NAME" --zone "$ZONE" ;;
start) gcloud compute instances start "$NAME" --zone "$ZONE"; echo "http://$(ip_of):8000 (the address can change after a stop)" ;;
down)
  gcloud compute instances delete "$NAME" --zone "$ZONE" --quiet
  gcloud compute firewall-rules delete gatemoe-gen-web --quiet || true
  ;;
*) sed -n '2,20p' "$0"; exit 1 ;;
esac
