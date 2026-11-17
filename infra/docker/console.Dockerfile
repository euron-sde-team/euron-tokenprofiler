FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/console/package.json apps/console/
COPY e2e/package.json e2e/
RUN pnpm install --frozen-lockfile --filter @tokenprofiler/console...
COPY apps/console apps/console
# NEXT_PUBLIC_* values are inlined at build time.
ARG NEXT_PUBLIC_TP_API_URL=http://localhost:8380
ARG NEXT_PUBLIC_TP_DEFAULT_API_KEY=
ENV NEXT_PUBLIC_TP_API_URL=$NEXT_PUBLIC_TP_API_URL NEXT_PUBLIC_TP_DEFAULT_API_KEY=$NEXT_PUBLIC_TP_DEFAULT_API_KEY NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter @tokenprofiler/console build

FROM node:22-alpine
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3300
WORKDIR /srv
COPY --from=build /repo/apps/console/.next/standalone ./
COPY --from=build /repo/apps/console/.next/static ./apps/console/.next/static
COPY --from=build /repo/apps/console/public ./apps/console/public
USER node
EXPOSE 3300
CMD ["node", "apps/console/server.js"]
