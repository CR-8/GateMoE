# Raspberry Pi 5 setup (Pi-only, fully offline)

Target: Raspberry Pi 5 **8 GB**, active cooler, Raspberry Pi OS 64-bit (Bookworm or Trixie),
1 TB USB HDD. No NVMe, no GPU, no cloud. Everything below is done **once while online**;
afterwards the Pi runs with no network at all (the phone connects to it over Wi-Fi / hotspot).

## 1. Hardware checklist

| Item | Why |
|---|---|
| Official 27 W (5 V / 5 A) USB-C supply | below 5 A the Pi limits USB peripherals to **600 mA** |
| **Powered USB hub or self-powered HDD** | a 2.5" HDD can draw more than the Pi gives; Raspberry Pi docs warn of intermittent failures without a powered hub |
| Active cooler | lessons are 20–40 min of all-core load; every job logs CPU temperature and throttling |
| 1 TB HDD formatted ext4, mounted at `/mnt/hdd` (`noatime`) | models (~9 GB), voices (~1 GB), Kiwix ZIMs (up to ~800 GB) |

Optional: `sudo tune2fs -m 0 /dev/sdX1` gives back the 5 % (~50 GB) ext4 reserves for root.

## 2. Memory settings (important on 8 GB)

GateMoE keeps **one** large model in RAM at a time (router ≈ 6–6.5 GB peak, generator ≈ 3.3 GB).
Never let the kernel swap to the USB HDD — it makes inference 20–40× slower.

```bash
sudo apt install -y zram-tools
echo -e "ALGO=zstd\nPERCENT=25" | sudo tee /etc/default/zramswap
sudo systemctl restart zramswap
sudo dphys-swapfile swapoff && sudo systemctl disable dphys-swapfile   # Bookworm's swap file on the SD card
```

## 3. Install

```bash
git clone https://github.com/CR-8/GateMoE.git && cd GateMoE
DATA_DIR=/mnt/hdd/gatemoe ./scripts/install_pi.sh       # apt deps, Node 22, llama.cpp, venv, HyperFrames, systemd unit
DATA_DIR=/mnt/hdd/gatemoe ./scripts/download_models.sh  # Clef-flash Q4_0, Qwen3.5-4B Q4_0, voices, starter ZIMs
export GATEMOE_CONFIG=/mnt/hdd/gatemoe/gatemoe.yaml
/mnt/hdd/gatemoe/venv/bin/gatemoe doctor
```

What the download script fetches (≈ 9 GB + ZIMs):

| File | Size | Licence |
|---|---|---|
| `Cloudflare_clef-flash-Q4_0.gguf` (router) | 5.60 GB | Apache-2.0 |
| `Qwen3.5-4B-Q4_0.gguf` (generator) | 2.58 GB | Apache-2.0 |
| Supertonic 3 ONNX + 10 voice styles | ≈ 401 MB | MIT code, OpenRAIL-M weights (AI-generated disclosure) |
| Piper te/ml/mr/bn voices | ≈ 64–77 MB each | per-voice model cards |
| sherpa-onnx zh voice | 60 MB | CC0 |
| MMS kn/ta/gu/pa (exported to ONNX once, needs a temporary torch venv) | 114 MB each | **CC-BY-NC-4.0** (non-commercial) |
| Kiwix ZIMs (default: science subsets + Hindi + Kannada Wikipedia) | 0.3–10 GB each | CC BY-SA (Wikipedia) |

Want all of Wikipedia? `ZIMS="wikipedia_en_all_nopic" ./scripts/download_models.sh` (≈ 53 GB),
or `wikipedia_en_all_maxi` with images (≈ 127 GB). Add `wikipedia_<lang>_all_nopic` for each learner language.

## 4. Run

```bash
sudo systemctl enable --now gatemoe        # web app on port 8000
# or in a terminal:
gatemoe serve
gatemoe lesson "Explain Ohm's law with a quiz and a podcast"          # CLI, prints every stage
gatemoe lesson "ಓಮ್ ನಿಯಮವನ್ನು ವಿವರಿಸಿ" --mode all                    # baseline: every gateway
gatemoe route "Show me why the derivative of x^2 is 2x"               # router only
gatemoe swapbench --repeats 3                                          # model load/unload cost
```

Open `http://<pi-address>:8000` on a phone on the same network. For a demo away from home Wi-Fi,
put the Pi on the phone's hotspot (many venue networks block device-to-device traffic).

## 5. First measurements to take on the Pi

1. `gatemoe swapbench --repeats 3` — cold vs warm load of both models from the HDD.
2. `gatemoe route "<5 requests>"` — Clef-flash decision latency (expected ≈ 1–2 min with Q4_0).
3. `llama-bench -m <model> -p 512 -n 64 -t 4` for both models.
4. One full lesson per language you care about; check `lesson.json → metrics`.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `llama-server exited` with an AMX/`GGML_ASSERT` error | only on x86 dev machines: add `llama: {extra_args: ["-nr"]}` to your config. Never on the Pi. |
| Router very slow and RAM full | you are on Q4_K_M; use the Q4_0 file (fits, and gets the Cortex-A76 repack) |
| Generation crawls (< 1 token/s) | something else is using CPU; GateMoE runs stages one after another — don't run other heavy jobs |
| Video beat falls back to a bullet slide | see `lesson.json → video_render.beats[].errors`; the template rejected the planner's data |
| No audio for a language | `gatemoe doctor` lists languages without an installed voice |
