FROM node:20-alpine

WORKDIR /app

ENV NODE_ENV=production

ARG NEXT_PUBLIC_API_URL=http://localhost:8001
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID

# Offline-сборка: node_modules с хоста (make deps-frontend)
COPY node_modules ./node_modules
COPY package.json package-lock.json* ./
COPY . .

RUN npm run build

EXPOSE 3000

CMD ["node", "node_modules/next/dist/bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
