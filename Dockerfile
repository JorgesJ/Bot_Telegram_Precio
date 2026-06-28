FROM python:3.12-slim

# Zona horaria por defecto (puedes sobreescribir con la variable TIMEZONE)
ENV TZ=Europe/Madrid \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencias del sistema necesarias para matplotlib en slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY main.py .

# El histórico de precios vive aquí; móntalo como volumen para persistirlo.
VOLUME ["/app/data"]

CMD ["python", "main.py"]
