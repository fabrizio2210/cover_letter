generate-proto:
	# 1. Generate the Go code
	protoc --go_out=paths=source_relative:. src/go/internal/proto/common/common.proto
	# 2. Inject the BSON tags
	protoc-go-inject-tag  --input=src/go/internal/proto/common/common.pb.go
	# 3. Generate Python code
	protoc --python_out=src/python/ai_querier/ --proto_path=src/go/internal/proto/common/ common.proto