generate-proto:
	# 1. Generate the Go code
	protoc --go_out=paths=source_relative:. src/go/internal/proto/common/common.proto
	# 2. Inject the BSON tags
	protoc-go-inject-tag  --input=src/go/internal/proto/common/common.pb.go
	# 3. Generate Python code for ai_querier
	protoc --plugin=protoc-gen-mypy=/usr/bin/protoc-gen-mypy --mypy_out=src/python/ai_querier/ --python_out=src/python/ai_querier/ --proto_path=src/go/internal/proto/common/ common.proto
	# 4. Generate Python code for ai_scorer
	protoc --plugin=protoc-gen-mypy=/usr/bin/protoc-gen-mypy --mypy_out=src/python/ai_scorer/ --python_out=src/python/ai_scorer/ --proto_path=src/go/internal/proto/common/ common.proto

.PHONY: test-fast test-full install-hooks

test-fast:
	bash scripts/test-gate.sh --mode fast

test-full:
	bash scripts/test-gate.sh --mode full

install-hooks:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit .githooks/pre-push scripts/test-gate.sh