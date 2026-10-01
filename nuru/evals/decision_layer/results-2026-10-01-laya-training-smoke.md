# Laya fine-tuning smoke test, 2026-10-01

**Question:** can Laya's English checkpoint be fully fine-tuned on this Mac (M-series, 16 GB, MPS), for the yes/no categorisation question from [categorisation-notes.md](categorisation-notes.md)?

**Answer: yes.** Training runs, memory stays flat at about 5 GB allocated (8.7 GB including the driver's reserve), a step takes about 2 s, and the loss falls on the items trained on.

## Setup

- **Checkpoint:** the pinned English checkpoint (`convaiinnovations/laya` at revision `55cf4c4e`, ModernBERT-large, 421M parameters). It was loaded at full precision on MPS, with gradient checkpointing on the encoder and the head.
- **Data:** 20 of the 50 test prompts, chosen at random, × the 7 yes/no category questions = 140 items. The questions use the wording from `categorise.py` ("Handling the user's `request` needs this: <definition>"). Targets are p(true) = 0.95 for a gold category and 0.05 otherwise. These are test prompts, which is fine for a smoke test, but they must stay out of real training.
- **Training:** 8 steps of 8 items each, with no gradient accumulation. The loss follows Convai's [fine-tuning notebook](https://github.com/NandhaKishorM/laya/blob/main/notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb): a policy-gradient term on `proper_reward` (group of 4, σ = 0.4) plus soft cross-entropy. The optimiser is AdamW with learning rates of 2.5e-5 for the encoder and 1e-4 for the head, and gradients are clipped at 1.0. The notebook's two-GPU and CUDA-only parts were removed.
- **Script:** `smoke_train.py`, a throwaway script kept outside the repo. Nothing was saved, and Ollama had no models loaded during the run.

## Terminal output

```
loaded in 2.4s, 421M parameters {'mps_allocated': '1.69 GB', 'mps_driver': '2.16 GB', 'process_peak_rss': '3.72 GB'}
140 items, tokens min 55 median 69 max 86
training 421M parameters (full)
step 1: 4.73s  soft CE 0.2883 {'mps_allocated': '5.07 GB', 'mps_driver': '7.58 GB', 'process_peak_rss': '3.72 GB'}
step 2: 2.10s  soft CE 0.9129 {'mps_allocated': '5.07 GB', 'mps_driver': '8.66 GB', 'process_peak_rss': '3.72 GB'}
step 3: 2.45s  soft CE 0.2283 {'mps_allocated': '5.07 GB', 'mps_driver': '8.65 GB', 'process_peak_rss': '3.72 GB'}
step 4: 1.99s  soft CE 0.3142 {'mps_allocated': '5.07 GB', 'mps_driver': '8.65 GB', 'process_peak_rss': '3.72 GB'}
step 5: 1.93s  soft CE 0.6598 {'mps_allocated': '5.07 GB', 'mps_driver': '8.65 GB', 'process_peak_rss': '3.72 GB'}
step 6: 1.99s  soft CE 0.2739 {'mps_allocated': '5.07 GB', 'mps_driver': '8.65 GB', 'process_peak_rss': '3.72 GB'}
step 7: 1.87s  soft CE 0.2363 {'mps_allocated': '5.07 GB', 'mps_driver': '8.65 GB', 'process_peak_rss': '3.72 GB'}
step 8: 1.99s  soft CE 0.3382 {'mps_allocated': '5.07 GB', 'mps_driver': '8.65 GB', 'process_peak_rss': '3.72 GB'}
soft CE on 32 training items: before 0.5995, after 8 steps 0.5222 (nothing saved)
```

## What it shows

- **Memory:** 1.7 GB for the weights alone, and 5.07 GB allocated once the optimiser state exists. It stays flat from step 1 to step 8, with the driver's reserve at 8.65 GB. That leaves room on 16 GB, so the head-only fallback (`--freeze-encoder`) isn't needed. Keep Ollama's models unloaded while training, since the 3B takes about 2.2 GB more.
- **Speed:** step 1 took 4.7 s, including warm-up. Steps 2-8 averaged about 2.0 s for 8 items, or about 0.25 s per item. Items are short (55-86 tokens).
- **Learning:** the soft cross-entropy on 32 of the training items fell from 0.600 to 0.522 (-13%) after 8 steps at the notebook's learning rates. The lowest possible value with 0.95/0.05 targets is about 0.199, so there's plenty of room left. The per-batch loss jumps around because each step is a different batch. This shows the gradients reach the weights, not how good a trained Laya would be.
- **No MPS problems:** no unsupported ops, no NaNs, and no fallback to CPU.

## Estimates for a real run

At about 0.25 s per item and 4 epochs (the notebook's setting), with 10% of items held out for temperature fitting:

| Run | Requests | Items (×7) | Per epoch | 4 epochs |
|---|---|---|---|---|
| First slice | 300 | 2,100 | about 8 min | about 30 min |
| Full set | 1,000 | 7,000 | about 26 min | about 1.75 h |

These estimates assume requests of similar length. Longer states (conversation history, note excerpts) will be slower.

## Next

1. Generate and label the first-slice data, keeping the 50 test prompts out.
2. Turn this script into a real training script:
   - save the checkpoint folder, outside the pinned Hugging Face cache;
   - fit the yes/no temperature on the held-out items;
   - run 4 epochs with gradient accumulation.
3. Check the result on the 50 prompts with `categorise.py`, loading the new checkpoint.
