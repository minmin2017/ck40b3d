import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';
import { RoomEnvironment } from './vendor/RoomEnvironment.js';

// DOM Elements
const playBtn = document.getElementById('play-btn');
const scrubber = document.getElementById('timeline-scrubber');
const timelineLabel = document.getElementById('timeline-label');
const droX = document.getElementById('dro-x');
const droZ = document.getElementById('dro-z');
const activeToolLabel = document.getElementById('active-tool-label');
const gcodeLine = document.getElementById('gcode-line');
const programName = document.getElementById('program-name');
const toolListContainer = document.getElementById('tool-list-container');
const collisionLogContainer = document.getElementById('collision-log-container');
const noCollisionsMsg = document.getElementById('no-collisions-msg');
const greenZoneBtn = document.getElementById('green-zone-btn');
const camIsoBtn = document.getElementById('cam-preset-iso');
const camFrontBtn = document.getElementById('cam-preset-front');
const camTopBtn = document.getElementById('cam-preset-top');
const loader = document.getElementById('loader');

// App Globals
let scene, camera, renderer, controls;
let apiState = null;
let apiAnalysis = null;

// Playback variables
let isPlaying = false;
let playbackSpeed = 1.0;
let timelineIndex = 0;
let lastFrameTime = 0;

// 3D Objects References
let chuckGroup = null;
let jawsGroup = null;
let workpieceMesh = null;
let slideTableGroup = null;
let greenZoneMeshGroup = null;
let collisionMarker = null;

// Materials cache
let materials = {};

// Initial Init
init();

function init() {
    // 1. Setup Three.js Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color('#0b0e14');
    scene.fog = new THREE.FogExp2('#0b0e14', 0.0015);

    // 2. Setup Camera
    camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 1, 2000);
    setCameraPreset('iso');

    // 3. Setup Renderer
    const container = document.getElementById('viewport');
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.7;
    container.appendChild(renderer.domElement);

    // Environment map — without one, high-metalness PBR materials reflect
    // nothing and render near-black under direct lights alone.
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

    // 4. Setup Controls
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 + 0.1; // Don't go too far below table
    controls.minDistance = 50;
    controls.maxDistance = 800;

    // 5. Setup Lights
    const ambientLight = new THREE.AmbientLight('#33405a', 2.2);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight('#ffffff', 3.2);
    dirLight1.position.set(150, 200, 100);
    dirLight1.castShadow = true;
    dirLight1.shadow.mapSize.width = 2048;
    dirLight1.shadow.mapSize.height = 2048;
    dirLight1.shadow.camera.near = 0.5;
    dirLight1.shadow.camera.far = 1000;
    const d = 250;
    dirLight1.shadow.camera.left = -d;
    dirLight1.shadow.camera.right = d;
    dirLight1.shadow.camera.top = d;
    dirLight1.shadow.camera.bottom = -d;
    dirLight1.shadow.bias = -0.0005;
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight('#8ab4ff', 1.2);
    dirLight2.position.set(-120, 140, 320); // front-left fill so the chuck face reads
    scene.add(dirLight2);

    const pointLight = new THREE.PointLight('#ffaa66', 1.2, 200);
    pointLight.position.set(0, 40, 20);
    scene.add(pointLight);

    // 6. Define PBR Materials
    materials = {
        chuckBody: new THREE.MeshStandardMaterial({
            color: '#46536a',
            metalness: 0.7,
            roughness: 0.35,
            clearcoat: 0.1
        }),
        jaws: new THREE.MeshStandardMaterial({
            color: '#202630',
            metalness: 0.8,
            roughness: 0.4
        }),
        workpiece: new THREE.MeshStandardMaterial({
            color: '#a0aab8',
            metalness: 0.9,
            roughness: 0.18,
            clearcoat: 0.2
        }),
        workpieceCarved: new THREE.MeshStandardMaterial({
            color: '#c2cbd6',
            metalness: 0.95,
            roughness: 0.12,
            clearcoat: 0.4
        }),
        table: new THREE.MeshStandardMaterial({
            color: '#1a1f26',
            metalness: 0.7,
            roughness: 0.5
        }),
        toolHolder: new THREE.MeshStandardMaterial({
            color: '#282e38',
            metalness: 0.5,
            roughness: 0.4
        }),
        toolHolderActive: new THREE.MeshStandardMaterial({
            color: '#00b8d4',
            emissive: '#00b8d4',
            emissiveIntensity: 0.35,
            metalness: 0.5,
            roughness: 0.35
        }),
        toolHolderColliding: new THREE.MeshStandardMaterial({
            color: '#ff1744',
            emissive: '#ff1744',
            emissiveIntensity: 0.8,
            metalness: 0.5,
            roughness: 0.2
        }),
        toolShank: new THREE.MeshStandardMaterial({
            color: '#707b8c',
            metalness: 0.9,
            roughness: 0.25
        }),
        insertTip: new THREE.MeshStandardMaterial({
            color: '#ffaa00',
            metalness: 0.95,
            roughness: 0.1,
            emissive: '#ff7700',
            emissiveIntensity: 0.1
        }),
        greenZone: new THREE.MeshStandardMaterial({
            color: '#00e676',
            transparent: true,
            opacity: 0.25,
            wireframe: false,
            side: THREE.DoubleSide
        })
    };

    // 7. Add Grid and Base helpers
    const gridHelper = new THREE.GridHelper(800, 40, '#28354a', '#17202c');
    gridHelper.position.y = -180;
    scene.add(gridHelper);

    // Collision Marker
    const markerGeo = new THREE.TorusGeometry(35, 1.5, 8, 32);
    const markerMat = new THREE.MeshBasicMaterial({ color: '#ff3366', side: THREE.DoubleSide });
    collisionMarker = new THREE.Mesh(markerGeo, markerMat);
    collisionMarker.rotation.x = Math.PI / 2; // Flat target ring in the horizontal working plane
    collisionMarker.visible = false;
    scene.add(collisionMarker);

    // 8. Bind Events
    window.addEventListener('resize', onWindowResize);
    setupEventListeners();

    // 9. Load API Data and Start Loop
    loadData();
    animate();
}

