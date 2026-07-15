# Immagine unica per tutti i servizi Python (engine, inference, sentiment,
# dashboard, watchdog): stesso codice e stesse dipendenze, cambia solo il
# command nel docker-compose. config/, data/ e logs/ sono bind mount, non
# fanno parte dell'immagine.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY dashboard/ dashboard/
COPY data_engine/ data_engine/
COPY watchdog.py .

# UID 1000 allineato all'utente host: i file scritti sui bind mount
# (data/, logs/, config/) restano editabili dall'host senza sudo.
# logs/ e data/ scrivibili anche senza bind mount (run standalone).
RUN useradd --create-home --uid 1000 hermes \
    && mkdir -p logs data config \
    && chown hermes:hermes logs data config
USER hermes

# Il codice è di root e hermes non può scrivere i __pycache__ accanto:
# meglio disattivare i .pyc che lasciare PermissionError silenziosi.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "src.engine.main"]
