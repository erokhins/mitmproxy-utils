echo 'Add to config:   "debugProxyUrl": "http://localhost:8080/"'

# Reverse-proxy routes from reverse_proxy.conf need their own --mode reverse:...
# listener per configured port; once any --mode is passed, mitmproxy no longer
# adds "regular" implicitly, so it's listed explicitly alongside them.
EXTRA_MODES=$(python3 reverse_proxy.py)

./mitmweb --set stream_large_bodies=1 --set store_streamed_bodies=true \
  --set connection_strategy=lazy \
  --mode regular $EXTRA_MODES \
  -s sse_capture.py -s llm_request_view.py -s llm_viewer.py -s reverse_proxy.py