// Coordinate mapping: the machine (x_r, z) plane is the HORIZONTAL working
// plane of a flat-bed gang lathe — spindle centerline and tool tips share one
// horizontal plane at y = 0. world x = machine z (spindle axis), world z =
// machine x_r (+X toward the operator/viewer), world y = up (visual only).
function mapCoords(xr, z) {
    return new THREE.Vector3(z, 0, xr);
}

// Visual height constants (machine plane sits at y = 0)
const TABLE_THICKNESS = 12;
const TABLE_TOP_Y = -26;   // slide table surface, below the tool-tip plane
const BLOCK_H = 30;        // tool holder block height standing on the table

// Set Camera presets
function setCameraPreset(preset) {
    camera.up.set(0, 1, 0);
    const target = new THREE.Vector3(20, -30, 130);
    if (preset === 'iso') {
        camera.position.set(400, 330, 470);
    } else if (preset === 'front') {
        // Operator's view: standing at +Z looking at the machine
        camera.position.set(10, 60, 450);
    } else if (preset === 'top') {
        // Straight down — matches the 2D app plot (machine +Z right, +X down)
        camera.up.set(0, 0, -1);
        camera.position.set(10, 480, 100.01);
    }
    camera.lookAt(target);
    if (controls) {
        controls.target.copy(target);
    }
}

// Setup standard event binding
function setupEventListeners() {
    // Playback
    playBtn.addEventListener('click', () => {
        isPlaying = !isPlaying;
        playBtn.innerText = isPlaying ? 'PAUSE' : 'PLAY';
        playBtn.className = isPlaying ? 'btn btn-active' : 'btn';
    });

    scrubber.addEventListener('input', (e) => {
        timelineIndex = parseInt(e.target.value);
        updatePlaybackUI();
    });

    // Speeds
    document.querySelectorAll('.speed-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            playbackSpeed = parseFloat(e.target.dataset.speed);
        });
    });

    // Camera presets
    camIsoBtn.addEventListener('click', () => setCameraPreset('iso'));
    camFrontBtn.addEventListener('click', () => setCameraPreset('front'));
    camTopBtn.addEventListener('click', () => setCameraPreset('top'));

    // Green zone toggle
    let showGreenZone = false;
    greenZoneBtn.addEventListener('click', () => {
        showGreenZone = !showGreenZone;
        greenZoneBtn.className = showGreenZone ? 'btn btn-green-active' : 'btn';
        if (greenZoneMeshGroup) {
            greenZoneMeshGroup.visible = showGreenZone;
        }
    });

    // Settings Toggle
    const settingsToggleBtn = document.getElementById('settings-toggle-btn');
    const settingsDrawer = document.getElementById('settings-drawer');
    const closeSettingsBtn = document.getElementById('close-settings-btn');
    
    settingsToggleBtn.addEventListener('click', () => {
        settingsDrawer.classList.toggle('open');
        if (settingsDrawer.classList.contains('open')) {
            populateSettingsForm();
        }
    });
    
    closeSettingsBtn.addEventListener('click', () => {
        settingsDrawer.classList.remove('open');
    });
}

function onWindowResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
}

