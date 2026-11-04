# SPDX-License-Identifier: GPL-3.0-or-later
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY src ./src
COPY LICENSE ./LICENSE
USER 65532:65532
CMD ["python3", "-m", "opos.public_demo"]
