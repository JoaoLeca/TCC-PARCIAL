FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY data/knowledge ./data/knowledge
RUN pip install --no-cache-dir .
ENV PYTHONPATH=/app/src
EXPOSE 8000 8501
CMD ["sh", "-c", "uvicorn barber_bot.api:app --host 0.0.0.0 --port 8000 & streamlit run src/streamlit_app.py --server.address 0.0.0.0 --server.port 8501"]
