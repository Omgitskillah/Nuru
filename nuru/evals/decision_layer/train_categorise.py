"""Fine-tune Laya's English checkpoint on the yes/no categorisation question (categorise_train.py).

Each training request becomes 7 yes/no items, one per category, worded exactly as categorise.py
asks them, with the teacher's soft target [1 - p, p]. 10% of the requests (never trained on) are
held out to watch for overfitting and to fit the yes/no temperature. The loss is Convai's
fine-tuning notebook's (policy gradient on proper_reward + soft cross-entropy), adapted to one Mac:
MPS, full precision, gradient checkpointing, no DDP.

The result is a checkpoint folder under .runs/ that `laya.Agent(path)` loads; the pinned Hugging
Face checkpoint is only read. Nuru's own Laya keeps using the pinned one.

    caffeinate -i uv run python train_categorise.py [--epochs 3]
"""

import argparse
import json
import math
import os
import random
import shutil
import time
from datetime import datetime

import torch
from safetensors.torch import load_file, save_file

from categorise import YES_NO
from categorise_train import TRAIN_VERSION, targets
from common import RUNS

from laya.agent import Agent, _fix_tokenizer_config, _load_tokenizer
from laya.common import QTYPES, build_model, build_sequence, proper_reward

REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
BASE = os.path.expanduser(f"~/.cache/huggingface/hub/models--convaiinnovations--laya/snapshots/{REVISION}")
MICRO_BATCH, GRAD_ACCUM, GROUP_SIZE = 8, 4, 4
LR_ENCODER, LR_HEAD = 2.5e-5, 1e-4
SIGMA_START, SIGMA_END = 0.4, 0.1
HELD_OUT = 0.10
SEED = 20261001


def make_items(tok, cfg, requests):
    out = []
    for request, p in requests:
        for cat, q in YES_NO.render().items():
            seq, markers = build_sequence(tok, {"request": request}, Agent._to_internal(q),
                                          cfg["max_len"], cfg["head_max_len"])
            out.append({"ids": seq, "markers": markers, "qtype": QTYPES["noul"],
                        "target": [1 - p[cat], p[cat]], "request": request, "category": cat})
    return out


def collate(batch, pad_id, device):
    n, L = len(batch), max(len(it["ids"]) for it in batch)
    ids = torch.full((n, L), pad_id, dtype=torch.long)
    att = torch.zeros((n, L), dtype=torch.long)
    for i, it in enumerate(batch):
        ids[i, :len(it["ids"])] = torch.tensor(it["ids"])
        att[i, :len(it["ids"])] = 1
    mpos = torch.tensor([it["markers"] for it in batch])
    return (ids.to(device), att.to(device), mpos.to(device), torch.ones_like(mpos, dtype=torch.bool).to(device),
            torch.tensor([it["qtype"] for it in batch]).to(device),
            torch.tensor([it["target"] for it in batch]).to(device))


@torch.no_grad()
def logits_of(model, items, pad_id, device):
    model.eval()
    out = []
    for i in range(0, len(items), 32):
        ids, att, mpos, mmask, qtype, _ = collate(items[i:i + 32], pad_id, device)
        out.append(model(ids, att, mpos, mmask, qtype)[0].float().cpu())
    model.train()
    return torch.cat(out)


def held_out_report(logits, items, temperature=1.0):
    """Soft cross-entropy, and how often p(true) lands on the teacher's side of 0.5."""
    target = torch.tensor([it["target"] for it in items])
    logp = torch.log_softmax(logits / temperature, -1)
    agree = ((logp[:, 1].exp() > 0.5) == (target[:, 1] > 0.5)).float().mean()
    return {"soft_ce": round(float(-(target * logp).sum(-1).mean()), 4), "agreement": round(float(agree), 4)}


