#!/bin/bash
# Novel Studio boot (v4) - fetched from GitHub by the template start command.
# Idempotent: reuses llama.cpp build + model already on /workspace.
B=/workspace/novel_llm
REPO=https://raw.githubusercontent.com/patet70-hash/novel-studio-app/main
SHA_APP=33ab95ebcb7c2a031370e45ac90f98cf78199600fc184633d92d6a2e856f8b6d
SHA_IDX=a4d6959988d648a0d93393a1a7589118426c44dbb3bbfca8762f57b944ac45a3
mkdir -p $B/models $B/novel $B/app
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }

# 1) wait for network (first seconds after start are not ready)
for i in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 https://raw.githubusercontent.com)" != "000" ] && break
  sleep 3
done
log "network wait done (try $i)"

# 2) fetch app files and verify sha256 BEFORE using them
fetch(){ # name expected_sha
  for t in 1 2 3 4 5; do
    curl -fsSL -m 60 "$REPO/$1" -o "$B/app/$1.tmp" && \
    [ "$(sha256sum "$B/app/$1.tmp" | cut -d' ' -f1)" = "$2" ] && mv "$B/app/$1.tmp" "$B/app/$1" && return 0
    sleep 3
  done
  return 1
}
if fetch app.py $SHA_APP && fetch index.html $SHA_IDX; then
  log "app files verified"
else
  log "ERROR: app files missing or sha mismatch - NOT starting app"; exit 1
fi

# 3) python deps (PDF export) - non-fatal for the app itself
( pip install -q --break-system-packages markdown weasyprint >/dev/null 2>&1; \
  apt-get update -qq >/dev/null 2>&1; \
  apt-get install -y -qq libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-noto-core fonts-thai-tlwg >> $B/apt.log 2>&1 ) &

# 4) start the web app first (port 8080); llama-server follows on 127.0.0.1:8079
pkill -f "$B/app/app.py" 2>/dev/null
NOVEL_CTX=131072 NOVEL_ROOT=$B/novel LLM_URL=http://127.0.0.1:8079 PORT=8080 nohup python3 $B/app/app.py >> $B/app.log 2>&1 &
log "app started"

# 5) llama.cpp (build once, arch 86/89/120, -j8)
if [ ! -x $B/llama.cpp/build/bin/llama-server ]; then
  log "building llama.cpp (first time, ~15 min)"
  command -v cmake >/dev/null || pip install -q --break-system-packages cmake
  [ -d $B/llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp $B/llama.cpp
  ( cd $B/llama.cpp && cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF -DCMAKE_CUDA_ARCHITECTURES="86;89;120" >/dev/null && \
    cmake --build build --config Release -j8 --target llama-server ) || { log "ERROR: llama.cpp build failed"; exit 1; }
fi

# 6) model: reuse Q4_K_M file if present, else download
M=$(ls $B/models/*Q4_K_M*.gguf 2>/dev/null | head -1)
if [ -z "$M" ]; then
  R=huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF
  F=$(curl -fsSL -m 30 "https://huggingface.co/api/models/$R" | python3 -c "import json,sys;print(next((s['rfilename'] for s in json.load(sys.stdin)['siblings'] if 'Q4_K_M' in s['rfilename'] and s['rfilename'].endswith('.gguf')),''))")
  [ -z "$F" ] && { log "ERROR: model file not found on HF"; exit 1; }
  log "downloading $F"
  curl -fL --retry 10 --retry-delay 5 -C - "https://huggingface.co/$R/resolve/main/$F" -o "$B/models/$(basename $F)" || { log "ERROR: model download failed"; exit 1; }
  M=$B/models/$(basename $F)
fi

# 7) llama-server
log "starting llama-server with $M"
exec $B/llama.cpp/build/bin/llama-server -m "$M" --host 127.0.0.1 --port 8079 -c 131072 -np 1 -ngl 99 --jinja >> $B/llama.log 2>&1
