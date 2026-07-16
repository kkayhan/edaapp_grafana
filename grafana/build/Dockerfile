FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/kkayhan/eda-catalog"
LABEL org.opencontainers.image.description="EDA Grafana Controller — auto-generates Grafana topology dashboards from EDA topology"
LABEL org.opencontainers.image.license="MIT"
COPY controller/ /app/
WORKDIR /app
USER 65534
CMD ["python3", "main.py"]
