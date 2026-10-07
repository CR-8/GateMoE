"""One-time exporter: facebook/mms-tts-<iso3> (HF transformers VitsModel) -> ONNX.

Run once per language in a *throw-away* venv (torch CPU + transformers + onnx +
onnxruntime); the runtime (mms_tts.py) needs only numpy + onnxruntime.

    python export_mms_onnx.py --lang kan --out /models/mms/kan [--int8]

Writes into --out:
    model.onnx        fp32, sherpa-onnx style signature (see below)
    model.int8.onnx   (with --int8) dynamic-quantised weights (MatMul only)
    vocab.json, tokenizer_config.json, config.json, tokens.txt, LICENSE.txt

ONNX signature (same names as sherpa-onnx VITS models):
    x             int64   [1, T]   token ids incl. interleaved blanks (id 0)
    x_length      int64   [1]
    noise_scale   float32 [1]      0.667 in transformers
    length_scale  float32 [1]      1/speaking_rate (1.0 = normal, 1.1 = slower)
    noise_scale_w float32 [1]      0.8 in transformers
    -> y          float32 [1, 1, N]  16 kHz waveform

MMS-TTS weights are licensed CC-BY-NC-4.0 (non-commercial).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn


class MMSForONNX(nn.Module):
    """Re-implementation of VitsModel.forward (transformers 5.x) with the
    sampling scales as tensor inputs; batch size 1, single speaker."""

    def __init__(self, m):
        super().__init__()
        self.m = m
        self.hop = int(np.prod(m.config.upsample_rates))

    def forward(self, x, x_length, noise_scale, length_scale, noise_scale_w):
        m = self.m
        T = x.shape[1]
        attention_mask = (torch.arange(T, device=x.device).unsqueeze(0) < x_length.unsqueeze(1)).long()
        pad_mask = attention_mask.unsqueeze(-1).float()
        enc = m.text_encoder(input_ids=x, padding_mask=pad_mask, attention_mask=attention_mask, return_dict=True)
        hidden = enc.last_hidden_state.transpose(1, 2)
        in_mask = pad_mask.transpose(1, 2)                     # [1,1,T]
        prior_means, prior_logv = enc.prior_means, enc.prior_log_variances
        if m.config.use_stochastic_duration_prediction:
            log_dur = m.duration_predictor(hidden, in_mask, None, reverse=True, noise_scale=noise_scale_w)
        else:
            log_dur = m.duration_predictor(hidden, in_mask, None)
        duration = torch.ceil(torch.exp(log_dur) * in_mask * length_scale)
        pred_len = torch.clamp_min(torch.sum(duration, [1, 2]), 1).long()
        idx = torch.arange(pred_len.max(), dtype=pred_len.dtype, device=x.device)
        out_mask = (idx.unsqueeze(0) < pred_len.unsqueeze(1)).unsqueeze(1).to(in_mask.dtype)
        attn_mask = torch.unsqueeze(in_mask, 2) * torch.unsqueeze(out_mask, -1)
        b, _, out_len, in_len = attn_mask.shape
        cum = torch.cumsum(duration, -1).view(b * in_len, 1)
        ind = torch.arange(out_len, dtype=duration.dtype, device=x.device)
        valid = (ind.unsqueeze(0) < cum).to(attn_mask.dtype).view(b, in_len, out_len)
        padded = valid - nn.functional.pad(valid, [0, 0, 1, 0, 0, 0])[:, :-1]
        attn = padded.unsqueeze(1).transpose(2, 3) * attn_mask
        prior_means = torch.matmul(attn.squeeze(1), prior_means).transpose(1, 2)
        prior_logv = torch.matmul(attn.squeeze(1), prior_logv).transpose(1, 2)
        z = prior_means + torch.randn_like(prior_means) * torch.exp(prior_logv) * noise_scale
        lat = m.flow(z, out_mask, None, reverse=True)
        wav = m.decoder(lat * out_mask, None)                  # [1,1,N]
        return wav


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True, help="MMS iso-639-3 code, e.g. kan tam tel ben mar mal guj pan")
    ap.add_argument("--repo", help="override HF repo (default facebook/mms-tts-<lang>)")
    ap.add_argument("--revision", default=None, help="pin a HF commit sha (default: main)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--int8", action="store_true")
    ap.add_argument("--opset", type=int, default=17)
    a = ap.parse_args()
    from transformers import VitsModel, VitsTokenizer
    from huggingface_hub import hf_hub_download

    repo = a.repo or f"facebook/mms-tts-{a.lang}"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    model = VitsModel.from_pretrained(repo, revision=a.revision).eval()
    tok = VitsTokenizer.from_pretrained(repo, revision=a.revision)
    for f in ("vocab.json", "tokenizer_config.json", "config.json"):
        shutil.copy(hf_hub_download(repo, f, revision=a.revision), out / f)
    vocab = json.loads((out / "vocab.json").read_text(encoding="utf-8"))
    with open(out / "tokens.txt", "w", encoding="utf-8") as fh:
        for ch, i in sorted(vocab.items(), key=lambda kv: kv[1]):
            fh.write(f"{ch} {i}\n")
    (out / "LICENSE.txt").write_text(
        f"Model: {repo} (Meta MMS-TTS), exported to ONNX by GateMoE export_mms_onnx.py\n"
        "License: CC-BY-NC-4.0 https://creativecommons.org/licenses/by-nc/4.0/\n")

    wrapper = MMSForONNX(model).eval()
    sample = "".join(list(vocab)[1:12]) * 3
    ids = tok(sample, return_tensors="pt")["input_ids"]
    x_len = torch.tensor([ids.shape[1]], dtype=torch.int64)
    ns, ls, nw = (torch.tensor([v], dtype=torch.float32) for v in (0.667, 1.0, 0.8))
    onnx_path = out / "model.onnx"
    with torch.no_grad():
        torch.onnx.export(
            wrapper, (ids, x_len, ns, ls, nw), str(onnx_path),
            input_names=["x", "x_length", "noise_scale", "length_scale", "noise_scale_w"],
            output_names=["y"],
            dynamic_axes={"x": {1: "T"}, "y": {2: "N"}},
            opset_version=a.opset, dynamo=False, do_constant_folding=True,
        )
    import onnx
    mp = onnx.load(str(onnx_path))
    meta = {"model_type": "vits", "comment": "mms", "frontend": "characters", "language": a.lang, "add_blank": "1",
            "n_speakers": "1", "sample_rate": str(model.config.sampling_rate),
            "punctuation": "", "url": f"https://huggingface.co/{repo}", "license": "CC-BY-NC-4.0"}
    for k, v in meta.items():
        e = mp.metadata_props.add()
        e.key, e.value = k, v
    onnx.save(mp, str(onnx_path))
    print(f"exported {onnx_path} ({onnx_path.stat().st_size/1e6:.1f} MB) in {time.time()-t0:.1f}s")

    # parity check against PyTorch with noise disabled (noise scales = 0)
    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    z = torch.tensor([0.0])
    with torch.no_grad():
        ref = wrapper(ids, x_len, z, ls, z).numpy().reshape(-1)
    got = sess.run(None, {"x": ids.numpy(), "x_length": x_len.numpy(), "noise_scale": np.zeros(1, np.float32),
                          "length_scale": np.ones(1, np.float32), "noise_scale_w": np.zeros(1, np.float32)})[0].reshape(-1)
    n = min(len(ref), len(got))
    print(f"parity: torch {len(ref)} vs onnx {len(got)} samples, max|diff| = {np.abs(ref[:n]-got[:n]).max():.2e}")
    for T in (5, 6, 51, 52, 301):                       # odd and even lengths must both work
        xx = np.zeros((1, T), np.int64); xx[0, 1::2] = 5
        sess.run(None, {"x": xx, "x_length": np.array([T], np.int64), "noise_scale": ns.numpy(),
                        "length_scale": ls.numpy(), "noise_scale_w": nw.numpy()})
    print("length sweep ok")

    if a.int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        q = out / "model.int8.onnx"
        quantize_dynamic(str(onnx_path), str(q), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"])
        mq = onnx.load(str(q))
        have = {p.key for p in mq.metadata_props}
        for k, v in meta.items():
            if k not in have:
                e = mq.metadata_props.add()
                e.key, e.value = k, v
        onnx.save(mq, str(q))
        print(f"int8: {q} ({q.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
