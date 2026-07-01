echo 'Forward proxy:        "debugProxyUrl": "http://localhost:8080/"'
echo 'Codex reverse proxy:  openai_base_url = "http://localhost:8082"  (~/.codex/config.toml)'

./mitmweb \
  --set stream_large_bodies=1 \
  --set store_streamed_bodies=true \
  --mode regular@8080 \
  --mode reverse:https://api.openai.com@8082 \
  -s llm_request_view.py \
  -s llm_viewer.py
