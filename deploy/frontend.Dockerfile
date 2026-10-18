FROM node:22-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci \
    && npm install --no-save --no-package-lock \
        "@rollup/rollup-linux-x64-musl@$(node -p "require('rollup/package.json').version")" \
        "@esbuild/linux-x64@$(node -p "require('esbuild/package.json').version")"

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine

ENV BACKEND_UPSTREAM=backend:8000

COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
