# Semantic-loop pipeline

Completed actions may enqueue a bounded embedding job. The job key is a
SHA-256 digest; Redis stores only the normalized vector with a TTL, never the
source prompt, code, tool arguments, or provider output. The worker can use a
local ONNX Runtime session through `ONNXSessionEmbedder`; development and
failure-safe deployments use the deterministic hash embedder until an ONNX
model/tokenizer is installed.

Governance receives only a bounded similarity value and an explicit
`embedding_available` flag. A high similarity value is not evidence by itself:
the action must also show no diff/test progress, and the configured semantic
repeat limit must be reached. Missing or invalid embedding data therefore
cannot cause a false pause, while an invalid policy threshold fails safe to a
policy-resolution pause.
