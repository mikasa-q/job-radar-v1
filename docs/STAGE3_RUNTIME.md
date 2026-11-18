# Optional local embedding runtime

Default BM25 paths do not import ML libraries. Base requirements are unchanged.

Verified isolated runtime: macOS 15.8.1 Intel x86_64, CPython 3.10.6, torch 2.2.2, sentence-transformers 3.4.1, transformers 4.46.3, tokenizers 0.20.3, numpy 1.26.4. All transitive binary-wheel URLs and SHA256 are in `config/requirements.embedding.macos-x86_64.lock.txt`. Import, CPU tensor sum, `local_files_only` constructor availability and `pip check` passed before model transfer. Initial lock generation handled pip22 `archive_info.hash` (not newer `hashes`) format; failed first attempt installed nothing.

Use a separate venv and `pip install --require-hashes --only-binary=:all: --no-deps -r <absolute-lock-path>`. This lock targets the verified OS/Python/CPU only. No source compilation or implicit alternate backend.

Approved candidate full revisions, independently read official API/model cards:

|model|revision|pooling|dimensions|native tokens|license|
|---|---|---|---|---|---|
|intfloat/multilingual-e5-small|614241f622f53c4eeff9890bdc4f31cfecc418b3|mean + normalize|384|512|MIT|
|BAAI/bge-small-zh-v1.5|7999e1d3359715c523056ef9478215996d62a620|CLS + normalize|512|512|MIT|
|sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2|e8f8c211226b894fcb81acc59f3b34ba3efd5f42|mean + normalize at call|384|128|Apache-2.0|

E5 uses `query: ` and `passage: `. BGE query uses `为这个句子生成表示以用于检索相关文章：`, passage no instruction; instruction benefit remains a dev evaluation question. MiniLM has neither prefix. Model files: README/config/modules/sentence_bert_config/tokenizer/special_tokens/1_Pooling/config plus one `model.safetensors`, optional vocab/sentencepiece/config_sentence_transformers if present. Never `.bin` duplicates, ONNX/OpenVINO variants or remote custom code.

Runtime `LocalTokenBudget` uses the actual fast tokenizer character offsets and counts role prefix+special tokens; long queries explicitly fail before encode truncation. `SupervisedSnapshotClient` launches local-only CPU batch1 worker processes and kills their process group on timeout/RSS threshold. Each call reloads the model; it is not the hot-model benchmark adapter. Process-tree RSS polling is sampled peak, not a guarantee against kernel OOM.

The main probe uses `examples/stage3_real_probe.py` with explicit local snapshot/model/revision/runtime hash/index/output arguments. `examples/stage3_benchmark.py` is a separate synthetic lexical scale benchmark. Human relevance quality stays pending without human labels; synthetic vectors are never used as model evidence.

## Explicit transfer evidence

The host's official huggingface.co connection returned Errno65 / curl28; DNS returned an unrelated address. No global DNS/proxy changes were made. Pinned metadata/cards were read through the external retrieval tool. The same pinned revisions were transferred explicitly through hf-mirror.com (transport mirror only, not another model/backend). TLS verification remained enabled; Python used certifi's CA file. Model safetensors and tokenizer LFS SHA256 values were checked against the official revision metadata, and ordinary text/JSON bytes were checked against official Git blob SHA1. Curl transfer succeeded where Python urllib downloads received403. All final files also receive local SHA256 manifests. Inference remains offline and never uses the mirror implicitly.
