FROM python:3.12-slim
WORKDIR /app
# 服务器不需要 playwright(同步器在用户电脑跑),只装后端依赖
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29" "requests>=2.31" "openai>=1.30" "httpx>=0.27" "python-multipart>=0.0.9" "pypdf>=4.0"
COPY app ./app
ENV PORT=8000 DENGJIE_DB=/data/dengjie.db TZ=Asia/Shanghai
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