// Fetch backend data
async function loadData() {
    try {
        const resState = await fetch('http://127.0.0.1:8360/api/state');
        apiState = await resState.json();

        const resAnalysis = await fetch('http://127.0.0.1:8360/api/analysis');
        apiAnalysis = await resAnalysis.json();

        // Setup HUD
        programName.innerText = apiState.gcode_name.toUpperCase();
        document.getElementById('machine-name').innerText = apiState.machine.name;

        // Build 3D Entities
        buildChuck(apiState.chuck);
        buildWorkpiece();
        buildTools(apiState.tools);
        buildGreenZone(apiAnalysis.green_zone);

        // Populate sidebars
        populateToolList();
        populateCollisionLog();

        // Update Scrubber range
        const totalFrames = apiAnalysis.timeline ? apiAnalysis.timeline.length : 0;
        scrubber.max = Math.max(0, totalFrames - 1);
        updatePlaybackUI();

        // Remove Loader
        loader.style.opacity = '0';
        setTimeout(() => loader.style.display = 'none', 500);

    } catch (e) {
        console.error('Error loading simulator data:', e);
        document.getElementById('loader-text').innerText = 'ล้มเหลวในการเชื่อมต่อเซิร์ฟเวอร์...';
    }
}

// ── 3D Builders ──────────────────────────────────────────────────────────

function buildChuck(c) {
    if (chuckGroup) scene.remove(chuckGroup);

    chuckGroup = new THREE.Group();

    // 1. Chuck Cylinder Body
    // In machine coordinates: Z centerline is world X. Chuck body has diameter body_diameter and length body_length_z.
    // Cylinder geometry: radiusTop, radiusBottom, height, radialSegments
    const bodyGeo = new THREE.CylinderGeometry(c.body_diameter / 2, c.body_diameter / 2, c.body_length_z, 64);
    const bodyMesh = new THREE.Mesh(bodyGeo, materials.chuckBody);
    bodyMesh.rotation.z = -Math.PI / 2; // Align local height (Y) along world X
    bodyMesh.receiveShadow = true;
    bodyMesh.castShadow = true;
    chuckGroup.add(bodyMesh);

    // Position chuck centered in body_length_z along Z (Z centerline -> world X)
    const chuckFaceZ = apiState.workpiece.z_face_position - apiState.workpiece.raw_length;
    bodyMesh.position.x = chuckFaceZ - c.body_length_z / 2;

    // 2. Add Jaws (3 distributed radially at 120 deg)
    jawsGroup = new THREE.Group();
    jawsGroup.position.x = chuckFaceZ; // Rotates at the chuck face plane
    
    // Jaw dimensions
    const jawZ = c.jaw_protrusion_z;
    const jawW = 24; // Z-axis width
    const jawH = 35; // radial height
    const jawGeo = new THREE.BoxGeometry(jawZ, jawH, jawW);
    
    // Offset each jaw radially
    const centerOffset = c.body_diameter / 2 - 10;

    for (let i = 0; i < 3; i++) {
        const angle = i * (Math.PI * 2 / 3);
        const jawMesh = new THREE.Mesh(jawGeo, materials.jaws);
        jawMesh.position.set(jawZ / 2, Math.cos(angle) * centerOffset, Math.sin(angle) * centerOffset);
        jawMesh.rotation.x = -angle; // rotate to point outward
        jawMesh.castShadow = true;
        jawsGroup.add(jawMesh);
    }
    chuckGroup.add(jawsGroup);
    scene.add(chuckGroup);
}

function buildWorkpiece() {
    if (workpieceMesh) scene.remove(workpieceMesh);

    // Initial shape represents the starting raw workpiece profile (keyframe 0)
    let points = [];
    if (apiAnalysis && apiAnalysis.carve_keyframes && apiAnalysis.carve_keyframes.length > 0) {
        points = apiAnalysis.carve_keyframes[0].profile.map(p => new THREE.Vector2(p[0], p[1]));
    } else {
        // Fallback default cylinder points
        const wp = apiState.workpiece;
        const zBack = wp.z_face_position - wp.raw_length;
        const zFront = wp.z_face_position;
        points = [
            new THREE.Vector2(0, zBack),
            new THREE.Vector2(wp.raw_diameter / 2, zBack),
            new THREE.Vector2(wp.raw_diameter / 2, zFront),
            new THREE.Vector2(0, zFront)
        ];
    }

    // LatheGeometry creates revolve mesh around Y axis
    const workpieceGeo = new THREE.LatheGeometry(points, 64);
    workpieceMesh = new THREE.Mesh(workpieceGeo, materials.workpiece);
    workpieceMesh.rotation.z = -Math.PI / 2; // Align revolve axis along world X
    workpieceMesh.castShadow = true;
    workpieceMesh.receiveShadow = true;
    scene.add(workpieceMesh);
}

// Re-generate workpiece geometry based on timeline index carve keyframe
function updateWorkpieceCarving(timelineIdx) {
    if (!workpieceMesh || !apiAnalysis || !apiAnalysis.carve_keyframes) return;

    // Find the closest carve keyframe up to timelineIdx
    let activeKeyframe = apiAnalysis.carve_keyframes[0];
    for (let kf of apiAnalysis.carve_keyframes) {
        if (kf.i <= timelineIdx) {
            activeKeyframe = kf;
        } else {
            break;
        }
    }

    // Construct Vector2 list for lathe
    const points = activeKeyframe.profile.map(p => new THREE.Vector2(p[0], p[1]));

    // Dispose old geometry to prevent memory leaks
    workpieceMesh.geometry.dispose();
    workpieceMesh.geometry = new THREE.LatheGeometry(points, 64);
    
    // Set shinier material if carved, default raw if untouched
    workpieceMesh.material = (activeKeyframe.i > 0) ? materials.workpieceCarved : materials.workpiece;
}