def fit_temperature(logits, items):
    """The notebook's fit: one temperature minimising soft cross-entropy on held-out items,
    clamped to the [0.5, 5] range laya applies at load time."""
    target = torch.tensor([it["target"] for it in items])
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():
        opt.zero_grad()
        loss = -(target * torch.log_softmax(logits / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    opt.step(closure)
    return float(torch.clamp(log_t.detach().exp(), 0.5, 5.0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=3)  # 4 in v1; held-out loss was flat after epoch 2
    args = parser.parse_args()
    device = torch.device("mps")
    random.seed(SEED)
    torch.manual_seed(SEED)

    _fix_tokenizer_config(BASE)
    cfg = json.load(open(os.path.join(BASE, "rl_agent_config.json")))
    tok = _load_tokenizer(os.path.join(BASE, "tokenizer"), cfg)
    model = build_model(cfg, encoder_dir=os.path.join(BASE, "encoder"), pretrained=False)
    model.load_state_dict({k: v.float() for k, v in load_file(os.path.join(BASE, "model.safetensors")).items()},
                          strict=True)
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True
    model.to(device).train()

    requests = targets()
    random.Random(SEED).shuffle(requests)
    n_held = round(len(requests) * HELD_OUT)
    held, train = make_items(tok, cfg, requests[:n_held]), make_items(tok, cfg, requests[n_held:])
    print(f"{len(requests) - n_held} training requests ({len(train)} items), {n_held} held out ({len(held)} items)")

    enc = [p for n, p in model.named_parameters() if n.startswith("encoder.")]
    head = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    optimizer = torch.optim.AdamW([{"params": enc, "lr": LR_ENCODER}, {"params": head, "lr": LR_HEAD}],
                                  weight_decay=0.01)
    updates = math.ceil(len(train) / (MICRO_BATCH * GRAD_ACCUM)) * args.epochs
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=updates, eta_min=1e-6)

    history = [{"epoch": 0, **held_out_report(logits_of(model, held, tok.pad_token_id, device), held)}]
    print(f"before training: held out {history[0]}", flush=True)
    started = time.monotonic()
    for epoch in range(args.epochs):
        sigma = SIGMA_START + (SIGMA_END - SIGMA_START) * epoch / max(1, args.epochs - 1)
        random.Random(SEED + epoch).shuffle(train)
        losses = []
        for step, i in enumerate(range(0, len(train), MICRO_BATCH), 1):
            ids, att, mpos, mmask, qtype, target = collate(train[i:i + MICRO_BATCH], tok.pad_token_id, device)
            logits, act = model(ids, att, mpos, mmask, qtype)
            logits = logits.float()
            eps = torch.randn((GROUP_SIZE,) + logits.shape, device=device) * sigma
            eps = eps - eps.mean(-1, keepdim=True)  # zero-mean across the two options
            z = logits.detach().unsqueeze(0) + eps
            with torch.no_grad():
                r = proper_reward(torch.softmax(z, -1), target.unsqueeze(0), qtype, mmask, w_sph=0.75, w_rps=1.0)
                adv = r - r.mean(0, keepdim=True)
                adv = adv / (adv.std() + 1e-6)
            logp = -((z - logits.unsqueeze(0)) ** 2).sum(-1) / (2 * sigma ** 2)
            loss_ce = -(target * torch.log_softmax(logits, -1)).sum(-1).mean()
            loss = (-(adv * logp).mean() + loss_ce) / GRAD_ACCUM + 0.0 * act.sum()
            loss.backward()
            losses.append(loss_ce.item())
            if step % GRAD_ACCUM == 0 or i + MICRO_BATCH >= len(train):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
            if step % 50 == 0:
                print(f"  epoch {epoch + 1} step {step}: soft CE (last 50) {sum(losses[-50:]) / 50:.4f}  "
                      f"{time.monotonic() - started:.0f}s", flush=True)
        report = held_out_report(logits_of(model, held, tok.pad_token_id, device), held)
        history.append({"epoch": epoch + 1, "train_soft_ce": round(sum(losses) / len(losses), 4), **report,
                        "seconds": round(time.monotonic() - started)})
        print(f"epoch {epoch + 1}: {history[-1]}", flush=True)

    held_logits = logits_of(model, held, tok.pad_token_id, device)
    temperature = fit_temperature(held_logits, held)
    calibrated = held_out_report(held_logits, held, temperature)
    print(f"yes/no temperature {temperature:.3f}; held out at that temperature {calibrated}")

    out = RUNS / f"laya-categorise-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    save_file({k: v.half().contiguous().cpu() for k, v in model.state_dict().items()}, str(out / "model.safetensors"))
    model.encoder.config.save_pretrained(str(out / "encoder"))
    shutil.copytree(os.path.join(BASE, "tokenizer"), out / "tokenizer")
    # Only the yes/no temperature is refitted; choice and score keep the base checkpoint's values.
    cfg["temperature"][QTYPES["noul"]] = temperature
    cfg.setdefault("temperature_by_options", {})["noul:2"] = temperature
    cfg["model_name"] = "laya-categorise"
    cfg["fine_tuned_for"] = {
        "question": "categorise_yes_no", "data": f"categorise_train.py {TRAIN_VERSION} (Claude as teacher)",
        "base": f"convaiinnovations/laya english @ {REVISION}", "date": f"{datetime.now():%Y-%m-%d}",
        "train_requests": len(requests) - n_held, "held_out_requests": n_held, "epochs": args.epochs,
        "history": history, "held_out_calibrated": calibrated,
    }
    (out / "rl_agent_config.json").write_text(json.dumps(cfg, indent=2))
    print(f"saved to {out}")


if __name__ == "__main__":
    main()
