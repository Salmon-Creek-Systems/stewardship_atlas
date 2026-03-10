# Submit a Photo by Email

You can add a geotagged photo to the atlas by emailing it from your smartphone. The photo's GPS location is automatically extracted and a new point feature is created in the layer you specify.

![Video Demo](../video/email_import.mov)

## Requirements

- An iPhone or Android phone with location services enabled for the camera app
- Your email address must be on the admin list for the atlas

## How to Submit

1. **Take a photo** with your phone's camera. Make sure location services are enabled — the photo must have GPS data embedded in it.

2. **Send the photo as an email attachment** to the atlas email address:
   - SCVFD: `scvfd@fireatlas.org`

3. **Format the subject line** as:
   ```
   <layer> | <title>
   ```
   For example:
   - `poi | Locked gate on Miller Road`
   - `hydrants | New hydrant at staging area`
   - `private_notes | Check drainage here next season`

   The layer name must match an existing layer in your atlas. The title is optional — if you leave it out, it defaults to "Photo submission".

4. **Send the email.** The feature will appear in the atlas within a minute or two.

## What Gets Created

A new point feature is placed at the GPS coordinates from the photo, with:
- **name** — the title from your subject line
- **source** — "email"
- **sender** — your email address
- **timestamp** — when the email was received
- **image_url** — a link to the photo stored in S3

## Checking Your Layers

If you're not sure what layer names are available, check the atlas layer list in the admin console or ask your atlas administrator.

## Troubleshooting

**Nothing appeared after a few minutes**
- Check that your email address is on the admin list
- Make sure the photo was sent as an attachment (not pasted inline)
- Verify the layer name in the subject line matches exactly (case doesn't matter, but spelling does)

**The feature appeared but in the wrong place**
- Your phone's GPS may not have had a fix when the photo was taken. Try outdoors with a clear view of the sky.
- Some phones strip GPS data when sending over certain email apps — try using the default mail app.

**"No GPS data" or similar error**
- The photo does not have location data embedded. Check that your camera app has permission to use location, take a new photo outdoors, and try again.
