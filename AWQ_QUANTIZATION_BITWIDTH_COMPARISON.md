# AWQ & Low-Bit Quantization: Bit-Width Comparison Report

**Research date:** 2026-07-06 · **Backend:** AtlasForge web proxy (Brave + verbatim PDF extraction)
**Primary sources:** AWQ (MLSys 2024, arXiv 2306.00978) · OmniQuant (ICLR 2024, arXiv 2308.13137) · AQLM (arXiv 2401.06118) · Red Hat / Neural Magic (500k+ evals) · oobabooga benchmark blog · DeepSeek-R1 quant benchmarks (dat1.co) · LocalLLM.in guide.

> All perplexity numbers are WikiText-2 (lower = better) unless noted C4. All accuracy numbers are zero-shot averages (higher = better). Papers report **weight-only** quantization as `INT4/INT3/INT2` or `W4A16/W3A16/W2A16` (weights at N-bit, activations kept at FP16).

---

## 1. Executive summary — which bit-widths stay usable

| Bit-width | Verdict | Typical quality vs FP16 | Notes |
|-----------|---------|--------------------------|-------|
| **8-bit** | ✅ Effectively lossless | <2% PPL increase; >99% accuracy recovery | Interchangeable with FP16 for most uses |
| **4-bit** | ✅ Production-standard | ~0.1–0.3 PPL increase; 98–99% recovery | The sweet spot. AWQ/GPTQ both strong. Slight drop on hard code/math |
| **3-bit** | ⚠️ Usable with a good method | ~0.5–1.0 PPL increase (8–15% degradation) | RTN fails; AWQ/GPTQ/OmniQuant hold. Needs group-wise + activation-aware |
| **2-bit** | ❌ with classic PTQ / ✅ only with SOTA MCQ | RTN & AWQ **collapse** (PPL → 10⁴–10⁵); AQLM/QuIP# survive | Naïve 2-bit is garbage. Only additive/vector quant (AQLM, QuIP#, VPTQ) is usable — and only on ≥13B models |

