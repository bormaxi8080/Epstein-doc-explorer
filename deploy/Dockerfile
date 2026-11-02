FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"
COPY package.json package-lock.json ./
RUN npm ci
COPY network-ui/package.json network-ui/package-lock.json ./network-ui/
RUN cd network-ui && npm ci
COPY . .
RUN cd network-ui && npm run build

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production PORT=3001 DB_PATH=/data/current/document_analysis.db TAG_CLUSTERS_PATH=/data/current/tag_clusters.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json /app/api_server.ts ./
COPY --from=build /app/network-ui/dist ./network-ui/dist
USER node
EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "fetch('http://localhost:3001/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["./node_modules/.bin/tsx", "api_server.ts"]
