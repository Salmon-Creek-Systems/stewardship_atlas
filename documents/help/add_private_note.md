# Add Private Notes

Private notes allow administrators to add location-specific comments, reminders, or annotations that are only visible to admin users. These are useful for:

- Recording observations from field visits
- Marking areas needing follow-up
- Adding context that shouldn't be public
- Coordinating between administrators

## Adding a Private Note

### From the Edit Map Interface

1. Go to the **Edit Map** (Admin access required)
2. In the layer selector, choose **"private_notes"**
3. Click the **Draw Point** tool
4. Click on the map where you want to place the note
5. In the popup form, enter your note text in the **"name"** field
6. Click **Save**

### Note Properties

Private notes support the following properties:
- **name**: The note text (required) - this will display as a label on the map
- **geometry**: Point location where the note appears

## Viewing Private Notes

Private notes are only visible when:
- You are logged in as an admin user
- The `private_notes` layer is enabled in the layer list
- You are viewing an admin-level interface

They will appear as pushpin icons on the map with the note text as a label.

## Editing or Deleting Notes

1. Click on an existing note marker
2. Click **"Edit"** in the popup
3. Modify the text or geometry
4. Click **Save** to update, or **Delete** to remove

## Best Practices

- Keep notes concise but informative
- Include dates if time-sensitive
- Use for temporary annotations that don't belong in permanent data
- Review and clean up old notes periodically

## Access Control

The `private_notes` layer is configured with `"access": ["admin"]`, meaning:
- Only admin users can view these notes
- They do not appear on public or internal maps
- They are excluded from public exports