**One-line takeaways:**
- **AWQ is a 4-bit method.** Its own repo/issue tracker confirms AWQ officially supports **only INT4** (INT3 works but is experimental; INT2 is not supported and collapses).
- **The 2-bit cliff is real for classic quantizers.** AWQ at 2-bit gives perplexity of **~200,000+** (OmniQuant Table 1). Usable 2-bit *only* comes from newer multi-codebook methods (AQLM, QuIP#, VPTQ).
- **Bigger models tolerate more compression.** A 70B at 2-bit beats a 7B at 2-bit by a wide margin; a quantized 13B routinely beats an unquantized 7B.

---

## 2. AWQ paper's own results (arXiv 2306.00978)

### 2a. AWQ vs RTN vs GPTQ — WikiText-2 PPL (INT4-g128 & INT3-g128)

| | Method | Llama-2 7B | Llama-2 13B | Llama-2 70B | LLaMA-1 7B | LLaMA-1 13B | LLaMA-1 30B | LLaMA-1 65B |
|--|--|--|--|--|--|--|--|--|
| **FP16** | — | 5.47 | 4.88 | 3.32 | 5.68 | 5.09 | 4.10 | 3.53 |
| **INT3 g128** | RTN | 6.66 | 5.52 | 3.98 | 7.01 | 5.88 | 4.88 | 4.24 |
| | GPTQ-R | 6.42 | 5.41 | 3.86 | 6.53 | 5.64 | 4.74 | 4.21 |
| | **AWQ** | **6.24** | **5.32** | **3.74** | **6.35** | **5.52** | **4.61** | **3.95** |
| **INT4 g128** | RTN | 5.73 | 4.98 | 3.46 | 5.96 | 5.25 | 4.23 | 3.67 |
| | GPTQ-R | 5.63 | 4.99 | 3.43 | 5.83 | 5.20 | 4.22 | 3.66 |
| | **AWQ** | **5.60** | **4.97** | **3.41** | **5.78** | **5.19** | **4.21** | **3.62** |

**Reading it:** At **INT4**, AWQ is within **0.1–0.3 PPL** of FP16 everywhere and beats both RTN and GPTQ. At **INT3**, AWQ still leads but the gap to FP16 widens (~0.6–0.8 on 7B). AWQ is consistently the best of the three classic methods at both bit-widths.

### 2b. AWQ on Mistral / Mixtral (MoE)

| WikiText-2 PPL | Mixtral-8×7B | Mistral-7B |
|--|--|--|
| FP16 | 5.94 | 4.14 |
| INT4-g128 (AWQ) | 6.05 | 4.30 |
| INT3-g128 (AWQ) | 6.52 | 4.83 |

### 2c. AWQ downstream accuracy (INT4-g128)

- **GSM8K (Llama-2):** FP16 → AWQ: 7B 13.87→**13.57**, 13B 26.16→**25.25**, 70B 56.41→**56.40** (essentially lossless; GPTQ lags at 7B: 12.13).
- **CodeLlama-7B MBPP pass@1:** FP16 38.53 → **AWQ 40.64** (AWQ even edges out FP16; GPTQ drops to 31.97).
- **VILA-7B/13B vision-language:** AWQ is lossless across 11 benchmarks (e.g. VILA-13B VQAv2 80.5→80.4).

### 2d. AWQ at 2-bit (INT2-g64, OPT — the only 2-bit table AWQ reports, combined with GPTQ)

| OPT WikiText PPL | 1.3B | 2.7B | 6.7B | 13B | 30B |
|--|--|--|--|--|--|
| FP16 | 14.62 | 12.47 | 10.86 | 10.13 | 9.56 |
| RTN | **10476** | **193210** | **7622** | **17564** | **8170** |
| GPTQ | 46.67 | 28.15 | 16.65 | 16.74 | 11.75 |
| **AWQ+GPTQ** | 35.71 | 25.70 | 15.71 | 13.25 | 11.38 |

Even the *best* 2-bit result here (AWQ+GPTQ) is 15–36 PPL vs ~10–14 FP16 — a **large** degradation, and RTN is utterly destroyed (PPL up to **193,210**). This is why AWQ is positioned as a 4-bit method.

### 2e. AWQ inference speedup & memory (TinyChat)

- **Memory:** W4A16 = **~4× smaller weights** (2 bytes/param FP16 → ~0.5 bytes/param INT4).
- **Speed vs Huggingface FP16:** **3.2–3.3× average** across desktop/laptop/mobile GPUs; up to **3.9× on RTX 4090**, **3.5× on Jetson Orin**.
- **TinyChat tokens/s (VILA-7B):** A100 81.6→**155.3**, RTX 4090 58.5→**168.1**, Orin 11.5→**35.6** (FP16→W4A16-AWQ).
- Enables a 13B model on an **8 GB laptop GPU** at ~30 tok/s (FP16 can't even load the 7B).

---

## 3. The definitive cross-method, cross-bit-width grid (OmniQuant, WikiText-2)

This is the single best table for "which bit-width maintains usable accuracy," because it puts RTN, GPTQ, AWQ, and OmniQuant side-by-side at 2/3/4-bit. **Bold = collapse.**

### LLaMA-2 7B (FP16 = 5.47)

| Config | RTN | GPTQ | AWQ | OmniQuant |
|--|--|--|--|--|
| W4A16 | 6.11 | 5.83 | 6.15 | **5.74** |
| W4A16 g128 | 5.72 | 5.61 | 5.62 | **5.58** |
| W3A16 | 539.48 | 8.37 | 24.00 | **6.58** |
| W3A16 g128 | 6.66 | 6.29 | 6.24 | **6.03** |
| W2A16 | **3.8e4** | **7.7e3** | — | **37.37** |
| W2A16 g128 | **4.2e3** | 36.77 | **2.2e5** | **11.06** |
| W2A16 g64 | 431.97 | 20.85 | **2.1e5** | **9.62** |

### LLaMA-2 13B (FP16 = 4.88)

| Config | RTN | GPTQ | AWQ | OmniQuant |
|--|--|--|--|--|
| W4A16 g128 | 4.98 | 4.98 | 4.97 | **4.95** |
| W3A16 g128 | 5.51 | 5.42 | 5.32 | **5.28** |
| W2A16 | **5.6e4** | **2.1e3** | — | **17.21** |
| W2A16 g64 | 26.22 | 22.44 | **1.2e5** | **7.56** |

### LLaMA-2 70B (FP16 = 3.31)

| Config | RTN | GPTQ | AWQ | OmniQuant |
|--|--|--|--|--|
| W4A16 g128 | 3.46 | 3.42 | — | **3.40** |
| W3A16 g128 | 3.97 | 3.85 | — | **3.78** |
| W2A16 | 2.0e4 | 77.95 | — | **7.81** |
| W2A16 g64 | 10.31 | NaN | — | **6.11** |

**Key facts from this grid:**
- **AWQ literally explodes at 2-bit** — perplexity of **120,000–280,000** across all LLaMA models. AWQ's grid-searched scaling cannot represent 4 levels; the paper explicitly notes AWQ fails at W2A16.
- **RTN dies at both 2-bit AND ungrouped 3-bit** (e.g. LLaMA-2-7B W3A16 RTN = 539.48).
- At **4-bit**, all four methods are near-FP16 — the method barely matters. At **3-bit**, method choice becomes critical. At **2-bit**, only a method built for it (OmniQuant, and the MCQ methods below) survives.
- **Scale helps:** LLaMA-2-70B at W2A16-g64 with OmniQuant is PPL 6.11 — only ~2.8 above FP16, i.e. genuinely usable. The 7B at the same setting is 9.62.

---

## 4. State-of-the-art 2-bit — AQLM (additive/multi-codebook quantization)

Classic PTQ can't do 2-bit; AQLM, QuIP#, and VPTQ can. AQLM results (LLaMA-2, WikiText-2 / C4 / avg zero-shot acc):

### ~2-bit

| Model | Method | Avg bits | Wiki2 | C4 | Avg acc |
|--|--|--|--|--|--|
| 7B | FP16 | 16 | 5.12 | 6.63 | 62.35 |
| 7B | **AQLM** | 2.02 | **6.59** | 8.54 | 57.28 |
| 7B | QuIP# | 2.02 | 8.22 | 11.01 | 52.23 |
| 13B | FP16 | 16 | 4.57 | 6.05 | 65.38 |
| 13B | **AQLM** | 1.97 | **5.60** | 7.49 | 61.32 |
| 13B | QuIP# | 2.01 | 6.06 | 8.07 | 57.55 |
| 70B | FP16 | 16 | 3.12 | 4.97 | 70.17 |
| 70B | **AQLM** | 2.07 | **3.94** | 5.72 | 68.75 |
| 70B | QuIP# | 2.01 | 4.16 | 6.01 | 67.67 |

### ~3-bit (vs GPTQ / SpQR / QuIP)

| Model | Method | Avg bits | Wiki2 | Avg acc |
|--|--|--|--|--|
| 7B | **AQLM** | 3.04 | **5.46** | 60.88 |
| 7B | GPTQ | 3.00 | 8.06 | 53.08 |
| 7B | SpQR | 2.98 | 6.20 | 59.07 |
| 70B | **AQLM** | 3.01 | **3.36** | 69.86 |
| 70B | GPTQ | 3.00 | 4.40 | 65.41 |

### ~4-bit (near-lossless for all good methods)

| Model | Method | Avg bits | Wiki2 | Avg acc |
|--|--|--|--|--|
| 7B | AQLM | 4.04 | 5.21 | 62.55 (vs FP16 62.35) |
| 70B | AQLM | 4.14 | 3.19 | 69.93 (vs FP16 70.17) |

**Headline AQLM findings:**
- AQLM is the **first method to push 2-bit onto the Pareto frontier** (~2.5 bits/param). At 2-bit, a **13B AQLM (PPL 5.60)** beats a **7B FP16 (PPL 5.12? — no, close)**; more strikingly, a **2.76-bit 13B beats the uncompressed 7B**, and a fine-tuned 2.19-bit 13B is comparable to the uncompressed 7B.
- **Memory:** up to **8× reduction** vs FP16.
- **Speed:** Llama-2-70B runs at **~14 tok/s on a single 24 GB RTX 3090** (2×8-bit config: 14.3 tok/s vs 5.8 FP16); matvec speedups up to **3.05× GPU / 4.07× CPU**.

### AQLM on Mistral / Mixtral

| Model | Bits | Method | Wiki2 | Avg acc (FP16) |
|--|--|--|--|--|
| Mixtral-8×7B | 1.98 | AQLM | 4.61 | 67.68 (FP16 72.33) |
| Mistral-7B | 2.01 | AQLM | 6.32 | 62.17 (FP16 68.67) |
| Mistral-7B | 2.01 | AQLM⋆(finetuned) | 5.76 | 63.75 |

---

## 5. Independent large-scale validation (Red Hat / Neural Magic, 500k+ evals, Llama-3.1)

Tested W8A8-INT, W8A8-FP, W4A16-INT on Llama-3.1 **8B / 70B / 405B**:

| Scheme | Compression | Speedup | Accuracy recovery |
|--|--|--|--|
| W8A8 (8-bit) | ~2× | ~1.8× (server) | >99% (OpenLLM v1); ~99% (v2) |
| W4A16 (4-bit) | ~3.5× | ~2.4× (single-stream) | >99% (v1); ≥96% (v2); **HumanEval 98.9% recovery** |

- **All schemes recover >99% of the FP16 average on OpenLLM Leaderboard v1**, regardless of model size.
- 8-bit HumanEval/HumanEval+ recovery = **99.9%**; 4-bit = **98.9%**.
- Larger models (70B, 405B) show **negligible** degradation; 8B shows slightly more variance but preserves semantic meaning.

---

## 6. Memory savings, speedup & degradation — consolidated reference

### Memory savings vs FP16/FP32 (LocalLLM.in + AWQ paper)

| Bit-width | Size reduction vs FP32 | Bytes/param | Practical effect |
|--|--|--|--|
| 8-bit | 75% | 1.0 | 7B ≈ 7 GB |
| 4-bit | 87.5% | 0.5 | 7B ≈ 3.5 GB; **~4×** vs FP16 |
| 3-bit | 90.6% | ~0.375 | ultra-compact |
| 2-bit | 93.75% | ~0.25 | up to **8×** vs FP16 (AQLM) |

### Inference speedup (bit-width, general)

| Method / bit-width | Speedup vs FP16 |
|--|--|
| 8-bit (W8A8) | 1.8× (server, Red Hat) |
| 4-bit (W4A16, Red Hat) | 2.4× single-stream |
| 4-bit AWQ / TinyChat | 3.2–3.3× avg, up to 3.9× (4090) |
| 2-bit AQLM (70B, RTX 3090) | ~2.5× tok/s (5.8→14.3) |

*Caveat (AutoAWQ):* the speedup is a **memory-bandwidth** win (weights are 3–4× smaller). At **high batch sizes** (compute-bound) W4A16 can lose its advantage because of INT4→FP16 dequant overhead.

### Quality degradation rule-of-thumb (LocalLLM.in)

| Bit-width | Quality degradation vs FP16 |
|--|--|
| 8-bit | <2% (minimal) |
| 4-bit | 2–8% (moderate) |
| 3-bit | 8–15% (noticeable but acceptable) |
| 2-bit | 15–30% (significant) — usable only with SOTA MCQ methods |

---

## 7. Real-world task behavior (DeepSeek-R1 benchmarks, dat1.co + LessWrong Llama-3)

- **Coding & data-analysis are the most quantization-sensitive** tasks — Q3_K_M and Q2_K "significantly reduce scores." HumanEval+/SWE-Bench drop more than averaged benchmarks suggest at 4-bit.
- **Reasoning is surprisingly robust** — a 14B Q2_K model *outperformed* all 8B variants on hard logic puzzles, reinforcing "bigger-model-heavily-quantized > smaller-model-lightly-quantized" for reasoning.
- **Llama-3 8B (LessWrong):** 16→8-bit is basically free; 4-bit shows "noticeable but not massive" degradation. AWQ-4 slightly beats GPTQ-4 (MMLU 55.55% vs 55.21%; The Pile PPL 8.483 vs 8.575).
- **Rule of thumb:** running FP16 rarely makes sense — a **larger, quantized** model usually beats a smaller full-precision one at equal memory. 4-bit is the popular balance point.

---

## 8. Bottom line by bit-width

| Bit-width | Use it? | Best method(s) | Memory | Speed | Quality |
|--|--|--|--|--|--|
| **8-bit** | Yes, always safe | RTN/W8A8 fine | 2× | 1.8× | Lossless (>99%) |
| **4-bit** | **Default choice** | **AWQ**, GPTQ, AQLM | 3.5–4× | 2.4–3.9× | 98–99% recovery |
| **3-bit** | Yes, with care | AWQ, OmniQuant, AQLM, SpQR (NOT RTN) | ~5× | — | 8–15% loss; ≥13B preferred |
| **2-bit** | Only with SOTA MCQ | **AQLM, QuIP#, VPTQ, OmniQuant** (NOT AWQ/RTN/GPTQ) | 6–8× | ~2.5× | Big loss on ≤7B; usable on 13B+/70B |

**The AWQ verdict specifically:** AWQ is the best-in-class **4-bit** activation-aware method — near-lossless at INT4, still leading at INT3, and it fully collapses at INT2 (PPL ~200k). For sub-3-bit, the field has moved to additive/vector-codebook methods (AQLM, QuIP#, VPTQ).
