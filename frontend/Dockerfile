# Base images are pinned by digest (the multi-arch index that `:latest` pointed to on 2026-11-21) so that a
# rebuild of the same commit produces the same image. Bump both digests deliberately to pick up updates.
FROM registry.access.redhat.com/ubi9/nodejs-22@sha256:9a8e871b44ff99e572f0489f6266c138fbf99890bd1cbc8b07675bea94fb5b3d AS build
USER 0
WORKDIR /opt/app-root/src
COPY package.json package-lock.json ./
RUN chown -R 1001:0 /opt/app-root/src
USER 1001
RUN npm ci --no-audit --no-fund
COPY --chown=1001:0 index.html vite.config.ts tsconfig.json tsconfig.build.json ./
COPY --chown=1001:0 src ./src
RUN npm run build:image

FROM registry.access.redhat.com/ubi9/nginx-124@sha256:3317aa0969ad5d266038b40ce64faee14058a0087617993ce36294777319fe60
USER 0
COPY nginx-default-cfg/api.conf /opt/app-root/etc/nginx.default.d/api.conf
COPY --from=build /opt/app-root/src/dist/ /opt/app-root/src/
RUN chown -R 1001:0 /opt/app-root/src /opt/app-root/etc/nginx.default.d
USER 1001
CMD ["/usr/libexec/s2i/run"]