function buildTools(toolsList) {
    if (slideTableGroup) scene.remove(slideTableGroup);

    slideTableGroup = new THREE.Group();

    // 1. Slide Table Plate
    // Positioned relative to slide_origin at home
    const s = apiState.machine.slide_table;
    const ox = apiState.machine.slide_origin_x;
    const oz = apiState.machine.slide_origin_z;

    const tableLengthZ = s.z_max - s.z_min;
    const tableWidthX = s.x_max - s.x_min;

    const tableGeo = new THREE.BoxGeometry(tableLengthZ, TABLE_THICKNESS, tableWidthX);
    const tableMesh = new THREE.Mesh(tableGeo, materials.table);
    tableMesh.receiveShadow = true;

    // Horizontal plate: machine Z extent -> world x, machine X extent -> world z,
    // surface TABLE_TOP_Y below the tool-tip plane
    const tblCenterZ = oz + (s.z_min + s.z_max) / 2;
    const tblCenterX = ox + (s.x_min + s.x_max) / 2;
    tableMesh.position.set(tblCenterZ, TABLE_TOP_Y - TABLE_THICKNESS / 2, tblCenterX);
    slideTableGroup.add(tableMesh);

    // 2. Build Each Tool in the setup
    toolsList.forEach(tool => {
        const toolGroup = new THREE.Group();
        toolGroup.name = `tool_${tool.id}`;

        // Get tool home tip position
        // home_tip = slide_origin + mount + tip_off
        // Coordinates: world_x = z, world_y = -x
        const bx = ox + tool.mount_x;
        const bz = oz + tool.mount_z;

        // Group origin = tool tip, in the horizontal working plane (y = 0)
        toolGroup.position.set(bz, 0, bx);

        // orientation_deg: CCW from machine +Z toward +X in the (Z, X) plane.
        // machine Z -> world x, machine X -> world z, so rotate about world -y.
        const angle = tool.orientation_deg * Math.PI / 180;
        toolGroup.rotation.y = -angle;

        // Draw Holder Block — stands on the table, clamp rises past the shank
        const h = tool.holder;
        const holderBlock = new THREE.Mesh(
            new THREE.BoxGeometry(h.block_length, BLOCK_H, h.block_width),
            materials.toolHolder
        );
        holderBlock.name = 'holder';
        // Local axes: x = shank axis (u), z = in-plane width (v), y = height.
        // Block sits on the table top, so its center is half a height above it.
        holderBlock.position.set(
            h.shank_length + h.block_length / 2,
            TABLE_TOP_Y + BLOCK_H / 2,
            h.tip_v_offset
        );
        holderBlock.castShadow = true;
        toolGroup.add(holderBlock);

        // Draw Shank — horizontal, centered on the tool-tip plane (y = 0)
        const shank = new THREE.Mesh(
            new THREE.BoxGeometry(h.shank_length, h.shank_diameter, h.shank_diameter),
            materials.toolShank
        );
        shank.position.set(h.shank_length / 2, 0, h.tip_v_offset);
        shank.castShadow = true;
        toolGroup.add(shank);

        // Draw Carbide Insert Tip (pointing along -u direction)
        const tipGeo = new THREE.ConeGeometry(5, 8, 4);
        const tipMesh = new THREE.Mesh(tipGeo, materials.insertTip);
        tipMesh.rotation.z = Math.PI / 2; // cone apex toward -local x (the tip)
        tipMesh.position.set(0, 0, 0);
        toolGroup.add(tipMesh);

        slideTableGroup.add(toolGroup);
    });

    scene.add(slideTableGroup);
}

function buildGreenZone(gz) {
    if (greenZoneMeshGroup) scene.remove(greenZoneMeshGroup);
    if (!gz) return;

    greenZoneMeshGroup = new THREE.Group();

    // Render cells in the mask grid
    const { x0, z0, dx, dz, nx, nz, mask } = gz;
    const ox = apiState.machine.slide_origin_x;
    const oz = apiState.machine.slide_origin_z;

    // Green zone: thin translucent slab lying flat ON the slide table surface
    const thickness = 6;

    // Single cell box geometry (reuse for batching)
    const cellGeo = new THREE.BoxGeometry(dz, thickness, dx);

    for (let iz = 0; iz < nz; iz++) {
        for (let ix = 0; ix < nx; ix++) {
            const val = mask[iz * nx + ix];
            if (val === 1) { // 1 = Green/Safe
                const cellMesh = new THREE.Mesh(cellGeo, materials.greenZone);

                // Calculate position relative to candidate tool mount home
                // grid coordinates map to slide table frame offsets
                const cand = apiState.tools.find(t => t.id === apiState.candidate_tool_id);
                if (!cand) continue;

                // x0/z0 are sample-point deltas; each box is centered on its
                // sample (same convention as the 2D app's half-cell shift).
                const cellMountX = cand.mount_x + (x0 + ix * dx);
                const cellMountZ = cand.mount_z + (z0 + iz * dz);

                const wx = ox + cellMountX;
                const wz = oz + cellMountZ;

                cellMesh.position.set(wz, TABLE_TOP_Y + thickness / 2 + 0.5, wx);
                greenZoneMeshGroup.add(cellMesh);
            }
        }
    }

    // Initially hide. Add to the slide-table group: mount positions live in the
    // slide frame, so the zone must ride along when the table moves.
    greenZoneMeshGroup.visible = false;
    slideTableGroup.add(greenZoneMeshGroup);
}

