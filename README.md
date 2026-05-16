# VidDrop Pro — Setup Guide

## Folder Structure
```
VidDropPro/
├── app.py
├── requirements.txt
├── templates/
│   └── index.html
└── downloads/   ← auto-created
```

## Windows Setup

### 1. Install Python
Download from https://python.org — check "Add Python to PATH"

### 2. Install ffmpeg
Download from https://ffmpeg.org/download.html → extract to C:\ffmpeg → add C:\ffmpeg\bin to PATH

### 3. Install dependencies
Open CMD in your project folder and run:
```
pip install -r requirements.txt
```

### 4. Run the app
```
python app.py
```

### 5. Open browser
http://localhost:5000

---

## Deploy to a Server

### Install on Ubuntu VPS
```bash
sudo apt update && sudo apt install python3 python3-pip ffmpeg -y
pip3 install -r requirements.txt
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

### Deploy to Render (free)
1. Push to GitHub
2. Create Web Service on render.com
3. Build command: pip install -r requirements.txt
4. Start command: gunicorn app:app

---

## Features
- Login / Register system
- Batch download (paste multiple links)
- Real-time speed & ETA progress bars
- Quality options: Best, 1080p, 720p, 480p, Audio
- Supports 1000+ sites via yt-dlp
- Files auto-delete after 10 minutes
