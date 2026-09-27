FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /artifact

COPY requirements.txt environment/requirements-lock.txt /artifact/
RUN python -m pip install --no-cache-dir --upgrade pip wheel \
    && python -m pip install --no-cache-dir -r requirements-lock.txt

COPY src /artifact/src
COPY artifact/invoke_service.py artifact/validate_results.py artifact/run_campaign.py /artifact/artifact/
COPY artifact/requests /artifact/artifact/requests
COPY configs /artifact/configs
COPY data/circuits/paper /artifact/data/circuits/paper
COPY analysis/data/paper_results.csv /artifact/analysis/data/paper_results.csv

ENTRYPOINT ["python", "artifact/invoke_service.py"]
CMD ["--request", "artifact/requests/smoke.json", "--dry-run"]