// ── Playback Logic ────────────────────────────────────────────────────────

function animate(currentTime) {
    requestAnimationFrame(animate);

    // Damping controls
    controls.update();

    // 1. Spindle rotation animation (Chuck & Jaws)
    if (isPlaying && chuckGroup && jawsGroup && workpieceMesh) {
        // Spin fast
        const spinSpeed = 0.08 * playbackSpeed;
        jawsGroup.rotation.x += spinSpeed;
        workpieceMesh.rotation.x += spinSpeed;
    }

    // 2. Playback progression
    if (isPlaying && apiAnalysis && apiAnalysis.timeline) {
        const delta = (currentTime - lastFrameTime) / 1000;
        if (delta > 0.02) { // limit updates
            const framesStep = Math.max(1, Math.round(30 * delta * playbackSpeed));
            timelineIndex = Math.min(apiAnalysis.timeline.length - 1, timelineIndex + framesStep);
            
            scrubber.value = timelineIndex;
            updatePlaybackUI();

            if (timelineIndex >= apiAnalysis.timeline.length - 1) {
                isPlaying = false;
                playBtn.innerText = 'PLAY';
                playBtn.className = 'btn';
            }
            lastFrameTime = currentTime;
        }
    } else {
        lastFrameTime = currentTime;
    }

    renderer.render(scene, camera);
}

function updatePlaybackUI() {
    if (!apiAnalysis || !apiAnalysis.timeline || apiAnalysis.timeline.length === 0) return;

    const frame = apiAnalysis.timeline[timelineIndex];
    if (!frame) return;

    // 1. Label count
    timelineLabel.innerText = `${timelineIndex + 1} / ${apiAnalysis.timeline.length}`;

    // 2. DRO values
    droX.innerText = (frame.x_r * 2).toFixed(3); // diameter
    droZ.innerText = frame.z.toFixed(3);

    // 3. Highlight current active tool
    const tLabel = apiState.tools.find(t => t.id === frame.tool_id);
    activeToolLabel.innerText = tLabel ? `${frame.tool_id} (${tLabel.name})` : frame.tool_id;
    activeToolLabel.style.color = frame.rapid ? '#ffffff' : 'var(--accent-green)';

    // Real source line number from the parser (SimFrame.line_no via the API)
    gcodeLine.innerText = (frame.n != null) ? `N${frame.n}` : '—';

    // 4. Update workpiece shape (carving)
    updateWorkpieceCarving(timelineIndex);

    // 5. Move slide table translation
    // translation_world = (tz, -tx, 0)
    // tx = wx_active - home_tip_x(active_tool)
    const activeTool = apiState.tools.find(t => t.id === frame.tool_id);
    if (activeTool && slideTableGroup) {
        const ox = apiState.machine.slide_origin_x;
        const oz = apiState.machine.slide_origin_z;
        const homeTipX = ox + activeTool.mount_x + activeTool.holder.tip_dx;
        const homeTipZ = oz + activeTool.mount_z + activeTool.holder.tip_dz;

        const tx = frame.x_r - homeTipX;
        const tz = frame.z - homeTipZ;

        // Apply translation to table group — motion stays in the horizontal plane
        slideTableGroup.position.set(tz, 0, tx);
    }

    // 6. Highlight active tool holder visually
    apiState.tools.forEach(tool => {
        const toolMeshGroup = slideTableGroup.getObjectByName(`tool_${tool.id}`);
        if (toolMeshGroup) {
            const holder = toolMeshGroup.getObjectByName('holder');
            if (holder) {
                if (tool.id === frame.tool_id) {
                    // Cyan = active tool; red stays reserved for collisions
                    holder.material = materials.toolHolderActive;
                } else {
                    holder.material = materials.toolHolder;
                }
            }
        }
    });

    // 7. Check collisions at current timeline index
    const colEvent = apiAnalysis.collisions.find(c => c.i === timelineIndex);
    if (colEvent) {
        // Show red torus ring at collision point
        collisionMarker.position.set(frame.z, 0, frame.x_r);
        collisionMarker.visible = true;

        // Flash offending tool holder red
        const colToolMesh = slideTableGroup.getObjectByName(`tool_${colEvent.tool_id}`);
        if (colToolMesh) {
            const holder = colToolMesh.getObjectByName('holder');
            if (holder) holder.material = materials.toolHolderColliding;
        }
    } else {
        collisionMarker.visible = false;
    }
}

