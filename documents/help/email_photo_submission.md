# Add a Location by Emailing a Geotagged Photo to the Atlas

You can add a geotagged photo to the atlas by emailing it from your smartphone. The photo's GPS location is automatically extracted and a new point feature is created in the layer you specify.

[Video Demo](https://www.youtube.com/watch?v=dy-H-tFVqGc)

## Requirements

- An iPhone or Android phone with location services enabled for the camera app (see below)
- Your email address must be on the admin list for the atlas

## How to Submit

1. **Take a photo** with your phone's camera outdoors, so your phone has a GPS fix.

2. **Send the photo as an email attachment** to the atlas email address:
   - SCVFD: `scvfd@fireatlas.org`

3. **Write a subject line** describing what you're documenting:
   - Just a description (goes to the default `poi` layer):
     ```
     Locked gate on Miller Road
     ```
   - Or specify a layer with a colon:
     ```
     hydrants: New hydrant at staging area
     private_notes: Check drainage here next season
     ```
   The layer name must match an existing layer in your atlas. If you omit the colon and layer name, the feature goes to `poi` automatically.

4. **Send the email.** The feature will appear in the atlas within a minute or two.

If something goes wrong, you will receive an automatic reply explaining what happened.

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

**Nothing appeared and I didn't get a bounce email**
- Check that your email address is on the admin list
- Make sure the photo was sent as an attachment (not pasted inline)
- Check the layer name spelling in your subject line (case doesn't matter)

**I got a bounce saying "No GPS data"**
- The photo doesn't have location data. Follow the location services steps above, retake the photo, and resend.
- Note: some apps (WhatsApp, Slack) strip GPS when sharing — attach the photo directly from your Camera Roll or Photos app instead.

**The feature appeared but in the wrong place**
- Your phone may not have had a GPS fix when the photo was taken. Go outdoors with a clear view of the sky and try again.
