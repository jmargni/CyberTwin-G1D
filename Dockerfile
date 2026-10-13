FROM python:3.12-slim
WORKDIR /app
COPY server.py engine.py ./
COPY public ./public
RUN useradd --uid 10001 --create-home twin && mkdir /app/data && chown twin:twin /app/data
USER twin
EXPOSE 3000 4000
VOLUME ["/app/data"]
ENV TWIN_HOST=0.0.0.0
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/api/state', timeout=3); urllib.request.urlopen('http://127.0.0.1:4000/api/config', timeout=3)"
CMD ["python", "server.py"]