// ── Sidebar Injectors ──────────────────────────────────────────────────────

function populateToolList() {
    toolListContainer.innerHTML = '';
    apiState.tools.forEach(t => {
        const el = document.createElement('div');
        el.className = `tool-item ${t.id === apiState.reference_tool_id ? 'active' : ''} ${t.id === apiState.candidate_tool_id ? 'candidate' : ''}`;
        
        const isRef = t.id === apiState.reference_tool_id ? ' (REF)' : '';
        const isCand = t.id === apiState.candidate_tool_id ? ' (CAND)' : '';
        
        el.innerHTML = `
            <div class="tool-color-dot" style="background: ${t.color || '#1f77b4'}"></div>
            <div class="tool-name">${t.id} - ${t.name}${isRef}${isCand}</div>
            <div class="tool-type">${t.type}</div>
        `;
        
        toolListContainer.appendChild(el);
    });
}

function populateCollisionLog() {
    collisionLogContainer.innerHTML = '';
    if (!apiAnalysis.collisions || apiAnalysis.collisions.length === 0) {
        noCollisionsMsg.style.display = 'block';
        return;
    }
    
    noCollisionsMsg.style.display = 'none';
    
    apiAnalysis.collisions.forEach(c => {
        const el = document.createElement('div');
        el.className = 'collision-item';
        
        const frameData = apiAnalysis.timeline[c.i];
        const zPos = frameData ? frameData.z.toFixed(1) : '??';
        const xPos = frameData ? (frameData.x_r * 2).toFixed(1) : '??';
        
        el.innerHTML = `
            <div class="collision-header">
                <span>ชนกับ / Collide: ${c.tool_id}</span>
                <span>จุด / Idx: ${c.i}</span>
            </div>
            <div>${c.msg}</div>
            <div style="color: var(--text-muted); font-size: 10px; margin-top: 4px;">DRO: X=${xPos}, Z=${zPos}</div>
        `;
        
        el.addEventListener('click', () => {
            timelineIndex = c.i;
            scrubber.value = timelineIndex;
            updatePlaybackUI();
            
            // Highlight frame
            isPlaying = false;
            playBtn.innerText = 'PLAY';
            playBtn.className = 'btn';
        });
        
        collisionLogContainer.appendChild(el);
    });
}

// ── Settings Drawer Logic ──────────────────────────────────────────────────

