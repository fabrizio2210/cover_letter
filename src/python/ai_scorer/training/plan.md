## Plan: Qwen2.5 CPU-Constrained Fine-Tuning Scripts

Build reproducible scripts to fine-tune qwen2.5:1.5b with QLoRA, then package to GGUF and Ollama, using the existing training JSONL and existing scorer eval gate for promotion. Because the requested stack (Unsloth + TRL + QLoRA) is CUDA-centric, the plan includes a CPU-compatible execution path so implementation is not blocked on unavailable GPU hardware.

**Steps**
1. Phase 1: Lock decisions into a single config contract: base model qwen2.5:1.5b, method QLoRA, preferred stack Unsloth + TRL, system prompt kept in samples, promotion gate eval-only, packaging target merged + GGUF + Ollama.
2. Phase 1: Add dataset preflight script validating message order, assistant labels (0..5 or N/A), empty content, split counts, and duplicate case ids; fail fast with a structured report.
3. Phase 1: Add dataset selector flags so scripts can train on with-system or no-system datasets, with default set to keep-system per decision.
4. Phase 2: Add a runtime capability detector script that chooses execution path: CUDA path uses Unsloth + TRL QLoRA, CPU path uses Transformers + PEFT fallback with reduced settings and explicit performance warning.
5. Phase 2: Add training launcher scripts with resumable checkpoints, deterministic seeds, gradient accumulation, and run manifests (config hash, dataset hash, git sha, elapsed time).
6. Phase 3: Add merge/export scripts to produce merged HF weights from adapters and prepare conversion inputs for GGUF.
7. Phase 3: Add GGUF + Ollama packaging scripts (convert, create Modelfile, build local Ollama tag) with naming conventions tied to run manifests.
8. Phase 4: Add evaluation gate script that runs existing scorer eval only and records pass/fail for promotion decisions.
9. Phase 4: Add make targets and docs for full flow: preflight -> train -> merge -> gguf -> ollama package -> eval gate.

**Parallelism and dependencies**
1. Steps 1-3 are mandatory before training.
2. Step 4 blocks step 5 because launcher behavior depends on detected runtime.
3. Steps 6-8 depend on at least one completed training run from step 5.
4. Step 9 depends on all prior steps.

**Relevant files**
- /home/fabrizio/Progetti/cover_letter/src/python/ai_scorer/training/data/export/train.jsonl — primary training input.
- /home/fabrizio/Progetti/cover_letter/src/python/ai_scorer/training/data/export/no-system-prompt/summary.json — optional alternate dataset path.
- /home/fabrizio/Progetti/cover_letter/src/python/ai_scorer/training/README.md — extend with training + packaging workflow.
- /home/fabrizio/Progetti/cover_letter/scripts/eval-scorer.sh — promotion gate command.
- /home/fabrizio/Progetti/cover_letter/src/python/ai_scorer/requirements.txt — training dependencies and fallback dependencies.
- /home/fabrizio/Progetti/cover_letter/Makefile — top-level training and packaging targets.
- /home/fabrizio/Progetti/cover_letter/docker/prod/stack.yml — reference for inference runtime alignment and Ollama service expectations.

**Verification**
1. Preflight passes on selected dataset split files with zero critical errors.
2. CPU path smoke run completes and writes checkpoints plus run manifest.
3. Merge and GGUF conversion complete and produce expected artifact files.
4. Ollama model creation succeeds and returns a runnable local tag.
5. Existing scorer eval script runs against candidate tag and passes promotion threshold.
6. Full repository test gate passes before final integration.

**Decisions**
- Base model: qwen2.5:1.5b.
- Training method: QLoRA.
- Preferred stack: Unsloth + TRL.
- Hardware profile: CPU-only.
- Inference artifact: merged weights -> GGUF -> Ollama.
- System prompt policy: keep system prompt in every sample by default.
- Promotion gate: existing scorer eval only.
- Included scope: training and packaging scripts plus eval gate automation.
- Excluded scope: distributed training, automated HPO, cloud provisioning.

**Further Considerations**
1. CPU-only constraint versus requested stack: keep Unsloth as preferred CUDA path, but implement CPU fallback path to keep project executable now.
2. Artifact storage policy remains undecided; default recommendation is local ignored artifacts folder with deterministic run ids.
3. If a GPU becomes available later, same scripts should switch automatically to Unsloth path without changing CLI interface.
