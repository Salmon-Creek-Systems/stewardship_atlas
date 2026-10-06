# Add Private Notes
<!-- audience: admin -->

Private notes allow administrators to add location-specific comments, reminders, or annotations that are only visible to admin users. These are useful for:

- Recording observations from field visits
- Marking areas needing follow-up
- Adding context that shouldn't be public
- Coordinating between administrators

## Adding a Private Note

### From the Edit Map Interface

1. In the Admin Console, find the **private_notes** layer
2. Open **Alter → Draw**
3. Click on the map where you want to place the note
4. Enter your note text in the **name** field
5. Click **Save**

See [Draw New Features](draw_vector.md) for more on drawing.

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

See [Editing Layer Data](editing_layer_data.md) for instructions on modifying or removing existing notes.

## Best Practices

- Keep notes concise but informative
- Include dates if time-sensitive
- Use for temporary annotations that don't belong in permanent data
- Review and clean up old notes periodically

## Access Control

The `private_notes` layer is configured with `"access": ["admin"]`, so it is
left off public and internal maps and out of public exports.

⚠️ That keeps notes out of sight, but it is not yet a hard security boundary:
someone who knows the exact address of the layer's data file can still open it.
Don't put anything in a private note that would be harmful if it got out.
