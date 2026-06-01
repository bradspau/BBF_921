FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY ontology/ ./ontology/

ENV FUSEKI_BASE_URL=http://fuseki:3030
ENV FUSEKI_DATASET=tmf921
ENV LOG_LEVEL=info

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
