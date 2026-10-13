#!/usr/bin/env bash
# Runs on the main VM (started by scale_test.sh up): the same 8 requests on 4, 2 and 1 node(s).
#   bash scale_runs.sh <ip-node-1> <ip-node-2> ... (node 1 = this VM)
set -uo pipefail
OUT=/opt/gatemoe/data/paper1/scale
mkdir -p "$OUT"
exec >>"$OUT/scale.log" 2>&1
PASS="$(sed -n "s/^GATEMOE_PASSWORD='\(.*\)'$/\1/p" /etc/gatemoe.env)"   # readable by the gatemoe group
IPS=("$@")
for n in 4 2 1; do
  [ "$n" -le "${#IPS[@]}" ] || continue
  URLS=$(printf "http://%s:8000," "${IPS[@]:0:$n}")
  echo "$(date -Is) == $n node(s): ${URLS%,}"
  GATEMOE_PASSWORD="$PASS" /opt/gatemoe/venv/bin/python /opt/gatemoe/GateMoE/research/paper1/loadgen.py \
    --nodes "${URLS%,}" --n 8 --mode clef --seed 11 --out "$OUT/scale_${n}nodes.json"
done
echo "$(date -Is) == done"
