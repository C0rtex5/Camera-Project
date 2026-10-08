function escapeHtml(text) { return String(text).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])); }
// Global State
    let currentScenario = 'scenario_exca_near_miss';
    let telemetryData = [];
    let currentFrameIdx = 0;
    let isPlaying = false;
    let playInterval = null;
    let playbackSpeed = 1.0;
    const imageCache = {};

    // DOM Elements
    const canvas = document.getElementById('vision-canvas');
    const ctx = canvas.getContext('2d');
    const scrubberFill = document.getElementById('scrubber-fill');
    const frameCounter = document.getElementById('frame-counter');
    const btnPlay = document.getElementById('btn-play');
    const alertBadge = document.getElementById('system-alert-badge');

    // -------------------------------------------------------------------
    // 1. Three.js Metric Digital Twin Initialization
    // -------------------------------------------------------------------
    const webglContainer = document.getElementById('webgl-wrapper');
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0c0e);

    const camera = new THREE.PerspectiveCamera(45, webglContainer.clientWidth / webglContainer.clientHeight, 0.1, 1000);
    camera.position.set(0, -32, 38);
    camera.lookAt(0, 4, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(webglContainer.clientWidth, webglContainer.clientHeight);
    webglContainer.appendChild(renderer.domElement);

    // Site Metric Grid (50m x 50m, 1m cells)
    const grid = new THREE.GridHelper(50, 50, 0x282f37, 0x161a1e);
    grid.rotation.x = Math.PI / 2;
    scene.add(grid);

    // BIM Trench Exclusion Zone
    const trenchGeom = new THREE.PlaneGeometry(20, 7);
    const trenchMat = new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.15, side: THREE.DoubleSide });
    const trenchMesh = new THREE.Mesh(trenchGeom, trenchMat);
    trenchMesh.position.set(2, 6.5, 0.05);
    scene.add(trenchMesh);

    // Dynamic 3D Object Meshes Pool
    const agentMeshes = {};

    function getOrCreateAgentMesh(id, className) {
      if (agentMeshes[id]) return agentMeshes[id];
      const grp = new THREE.Group();
      if (className === 'WORKER') {
        const disc = new THREE.Mesh(
          new THREE.CircleGeometry(0.8, 32),
          new THREE.MeshBasicMaterial({ color: 0x3b82f6, side: THREE.DoubleSide })
        );
        const cyl = new THREE.Mesh(
          new THREE.CylinderGeometry(0.3, 0.3, 1.8, 16),
          new THREE.MeshBasicMaterial({ color: 0x60a5fa })
        );
        cyl.rotation.x = Math.PI / 2;
        cyl.position.z = 0.9;
        grp.add(disc);
        grp.add(cyl);
      } else {
        const body = new THREE.Mesh(
          new THREE.BoxGeometry(2.6, 5.0, 1.8),
          new THREE.MeshBasicMaterial({ color: 0xf59e0b })
        );
        body.position.z = 0.9;
        const cab = new THREE.Mesh(
          new THREE.BoxGeometry(1.8, 2.0, 1.2),
          new THREE.MeshBasicMaterial({ color: 0x2563eb })
        );
        cab.position.set(0, 0.5, 2.2);
        grp.add(body);
        grp.add(cab);
      }
      scene.add(grp);
      agentMeshes[id] = grp;
      return grp;
    }

    // Trajectory lines
    const workerPathLine = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0x38bdf8, linewidth: 3 })
    );
    scene.add(workerPathLine);

    const loaderPathLine = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0xfbbf24, linewidth: 3 })
    );
    scene.add(loaderPathLine);

    // Conflict Ring
    const conflictRing = new THREE.Mesh(
      new THREE.RingGeometry(1.0, 1.4, 32),
      new THREE.MeshBasicMaterial({ color: 0xef4444, side: THREE.DoubleSide, transparent: true, opacity: 0.0 })
    );
    conflictRing.position.set(0, 5, 0.1);
    scene.add(conflictRing);

    // Mouse Orbit Controls
    let isDragging = false;
    let prevMousePos = { x: 0, y: 0 };
    webglContainer.addEventListener('mousedown', (e) => {
      isDragging = true;
      prevMousePos = { x: e.clientX, y: e.clientY };
    });
    window.addEventListener('mouseup', () => { isDragging = false; });
    webglContainer.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      const deltaX = e.clientX - prevMousePos.x;
      const deltaY = e.clientY - prevMousePos.y;
      camera.position.x -= deltaX * 0.05;
      camera.position.y += deltaY * 0.05;
      camera.lookAt(0, 4, 0);
      prevMousePos = { x: e.clientX, y: e.clientY };
    });
    webglContainer.addEventListener('wheel', (e) => {
      e.preventDefault();
      camera.position.z += e.deltaY * 0.03;
      camera.position.z = Math.max(10, Math.min(100, camera.position.z));
    });

    function render3D() {
      requestAnimationFrame(render3D);
      conflictRing.rotation.z += 0.03;
      renderer.render(scene, camera);
    }
    render3D();

    window.addEventListener('resize', () => {
      camera.aspect = webglContainer.clientWidth / webglContainer.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(webglContainer.clientWidth, webglContainer.clientHeight);
      resizeCanvas();
    });

    function resizeCanvas() {
      canvas.width = canvas.parentElement.clientWidth;
      canvas.height = canvas.parentElement.clientHeight;
      if (telemetryData && telemetryData[currentFrameIdx]) {
        updateUIForFrame(currentFrameIdx);
      }
    }
    setTimeout(resizeCanvas, 100);

    // -------------------------------------------------------------------
    // 2. Telemetry Ingestion & Image Preloading
    // -------------------------------------------------------------------
    async function loadScenarioTelemetry(scenarioId) {
      try {
        pause();
        const resp = await fetch(`/api/v1/demo/telemetry/${scenarioId}`);
        telemetryData = await resp.json();
        console.log(`Loaded ${telemetryData.length} real frames for ${scenarioId}`);
        currentFrameIdx = 0;
        // Pre-fetch first few images
        preloadFrames(scenarioId, 0, 10);
        updateUIForFrame(currentFrameIdx);
      } catch (err) {
        console.error("Failed to load telemetry:", err);
      }
    }

    function preloadFrames(scenarioId, startIdx, count) {
      for (let i = startIdx; i < startIdx + count; i++) {
        const key = `${scenarioId}_${i}`;
        if (!imageCache[key]) {
          const img = new Image();
          img.src = `/api/v1/demo/frame_image/${scenarioId}/${i}`;
          imageCache[key] = img;
        }
      }
    }

    function changeScenario(scenarioId) {
      currentScenario = scenarioId;
      loadScenarioTelemetry(scenarioId);
    }

    // -------------------------------------------------------------------
    // 3. Playback & Synchronization Engine
    // -------------------------------------------------------------------
    function togglePlay() {
      if (isPlaying) {
        pause();
      } else {
        play();
      }
    }

    function play() {
      isPlaying = true;
      btnPlay.textContent = 'Pause';
      btnPlay.classList.add('active');

      const intervalMs = (1000 / 20) / playbackSpeed;
      playInterval = setInterval(() => {
        currentFrameIdx++;
        if (currentFrameIdx >= (telemetryData.length || 120)) {
          currentFrameIdx = 0;
        }
        updateUIForFrame(currentFrameIdx);
        // Preload upcoming frames
        preloadFrames(currentScenario, currentFrameIdx, 10);
      }, intervalMs);
    }

    function pause() {
      isPlaying = false;
      btnPlay.textContent = 'Play';
      btnPlay.classList.remove('active');
      if (playInterval) clearInterval(playInterval);
    }

    function stepFrame(delta) {
      pause();
      currentFrameIdx = Math.max(0, Math.min((telemetryData.length || 120) - 1, currentFrameIdx + delta));
      updateUIForFrame(currentFrameIdx);
      preloadFrames(currentScenario, currentFrameIdx, 5);
    }

    function onScrubberClick(e) {
      const rect = e.currentTarget.getBoundingClientRect();
      const pos = (e.clientX - rect.left) / rect.width;
      currentFrameIdx = Math.floor(pos * ((telemetryData.length || 120) - 1));
      updateUIForFrame(currentFrameIdx);
    }

    function setSpeed(speed) {
      playbackSpeed = speed;
      document.querySelectorAll('.video-controls .ctrl-btn').forEach(btn => {
        if (btn.textContent.includes('x')) btn.classList.remove('active');
      });
      event.target.classList.add('active');
      if (isPlaying) {
        pause();
        play();
      }
    }

    // -------------------------------------------------------------------
    // 4. Telemetry Update & Direct Canvas Rendering (Zero Black Screen!)
    // -------------------------------------------------------------------
    function updateUIForFrame(idx) {
      const totalFrames = telemetryData.length || 120;
      frameCounter.textContent = `${idx + 1} / ${totalFrames}`;
      scrubberFill.style.width = `${((idx + 1) / totalFrames) * 100}%`;

      if (!telemetryData || !telemetryData[idx]) return;
      const data = telemetryData[idx];

      // A. Fetch and Draw Real Frame Image Directly onto Canvas
      const imgKey = `${currentScenario}_${idx}`;
      let frameImg = imageCache[imgKey];
      if (!frameImg) {
        frameImg = new Image();
        frameImg.src = `/api/v1/demo/frame_image/${currentScenario}/${idx}`;
        imageCache[imgKey] = frameImg;
        frameImg.onload = () => {
          renderFrameWithOverlays(frameImg, data);
        };
      } else if (frameImg.complete && frameImg.naturalWidth > 0) {
        renderFrameWithOverlays(frameImg, data);
      } else {
        frameImg.onload = () => {
          renderFrameWithOverlays(frameImg, data);
        };
      }

      // B. Update 3D Digital Twin Positions & Trajectories
      update3DTwin(data);

      // C. Update Quantitative Metrics
      updateMetrics(data);

      // D. Update Validation Matrix
      updateValidationTable(data);
    }

    function renderFrameWithOverlays(img, data) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const frameW = data.frame_width || img.naturalWidth || 1280;
      const frameH = data.frame_height || img.naturalHeight || 720;

      // Aspect ratio fitting (letterbox / pillarbox)
      const scale = Math.min(canvas.width / frameW, canvas.height / frameH);
      const drawW = frameW * scale;
      const drawH = frameH * scale;
      const offsetX = (canvas.width - drawW) / 2;
      const offsetY = (canvas.height - drawH) / 2;

      // 1. Draw Real Video Frame Photograph (Centered)
      ctx.drawImage(img, offsetX, offsetY, drawW, drawH);

      const annotations = data.annotations || [];
      annotations.forEach((ann) => {
        const [x1, y1, x2, y2] = ann.bbox;
        const sx1 = offsetX + x1 * scale;
        const sy1 = offsetY + y1 * scale;
        const sx2 = offsetX + x2 * scale;
        const sy2 = offsetY + y2 * scale;
        const w = sx2 - sx1;
        const h = sy2 - sy1;

        const isWorker = (ann.class_name === 'WORKER');
        const color = isWorker ? '#3b82f6' : '#f59e0b';

        // Bounding Box
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.strokeRect(sx1, sy1, w, h);

        // Corner brackets
        const cLen = 12;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(sx1, sy1 + cLen); ctx.lineTo(sx1, sy1); ctx.lineTo(sx1 + cLen, sy1);
        ctx.moveTo(sx2, sy2 - cLen); ctx.lineTo(sx2, sy2); ctx.lineTo(sx2 - cLen, sy2);
        ctx.stroke();

        // Class Badge
        ctx.fillStyle = color;
        ctx.fillRect(sx1, sy1 - 22, 130, 22);
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 11px monospace';
        ctx.fillText(`${ann.class_name} [${(ann.confidence * 100).toFixed(0)}%]`, sx1 + 6, sy1 - 6);

        // PPE Tags for Worker
        if (isWorker && ann.ppe) {
          const hhColor = ann.ppe.hardhat ? '#10b981' : '#ef4444';
          const hhText = ann.ppe.hardhat ? 'HARDHAT: OK' : 'NO HARDHAT';
          ctx.fillStyle = 'rgba(17, 20, 23, 0.85)';
          ctx.fillRect(sx1, sy2 + 4, 110, 20);
          ctx.fillStyle = hhColor;
          ctx.font = 'bold 10px monospace';
          ctx.fillText(`👷 ${hhText}`, sx1 + 6, sy2 + 18);

          const vestColor = ann.ppe.vest ? '#10b981' : '#ef4444';
          const vestText = ann.ppe.vest ? 'VEST: OK' : 'NO VEST';
          ctx.fillStyle = 'rgba(17, 20, 23, 0.85)';
          ctx.fillRect(sx1, sy2 + 26, 110, 20);
          ctx.fillStyle = vestColor;
          ctx.fillText(`🦺 ${vestText}`, sx1 + 6, sy2 + 40);
        }

        // Bottom-Center Metric Anchor
        ctx.fillStyle = '#06b6d4';
        ctx.beginPath();
        ctx.arc(sx1 + w/2, sy2, 5, 0, Math.PI * 2);
        ctx.fill();
      });

      // Connecting Conflict Line
      if (annotations.length >= 2) {
        const p1 = [annotations[0].bbox[0] + (annotations[0].bbox[2] - annotations[0].bbox[0])/2, annotations[0].bbox[3]];
        const p2 = [annotations[1].bbox[0] + (annotations[1].bbox[2] - annotations[1].bbox[0])/2, annotations[1].bbox[3]];
        
        ctx.setLineDash([6, 6]);
        ctx.strokeStyle = (data.conflict && data.conflict.active) ? '#ef4444' : 'rgba(59, 130, 246, 0.5)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(offsetX + p1[0] * scale, offsetY + p1[1] * scale);
        ctx.lineTo(offsetX + p2[0] * scale, offsetY + p2[1] * scale);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    function update3DTwin(data) {
      const tracks = data.tracks_3d || [];
      const currentIds = new Set();

      tracks.forEach(trk => {
        currentIds.add(trk.track_id);
        const mesh = getOrCreateAgentMesh(trk.track_id, trk.class_name);
        mesh.position.set(trk.position[0], trk.position[1], 0);

        if (trk.class_name === 'WORKER') {
          if (trk.forecast_trajectory && trk.forecast_trajectory.length > 0) {
            const pts = [new THREE.Vector3(trk.position[0], trk.position[1], 0.1)];
            trk.forecast_trajectory.forEach(pt => pts.push(new THREE.Vector3(pt[0], pt[1], 0.1)));
            workerPathLine.geometry.setFromPoints(pts);
          }
        } else if (trk.class_name === 'HEAVY_EQUIPMENT') {
          if (trk.forecast_trajectory && trk.forecast_trajectory.length > 0) {
            const pts = [new THREE.Vector3(trk.position[0], trk.position[1], 0.1)];
            trk.forecast_trajectory.forEach(pt => pts.push(new THREE.Vector3(pt[0], pt[1], 0.1)));
            loaderPathLine.geometry.setFromPoints(pts);
          }
        }
      });

      // Hide inactive agents
      Object.keys(agentMeshes).forEach(tid => {
        if (!currentIds.has(parseInt(tid))) {
          agentMeshes[tid].position.set(999, 999, 999);
        }
      });

      // Conflict Ring Opacity
      if (data.conflict && data.conflict.active) {
        conflictRing.material.opacity = 0.9;
        if (data.conflict.conflict_coords) {
          conflictRing.position.set(data.conflict.conflict_coords[0], data.conflict.conflict_coords[1], 0.1);
        }
      } else {
        conflictRing.material.opacity = 0.0;
      }
    }

    function updateMetrics(data) {
      const conflict = data.conflict || {};
      const ttcEl = document.getElementById('metric-ttc');
      const pcolEl = document.getElementById('metric-pcol');
      const wltEl = document.getElementById('metric-wlt');
      const latEl = document.getElementById('metric-latency');

      if (conflict.min_ttc_seconds !== null && conflict.min_ttc_seconds !== undefined) {
        ttcEl.textContent = `${conflict.min_ttc_seconds.toFixed(2)} s`;
        ttcEl.style.color = conflict.min_ttc_seconds <= 2.0 ? '#ef4444' : (conflict.min_ttc_seconds <= 4.0 ? '#f59e0b' : '#10b981');
      } else {
        ttcEl.textContent = '> 5.0 s';
        ttcEl.style.color = '#10b981';
      }

      const pcol = conflict.collision_probability || 0.0;
      pcolEl.textContent = `${(pcol * 100).toFixed(1)} %`;
      pcolEl.style.color = pcol > 0.5 ? '#ef4444' : (pcol > 0.2 ? '#f59e0b' : '#10b981');

      wltEl.textContent = 'Not measured';
      latEl.textContent = data.edge_cycle_latency_ms === undefined ? 'Unavailable' : `${data.edge_cycle_latency_ms.toFixed(2)} ms`;

      const alarm = conflict.debounced_alarm || 'NORMAL_LEVEL_0';
      if (alarm === 'CRITICAL_LEVEL_3') {
        alertBadge.textContent = 'CRITICAL EMERGENCY ALERT';
        alertBadge.className = 'badge badge-alert-critical';
      } else if (alarm === 'WARNING_LEVEL_2') {
        alertBadge.textContent = 'WARNING LEVEL 2';
        alertBadge.className = 'badge badge-alert-warning';
      } else if (alarm === 'ADVISORY_LEVEL_1') {
        alertBadge.textContent = 'ADVISORY LEVEL 1';
        alertBadge.className = 'badge badge-alert-advisory';
      } else {
        alertBadge.textContent = 'NORMAL (SAFE)';
        alertBadge.className = 'badge badge-alert-normal';
      }
    }

    function updateValidationTable(data) {
      const comp = data.comparison || {};
      const baselines = comp.baselines || {};

      const r3 = baselines.radial_3m || {};
      document.getElementById('val-r3-status').textContent = r3.alert || 'SAFE';
      document.getElementById('val-r3-status').style.color = r3.alert === 'CRITICAL' ? '#ef4444' : '#9ca3af';
      document.getElementById('val-r3-verdict').textContent = r3.verdict || 'Delayed intervention';

      const r5 = baselines.radial_5m || {};
      document.getElementById('val-r5-status').textContent = r5.alert || 'SAFE';
      document.getElementById('val-r5-status').style.color = r5.alert === 'CRITICAL' ? '#f59e0b' : '#9ca3af';
      document.getElementById('val-r5-verdict').textContent = r5.verdict || 'Over-triggers on co-work';

      const cvkm = baselines.cvkm || {};
      document.getElementById('val-cvkm-status').textContent = cvkm.alert || 'SAFE';
      document.getElementById('val-cvkm-status').style.color = cvkm.alert === 'CRITICAL' ? '#f59e0b' : '#9ca3af';
      document.getElementById('val-cvkm-verdict').textContent = cvkm.verdict || 'Overshoots curves';

      const stgnn = baselines.sentinel_stgnn || {};
      const stAlarm = (data.conflict && data.conflict.debounced_alarm === 'CRITICAL_LEVEL_3') ? 'CRITICAL' : 'SAFE';
      document.getElementById('val-stgnn-status').textContent = stAlarm;
      document.getElementById('val-stgnn-status').style.color = stAlarm === 'CRITICAL' ? '#ef4444' : '#34d399';
    }

    // -------------------------------------------------------------------
    // 5. Human-in-the-Loop Adjudication
    // -------------------------------------------------------------------
    window.addEventListener('keydown', (e) => {
      if (e.key === 't' || e.key === 'T') recordVerdict('TRUE_POSITIVE');
      if (e.key === 'f' || e.key === 'F') recordVerdict('FALSE_POSITIVE');
      if (e.key === 'c' || e.key === 'C') recordVerdict('CONTROLLED_WORK');
      if (e.key === ' ') { e.preventDefault(); togglePlay(); }
      if (e.key === '.') stepFrame(1);
      if (e.key === ',') stepFrame(-1);
    });

    async function recordVerdict(verdict) {
      const event_id = `INC-DEMO-${currentScenario.toUpperCase()}-F${currentFrameIdx}`;
      try {
        const published = await fetch('/api/v1/incidents/publish', {
          method: 'POST', headers: {'Content-Type': 'application/json', 'X-Sentinel-Request': 'hub'},
          body: JSON.stringify({event_id, camera_id: currentScenario, source: 'recorded_demo', frame_index: currentFrameIdx})
        });
        if (!published.ok && published.status !== 409) throw new Error('Incident could not be stored');
        const saved = await fetch('/api/v1/adjudication/submit', {
          method: 'POST', headers: {'Content-Type': 'application/json', 'X-Sentinel-Request': 'hub'},
          body: JSON.stringify({event_id, verdict, root_cause_summary: 'Human review of recorded demo frame'})
        });
        if (!saved.ok) throw new Error('Review could not be stored; check permissions');
        alert(`Review saved: ${verdict}`);
      } catch (e) { alert(e.message); }
    }

    // Dynamic Scenario Discovery & Dropdown Population
    async function initDynamicDropdown(preserveSelected = true) {
      const select = document.getElementById('scenario-dropdown');
      try {
        const resp = await fetch('/api/v1/demo/scenarios');
        const scenarios = await resp.json();
        if (scenarios && scenarios.length > 0) {
          const currentVal = preserveSelected ? select.value : null;
          select.innerHTML = '';
          scenarios.forEach(scen => {
            const opt = document.createElement('option');
            opt.value = scen.id;
            opt.textContent = scen.title;
            select.appendChild(opt);
          });
          
          if (currentVal && scenarios.some(s => s.id === currentVal)) {
            currentScenario = currentVal;
          } else {
            const preferred = scenarios.find(s => s.id === 'scenario_worker_in_excavator_blind_spot')
                           || scenarios.find(s => s.id === 'scenario_exca_near_miss')
                           || scenarios[0];
            currentScenario = preferred ? preferred.id : scenarios[0].id;
          }
          select.value = currentScenario;
        }
      } catch (err) {
        console.warn('Dynamic scenario fetch failed, using fallback:', err);
      }
      loadScenarioTelemetry(currentScenario);
    }

    async function refreshScenarioList() {
      const select = document.getElementById('scenario-dropdown');
      select.disabled = true;
      await initDynamicDropdown(true);
      select.disabled = false;
      console.log('Dynamic scenarios updated from folder.');
    }

    // Initial boot
    initDynamicDropdown(false);

    // ===================================================================
    // AI Safety Supervisor Agent Modal & Chat
    // ===================================================================
    let agentConversationHistory = [];

    function openAgentModal() {
      document.getElementById('agent-modal-backdrop').style.display = 'flex';
      document.getElementById('agent-input-field').focus();
    }

    function closeAgentModal() {
      document.getElementById('agent-modal-backdrop').style.display = 'none';
    }

    function onBackdropClick(e) {
      if (e.target.id === 'agent-modal-backdrop') {
        closeAgentModal();
      }
    }

    function formatMarkdown(text) {
      if (!text) return '';
      let html = escapeHtml(text)
        .replace(/### (.*?)\n/g, '<h3>$1</h3>')
        .replace(/## (.*?)\n/g, '<h2>$1</h2>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code style="background: rgba(0,0,0,0.4); padding: 1px 4px; border-radius: 3px; font-family: var(--mono-font); color: #34d399;">$1</code>')
        .replace(/\n\n/g, '<br><br>')
        .replace(/\n- (.*?)/g, '<br>• $1');
      return html;
    }

    function appendAgentChatMessage(role, content, tools = []) {
      const chatBody = document.getElementById('agent-chat-body');
      const msgDiv = document.createElement('div');
      msgDiv.className = `chat-msg ${role === 'user' ? 'chat-msg-user' : 'chat-msg-agent'}`;

      let inner = '';
      if (tools && tools.length > 0) {
        tools.forEach(t => {
          inner += `<div class="tool-badge">🔧 Tool Executed: ${escapeHtml(t.tool)}</div>`;
        });
      }
      inner += `<div>${formatMarkdown(content)}</div>`;
      msgDiv.innerHTML = inner;
      chatBody.appendChild(msgDiv);
      chatBody.scrollTop = chatBody.scrollHeight;
    }

    async function sendAgentMessage(customText = null) {
      const input = document.getElementById('agent-input-field');
      const text = (customText || input.value || '').trim();
      if (!text) return;

      appendAgentChatMessage('user', text);
      if (!customText) input.value = '';

      // Loading indicator
      const chatBody = document.getElementById('agent-chat-body');
      const loadingDiv = document.createElement('div');
      loadingDiv.id = 'agent-loading-indicator';
      loadingDiv.className = 'chat-msg chat-msg-agent';
      loadingDiv.innerHTML = '<span style="color: var(--accent-cyan);">⚙️ Agent ReAct loop executing domain tools...</span>';
      chatBody.appendChild(loadingDiv);
      chatBody.scrollTop = chatBody.scrollHeight;

      try {
        const resp = await fetch('/api/v1/agent/supervisor/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Sentinel-Request': 'hub' },
          body: JSON.stringify({
            message: text,
            site_id: 'SITE-01',
            history: agentConversationHistory.slice(-4)
          })
        });
        const data = await resp.json();
        const loadElem = document.getElementById('agent-loading-indicator');
        if (loadElem) loadElem.remove();

        if (data.response) {
          appendAgentChatMessage('agent', data.response, data.tool_executions || []);
          agentConversationHistory.push({ role: 'user', content: text });
          agentConversationHistory.push({ role: 'assistant', content: data.response });
        } else {
          appendAgentChatMessage('agent', 'Error: Received empty response from supervisor agent.');
        }
      } catch (err) {
        const loadElem = document.getElementById('agent-loading-indicator');
        if (loadElem) loadElem.remove();
        appendAgentChatMessage('agent', `Failed to connect to agent server: ${err.message}`);
      }
    }

    function runAgentQuickAction(type) {
      if (type === 'ptw') {
        sendAgentMessage('Compile work permit for Pier B4: Trench excavation with heavy articulated loader active, spotter present.');
      } else if (type === 'telemetry') {
        sendAgentMessage(`Check live telemetry and kinematic risk for ${currentScenario}`);
      } else if (type === 'adjudicate') {
        sendAgentMessage(`Run multimodal adjudication and active learning triage for ${currentScenario}`);
      } else if (type === 'toolbox') {
        sendAgentMessage('Generate OSHA daily safety toolbox briefing for heavy machinery swing zones');
      } else if (type === 'active_learning') {
        sendAgentMessage('Inspect active learning retraining queue and show curated edge cases');
      }
    }

    async function triggerAgentAdjudication() {
      openAgentModal();
      runAgentQuickAction('adjudicate');
    }

    // Keyboard shortcut for Agent modal: 'A' or 'a'
    window.addEventListener('keydown', (e) => {
      if (document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
        return;
      }
      if (e.key === 'a' || e.key === 'A') {
        triggerAgentAdjudication();
      }
    });
  
document.querySelector('[data-demo-action="0"]').addEventListener("change", function(event) { changeScenario(this.value); });
document.querySelector('[data-demo-action="1"]').addEventListener("click", function(event) { refreshScenarioList(); });
document.querySelector('[data-demo-action="2"]').addEventListener("click", function(event) { openAgentModal(); });
document.querySelector('[data-demo-action="3"]').addEventListener("click", function(event) { togglePlay(); });
document.querySelector('[data-demo-action="4"]').addEventListener("click", function(event) { stepFrame(-1); });
document.querySelector('[data-demo-action="5"]').addEventListener("click", function(event) { stepFrame(1); });
document.querySelector('[data-demo-action="6"]').addEventListener("click", function(event) { onScrubberClick(event); });
document.querySelector('[data-demo-action="7"]').addEventListener("click", function(event) { setSpeed(0.5); });
document.querySelector('[data-demo-action="8"]').addEventListener("click", function(event) { setSpeed(1.0); });
document.querySelector('[data-demo-action="9"]').addEventListener("click", function(event) { setSpeed(2.0); });
document.querySelector('[data-demo-action="10"]').addEventListener("click", function(event) { recordVerdict('TRUE_POSITIVE'); });
document.querySelector('[data-demo-action="11"]').addEventListener("click", function(event) { recordVerdict('FALSE_POSITIVE'); });
document.querySelector('[data-demo-action="12"]').addEventListener("click", function(event) { recordVerdict('CONTROLLED_WORK'); });
document.querySelector('[data-demo-action="13"]').addEventListener("click", function(event) { triggerAgentAdjudication(); });
document.querySelector('[data-demo-action="14"]').addEventListener("click", function(event) { onBackdropClick(event); });
document.querySelector('[data-demo-action="15"]').addEventListener("click", function(event) { event.stopPropagation(); });
document.querySelector('[data-demo-action="16"]').addEventListener("click", function(event) { closeAgentModal(); });
document.querySelector('[data-demo-action="17"]').addEventListener("click", function(event) { runAgentQuickAction('ptw'); });
document.querySelector('[data-demo-action="18"]').addEventListener("click", function(event) { runAgentQuickAction('telemetry'); });
document.querySelector('[data-demo-action="19"]').addEventListener("click", function(event) { runAgentQuickAction('adjudicate'); });
document.querySelector('[data-demo-action="20"]').addEventListener("click", function(event) { runAgentQuickAction('toolbox'); });
document.querySelector('[data-demo-action="21"]').addEventListener("click", function(event) { runAgentQuickAction('active_learning'); });
document.querySelector('[data-demo-action="22"]').addEventListener("click", function(event) { sendAgentMessage(); });