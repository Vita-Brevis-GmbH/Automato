# Dev-Server-Image (Grundaufbau). Produktions-Build (nginx + statische Assets)
# folgt, sobald das UI Inhalte hat.
FROM node:22-slim
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
