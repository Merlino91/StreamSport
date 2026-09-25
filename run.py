import uvicorn
from app.config import HOST, PORT, DEBUG

if __name__ == "__main__":
    print(f"🏆 Starting StreamSport Addon server on http://{HOST}:{PORT}")
    print(f"👉 Configuration panel: http://localhost:{PORT}/configure")
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=DEBUG)
