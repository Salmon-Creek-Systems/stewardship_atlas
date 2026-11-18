#!/usr/bin/env python3
"""The box worker: consume the render queue and run each job (Phase 4, task 4).

    ATLAS_RENDER_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/…/atlas-render-staging \
    ATLAS_PRIVATE_BUCKET=… ATLAS_OUTLETS_BUCKET=… ATLAS_WORKSPACE_ROOT=… \
    ./scripts/atlas_worker.py

One job at a time. Runs under systemd (infrastructure/systemd/atlas-worker.service),
which restarts it on a crash; a job interrupted by a crash is redelivered by SQS
and runs again under the atlas lease.
"""
import logging
import os
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
import atlas_job_consumer  # noqa: E402


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(name)s %(levelname)s %(message)s')
    queue_url = os.environ.get('ATLAS_RENDER_QUEUE_URL')
    if not queue_url:
        sys.exit("ATLAS_RENDER_QUEUE_URL is not set")
    stop = threading.Event()
    # systemd stops with SIGTERM: finish the job in hand, then exit.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    atlas_job_consumer.serve(queue_url, stop=stop)


if __name__ == '__main__':
    main()
