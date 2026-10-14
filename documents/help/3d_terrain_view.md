# 3D Terrain View
<!-- audience: user -->

The 3D terrain view lets you explore your atlas area as an interactive 3D map, with
real elevation data derived from the same DEM used for the atlas hillshade.

## Opening the 3D View

Click **3D** in the header of the web map.

## Controls

- **Tilt**: Hold right-click and drag up/down, or use two-finger drag on trackpad
- **Rotate**: Hold right-click and drag left/right
- **Zoom**: Scroll wheel or pinch
- **Pan**: Left-click and drag

The map starts in a slightly tilted overhead view. Drag to tilt further to see terrain
relief clearly.

## Sharing a 3D view

Click **Share view** to copy a link that opens the 3D view exactly as you have
it — the same spot, camera angle and tilt, and the same layers showing.

## Animating a camera route

The **Animate Camera Route** box (top right) flies the camera along a line layer:
a road, a creek, or a route you drew for the purpose.

1. Choose a line layer from the dropdown. The map zooms out to show the whole route,
   and the layer is switched on so you can see it.
2. Set the camera:
   - **Height above ground (m)**: how high the camera sits above the point it is
     looking at. The camera rises and falls with the terrain. Default 100.
   - **Camera angle (°)**: 0 looks straight down, 85 looks almost level toward
     the horizon. Default 45.
   - **Duration (s)**: how long the whole trip takes. Default 60.
3. Press **Play**. **Pause** holds the camera where it is; **Stop** resets to the start.
   You can change the height and angle while it plays.

The camera faces the direction of travel and turns smoothly into bends. Every line
in the layer is followed in turn, so a layer with many separate lines (such as a
whole road network) makes the camera jump from one to the next. For a clean flight,
draw the route as one line in its own layer.

The height is measured above the spot the camera is looking at, and the camera sits
back from that spot. On a steep descent the hillside behind can rise above the camera;
the map then lifts the camera and flattens its angle for a moment rather than going
underground. Very low heights zoom in past the sharpest satellite imagery, so the
ground looks blurry.

**Share view** includes the route and its settings; the link opens the 3D view and
starts the flight. You can also write the link by hand, for example
`…/outlets/3dview/?route=evacuation_routes&height=150&angle=45&secs=90`.

## Notes

The 3D view requires elevation data (terrain-RGB tiles) to have been generated for
your atlas. If the button appears but the page is blank, contact your atlas administrator.
The surrounding terrain beyond the atlas boundary uses global elevation data and will
always be present.
