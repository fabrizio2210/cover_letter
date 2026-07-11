generate-proto:
	# 1. Generate the Go code
	protoc --go_out=paths=source_relative:. src/go/internal/proto/common/common.proto
	# 2. Inject the BSON tags
	protoc-go-inject-tag  --input=src/go/internal/proto/common/common.pb.go
	# 3. Generate Python code for ai_querier
	protoc --plugin=protoc-gen-mypy=/usr/bin/protoc-gen-mypy --mypy_out=src/python/ai_querier/ --python_out=src/python/ai_querier/ --proto_path=src/go/internal/proto/common/ common.proto
	# 4. Generate Python code for ai_scorer
	protoc --plugin=protoc-gen-mypy=/usr/bin/protoc-gen-mypy --mypy_out=src/python/ai_scorer/ --python_out=src/python/ai_scorer/ --proto_path=src/go/internal/proto/common/ common.proto

.PHONY: test-fast test-full install-hooks eval-scorer training-preflight training-runtime training-train training-merge training-package training-eval-gate

test-fast:
	bash scripts/test-gate.sh --mode fast

test-full:
	bash scripts/test-gate.sh --mode full

install-hooks:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit .githooks/pre-push scripts/test-gate.sh scripts/eval-scorer.sh

eval-scorer:
	bash scripts/eval-scorer.sh $(CANDIDATE_MODEL)

training-preflight:
	python3 -m src.python.ai_scorer.training.cli preflight --dataset-profile $(or $(DATASET_PROFILE),keep-system)

training-runtime:
	python3 -m src.python.ai_scorer.training.cli detect-runtime

training-train:
	python3 -m src.python.ai_scorer.training.cli train \
		--dataset-profile $(or $(DATASET_PROFILE),keep-system) \
		--run-id $(RUN_ID) \
		$(if $(SMOKE_RUN),--smoke-run,)

training-merge:
	python3 -m src.python.ai_scorer.training.cli merge \
		--run-dir src/python/ai_scorer/training/artifacts/runs/$(RUN_ID)

training-package:
	python3 -m src.python.ai_scorer.training.cli package \
		--run-dir src/python/ai_scorer/training/artifacts/runs/$(RUN_ID) \
		--convert-script $(CONVERT_SCRIPT) \
		--ollama-tag $(CANDIDATE_MODEL)

training-eval-gate:
	python3 -m src.python.ai_scorer.training.cli eval-gate \
		--candidate-model $(CANDIDATE_MODEL) \
		--run-dir src/python/ai_scorer/training/artifacts/runs/$(RUN_ID)