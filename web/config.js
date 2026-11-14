// Where the API lives. Add ?api=https://your-api-url to the page address to override.
const local = ["localhost", "127.0.0.1"].includes(location.hostname) || location.protocol === "file:";
window.API_BASE = new URLSearchParams(location.search).get("api")
  || (local ? "http://localhost:8000" : "https://raceday-cover-agent.onrender.com");
