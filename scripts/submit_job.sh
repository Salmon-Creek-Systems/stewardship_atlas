#!/bin/bash
# Submit a job to a staging queue the way atlas_jobs.submit does, from the laptop
# (aws CLI only): status object first, then the message.
#   submit_job.sh <atlas> <target: box|lambda> <kind> '<params json>'
set -euo pipefail
ATLAS=$1 TARGET=$2 KIND=$3 PARAMS=${4:-'{}'}
BUCKET=scs-atlas-private-staging
case $TARGET in
  box) QUEUE=https://sqs.us-east-1.amazonaws.com/438886543302/atlas-render-staging ;;
  lambda) QUEUE=https://sqs.us-east-1.amazonaws.com/438886543302/atlas-jobs-staging ;;
esac
ID="$(date -u +%Y%m%d-%H%M%S)-$(openssl rand -hex 4)"
NOW=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
cat > /tmp/job_$ID.json <<EOF
{"id": "$ID", "atlas": "$ATLAS", "kind": "$KIND", "params": $PARAMS, "requested_by": "rehearsal",
 "state": "queued", "submitted_at": "$NOW", "started_at": null, "finished_at": null,
 "attempts": 0, "target": "$TARGET", "result": null, "error": null}
EOF
aws s3 cp /tmp/job_$ID.json "s3://$BUCKET/$ATLAS/jobs/$ID.json" --content-type application/json \
  --cache-control no-cache --profile atlas --region us-east-1 >/dev/null
aws sqs send-message --queue-url "$QUEUE" --message-body "{\"atlas\": \"$ATLAS\", \"id\": \"$ID\"}" \
  --profile atlas --region us-east-1 >/dev/null
rm -f /tmp/job_$ID.json
echo "$ID"
