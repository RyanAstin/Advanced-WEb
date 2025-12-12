import os
import time
import pathlib
from datetime import datetime

import requests
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy



APP_DIR = pathlib.Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
DB_URI = f"sqlite:///{DB_PATH}"

app = Flask(__name__)
app.secret_key = "replace-with-a-real-secret-for-prod"
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)




# Database Models

class Favourite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appid = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.String(512), nullable=False)
    image = db.Column(db.String(1024))
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"appid": self.appid, "name": self.name, "image": self.image}


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appid = db.Column(db.Integer, nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "appid": self.appid,
            "rating": self.rating,
            "text": self.text,
            "created_at": int(self.created_at.timestamp()),
        }




# Setup


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_db():
    ensure_data_dir()
    if not DB_PATH.exists():
        db.create_all()




# Favourites


def get_all_favourites():
    rows = Favourite.query.order_by(Favourite.added_at.desc()).all()
    return [r.to_dict() for r in rows]


def add_favourite_db(appid, name, image):
    existing = Favourite.query.filter_by(appid=int(appid)).first()
    if existing:
        return False
    fav = Favourite(appid=int(appid), name=name, image=image)
    db.session.add(fav)
    db.session.commit()
    return True


def remove_favourite_db(appid):
    row = Favourite.query.filter_by(appid=int(appid)).first()
    if not row:
        return False
    db.session.delete(row)
    db.session.commit()
    return True




# Reviews


def get_reviews_for_app(appid):
    rows = Review.query.filter_by(appid=int(appid)).order_by(Review.created_at.desc()).all()
    return [r.to_dict() for r in rows]


def add_review_db(appid, rating, text):
    r = Review(appid=int(appid), rating=int(rating), text=text.strip())
    db.session.add(r)
    db.session.commit()
    return True




# YouTube Fallback


def search_youtube_trailer(game_name):
    """Finds the first YouTube trailer result for the game."""
    try:
        q = f"{game_name} official trailer"
        url = f"https://www.youtube.com/results?search_query={requests.utils.quote(q)}"
        r = requests.get(url, timeout=10)
        
        # Look for video IDs
        import re
        match = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", r.text)
        if match:
            video_id = match.group(1)
            return f"https://www.youtube.com/embed/{video_id}"
    except:
        pass
    return None




# Steam API


def get_steam_game(appid, lang="english"):
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l={lang}"
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        j = r.json()
        key = str(appid)

        if key in j and j[key].get("success"):
            d = j[key]["data"]
            header_image = d.get("header_image")
            short = d.get("short_description") or ""
            genres = [g.get("description") for g in d.get("genres", [])]
            price = d.get("price_overview", {}).get("final_formatted") or ("Free" if d.get("is_free") else "—")

            # Steam trailer attempt
            trailer = None
            movies = d.get("movies") or []
            if movies:
                first = movies[0]
                webm = first.get("webm") or {}
                trailer = webm.get("max") or webm.get("480")
                if not trailer:
                    mp4 = first.get("mp4") or {}
                    trailer = mp4.get("max") or mp4.get("480")

            # if Steam trailer missing, use YouTube fallback
            if not trailer:
                trailer = search_youtube_trailer(d.get("name"))

            return {
                "appid": appid,
                "name": d.get("name"),
                "short_description": short,
                "header_image": header_image,
                "genres": genres,
                "price": price,
                "store_link": f"https://store.steampowered.com/app/{appid}",
                "trailer_url": trailer,
            }

    except Exception as e:
        print(f"[get_steam_game] error for {appid}: {e}")

    return None



def fetch_top_sellers(limit=9):
    try:
        url = "https://store.steampowered.com/api/featuredcategories"
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        j = r.json()

        sellers = []
        for key, val in j.items():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                for item in val:
                    appid = item.get("id") or item.get("appid")
                    if appid:
                        sellers.append(int(appid))

            if isinstance(val, dict) and isinstance(val.get("items"), list):
                for it in val["items"]:
                    appid = it.get("id") or it.get("appid")
                    if appid:
                        sellers.append(int(appid))

        out = []
        seen = set()

        for a in sellers:
            if a not in seen:
                out.append(a)
                seen.add(a)
            if len(out) >= limit:
                break

        return out
    except:
        return []




# Jinja Filter


@app.template_filter("datetimeformat")
def datetimeformat(value):
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")
    except:
        return value




# Routes


@app.route("/")
def index():
    top_ids = fetch_top_sellers(limit=9)
    games = [get_steam_game(aid) for aid in top_ids if get_steam_game(aid)]
    return render_template("index.html", games=games)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        flash("Please enter a search term.", "warning")
        return redirect(url_for("index"))

    search_url = f"https://steamcommunity.com/actions/SearchApps/{requests.utils.requote_uri(q)}"

    try:
        r = requests.get(search_url, timeout=8)
        r.raise_for_status()
        results = r.json()
    except:
        results = []

    enriched = []
    for item in results[:9]:
        appid = item.get("appid")
        details = get_steam_game(appid)
        if details:
            enriched.append(details)

    return render_template("search_results.html", query=q, results=enriched)


@app.route("/game/<int:appid>")
def game_detail(appid):
    g = get_steam_game(appid)
    if not g:
        flash("Game details unavailable.", "warning")
        return redirect(url_for("index"))

    reviews = get_reviews_for_app(appid)
    return render_template("game_detail.html", game=g, reviews=reviews)


@app.route("/game/<int:appid>/review", methods=["POST"])
def add_review_route(appid):
    rating = request.form.get("rating")
    text = request.form.get("text")

    if not rating or not text.strip():
        flash("Please provide both a rating and a review.", "warning")
        return redirect(url_for("game_detail", appid=appid))

    add_review_db(appid, rating, text)
    flash("Review submitted!", "success")
    return redirect(url_for("game_detail", appid=appid))


@app.route("/favourites")
def favourites_page():
    favs = get_all_favourites()
    return render_template("favourites.html", favourites=favs)


@app.route("/favourites/add", methods=["POST"])
def favourites_add():
    appid = request.form.get("appid")
    name = request.form.get("name")
    image = request.form.get("image")

    if not appid or not name:
        flash("Missing game details.", "warning")
        return redirect(request.referrer or url_for("index"))

    ok = add_favourite_db(appid, name, image)
    flash("Added to favourites." if ok else "Already in favourites.", "info")
    return redirect(request.referrer or url_for("favourites_page"))


@app.route("/favourites/remove", methods=["POST"])
def favourites_remove():
    appid = request.form.get("appid")

    if not appid:
        flash("No app specified.", "warning")
        return redirect(url_for("favourites_page"))

    removed = remove_favourite_db(appid)
    flash("Removed from favourites." if removed else "Not in favourites.", "info")
    return redirect(url_for("favourites_page"))



# Auto-create database
with app.app_context():
    db.create_all()



# Start
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
