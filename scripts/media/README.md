# README media

The architecture image and the dashboard GIFs in `README.md` are generated, not drawn.

**Architecture.** `docs/assets/slm-architecture.svg` is the hand-drawn source. `build-architecture.sh`
renders the PNG next to it at 2x with headless Chrome and ImageMagick (set `CHROME` if Chrome lives elsewhere).

**GIFs.** Each `record-*.mjs` drives the dashboard with `playwright-core`, takes timed screenshots and
builds a 960-pixel GIF with `ffmpeg` (palettegen and paletteuse). The scripts refuse to run against the
default daemon ports.

Record against a throwaway daemon only, never against the daemon that holds your own memories:

```bash
export SLM_DATA_DIR=/tmp/slm-demo/data SLM_DAEMON_PORT=18765 SLM_DISABLE_LEGACY_PORT=1 SLM_SKIP_LAYA=1
slm mode a && slm serve start
while IFS='|' read -r kind text; do slm remember "$text" --kind "$kind" --sync; done < scripts/media/demo-memories.txt
# Memory health: stop the daemon, run reset-health-demo.py /tmp/slm-demo/data, start it, wait a minute
npm i playwright-core          # in a scratch folder; point NODE_PATH or run the scripts from there
node scripts/media/record-recall.mjs docs/assets/gifs
```

`demo-memories.txt` holds only fictional memories (a made-up product, made-up people). Delete the demo
data folder when you are done. The GIFs are not in the npm package or the Python wheel.
