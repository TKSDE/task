import os
import psycopg2
from flask import Flask, jsonify

app = Flask(__name__)

def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        dbname=os.environ["DB_NAME"]
    )
    return conn

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "tksde"}), 200

@app.route("/", methods=["GET"])
def index():
    return jsonify({"message": "tksde is live"}), 200

@app.route("/db-check", methods=["GET"])
def db_check():
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({"db": "connected"}), 200
    except Exception as e:
        return jsonify({"db": "error", "detail": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
