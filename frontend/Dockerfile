FROM node:14-buster AS build

RUN mkdir /src
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json /src/
RUN lock_hash="$(shasum -a 256 package-lock.json)" && \
    npm install && \
    echo "$lock_hash" | shasum -c
COPY frontend /src/
RUN npm run build


FROM nginx:1.19

COPY --from=build /src/build /var/www/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# nginx default CMD is ["nginx", "-g", "daemon off;"]
CMD ["sh", "-c", "sed -i 's|<meta name=\"api_url\"[^>]\\+>|<meta name=\"api_url\" content=\"'\"$API_URL\"'\">|' /var/www/html/index.html && nginx -g \"daemon off; worker_shutdown_timeout 2s;\""]
