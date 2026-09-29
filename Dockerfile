# One image for the study, the interactive demo and the HTTP service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Run as an unprivileged user. The study writes results/ and figures/, so those
# two directories are the only ones it owns.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app/results /app/figures
USER app

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

# Default entry point is the HTTP service. The other two:
#   docker run --rm -p 8501:8501 choke-controller \
#       streamlit run app/streamlit_app.py --server.address 0.0.0.0
#   docker run --rm choke-controller python src/studies.py
CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]
