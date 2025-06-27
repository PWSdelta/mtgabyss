# MTG Card Analysis System

A simple Flask application for analyzing Magic: The Gathering cards using AI.

## Features

- Card search and browsing
- Art gallery view
- AI-powered card analysis using Ollama
- Distributed worker system for analysis

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up MongoDB:
- Install MongoDB if not already installed
- Create a database named 'mtg'
- Set MONGODB_URI environment variable (optional, defaults to localhost)

3. Start the Flask server:
```bash
python app.py
```

4. Run worker(s):
```bash
python worker.py
```

## Environment Variables

- `MONGODB_URI`: MongoDB connection string (default: mongodb://localhost:27017)
- `OLLAMA_MODEL`: Ollama model to use for analysis (default: llama2)
- `API_URL`: API URL for workers (default: http://localhost:5000/api)

## Project Structure

- `app.py`: Main Flask application
- `worker.py`: Analysis worker script
- `templates/`: HTML templates
  - `base.html`: Base template with common layout
  - `search.html`: Card search page
  - `card.html`: Card detail page
  - `gallery.html`: Art gallery page
- `requirements.txt`: Python dependencies
