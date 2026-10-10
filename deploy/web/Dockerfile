# The web app image (build context: the repository root). Vercel is the intended host; this image exists so CI proves the app builds and starts
# from a clean checkout, and so it can be run anywhere else. Only PUBLIC values are build arguments (NEXT_PUBLIC_*): no secret belongs here.
FROM node:22-slim

ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /srv/app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_SUPABASE_URL=http://localhost:54321
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
RUN pnpm --filter @mep/web exec next build

USER node
ENV NODE_ENV=production PORT=3000
EXPOSE 3000
WORKDIR /srv/app/apps/web
CMD ["sh", "-c", "exec ./node_modules/.bin/next start -p ${PORT}"]
