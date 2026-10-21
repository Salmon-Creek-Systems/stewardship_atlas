# Add a Location by Emailing a Geotagged Photo to the Atlas
<!-- audience: user -->

You can add a geotagged photo to the atlas by emailing it from your smartphone. The photo's GPS location is automatically extracted and a new point feature is created in the layer you specify.

[Video Demo](https://www.youtube.com/watch?v=dy-H-tFVqGc)

## Requirements

- An iPhone or Android phone with location services enabled for the camera app (see below)
- Your email address may need to be on the atlas's list of approved senders —
  ask your atlas administrator

## How to Submit

1. **Take a photo** with your phone's camera outdoors, so your phone has a GPS fix.

2. **Send the photo as an email attachment** to your atlas's address, which is
   its ID followed by `@fireatlas.org` — for example `scvfd@fireatlas.org`.
   Attach one photo per email.

3. **Write a subject line** describing what you're documenting:
   - Just a description (goes to the atlas's default photo layer):
     ```
     Locked gate on Miller Road
     ```
   - Or specify a layer with a colon:
     ```
     hydrants: New hydrant at staging area
     private_notes: Check drainage here next season
     ```
   The layer name must match an existing layer in your atlas. If you leave out the colon and layer name, the feature goes to the default photo layer.

4. **Send the email.** The feature will appear in the atlas within a minute or two.

Automatic replies for failed submissions are turned off for now, so if nothing
appears after a few minutes, check the troubleshooting list below.

## Making Sure Location Services Are On

The most common reason a submission fails is that the Camera app doesn't have permission to record your location. The photo looks normal but has no GPS coordinates.

**How to check on iPhone:**
1. Go to **Settings → Privacy & Security → Location Services**
2. Scroll down to **Camera** and tap it
3. It should say **While Using** — change it if it says Never or Ask Next Time

**How to verify before sending:**
- Open the photo in the **Photos app** and swipe up (or tap ⓘ)
- If a small map appears showing where the photo was taken, GPS is present ✓
- If no map appears, the photo has no location data and the submission will fail

**Android:**
- Go to **Settings → Apps → Camera → Permissions → Location** → set to "Allow while using"
- In Google Photos, check the photo details for a location

If location was off when you took the photo, retake it with location enabled — GPS cannot be added to an existing photo after the fact.

## What Gets Created

A new point feature is placed at the GPS coordinates from the photo, with:
- **name** — the title from your subject line
- **source** — "email"
- **sender** — your email address
- **timestamp** — when the email was received
- **URL** — a link to the stored photo (click the feature on the map to view it)

## Troubleshooting

**Nothing appeared**
- Check that your email address is on the atlas's approved senders list
- Make sure the photo was sent as an attachment (not pasted inline)
- Check the layer name spelling in your subject line (case doesn't matter)

**The photo has no location**
- The photo doesn't have location data — submissions without it are rejected. Follow the location services steps above, retake the photo, and resend.
- Note: some apps (WhatsApp, Slack) strip GPS when sharing — attach the photo directly from your Camera Roll or Photos app instead.

**The feature appeared but in the wrong place**
- Your phone may not have had a GPS fix when the photo was taken. Go outdoors with a clear view of the sky and try again.
