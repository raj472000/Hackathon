FROM node:22-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY infra/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
# Where /api and /health are forwarded. Override in the cloud, e.g. API_UPSTREAM=127.0.0.1:8000.
ENV API_UPSTREAM=api:8000
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
