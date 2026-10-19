FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir flask
COPY src/app.py .
EXPOSE 8000
CMD ["python", "app.py"]