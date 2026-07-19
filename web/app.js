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

    const dirLight2 = new THREE.DirectionalLight('#00ccff', 0.9);
    dirLight2.position.set(-150, -100, -100);
    scene.add(dirLight2);

    const pointLight = new THREE.PointLight('#ffaa66', 1.2, 200);
    pointLight.position.set(0, 40, 20);
    scene.add(pointLight);

    // 6. Define PBR Materials
    materials = {
        chuckBody: new THREE.MeshStandardMaterial({
            color: '#3a4454',
            metalness: 0.85,
            roughness: 0.25,
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
    collisionMarker.rotation.y = Math.PI / 2; // Revolves around X axis (spindle)
    collisionMarker.visible = false;
    scene.add(collisionMarker);

    // 8. Bind Events
    window.addEventListener('resize', onWindowResize);
    setupEventListeners();

    // 9. Load API Data and Start Loop
    loadData();
    animate();
}

// Coordinate mapping: machine (xr, z) -> world (x_w = z, y_w = -xr, z_w = 0)
function mapCoords(xr, z) {
    return new THREE.Vector3(z, -xr, 0);
}

// Set Camera presets
function setCameraPreset(preset) {
    if (preset === 'iso') {
        camera.position.set(-150, 180, 260);
        camera.lookAt(0, -50, 0);
    } else if (preset === 'front') {
        camera.position.set(0, -60, 320);
        camera.lookAt(0, -60, 0);
    } else if (preset === 'top') {
        camera.position.set(0, 300, 0);
        camera.lookAt(0, -50, 0);
    }
    if (controls) {
        controls.target.set(0, -50, 0);
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
    const tableThickness = 12;

    const tableGeo = new THREE.BoxGeometry(tableLengthZ, tableThickness, tableWidthX);
    const tableMesh = new THREE.Mesh(tableGeo, materials.table);
    tableMesh.receiveShadow = true;

    // Center table centered on the extent
    const tblCenterZ = oz + (s.z_min + s.z_max) / 2;
    const tblCenterX = ox + (s.x_min + s.x_max) / 2;
    // Offset Y below workpiece plane (so tools sit on it at Z=0)
    tableMesh.position.set(tblCenterZ, -tblCenterX, -tableThickness / 2 - 5);
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

        // Position group at the block home position
        toolGroup.position.set(bz, -bx, 0);

        // Rotation around Z axis (Z centerline -> world X, orientation_deg=90 aligns with -Y)
        const angle = tool.orientation_deg * Math.PI / 180;
        toolGroup.rotation.z = -angle;

        // Draw Holder Block
        const h = tool.holder;
        const holderBlock = new THREE.Mesh(
            new THREE.BoxGeometry(h.block_length, h.block_width, 18),
            materials.toolHolder
        );
        holderBlock.name = 'holder';
        // Local coordinates: u axis is block_length, v axis is block_width
        // Block centers at u = shank_length + block_length/2, v = tip_v_offset
        holderBlock.position.set(h.shank_length + h.block_length / 2, h.tip_v_offset, 0);
        holderBlock.castShadow = true;
        toolGroup.add(holderBlock);

        // Draw Shank
        const shank = new THREE.Mesh(
            new THREE.BoxGeometry(h.shank_length, h.shank_diameter, 10),
            materials.toolShank
        );
        shank.position.set(h.shank_length / 2, h.tip_v_offset, 0);
        shank.castShadow = true;
        toolGroup.add(shank);

        // Draw Carbide Insert Tip (pointing along -u direction)
        const tipGeo = new THREE.ConeGeometry(5, 8, 4);
        const tipMesh = new THREE.Mesh(tipGeo, materials.insertTip);
        tipMesh.rotation.z = -Math.PI / 2; // points left (-u)
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

    // Green zone depth (thickness)
    const thickness = 10;

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

                const cellMountX = cand.mount_x + (x0 + ix * dx + dx / 2);
                const cellMountZ = cand.mount_z + (z0 + iz * dz + dz / 2);

                const wx = ox + cellMountX;
                const wz = oz + cellMountZ;

                cellMesh.position.set(wz, -wx, -thickness / 2 - 2);
                greenZoneMeshGroup.add(cellMesh);
            }
        }
    }

    // Initially hide
    greenZoneMeshGroup.visible = false;
    scene.add(greenZoneMeshGroup);
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

    // Find original motion block for N line number
    const block = apiState.gcode_name ? apiAnalysis.timeline[timelineIndex] : null;
    gcodeLine.innerText = `N${timelineIndex * 2}`;

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

        // Apply translation to table group
        slideTableGroup.position.set(tz, -tx, 0);
    }

    // 6. Highlight active tool holder visually
    apiState.tools.forEach(tool => {
        const toolMeshGroup = slideTableGroup.getObjectByName(`tool_${tool.id}`);
        if (toolMeshGroup) {
            const holder = toolMeshGroup.getObjectByName('holder');
            if (holder) {
                if (tool.id === frame.tool_id) {
                    // Set brighter material to indicate active
                    holder.material = materials.toolHolderColliding;
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
        collisionMarker.position.set(frame.z, -frame.x_r, 0);
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
