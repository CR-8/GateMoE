"""The paper-1 campaign runner end to end against the fake llama-server (few items per phase)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "research" / "paper1" / "run.py"


def test_campaign_smoke(cfg, tmp_path):
    conf = tmp_path / "paper1.yaml"
    tree = json.loads(json.dumps(cfg.tree))
    tree["router"]["memo_cache"] = False
    conf.write_text(yaml.safe_dump(tree))
    env = dict(os.environ, GATEMOE_CONFIG=str(conf), PAPER1_DIR=str(tmp_path / "out"))
    res = subprocess.run([sys.executable, str(RUN), "run", "--days", "1", "--limit", "2",
                          "--phases", "p1_swap,p2_router,p3_ablation,p4_e2e,p9_knowledge"],
                         env=env, capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stdout + res.stderr
    out = tmp_path / "out"
    assert len(list((out / "p2_router" / "items").glob("*.json"))) == 2
    e2e = [json.loads(p.read_text()) for p in (out / "p4_e2e" / "items").glob("*.json")]
    assert len(e2e) == 2 and all(r["wall_outer_s"] > 0 and r["metrics"] for r in e2e)
    kb = {json.loads(p.read_text())["arm"]: json.loads(p.read_text()) for p in (out / "p9_knowledge" / "items").glob("*.json")}
    assert kb["kb_on"]["knowledge_enabled"] and not kb["kb_off"]["knowledge_enabled"]
    res = subprocess.run([sys.executable, str(RUN), "analyze"], env=env, capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stdout + res.stderr
    summary = json.loads((out / "analysis" / "summary.json").read_text())
    assert summary["router"]["n_requests"] == 2 and summary["e2e"]["lessons"] == 2
    res = subprocess.run([sys.executable, str(RUN), "status"], env=env, capture_output=True, text=True, timeout=120)
    assert "p4_e2e" in res.stdout
