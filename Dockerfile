FROM python:3.12.1-alpine
WORKDIR /app
RUN apk update && apk add --no-cache git gcc musl-dev
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["python", "main.py"]
