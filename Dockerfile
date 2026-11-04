FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py auth_helpers.py errors.py portal_settings.py provider_io.py recognition_compare.py runtime_settings.py speech_display.py ./
COPY config ./config
COPY static ./static
COPY tools/build_uat.py ./tools/build_uat.py
RUN python tools/build_uat.py && python -c "import app, recognition_compare"
RUN useradd --uid 10001 --create-home portal
USER portal
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --ws-max-size 131072"]
