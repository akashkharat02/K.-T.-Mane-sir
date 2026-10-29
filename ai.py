"""AI layer: image classification, severity scoring, department routing."""
import math

CATEGORIES = {
    "pothole":        {"label": "Pothole / Road Damage",  "dept": "Road Department",        "base": 25, "prompt": "a photo of a pothole or damaged road"},
    "garbage":        {"label": "Garbage Dumping",        "dept": "Sanitation Department",  "base": 15, "prompt": "a photo of a pile of garbage dumped on the street"},
    "streetlight":    {"label": "Streetlight Problem",    "dept": "Electrical Department",  "base": 15, "prompt": "a photo of a broken street light or lamp post"},
    "leakage":        {"label": "Water Leakage",          "dept": "Water Supply Department","base": 20, "prompt": "a photo of water leaking from a pipe onto the road"},
    "infrastructure": {"label": "Infrastructure Damage",  "dept": "Public Works Department","base": 25, "prompt": "a photo of a broken railing, wall or public structure"},
    "hazard":         {"label": "Public Safety Hazard",   "dept": "Disaster & Safety Cell", "base": 35, "prompt": "a photo of a dangerous open manhole or open hazardous area"},
}
DEPARTMENTS = sorted({c["dept"] for c in CATEGORIES.values()})

_pipe = None
def _load():
    global _pipe
    if _pipe is None:
        from transformers import pipeline
        _pipe = pipeline("zero-shot-image-classification", model="openai/clip-vit-base-patch32")
    return _pipe

def classify_image(path):
    """Returns (category_key, confidence 0-1) or (None, 0) if no model available."""
    try:
        from PIL import Image
        keys = list(CATEGORIES)
        out = _load()(Image.open(path).convert("RGB"),
                      candidate_labels=[CATEGORIES[k]["prompt"] for k in keys])
        best = out[0]
        for k in keys:
            if CATEGORIES[k]["prompt"] == best["label"]:
                return k, float(best["score"])
    except Exception as e:
        print("AI classification unavailable:", e)
    return None, 0.0

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def severity(category, size, near_sensitive, main_road, duplicate_reports):
    """Rule-based score out of 100 (explainable, easy to defend in a viva)."""
    parts = {
        "Problem type": CATEGORIES[category]["base"],                       # up to 35
        "Size / extent": {"small": 0, "medium": 12, "large": 25}[size],     # up to 25
        "Near school/hospital": 20 if near_sensitive else 0,
        "Main road / high traffic": 10 if main_road else 0,
        "Repeat reports": min(duplicate_reports * 3, 10),
    }
    score = min(sum(parts.values()), 100)
    priority = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
    return score, priority, parts
