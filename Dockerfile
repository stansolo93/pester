FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .[all] \
    && useradd -m -u 1000 pester
USER pester