function renderCfgToolsList(tools) {
    const container = document.getElementById('cfg-tools-container');
    container.innerHTML = '';
    
    tools.forEach(t => {
        const card = document.createElement('div');
        card.className = 'cfg-tool-card';
        card.dataset.id = t.id;
        card.style.cssText = 'background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 6px; padding: 10px; margin-bottom: 8px; position: relative;';
        
        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <input type="text" class="cfg-tool-id" value="${t.id}" style="width: 50px; font-weight: bold; padding: 2px 4px; border: 1px solid var(--border-color); background: rgba(0,0,0,0.3); color: var(--accent-blue); border-radius: 4px;" required>
                <button type="button" class="cfg-del-tool-btn" style="background: transparent; border: none; color: var(--accent-red); cursor: pointer; font-size: 11px;">DELETE</button>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 10px;">
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Name</label>
                    <input type="text" class="cfg-tool-name" value="${t.name}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Type</label>
                    <select class="cfg-tool-type" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;">
                        <option value="turning_OD" ${t.type === 'turning_OD' ? 'selected' : ''}>OD Turning</option>
                        <option value="turning_ID" ${t.type === 'turning_ID' ? 'selected' : ''}>ID Turning</option>
                        <option value="boring" ${t.type === 'boring' ? 'selected' : ''}>Boring</option>
                        <option value="drilling" ${t.type === 'drilling' ? 'selected' : ''}>Drilling</option>
                        <option value="parting" ${t.type === 'parting' ? 'selected' : ''}>Parting</option>
                        <option value="threading" ${t.type === 'threading' ? 'selected' : ''}>Threading</option>
                        <option value="other" ${t.type === 'other' ? 'selected' : ''}>Other</option>
                    </select>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Mount X</label>
                    <input type="number" step="any" class="cfg-tool-mount-x" value="${t.mount_x}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Mount Z</label>
                    <input type="number" step="any" class="cfg-tool-mount-z" value="${t.mount_z}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Orient (deg)</label>
                    <input type="number" step="any" class="cfg-tool-orient" value="${t.orientation_deg}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Slot</label>
                    <input type="number" class="cfg-tool-slot" value="${t.slot || ''}" placeholder="None" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;">
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Slot Attach Z</label>
                    <input type="number" step="any" class="cfg-tool-slot-attach-z" value="${t.slot_attach_z || 0.0}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Tip dx</label>
                    <input type="number" step="any" class="cfg-tool-tip-dx" value="${t.holder.tip_dx || 0.0}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Tip dz</label>
                    <input type="number" step="any" class="cfg-tool-tip-dz" value="${t.holder.tip_dz || 0.0}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Block Width</label>
                    <input type="number" step="any" class="cfg-tool-block-w" value="${t.holder.block_width}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Block Length</label>
                    <input type="number" step="any" class="cfg-tool-block-l" value="${t.holder.block_length}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Shank Len</label>
                    <input type="number" step="any" class="cfg-tool-shank-l" value="${t.holder.shank_length}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Shank Dia</label>
                    <input type="number" step="any" class="cfg-tool-shank-d" value="${t.holder.shank_diameter}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
                <div>
                    <label style="color: var(--text-muted); display:block; margin-bottom:2px;">Tip v offset</label>
                    <input type="number" step="any" class="cfg-tool-v-off" value="${t.holder.tip_v_offset || 0.0}" style="width:100%; padding:4px; border:1px solid var(--border-color); background:rgba(0,0,0,0.3); color:white; border-radius:4px;" required>
                </div>
            </div>
        `;
        
        card.querySelector('.cfg-del-tool-btn').addEventListener('click', () => {
            card.remove();
        });
        
        container.appendChild(card);
    });
}

function getToolsFromForm() {
    const cards = document.querySelectorAll('.cfg-tool-card');
    const tools = [];
    
    cards.forEach(card => {
        const id = card.querySelector('.cfg-tool-id').value.trim() || 'T01';
        const name = card.querySelector('.cfg-tool-name').value.trim() || 'Unnamed';
        const type = card.querySelector('.cfg-tool-type').value;
        const mount_x = parseFloat(card.querySelector('.cfg-tool-mount-x').value) || 0;
        const mount_z = parseFloat(card.querySelector('.cfg-tool-mount-z').value) || 0;
        const orientation_deg = parseFloat(card.querySelector('.cfg-tool-orient').value) || 0;
        
        const slotVal = card.querySelector('.cfg-tool-slot').value.trim();
        const slot = slotVal ? parseInt(slotVal) : null;
        const slot_attach_z = parseFloat(card.querySelector('.cfg-tool-slot-attach-z').value) || 0;
        
        const tip_dx = parseFloat(card.querySelector('.cfg-tool-tip-dx').value) || 0;
        const tip_dz = parseFloat(card.querySelector('.cfg-tool-tip-dz').value) || 0;
        const block_width = parseFloat(card.querySelector('.cfg-tool-block-w').value) || 20;
        const block_length = parseFloat(card.querySelector('.cfg-tool-block-l').value) || 50;
        const shank_length = parseFloat(card.querySelector('.cfg-tool-shank-l').value) || 50;
        const shank_diameter = parseFloat(card.querySelector('.cfg-tool-shank-d').value) || 10;
        const tip_v_offset = parseFloat(card.querySelector('.cfg-tool-v-off').value) || 0;
        
        tools.push({
            id, name, type, mount_x, mount_z, orientation_deg, slot, slot_attach_z,
            holder: {
                block_width, block_length, shank_length, shank_diameter, tip_v_offset, tip_dx, tip_dz
            },
            active_in_program: true
        });
    });
    
    return tools;
}

function populateSettingsForm() {
    if (!apiState) return;
    
    // Chuck
    document.getElementById('cfg-chuck-dia').value = apiState.chuck.body_diameter;
    document.getElementById('cfg-chuck-len').value = apiState.chuck.body_length_z;
    document.getElementById('cfg-jaw-prot').value = apiState.chuck.jaw_protrusion_z;
    document.getElementById('cfg-jaw-dia').value = apiState.chuck.jaw_outer_diameter;
    
    // Workpiece
    document.getElementById('cfg-wp-dia').value = apiState.workpiece.raw_diameter;
    document.getElementById('cfg-wp-len').value = apiState.workpiece.raw_length;
    document.getElementById('cfg-wp-grip').value = apiState.workpiece.grip_length_in_chuck;
    
    // Dropdowns
    const refSelect = document.getElementById('cfg-ref-tool');
    const candSelect = document.getElementById('cfg-cand-tool');
    
    refSelect.innerHTML = '';
    candSelect.innerHTML = '';
    
    apiState.tools.forEach(t => {
        const optRef = document.createElement('option');
        optRef.value = t.id;
        optRef.text = t.id;
        optRef.selected = (t.id === apiState.reference_tool_id);
        refSelect.appendChild(optRef);
        
        const optCand = document.createElement('option');
        optCand.value = t.id;
        optCand.text = t.id;
        optCand.selected = (t.id === apiState.candidate_tool_id);
        candSelect.appendChild(optCand);
    });
    
    // Tools list
    renderCfgToolsList(apiState.tools);
}

// Bind Settings events
document.getElementById('cfg-add-tool-btn').addEventListener('click', () => {
    const list = getToolsFromForm();
    list.push({
        id: 'T' + String(list.length + 1).padStart(2, '0'),
        name: 'New Tool',
        type: 'turning_OD',
        mount_x: 50.0,
        mount_z: 30.0,
        orientation_deg: 90.0,
        holder: {
            block_width: 20.0,
            block_length: 50.0,
            shank_length: 50.0,
            shank_diameter: 10.0,
            tip_v_offset: 10.0,
            tip_dx: 0.0,
            tip_dz: 0.0
        }
    });
    renderCfgToolsList(list);
});

document.getElementById('cfg-load-gcode-btn').addEventListener('click', async () => {
    const text = document.getElementById('cfg-gcode-text').value.trim();
    const path = document.getElementById('cfg-gcode-path').value.trim();
    
    const payload = {};
    if (path) {
        payload.path = path;
    } else if (text) {
        payload.text = text;
    } else {
        alert('กรุณากรอก G-code หรือระบุพาธของไฟล์');
        return;
    }
    
    try {
        loader.style.display = 'flex';
        loader.style.opacity = '1';
        document.getElementById('loader-text').innerText = 'กำลังอัปโหลด G-code...';
        
        const res = await fetch('http://127.0.0.1:8360/api/gcode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) {
            const errData = await res.json();
            throw new Error(errData.detail || 'Failed to upload G-code');
        }
        
        const result = await res.json();
        alert(`โหลดสำเร็จ! จำนวนบล็อก: ${result.blocks}`);
        
        // Refresh analysis & state
        await loadData();
    } catch (e) {
        alert(`ข้อผิดพลาด: ${e.message}`);
        loader.style.opacity = '0';
        setTimeout(() => loader.style.display = 'none', 500);
    }
});

document.getElementById('cfg-save-btn').addEventListener('click', async () => {
    // Validate chuck/workpiece
    const chuck_dia = parseFloat(document.getElementById('cfg-chuck-dia').value);
    const chuck_len = parseFloat(document.getElementById('cfg-chuck-len').value);
    const jaw_prot = parseFloat(document.getElementById('cfg-jaw-prot').value);
    const jaw_dia = parseFloat(document.getElementById('cfg-jaw-dia').value);
    
    const wp_dia = parseFloat(document.getElementById('cfg-wp-dia').value);
    const wp_len = parseFloat(document.getElementById('cfg-wp-len').value);
    const wp_grip = parseFloat(document.getElementById('cfg-wp-grip').value);
    
    if ([chuck_dia, chuck_len, jaw_prot, jaw_dia, wp_dia, wp_len, wp_grip].some(isNaN)) {
        alert('กรุณากรอกค่าตัวเลขให้ถูกต้อง');
        return;
    }
    
    const tools = getToolsFromForm();
    // Validate tools
    for (let t of tools) {
        if (t.id === '') {
            alert('กรุณากรอก Tool ID ของทุกตัว');
            return;
        }
        if ([t.mount_x, t.mount_z, t.orientation_deg, t.holder.block_width, t.holder.block_length, t.holder.shank_length, t.holder.shank_diameter, t.holder.tip_v_offset].some(isNaN)) {
            alert(`ข้อมูลทูล ${t.id} ไม่ถูกต้อง (ค่าตัวเลขห้ามว่าง)`);
            return;
        }
    }
    
    const ref_tool_id = document.getElementById('cfg-ref-tool').value;
    const cand_tool_id = document.getElementById('cfg-cand-tool').value;
    
    const payload = {
        chuck: {
            body_diameter: chuck_dia,
            body_length_z: chuck_len,
            jaw_protrusion_z: jaw_prot,
            jaw_outer_diameter: jaw_dia
        },
        workpiece: {
            raw_diameter: wp_dia,
            raw_length: wp_len,
            grip_length_in_chuck: wp_grip,
            z_face_position: apiState.workpiece.z_face_position
        },
        tools,
        reference_tool_id: ref_tool_id,
        candidate_tool_id: cand_tool_id
    };
    
    try {
        loader.style.display = 'flex';
        loader.style.opacity = '1';
        document.getElementById('loader-text').innerText = 'กำลังบันทึกและคำนวณความปลอดภัยใหม่...';
        
        const res = await fetch('http://127.0.0.1:8360/api/profile', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) {
            const errData = await res.json();
            throw new Error(errData.detail || 'Failed to update profile');
        }
        
        // Close drawer
        document.getElementById('settings-drawer').classList.remove('open');
        
        // Reload and rebuild scene
        await loadData();
        
        // Reset index to beginning after changes
        timelineIndex = 0;
        scrubber.value = 0;
        updatePlaybackUI();
        
    } catch (e) {
        alert(`ข้อผิดพลาด: ${e.message}`);
        loader.style.opacity = '0';
        setTimeout(() => loader.style.display = 'none', 500);
    }
});

