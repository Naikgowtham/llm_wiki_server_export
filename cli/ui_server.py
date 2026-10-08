import click
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import asyncio
import json

app = FastAPI()
progress_queue = asyncio.Queue()

html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>LLM Wiki Dashboard</title>
    <style>
        body { font-family: sans-serif; padding: 20px; max-width: 800px; margin: auto; background: #121212; color: #e0e0e0; }
        #log { background: #1e1e1e; padding: 15px; border-radius: 8px; font-family: monospace; height: 400px; overflow-y: auto; }
        .progress-container { width: 100%; background-color: #333; border-radius: 8px; margin-bottom: 20px; overflow: hidden; }
        .progress-bar { width: 0%; height: 24px; background-color: #4CAF50; transition: width 0.3s; }
        .message { margin-bottom: 5px; border-bottom: 1px solid #333; padding-bottom: 5px; }
    </style>
</head>
<body>
    <h1>LLM Wiki Dashboard</h1>
    <div class="progress-container">
        <div id="progress" class="progress-bar"></div>
    </div>
    <div id="log"></div>
    <script>
        var source = new EventSource("/progress");
        source.onmessage = function(event) {
            var data = JSON.parse(event.data);
            if (data.progress) {
                document.getElementById("progress").style.width = data.progress + "%";
            }
            if (data.message) {
                var log = document.getElementById("log");
                log.innerHTML += "<div class='message'>" + data.message + "</div>";
                log.scrollTop = log.scrollHeight;
            }
        };
    </script>
</body>
</html>
"""

@app.get("/")
async def get_dashboard():
    return HTMLResponse(content=html_content)

@app.get("/progress")
async def progress_stream(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                update = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                yield f"data: {update}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'ping': 1})}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/update")
async def post_update(update: dict):
    await progress_queue.put(json.dumps(update))
    return {"status": "ok"}

@click.command(name="dashboard")
@click.option("--port", default=8000, help="Port to run the dashboard on")
def dashboard_cmd(port):
    """Start the live progress dashboard Web UI."""
    click.echo(f"Starting live progress dashboard on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="error")
