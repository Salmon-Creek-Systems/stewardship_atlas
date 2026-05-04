# S3 COG Raster Setup

One-time AWS configuration required before COG layers render in the webmap.

## Bucket: `scs-atlas-data`

**Region: `us-east-1`**

This matters. The bucket returns `null` from `get-bucket-location`, which is AWS's
way of saying `us-east-1`. Any code or URL that references `us-west-1` will get a
301 redirect. The browser does NOT follow 301s for CORS range requests — the redirect
response itself has no CORS headers, so the browser blocks the request entirely.
Everything looks like a CORS failure when the real issue is the wrong region.

## 1. CORS rule

Required so browsers on `fireatlas.org` can make HTTP range requests to S3.

```bash
cat > /tmp/cors.json << 'EOF'
{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://fireatlas.org"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["Content-Range", "Content-Length", "ETag"],
      "MaxAgeSeconds": 3000
    }
  ]
}
EOF

aws s3api put-bucket-cors \
  --bucket scs-atlas-data \
  --profile atlas \
  --cors-configuration file:///tmp/cors.json
```

**Why `AllowedHeaders: ["*"]`**: enables the `Range` header, which is how the COG
protocol fetches individual tile byte ranges from the file.

**Why `ExposeHeaders` includes `Content-Range`**: the COG protocol library reads
`Content-Range` from responses to track which byte ranges it has fetched. Without it,
the fetch appears to succeed but the library can't interpret the data.

To verify:
```bash
aws s3api get-bucket-cors --bucket scs-atlas-data --profile atlas
```

## 2. Bucket policy — public read for rasters

The bucket uses `BucketOwnerEnforced` ownership, which disables per-object
`ACL: public-read`. Use a prefix-scoped bucket policy statement instead.

Run on the server (where boto3 + EC2 role have the right credentials):

```python
import boto3, json

s3 = boto3.client('s3', region_name='us-east-1')

try:
    policy = json.loads(s3.get_bucket_policy(Bucket='scs-atlas-data')['Policy'])
except:
    policy = {"Version": "2012-10-17", "Statement": []}

policy['Statement'].append({
    "Sid": "PublicReadCOGRasters",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::scs-atlas-data/*/rasters/*"
})

s3.put_bucket_policy(Bucket='scs-atlas-data', Policy=json.dumps(policy))
print("Policy updated")
```

COG files are uploaded to `{atlas_name}/rasters/{layer_name}/{layer_name}.cog.tif`.

## 3. Verify a COG is publicly accessible

```bash
curl -I "https://scs-atlas-data.s3.amazonaws.com/fhe/rasters/canopy_density/canopy_density.cog.tif"
# Expect: HTTP/1.1 200 OK

# Verify range requests work (what the browser actually does):
curl -I -H "Range: bytes=0-16383" \
  "https://scs-atlas-data.s3.amazonaws.com/fhe/rasters/canopy_density/canopy_density.cog.tif"
# Expect: HTTP/1.1 206 Partial Content
```

If you see 301, the URL region is wrong. If you see 403, the bucket policy is missing.
If you see 200/206 but the browser still blocks it, the CORS rule is missing or wrong.

## COG file requirements

COG files must be in **EPSG:3857** (Web Mercator). `maplibre-cog-protocol` does not
reproject and silently renders nothing if the CRS is anything else — including the
common EPSG:4269 (NAD83) that many USGS/NLCD rasters ship in.

The `tiff_to_cog` eddy handles this via `gdalwarp -t_srs EPSG:3857`. To verify an
existing COG:

```bash
gdalinfo /path/to/file.cog.tif | grep -A2 "AUTHORITY"
# Should show: AUTHORITY["EPSG","3857"]
```

## Version compatibility

`maplibre-cog-protocol` version must match the MapLibre GL JS version:

| `@geomatico/maplibre-cog-protocol` | MapLibre GL JS | API style |
|------------------------------------|----------------|-----------|
| 0.5.x and earlier                  | 3.x            | callback  |
| 0.6.0+                             | 4.5.0+         | Promise   |

Using mismatched versions causes `map.on('load')` to never fire (the broken COG source
hangs the event). Currently pinned to `@0.8.0` + MapLibre `4.7.1`.

Export name also changed: v0.5.x uses `MaplibreCOGProtocol.default`; v0.6.0+ uses
`MaplibreCOGProtocol.cogProtocol`.

## COG sources must be added post-load

COG sources cannot be in the initial MapLibre style object. Add them inside
`map.on('load', ...)` after the map is ready. Putting a COG source in the initial
style causes the load event to hang indefinitely (the protocol handler hasn't been
invoked yet at style parse time).

`templates/js/webmap.js` handles this via the `COG_SOURCES` / `COG_LAYERS` globals
injected from `map.html`.
