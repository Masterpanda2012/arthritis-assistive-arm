# Figma Build Guide: Robot Arm LiDAR Placement

Use this guide to recreate or extend the diagrams in Figma.

## Files to import first
- `diagrams/lidar-placement-figma-ready.svg`
- `diagrams/lidar-photo-overlay.svg`

## Page setup
Create one Figma page named `Robot Arm Sensor Placement`.

Create these frames:
- `Board / Overview` - `1920 x 1160`
- `Board / Photo Overlay` - `772 x 845`
- `Board / Notes` - `1280 x 900`

## Color styles
Create these color styles:
- `Canvas / Warm` - `#F7F4EF`
- `Panel / Cream` - `#FCFAF6`
- `Text / Strong` - `#1A1E22`
- `Text / Soft` - `#5B544B`
- `Accent / LiDAR` - `#3BB273`
- `Accent / Beam` - `#56CCF2`
- `Accent / Danger` - `#D64545`
- `Stroke / Soft` - `#D8CDBE`

## Type styles
Create these text styles:
- `Title / 40 / Bold`
- `Section / 26 / Bold`
- `Body / 18 / Regular`
- `Caption / 16 / Regular`

Suggested font stack:
- `Avenir Next`
- Fallback: `Inter`

## Frame 1: Overview board
1. Create a `1920 x 1160` frame with fill `Canvas / Warm`.
2. Add an inner card `1780 x 1020`, radius `34`, fill `Panel / Cream`, stroke `Stroke / Soft`.
3. Add title:
   - `Robot Arm LiDAR Placement`
   - Subhead: `Best for TF-Luna style single-point LiDAR: mount on the wrist housing, centered over the claw path, aimed 10-15 deg down.`
4. Create a left content panel `1010 x 780`, radius `28`.
5. Inside it, build a simplified isometric robot arm:
   - Use dark gray polygons for base plate, shoulder, upper wrist box, and forearm.
   - Use small silver circles for hinge bolts.
   - Keep the claw open and pointed left.
6. Add the LiDAR as a small green rectangular prism on the top-front of the wrist box.
7. Add a cyan beam wedge from the LiDAR down toward the claw center.
8. Add a green hotspot circle at the pickup zone between the fingers.
9. Add three callouts on the right:
   - `Mount LiDAR here`
   - `Tilt 10-15 deg down`
   - `Why this location works`

## Frame 2: Beam alignment detail
1. Create a panel `660 x 360`.
2. Draw a top-front view of the open claw.
3. Add a dashed centerline through the middle of the claw.
4. Draw the beam as a vertical cyan arrow entering the center gap.
5. Mark the target hit point with a green circle.
6. Add caption:
   - `The beam should follow the claw centerline, not one finger.`

## Frame 3: Avoid-these panel
1. Create a panel `660 x 370`.
2. Add three small cards, each `172 x 190`.
3. Card labels:
   - `Base mount`
   - `Side mount`
   - `Finger mount`
4. Use red X marks and short one-line explanations.

## Frame 4: Photo overlay workflow
1. Place your robot-arm photo into `Board / Photo Overlay`.
2. Import `diagrams/lidar-photo-overlay.svg`.
3. Scale the overlay until the green mount zone sits exactly on the top-front of the wrist box.
4. Lower overlay opacity to `85%` if you want the photo to stay dominant.
5. Lock the photo layer and keep callouts editable on top.

## Recommended layer structure
- `00 Background`
- `01 Titles`
- `02 Robot Arm`
- `03 LiDAR`
- `04 Beam`
- `05 Hotspots`
- `06 Callouts`
- `07 Bad Placements`
- `08 Photo Overlay`

## If you want to refine the design further
- Add a translucent shadow under the arm to improve depth.
- Use a slightly brighter green on the LiDAR top face than the side face.
- Keep beam opacity low enough that the claw remains visible beneath it.
- If the real arm geometry changes, keep the beam aligned to the claw center before adjusting any labels.
