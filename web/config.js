// Where the API lives. Add ?api=https://your-api-url to the page address to override.
window.API_BASE = new URLSearchParams(location.search).get("api") || "http://localhost:8000";
