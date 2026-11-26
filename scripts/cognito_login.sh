#!/bin/bash
# Log in through the Cognito hosted UI from the command line, for checking auth
# on a deployed stack without a browser (Phase 4 task 6).
#
#   scripts/cognito_login.sh USER PASSWORD [NEXT_PATH]
#   curl -b jar_USER.txt https://$ATLAS_HOST/api/me
#
# Leaves the session and CloudFront cookies in ./jar_USER.txt. Password users
# only; Google sign-in needs a browser. ATLAS_HOST defaults to staging.
set -euo pipefail
H=https://${ATLAS_HOST:-d1y2f6bmf8ds3m.cloudfront.net}
J=jar_$1.txt; rm -f "$J"
AUTH=$(curl -s -c "$J" -b "$J" -o /dev/null -w '%{redirect_url}' "$H/api/auth/login?next=${3:-/}")
PAGE=$(curl -s -c "$J" -b "$J" -L "$AUTH")
CSRF=$(echo "$PAGE" | grep -o 'name="_csrf" value="[^"]*"' | head -1 | sed 's/.*value="//;s/"$//')
CB=$(curl -s -c "$J" -b "$J" -o /dev/null -w '%{redirect_url}' --data-urlencode "_csrf=$CSRF" \
  --data-urlencode "username=$1" --data-urlencode "password=$2" --data-urlencode "cognitoAsfData=" \
  "${AUTH/oauth2\/authorize/login}")
[ -n "$CB" ] || { echo "login refused (wrong password?)" >&2; exit 1; }
curl -s -c "$J" -b "$J" -o /dev/null -w "$1: callback -> %{http_code} %{redirect_url}\n" "$CB"
