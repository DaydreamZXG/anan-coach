# Gemma 4 benchmark runtime

This directory contains the temporary, localhost-only inference adapter used to
compare Gemma 4 with the earlier Qwen semantic extraction benchmark.

The server accepts OpenAI-compatible `/v1/models` and `/v1/chat/completions`
requests, including `data:` image URLs. It serializes inference requests and
does not contain credentials. The workstation is reached only through an SSH
local-forward tunnel.

This runtime generates semantic candidates only. Source images and their
SHA-256 hashes remain authoritative; model text is never treated as geometry.
