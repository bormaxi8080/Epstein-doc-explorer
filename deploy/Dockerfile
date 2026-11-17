ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS dependencies
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ ca-certificates && rm -rf /var/lib/apt/lists/*
COPY deploy/viewer/package.json deploy/viewer/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM ${NODE_IMAGE} AS frontend
WORKDIR /ui
COPY network-ui/package.json network-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY network-ui/ ./
RUN npm run build

FROM ${NODE_IMAGE}
WORKDIR /app
ENV NODE_ENV=production PORT=3001 DB_READONLY=true DB_PATH=/data/current/document_analysis.db TAG_CLUSTERS_PATH=/data/current/tag_clusters.json
COPY --from=dependencies /app/node_modules ./node_modules
COPY deploy/viewer/package.json ./package.json
COPY api_server.ts api_archive.ts ./
COPY --from=frontend /ui/dist ./network-ui/dist
LABEL org.opencontainers.image.title="Epstein Document Explorer Viewer" org.opencontainers.image.licenses="MIT"
USER node
EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "fetch('http://127.0.0.1:3001/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["./node_modules/.bin/tsx", "api_server.ts"]
