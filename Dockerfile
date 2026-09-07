# One image for the study, the interactive demo and the HTTP service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 8501

# Default entry point is the HTTP service. The other two:
#   docker run --rm -p 8501:8501 choke-controller \
#       streamlit run app/streamlit_app.py --server.address 0.0.0.0
#   docker run --rm choke-controller python src/studies.py
CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]
