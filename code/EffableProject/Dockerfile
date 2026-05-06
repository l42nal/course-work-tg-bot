#базовый образ Python 3.12-slim-bookworm
FROM python:3.12-slim-bookworm

#это переменные окружения, которые используются для настройки Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
#рабочая директория в контейнере
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

#копируем все файлы из текущей директории в рабочую директорию в контейнере
COPY . .

#делаем файл docker-entrypoint.sh исполняемым
RUN chmod +x docker-entrypoint.sh

#устанавливаем точку входа в контейнер
ENTRYPOINT ["./docker-entrypoint.sh"]