"""The jobs Lambda's entry point: the light-jobs queue consumer (Phase 4, task 5)."""
import logging

import lambda_env

lambda_env.prepare()
logging.getLogger().setLevel(logging.INFO)

import atlas_job_consumer  # noqa: E402


def handler(event, context):
    return atlas_job_consumer.lambda_handler(event, context)
