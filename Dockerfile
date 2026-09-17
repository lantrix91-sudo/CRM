FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_PRODUCTION=true
WORKDIR /app
COPY requirements.txt requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt
COPY backend/ ./backend/
COPY deploy/ ./deploy/
COPY --from=frontend /build/dist/ ./frontend/dist/
RUN useradd --create-home crm && chown -R crm:crm /app
USER crm
CMD ["sh", "/app/deploy/start-web.sh"]
