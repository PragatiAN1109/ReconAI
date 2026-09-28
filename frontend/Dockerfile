# Operations Console — React/Vite, built once and served as static assets.
#
# Multi-stage: Node builds, nginx serves. The published image contains no Node,
# no node_modules and no source.

# ---------------------------------------------------------------- build ----
FROM node:20-alpine AS build

WORKDIR /build

# package-lock.json is copied with the manifest and `npm ci` is used rather than
# `npm install`, so the image is built from the exact dependency tree the tests
# ran against instead of whatever resolves today.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
# `npm run build` typechecks before bundling, so a type error fails the image
# build rather than shipping.
RUN npm run build

# -------------------------------------------------------------- runtime ----
FROM nginx:1.27-alpine

# nginx is here for two jobs the dev server did and a static file server cannot:
# SPA history fallback, and reverse-proxying the two API prefixes so the browser
# stays same-origin. Without it the frontend would need CORS on both backends —
# a backend change made solely to suit the frontend's deployment.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

# The stock nginx image already drops worker processes to an unprivileged user;
# the master needs root to bind port 80 inside the container.
