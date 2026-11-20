FROM registry.access.redhat.com/ubi9/nodejs-22:latest AS build
USER 0
WORKDIR /opt/app-root/src
COPY package.json ./
RUN chown -R 1001:0 /opt/app-root/src
USER 1001
RUN npm install
COPY --chown=1001:0 index.html vite.config.js ./
COPY --chown=1001:0 src ./src
RUN npm run build

FROM registry.access.redhat.com/ubi9/nginx-124:latest
USER 0
COPY nginx-default-cfg/api.conf /opt/app-root/etc/nginx.default.d/api.conf
COPY --from=build /opt/app-root/src/dist/ /opt/app-root/src/
RUN chown -R 1001:0 /opt/app-root/src /opt/app-root/etc/nginx.default.d
USER 1001
CMD ["/usr/libexec/s2i/run"]
