FROM registry.access.redhat.com/ubi9/nodejs-20:latest AS build

USER root
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM registry.access.redhat.com/ubi9/nginx-124:latest

USER root
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN chgrp -R 0 /usr/share/nginx/html /etc/nginx/conf.d && \
    chmod -R g+rwX /usr/share/nginx/html && \
    chmod g+rw /etc/nginx/conf.d/default.conf

USER 101
EXPOSE 8080
