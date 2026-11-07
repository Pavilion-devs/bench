# The sandbox the Referee runs hidden tests in. No network at run time.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.3 \
    && useradd --create-home scorer
ENV PYTHONDONTWRITEBYTECODE=1
USER scorer
WORKDIR /score
