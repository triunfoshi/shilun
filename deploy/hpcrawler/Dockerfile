FROM node:24-bookworm-slim

ENV NODE_ENV=production TZ=Asia/Shanghai
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY *.js ./
COPY test ./test
RUN npm test

USER node
CMD ["node", "monitor.js"]
