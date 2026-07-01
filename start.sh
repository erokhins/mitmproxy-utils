echo 'Add to config:   "debugProxyUrl": "http://localhost:8080/"'

./mitmweb --set stream_large_bodies=1 --set store_streamed_bodies=true -s llm_request_view.py -s llm_viewer.py
