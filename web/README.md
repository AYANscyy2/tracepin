# tracepin viewer

Static Next.js viewer over `data/` (written by `python scripts/export_web_data.py` from the
repo root). No database, no API: server components read JSON with `fs`, the site exports
to `out/`.

```bash
npm install
npm run dev        # http://localhost:3000
npm run build      # static export in out/
```

Routes: `/` runs · `/runs/[runId]` tasks + detector summary + ranked locations ·
`/traces/[traceId]` waterfall, findings, code panel · `/compare` v1 → v2 → v3 buckets.

Deploy: `npx vercel --prod` from this directory (framework preset Next.js, output `out/`).
