# The demo image: the builder UI (built with Node) served by the Python voice bot.
FROM node:22-alpine AS ui
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app/backend
# opencv-python (pulled in by the WebRTC transport) needs these X/GL libraries even headless.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=ui /app/frontend/dist /app/frontend/dist
# Load the given catalog (calendar starting this week), then serve UI + API + WebRTC.
CMD python -m store.ingest given --anchor today && exec python bot.py --host "${HOST:-0.0.0.0}" --port 7860 -t webrtc
