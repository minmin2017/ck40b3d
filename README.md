# CK40B-3D — 3D Lathe Gang-Tool Clearance Simulator

Interactive 3D simulator for the CK40B gang-tool CNC lathe. It lets you simulate toolpaths, visual workpiece material removal (carving), safety clearance zone (Green Zone), and gang-tool collisions.

## Key Features

1. **Realistic 3D Machine Environment**: Built using Three.js with OrbitControls, PBR textures (metallic roughness), and studio lighting.
2. **Timeline Playback Controls**: bottom play/pause, scrub, speed multipliers (0.5x, 1x, 2x, 4x) linked with real-time DRO (Digital Read Out).
3. **Dynamic Carving**: Displays workpiece material removal in real-time using `THREE.LatheGeometry` loaded dynamically from carve profile keyframes.
4. **Interactive Collision Detection**: Highlights colliding holders in glowing red, places red collision rings at contact points, and lists warnings in the collision panel (click to jump directly to the collision time).
5. **Translucent Green Zone**: Heatmap visualization showing safe/danger mounting regions on the gang-slide.

## How to Run

Execute the `run.sh` script to set up a virtual environment, install requirements, and launch the backend:

```bash
bash run.sh
```

Then, open your browser and navigate to:
**http://localhost:8360**
