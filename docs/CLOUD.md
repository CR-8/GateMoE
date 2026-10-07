# Running GateMoE on a cloud VM (AWS EC2 and others)

GateMoE is designed for a Raspberry Pi 5, but any Linux VM with enough RAM runs it the same
way (CPU only, no GPU needed). One command installs everything and starts a web server.

## 1. Pick an instance

The Clef-flash router needs ~6.6 GB of RAM while it decides; the generator ~3.3 GB (only one
is loaded at a time). **Less than 8 GB will not work** - a t3.micro (1 GB) cannot run it.

| AWS type | vCPU / RAM | approx. us-east-1 on-demand | verdict |
|---|---|---|---|
| t3.micro / t3.small / t3.medium | 2 / 1-4 GB | free tier - $0.04/h | too small |
| t3.large | 2 / 8 GB | ~$0.08/h | minimum: works, tight and slow |
| **t3.xlarge** | 4 / 16 GB | ~$0.17/h | recommended for trying it out |
| m7i.xlarge | 4 / 16 GB | ~$0.20/h | steady speed (not burstable) |
| t4g.xlarge (Graviton, arm64) | 4 / 16 GB | ~$0.13/h | cheaper; arm64 works the same |

T3 instances are *burstable*: lesson generation keeps the CPU at 100 %, which uses CPU credits
(AWS's default "unlimited" mode bills extra, roughly $0.05 per vCPU-hour above the baseline).
For long sessions an m7i/m6i instance is simpler. **Stop the instance when you are not using it.**

Disk: 30 GB is enough (software ~3 GB, models ~8 GB, voices ~1 GB, offline knowledge ~2 GB).
Ubuntu 22.04 / 24.04 or Debian 12 / 13, x86_64 or arm64.

Already launched a smaller instance? Stop it -> Actions -> Instance settings -> **Change instance
type** -> start it. The disk and everything on it stay.

## 2. Install (one command)

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/CR-8/GateMoE/main/scripts/install_cloud.sh | sudo bash
```

It takes ~20-30 minutes (mostly compiling llama.cpp and downloading ~10 GB of models). It:
checks RAM/disk, installs system packages (ffmpeg, Graphviz, Noto fonts, Node 22, a headless
Chromium), builds llama.cpp for this CPU, installs the Python app, downloads the models, voices and
offline knowledge (Wikipedia physics/chemistry/computing, LibreTexts K-12, PhET simulations), sets a
login password and starts a systemd service. At the end it prints the URL and the password.

Options go in front of `bash`, e.g. Kannada lessons with Kannada Wikipedia + PhET + voice:

```bash
curl -fsSL https://raw.githubusercontent.com/CR-8/GateMoE/main/scripts/install_cloud.sh | sudo LANGS="kn" bash
```

| variable | default | meaning |
|---|---|---|
| `LANGS` | (none) | extra languages to download knowledge for, e.g. `"kn hi"`; kn/ta/gu/pa also export the Meta MMS voice (~2.5 GB temporarily) |
| `ZIMS` | small STEM set | exact list of Kiwix archive prefixes (see `scripts/download_models.sh`) |
| `GATEMOE_PASSWORD` | random | web login password (user `gatemoe`) |
| `PORT` | 8000 | web port |
| `GATEMOE_REF` | main | git branch or tag |
| `SKIP_MODELS=1` | | software only |

Re-running the command is safe (finished steps are skipped, downloads resume) and is also how you
update.

## 3. Open it

Either open the port, or tunnel through SSH:

* **Security group:** EC2 -> the instance -> Security -> security group -> Edit inbound rules ->
  add *Custom TCP 8000*, source **My IP**. Then browse to `http://<public-ip>:8000` and log in.
  The password travels unencrypted over plain HTTP; use the tunnel or put HTTPS in front for
  anything beyond a quick test.
* **SSH tunnel (no open port, encrypted):** `ssh -L 8000:localhost:8000 ubuntu@<public-ip>` and
  open `http://localhost:8000`.

If you open it by a host name (e.g. your own domain), add the name to `GATEMOE_ALLOWED_HOSTS` in
`/etc/gatemoe.env` (IP addresses and the EC2 public DNS name are allowed already) and
`sudo systemctl restart gatemoe`.

## 4. Operate

```bash
sudo systemctl status gatemoe            # running?
journalctl -u gatemoe -f                 # logs
sudo -u gatemoe bash -c 'set -a; . /etc/gatemoe.env; /opt/gatemoe/venv/bin/gatemoe doctor'
sudo nano /opt/gatemoe/data/gatemoe.yaml # settings (then: sudo systemctl restart gatemoe)
```

Lessons are stored under `/opt/gatemoe/data/jobs/`. A lesson takes several minutes on 4 vCPUs
(the router ~30-60 s, text generation most of the rest).

## 5. Troubleshooting

| symptom | cause / fix |
|---|---|
| installer stops with "needs >= 8 GB" | change the instance type (section 1) |
| lesson fails at "route" / "acquire" with a killed process | out of memory: use a 16 GB instance |
| page asks for a password again and again | wrong password; see `/etc/gatemoe.env` |
| "host not allowed" | add the host name to `GATEMOE_ALLOWED_HOSTS` in `/etc/gatemoe.env` |
| very slow after a while on t3 | CPU credits exhausted (standard mode) - switch to unlimited or m7i |
| llama-server crashed on an Intel Sapphire Rapids VM | handled: `-nr` is added automatically on CPUs with AMX (`llama.auto_no_repack`) |

## 6. Google Cloud (free trial) - a 3-month plan for the $300 / ₹28,797 credit

Free-trial rules that shape the plan: **at most 8 vCPUs running at once, no GPUs, no quota
increases**, and nothing is ever charged unless you click *Upgrade* (when the credit or the 90 days
run out, VMs are stopped). GateMoE is CPU-only, so the limits fit. Prices below are approximate
list prices (us-central1, Linux, on-demand); the console shows the exact INR estimate on the
"Create instance" page.

| VM | machine | why | hours | approx. cost |
|---|---|---|---|---|
| `gatemoe-demo` | **e2-standard-4** (4 vCPU, 16 GB, x86), 60 GB balanced disk, Ubuntu 24.04 | the app for daily use and demos | 12 h/day (auto schedule) | $0.134/h -> ~$145 + disk ~$18 + IP ~$5 = **~$168** |
| `gatemoe-arm` | **t2a-standard-4** (4 vCPU Ampere Altra, 16 GB, arm64), 60 GB balanced disk, Ubuntu 24.04 arm64 | Altra's Neoverse N1 cores are the server version of the Pi 5's Cortex-A76: the closest cloud stand-in for Pi measurements (arm64 builds, Q4_0 repack path) | ~5 h/day | $0.154/h -> ~$69 + disk ~$18 + IP ~$2 = **~$90** |
| buffer | | extra hours in the final weeks, egress, snapshots | | **~$40** |

Both VMs together use exactly the 8-vCPU limit. If `t2a-standard-4` is refused (zone capacity or
quota), try zones `us-central1-b` / `-f`, or `c4a-standard-4` (Google Axion, also arm64).

### Commands (Cloud Shell: the `>_` icon at the top right of the console)

```bash
gcloud config set project headlessui            # your project ID
gcloud services enable compute.googleapis.com

INSTALL='#!/bin/bash
[ -f /etc/systemd/system/gatemoe.service ] || curl -fsSL https://raw.githubusercontent.com/CR-8/GateMoE/main/scripts/install_cloud.sh | GATEMOE_PASSWORD=pick-a-strong-one LANGS=kn bash > /var/log/gatemoe-install.log 2>&1'

gcloud compute instances create gatemoe-demo --zone=us-central1-a --machine-type=e2-standard-4 \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=60GB --boot-disk-type=pd-balanced --tags=gatemoe --metadata=startup-script="$INSTALL"

gcloud compute instances create gatemoe-arm --zone=us-central1-a --machine-type=t2a-standard-4 \
  --image-family=ubuntu-2404-lts-arm64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=60GB --boot-disk-type=pd-balanced --tags=gatemoe --metadata=startup-script="$INSTALL"
```

Each VM installs itself on first boot (~30 min). Follow it with
`gcloud compute ssh gatemoe-demo --zone=us-central1-a --command='sudo tail -f /var/log/gatemoe-install.log'`.

Access - open port 8000 for **your own** IP (look it up in your laptop's browser, "what is my ip";
Cloud Shell's own IP is different), then browse to `http://<EXTERNAL_IP>:8000`, user `gatemoe`:

```bash
gcloud compute firewall-rules create gatemoe-web --allow=tcp:8000 --target-tags=gatemoe \
  --source-ranges=YOUR.IP.ADDR.ESS/32
gcloud compute instances list          # EXTERNAL_IP column
```

Home connections in India often change IP; update the rule with
`gcloud compute firewall-rules update gatemoe-web --source-ranges=NEW.IP/32`.

Run the demo VM 09:00-21:00 IST automatically (one-time setup):

```bash
gcloud compute resource-policies create instance-schedule gatemoe-hours --region=us-central1 \
  --vm-start-schedule="0 9 * * *" --vm-stop-schedule="0 21 * * *" --timezone=Asia/Kolkata
PN=$(gcloud projects describe headlessui --format='value(projectNumber)')
gcloud projects add-iam-policy-binding headlessui --role=roles/compute.instanceAdmin.v1 \
  --member="serviceAccount:service-$PN@compute-system.iam.gserviceaccount.com"
gcloud compute instances add-resource-policies gatemoe-demo --zone=us-central1-a --resource-policies=gatemoe-hours
```

Start / stop the ARM VM around experiment sessions:
`gcloud compute instances start|stop gatemoe-arm --zone=us-central1-a`.

Budget alerts: Billing -> Budgets & alerts -> Create budget, amount ₹28,797, alerts at
25 / 50 / 75 / 90 %. Check Billing -> Reports weekly; if spend runs behind plan in December, run
`gatemoe-arm` longer (more experiments) or remove the demo schedule.

### Experiments worth the ARM hours (Pi-like measurements)

```bash
sudo -u gatemoe bash -c 'set -a; . /etc/gatemoe.env; cd /opt/gatemoe/GateMoE
  /opt/gatemoe/venv/bin/gatemoe swapbench                         # model load/unload cost
  /opt/gatemoe/venv/bin/gatemoe route "Explain Ohm'"'"'s law with a quiz"   # router latency
  /opt/gatemoe/venv/bin/python research/genbench/genbench.py --lesson <jobs/ID/lesson.json> \
     --tasks notes,quiz,video --variants baseline,compact,draft-0.8b-n2 \
     --draft-model /opt/gatemoe/data/models/<small-draft>.gguf --out research/genbench/results/t2a.json'
```

Run each lesson request with `--mode clef` and `--mode all` and compare with
`scripts/analyze_jobs.py` (theoretical vs realised savings - the project's research question).
