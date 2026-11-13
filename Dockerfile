FROM python:3.12-slim

WORKDIR /app
COPY visa_tracker ./visa_tracker
COPY docs ./docs

ENV VISA_TRACKER_DATA_DIR=/data
ENV VISA_TRACKER_DASHBOARD_HOST=0.0.0.0
ENV VISA_TRACKER_DASHBOARD_PORT=8080
ENV VISA_TRACKER_COLLECTOR=demo

VOLUME ["/data"]
EXPOSE 8080

# Default: dashboard. Override command for loop worker.
CMD ["python", "-m", "visa_tracker", "serve"]
