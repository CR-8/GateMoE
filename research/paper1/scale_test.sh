#!/usr/bin/env bash
# OPTIONAL, after the week-long campaign: measured horizontal scaling with 1, 2 and 4 GateMoE nodes.
# Run in Google Cloud Shell (the >_ icon in the console). Cost: about $2-6 of credit.
#
#   VM=<your-vm-name> ZONE=<its-zone> bash scale_test.sh up        # clone the VM into 3 more nodes, start runs
#   VM=<your-vm-name> ZONE=<its-zone> bash scale_test.sh status    # progress of the runs (~5 h in total)
#   VM=<your-vm-name> ZONE=<its-zone> bash scale_test.sh down      # delete the clones and the image (do this!)
#
# The nodes are copies of the campaign VM (same models, archives and password) and need no internet:
# the clones get no external IP. Free-trial limit: 8 vCPUs in total, so every node (the VM included)
# runs as an e2-standard-2 (2 vCPU, 8 GB - a real 8 GB node); `up` resizes the VM to it and `down`
# puts the original machine type back.
# The runs: the same 8 learner requests (CLEF routing) dispatched by loadgen.py to 4, then 2, then 1
# node(s); results land in /opt/gatemoe/data/paper1/scale/ and `paper1 bundle` includes them.
set -euo pipefail
VM="${VM:?set VM=<name of your GateMoE VM>}"
ZONE="${ZONE:?set ZONE=<zone of the VM, e.g. us-central1-a>}"
NODES="${NODES:-4}"
NODE_TYPE="${NODE_TYPE:-e2-standard-2}"
IMG=gatemoe-scale-img
ORIG_FILE="$HOME/.gatemoe_scale_orig_type_$VM"
ssh_vm() { gcloud compute ssh "$VM" --zone "$ZONE" --command "$1"; }

case "${1:-}" in
up)
  ssh_vm 'sudo systemctl disable --now gatemoe-paper1 2>/dev/null || true'
  CUR="$(gcloud compute instances describe "$VM" --zone "$ZONE" --format='value(machineType.basename())')"
  if [ "$CUR" != "$NODE_TYPE" ]; then
    echo "== resizing $VM from $CUR to $NODE_TYPE for the test (scale_test.sh down restores $CUR)"
    [ -f "$ORIG_FILE" ] || echo "$CUR" > "$ORIG_FILE"
    gcloud compute instances stop "$VM" --zone "$ZONE"
    gcloud compute instances set-machine-type "$VM" --zone "$ZONE" --machine-type "$NODE_TYPE"
    gcloud compute instances start "$VM" --zone "$ZONE"
    sleep 60
  fi
  echo "== prepare $VM: campaign service off, web service on, router memo cache off, threads = vCPUs"
  ssh_vm 'sudo /opt/gatemoe/venv/bin/python - <<EOF
import os, yaml, pathlib
p = pathlib.Path("/opt/gatemoe/data/gatemoe.yaml"); c = yaml.safe_load(p.read_text()) or {}
c.setdefault("router", {})["memo_cache"] = False
c.setdefault("hardware", {})["threads"] = os.cpu_count()
p.write_text(yaml.safe_dump(c, sort_keys=False))
EOF
    sudo rm -f /opt/gatemoe/data/cache/route_cache.json
    sudo systemctl enable --now gatemoe && sleep 5 && curl -fs http://127.0.0.1:8000/healthz'
  echo "== machine image (a few minutes)"
  gcloud compute machine-images describe "$IMG" >/dev/null 2>&1 || \
    gcloud compute machine-images create "$IMG" --source-instance "$VM" --source-instance-zone "$ZONE"
  for i in $(seq 2 "$NODES"); do
    gcloud compute instances describe "gatemoe-node-$i" --zone "$ZONE" >/dev/null 2>&1 || \
      gcloud compute instances create "gatemoe-node-$i" --zone "$ZONE" --source-machine-image "$IMG" \
        --machine-type "$NODE_TYPE" --no-address
  done
  gcloud compute firewall-rules describe gatemoe-internal >/dev/null 2>&1 || \
    gcloud compute firewall-rules create gatemoe-internal --allow tcp:8000 --source-ranges 10.0.0.0/8
  IPS="$(gcloud compute instances describe "$VM" --zone "$ZONE" --format='value(networkInterfaces[0].networkIP)')"
  for i in $(seq 2 "$NODES"); do
    IPS="$IPS $(gcloud compute instances describe "gatemoe-node-$i" --zone "$ZONE" --format='value(networkInterfaces[0].networkIP)')"
  done
  echo "== node IPs: $IPS - starting the runs on $VM (4 -> 2 -> 1 nodes)"
  ssh_vm "sudo systemd-run --unit gatemoe-scale --uid gatemoe --setenv HOME=/opt/gatemoe \
    bash /opt/gatemoe/GateMoE/research/paper1/scale_runs.sh $IPS"
  echo "started. Check with: VM=$VM ZONE=$ZONE bash scale_test.sh status"
  ;;
status)
  ssh_vm 'systemctl --no-pager status gatemoe-scale | head -4; tail -n 15 /opt/gatemoe/data/paper1/scale/scale.log 2>/dev/null; ls -l /opt/gatemoe/data/paper1/scale/ 2>/dev/null'
  ;;
down)
  for i in $(seq 2 "$NODES"); do
    gcloud compute instances delete "gatemoe-node-$i" --zone "$ZONE" --quiet || true
  done
  gcloud compute machine-images delete "$IMG" --quiet || true
  gcloud compute firewall-rules delete gatemoe-internal --quiet || true
  if [ -f "$ORIG_FILE" ]; then
    ORIG="$(cat "$ORIG_FILE")"
    echo "== restoring $VM to $ORIG"
    gcloud compute instances stop "$VM" --zone "$ZONE"
    gcloud compute instances set-machine-type "$VM" --zone "$ZONE" --machine-type "$ORIG"
    gcloud compute instances start "$VM" --zone "$ZONE"
    rm -f "$ORIG_FILE"
  fi
  echo "clones and image deleted. On $VM: 'paper1 bundle' to include the scaling results."
  ;;
*)
  echo "usage: VM=<vm> ZONE=<zone> bash scale_test.sh up | status | down"; exit 1 ;;
esac
