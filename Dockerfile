# Образ для Render.com (и любого другого хостинга с Docker)
FROM python:3.13-slim

# Время внутри контейнера — UTC, поэтому расписание 20:00-21:00 приложение
# считает само по часовому поясу APP_TZ; пакет tzdata нужен для zoneinfo.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_TZ=Europe/Moscow \
    DATA_DIR=/data \
    LOG_DIR=/data/logs \
    PORT=10000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Каталог для базы и журналов; на бесплатном тарифе он живёт до перезапуска,
# на платном сюда монтируется постоянный диск Render.
RUN mkdir -p /data/logs && \
    adduser --disabled-password --gecos "" app && \
    chown -R app:app /app /data
USER app

EXPOSE 10000

# Один рабочий процесс: SQLite не любит параллельную запись, а планировщик
# сброса в 19:55 должен существовать в единственном экземпляре.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
