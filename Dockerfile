FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /artifact

COPY requirements.txt environment/requirements-lock.txt /artifact/
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r requirements-lock.txt

COPY src /artifact/src
COPY artifact /artifact/artifact
COPY configs /artifact/configs
COPY data/circuits/paper /artifact/data/circuits/paper
COPY analysis/data/paper_results.csv /artifact/analysis/data/paper_results.csv

ENTRYPOINT ["python", "artifact/invoke_service.py"]
CMD ["--request", "artifact/requests/smoke.json", "--dry-run"]
