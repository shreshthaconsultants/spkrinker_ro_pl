# Real-Time GA Progress Streaming Guide

## Architecture

```
Backend (FastAPI)                Frontend (ZWCAD/Terminal)
├─ /api/ga/optimise              └─ EventSource("/api/ga/progress")
│  └─ runs GA                        └─ receives live updates
│     └─ progress_cb → _progress_queue
│
└─ /api/ga/progress (SSE)
   └─ yields progress events
```

## Backend Changes Made

1. **Progress Queue** — Global queue to collect GA updates
2. **Progress Callback Factory** — Creates callbacks per zone with zone tracking
3. **SSE Endpoint** (`/api/ga/progress`) — Server-Sent Events stream for real-time updates

### Flow

Each GA generation triggers:
```python
progress_cb(generation, fitness, coverage_pct)
  ↓
_progress_queue.put({
    "zone": 1,
    "total_zones": 2,
    "generation": 15,
    "fitness": 8543.2,
    "coverage_pct": 92.5
})
  ↓
SSE sends: "data: {...}\n\n"
```

---

## Frontend: HTML/JavaScript Example

Save as `ga_monitor.html` and open in browser while GA is running:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Sprinkler GA Progress Monitor</title>
    <style>
        body { font-family: monospace; background: #1a1a1a; color: #0f0; padding: 20px; }
        .zone { border: 1px solid #0f0; margin: 10px; padding: 10px; }
        .progress-bar { width: 300px; height: 20px; background: #333; border: 1px solid #0f0; }
        .progress { height: 100%; background: #0f0; width: 0%; transition: width 0.2s; }
        .stat { margin: 5px 0; }
    </style>
</head>
<body>
    <h1>GA Optimization Progress</h1>
    <div id="zones"></div>

    <script>
        const zones = {};

        const es = new EventSource('http://localhost:8000/api/ga/progress');

        es.addEventListener('ga_progress', (e) => {
            const data = JSON.parse(e.data);
            const zoneKey = `zone_${data.zone}`;

            if (!zones[zoneKey]) {
                zones[zoneKey] = {
                    zone: data.zone,
                    total: data.total_zones,
                    generation: 0,
                    coverage: 0,
                };
                renderZones();
            }

            zones[zoneKey].generation = data.generation;
            zones[zoneKey].coverage = data.coverage_pct;
            zones[zoneKey].fitness = data.fitness;

            updateZoneDisplay(zoneKey);
        });

        es.addEventListener('error', (e) => {
            console.error('SSE Error:', e);
            es.close();
            document.body.innerHTML += '<p style="color: red;">Connection lost</p>';
        });

        function renderZones() {
            const container = document.getElementById('zones');
            container.innerHTML = '';
            Object.entries(zones).forEach(([key, zone]) => {
                const el = document.createElement('div');
                el.className = 'zone';
                el.id = key;
                el.innerHTML = `
                    <h2>Zone ${zone.zone}/${zone.total}</h2>
                    <div class="stat">Generation: <span class="gen">-</span></div>
                    <div class="stat">Coverage: <span class="cov">-</span>%</div>
                    <div class="stat">Fitness: <span class="fit">-</span></div>
                    <div class="progress-bar">
                        <div class="progress" style="width: 0%"></div>
                    </div>
                `;
                container.appendChild(el);
            });
        }

        function updateZoneDisplay(zoneKey) {
            const zone = zones[zoneKey];
            const el = document.getElementById(zoneKey);
            el.querySelector('.gen').textContent = zone.generation;
            el.querySelector('.cov').textContent = zone.coverage.toFixed(1);
            el.querySelector('.fit').textContent = (zone.fitness || 0).toFixed(1);

            // Assume max 120 generations (thorough preset)
            const progress = (zone.generation / 120) * 100;
            el.querySelector('.progress').style.width = progress + '%';
        }
    </script>
</body>
</html>
```

---

## Frontend: ZWCAD LISP Terminal Example

For real-time feedback in ZWCAD terminal (integrate into your LISP plugin):

```lisp
(defun show-ga-progress (url)
  "Display GA progress in ZWCAD terminal using websocket polling."
  (command "._. PROMPT \"GA Optimization in progress...")
  
  (let ((http-client (vlax-create-object "MSXML2.XMLHTTP.6.0"))
        (prev-gen 0))
    
    (vlax-invoke http-client 'open "GET" url :vlax-false)
    (vlax-invoke http-client 'send)
    
    (while (< prev-gen 120)
      (if (= (vlax-get http-client 'readystate) 4)
        (let* ((response (vlax-get http-client 'responseText))
               (data (parse-json response)))
          
          (if (> (cdr (assoc 'generation data)) prev-gen)
            (progn
              (setq prev-gen (cdr (assoc 'generation data)))
              (princ 
                (strcat 
                  "\nGen " (itoa prev-gen) 
                  " | Coverage: " 
                  (rtos (cdr (assoc 'coverage_pct data)) 2 1)
                  "%"
                ))
            )
          )
        )
      )
      (delay 0.1)
    )
    
    (command "._. PROMPT \"GA Complete!")
    (vlax-release-object http-client)
  )
)

;; Usage:
;; (show-ga-progress "http://localhost:8000/api/ga/progress")
```

---

## Alternative: WebSocket (for bidirectional communication)

If you want to also **cancel** or **pause** GA from frontend:

```python
from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

@app.websocket("/ws/ga/progress")
async def websocket_ga_progress(websocket: WebSocket):
    await websocket.accept()
    global _progress_queue
    _progress_queue = queue.Queue()
    
    try:
        while True:
            try:
                msg = _progress_queue.get(timeout=0.1)
                await websocket.send_json(msg)
            except queue.Empty:
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
```

---

## Testing

### 1. Start backend
```bash
uvicorn main:app --reload --port 8000
```

### 2. In another terminal, test progress stream
```bash
curl -N http://localhost:8000/api/ga/progress
```
You should see events streaming out.

### 3. Trigger GA optimization
```bash
# POST to /api/ga/optimise (in another terminal)
curl -X POST http://localhost:8000/api/ga/optimise \
  -F file=@myplan.dxf \
  -F ga_preset=balanced
```

The `/api/ga/progress` stream will immediately start emitting zone progress.

---

## Key Improvements Over Original

| Feature | Before | After |
|---------|--------|-------|
| User feedback during GA | ❌ Silent for 10-60s | ✅ Real-time per-generation |
| Zone awareness | ❌ No tracking | ✅ "Zone 1/3, Gen 15" |
| Progress visualization | ❌ None | ✅ Progress bar + coverage % |
| LISP integration | ❌ Manual polling | ✅ SSE stream parsing |
| Resource efficient | ✅ Callback only | ✅ Queue + event loop |

---

## Notes

- **SSE vs WebSocket**: SSE is simpler for one-way progress (what we have). Use WebSocket if you need cancellation/control.
- **Timeout**: SSE endpoint sends heartbeats every 0.5s to keep connection alive
- **Multiple clients**: Each client gets its own queue, so multiple frontends can monitor simultaneously
- **Zone ordering**: Progress events include zone index, so even if zones run out of order, UI stays synchronized
