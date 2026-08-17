# Container image for the Atlas API Lambda.
#
# Phase 1 ships a placeholder so the CI → ECR → cdk deploy spine can be proven
# end to end before any application code moves. Phase 4 replaces the handler
# with the FastAPI app (python/webapp.py) and adds the geo dependencies, which
# is why this is a container image and not a zip in the first place.

FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
