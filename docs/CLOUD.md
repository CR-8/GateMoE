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
