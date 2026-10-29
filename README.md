# Civic Resolve – AI Civic Problem Reporting & Resolution System

## Run
    pip install -r requirements.txt
    python app.py          # http://127.0.0.1:5000

Demo logins: citizen/citizen123 · admin/admin123 · worker1..worker6 / worker123
(worker N belongs to one department – assign tickets only to workers of the matching department).

## Enable real AI (optional)
    pip install transformers torch
The first photo upload downloads CLIP (~600MB) and classifies zero-shot into the 6 categories.
Without it, the citizen picks the category manually and everything else still works.

## Flow
Photo -> AI category -> GPS -> severity score -> department -> ticket -> verify -> assign -> in progress -> resolved (after photo) -> citizen confirms

## Files
app.py (routes/DB) · ai.py (classifier, severity, departments) · templates/ (UI)

## Language and navigation
The interface includes an English/Marathi language selector in the top navigation. The selected language is saved in the browser. A Back button is available on each page; it returns to the previous page within the app, or to the role's home page when there is no in-app page to return to.
