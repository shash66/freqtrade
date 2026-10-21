FROM node:22-alpine AS deps
ARG HTTP_PROXY HTTPS_PROXY NO_PROXY
WORKDIR /app
COPY package.json package-lock.json ./
# Optional corporate/sandbox proxy CA (BuildKit secret `proxy_ca`); production builds pass nothing and skip this.
RUN --mount=type=secret,id=proxy_ca,target=/tmp/proxy-ca.crt,required=false \
    if [ -s /tmp/proxy-ca.crt ]; then cat /tmp/proxy-ca.crt >> /etc/ssl/certs/ca-certificates.crt && export NODE_EXTRA_CA_CERTS=/tmp/proxy-ca.crt; fi; \
    npm ci
FROM node:22-alpine AS build
ARG HTTP_PROXY HTTPS_PROXY NO_PROXY
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# next.config.ts bakes the /api and /ws proxy destination into the standalone server at build time,
# so the compose service name is the default here; override with --build-arg for other topologies.
ARG API_INTERNAL_URL=http://api:8000
ENV NEXT_TELEMETRY_DISABLED=1 API_INTERNAL_URL=$API_INTERNAL_URL
RUN npm run build
FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S fll && adduser -S fll -G fll
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER fll
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://localhost:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
