# The console is built, not served from source: NEXT_PUBLIC_GATEWAY_URL is inlined into
# the client bundle at build time, so the gateway address has to be present here rather
# than at `docker run`. Compose passes it as an environment variable to the running
# container as well, which is harmless and keeps the two declarations in one place.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile exists, `npm install` when one does not. The lockfile is the
# reproducible path; this stays buildable before the first install has ever been run.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_GATEWAY_URL=http://localhost:8000
ENV NEXT_PUBLIC_GATEWAY_URL=$NEXT_PUBLIC_GATEWAY_URL
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
# `output: "standalone"` in next.config.ts produces this tree: a server plus exactly the
# modules it imports. Copying node_modules instead would triple the image for no gain.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
