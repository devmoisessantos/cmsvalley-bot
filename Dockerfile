# Imagem base enxuta do Python
FROM python:3.12-slim

# Pasta de trabalho dentro do container
WORKDIR /app

# Evita arquivos .pyc e força logs sem buffer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Dependências de sistema necessárias para algumas libs Python
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala as dependências do projeto
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do bot
COPY . .

# Comando que sobe o bot
CMD ["python", "main.py"]
