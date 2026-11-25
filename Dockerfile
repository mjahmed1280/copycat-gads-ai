FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements_api.txt requirements_deploy.txt ./
COPY py ./py
RUN pip install -r requirements_deploy.txt && pip install ./py

COPY app.py ui.py start.sh ./
# Windows checkouts may have CRLF line endings; bash would choke on them.
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

# The trained model is NOT baked in (it is client data). It lives at COPYCAT_MODEL_FILE,
# ideally on a mounted Cloud Storage bucket.
ENV COPYCAT_MODEL_FILE=/models/copycat_model.json
RUN mkdir -p /models

CMD ["./start.sh"]
