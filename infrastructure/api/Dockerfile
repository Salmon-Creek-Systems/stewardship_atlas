# The API and jobs Lambda image (Phase 4, task 5).
#
# Built from the repository root (see .github/workflows/deploy-cloud.yml):
#   docker build -f infrastructure/api/Dockerfile .
#
# Base: the OSGeo GDAL image, so the GDAL CLI tools (ogr2ogr, gdal_translate,
# gdalwarp) and the osgeo bindings that file inlets, raster helpers and webmap
# previews shell out to are all present. Only QGIS and Chromium stay box-only
# (runs_on: box). Not an AWS Lambda base image, so the runtime interface client
# (awslambdaric) is the entrypoint.
#
# Two functions, one image:
#   API   — CMD ["api_handler.handler"]       (Mangum around webapp.app)
#   jobs  — CMD ["jobs_handler.handler"]      (the light-jobs queue consumer)
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.9.3

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-venv python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# A venv that still sees the base image's osgeo bindings.
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY infrastructure/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# DuckDB's spatial extension, installed at build so a cold start never fetches
# it. lambda_env copies it under $HOME (/tmp on Lambda) where duckdb looks.
RUN HOME=/opt/duckdb-home python -c "import duckdb; duckdb.sql('INSTALL spatial; LOAD spatial;')"

# Application code. Paths match the repo, because the code finds templates and
# shared configs relative to python/.
WORKDIR /var/task
COPY python/ /var/task/python/
COPY templates/ /var/task/templates/
COPY configuration/ /var/task/configuration/
COPY documents/help/ /var/task/documents/help/
ENV PYTHONPATH=/var/task/python

WORKDIR /var/task/python
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["api_handler.handler"]
