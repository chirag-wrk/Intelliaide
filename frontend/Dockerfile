FROM registry.access.redhat.com/ubi9/nodejs-22:latest AS build

USER root
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM registry.access.redhat.com/ubi9/nginx-124:latest

USER root
RUN mkdir -p /opt/intelliaide/www /licenses
COPY --from=build /app/dist /opt/intelliaide/www
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY LICENSE /licenses/
RUN chgrp -R 0 /opt/intelliaide/www /etc/nginx/conf.d /licenses && \
    chmod -R g+rwX /opt/intelliaide/www /licenses && \
    chmod g+rw /etc/nginx/conf.d/default.conf

USER 101
EXPOSE 8080
