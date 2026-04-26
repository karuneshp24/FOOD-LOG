from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import datetime
import os
from functools import wraps
import socket
import qrcode
import io
from flask import send_file

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_caltrack'  # Required for sessions
DB_NAME = os.path.join(os.path.dirname(__file__), "caltrack.db")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        # Use a non-reachable IP to just get the local interface
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Create tables
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            daily_limit INTEGER DEFAULT 2000
        )
    ''')
    
    # Check if entries table needs migration
    c.execute("PRAGMA table_info(entries)")
    columns = [col[1] for col in c.fetchall()]
    if 'user_id' not in columns:
        c.execute('DROP TABLE IF EXISTS entries')

    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            calories INTEGER NOT NULL,
            meal_type TEXT NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Add is_suggestion column if it doesn't exist
    if 'is_suggestion' not in columns:
        try:
            c.execute("ALTER TABLE entries ADD COLUMN is_suggestion BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

# Initialize DB on startup
with app.app_context():
    init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        conn = get_db_connection()
        g.user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()

def get_greeting():
    hour = datetime.datetime.now().hour
    if hour < 12:
        return "Good Morning"
    elif hour < 18:
        return "Good Afternoon"
    else:
        return "Good Evening"

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        error = None

        if not username or not email or not password:
            error = 'All fields are required.'
        elif conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone() is not None:
            error = f'User {username} is already registered.'
        elif conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone() is not None:
            error = f'Email {email} is already registered.'

        if error is None:
            conn.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                         (username, email, generate_password_hash(password)))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        
        conn.close()
        flash(error)

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        error = None
        if user is None:
            error = 'Incorrect username.'
        elif not check_password_hash(user['password_hash'], password):
            error = 'Incorrect password.'

        if error is None:
            session.clear()
            session['user_id'] = user['id']
            return redirect(url_for('index'))
            
        flash(error)

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    conn = get_db_connection()
    today = datetime.date.today().isoformat()
    entries = conn.execute('SELECT * FROM entries WHERE user_id = ? AND date = ? ORDER BY id DESC', 
                           (g.user['id'], today)).fetchall()
    conn.close()
    
    total_calories = sum(entry['calories'] for entry in entries)
    daily_limit = g.user['daily_limit']
    progress = min((total_calories / daily_limit) * 100, 100)
    
    if total_calories == 0:
        message = "Let's get tracking!"
    elif total_calories < daily_limit * 0.5:
        message = "You're doing great! Keep it up."
    elif total_calories < daily_limit * 0.9:
        message = "Getting closer to your limit."
    elif total_calories <= daily_limit:
        message = "Almost at your limit!"
    else:
        message = "You've exceeded your daily goal."
        
    meal_icons = {
        'Breakfast': '🍳',
        'Lunch': '🥗',
        'Snack': '🍎',
        'Snacks': '🍎',
        'Dinner': '🍛'
    }

    greeting = f"{get_greeting()}, {g.user['username']}!"

    return render_template('index.html', entries=entries, total_calories=total_calories, 
                           progress=progress, message=message, meal_icons=meal_icons, greeting=greeting)

@app.route('/add', methods=['POST'])
@login_required
def add():
    name = request.form.get('name')
    calories = request.form.get('calories')
    meal_type = request.form.get('meal_type')
    is_suggestion = request.form.get('is_suggestion', 0)
    today = datetime.date.today().isoformat()

    if name and calories and meal_type:
        try:
            cal_int = int(calories)
            is_sug_int = int(is_suggestion)
            conn = get_db_connection()
            conn.execute('INSERT INTO entries (user_id, name, calories, meal_type, date, is_suggestion) VALUES (?, ?, ?, ?, ?, ?)',
                         (g.user['id'], name, cal_int, meal_type, today, is_sug_int))
            conn.commit()
            conn.close()
        except ValueError:
            pass 

    referrer = request.referrer
    if referrer and 'suggestions' in referrer:
        flash(f'Added {name} to your log!')
        return redirect(url_for('suggestions'))
    return redirect(url_for('index'))

@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM entries WHERE id = ? AND user_id = ?', (id, g.user['id']))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/clear', methods=['POST'])
@login_required
def clear():
    conn = get_db_connection()
    today = datetime.date.today().isoformat()
    conn.execute('DELETE FROM entries WHERE user_id = ? AND date = ?', (g.user['id'], today))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = get_db_connection()
    if request.method == 'POST':
        new_username = request.form['username']
        new_email = request.form['email']
        new_limit = request.form['daily_limit']
        try:
            new_limit = int(new_limit)
            conn.execute('UPDATE users SET username = ?, email = ?, daily_limit = ? WHERE id = ?',
                         (new_username, new_email, new_limit, g.user['id']))
            conn.commit()
            flash('Profile updated successfully!')
            g.user = conn.execute('SELECT * FROM users WHERE id = ?', (g.user['id'],)).fetchone()
        except Exception as e:
            flash('Error updating profile.')

    history_rows = conn.execute('''
        SELECT date, SUM(calories) as daily_total 
        FROM entries 
        WHERE user_id = ? 
        GROUP BY date 
        ORDER BY date DESC
        LIMIT 10
    ''', (g.user['id'],)).fetchall()
    
    suggested_meals = conn.execute('''
        SELECT name, calories, meal_type, date 
        FROM entries 
        WHERE user_id = ? AND is_suggestion = 1 
        ORDER BY id DESC 
        LIMIT 10
    ''', (g.user['id'],)).fetchall()
    
    conn.close()
    
    return render_template('profile.html', history=history_rows, suggested_meals=suggested_meals)

@app.route('/suggestions')
@login_required
def suggestions():
    suggestions_data = {
        'Breakfast': [
            {'name': 'Idli with sambar', 'calories': 250, 'icon': '🍳', 'type': 'Medium'},
            {'name': 'Oats with fruits', 'calories': 200, 'icon': '🥣', 'type': 'Light'},
            {'name': 'Boiled eggs + toast', 'calories': 300, 'icon': '🍞', 'type': 'Heavy'}
        ],
        'Lunch': [
            {'name': 'Rice + dal + vegetables', 'calories': 450, 'icon': '🍛', 'type': 'Heavy'},
            {'name': 'Chapati + curry', 'calories': 350, 'icon': '🫓', 'type': 'Medium'},
            {'name': 'Curd rice', 'calories': 250, 'icon': '🍚', 'type': 'Light'}
        ],
        'Snacks': [
            {'name': 'Fruits (apple, banana)', 'calories': 150, 'icon': '🍎', 'type': 'Light'},
            {'name': 'Nuts (almonds, peanuts)', 'calories': 200, 'icon': '🥜', 'type': 'Medium'},
            {'name': 'Sprouts salad', 'calories': 120, 'icon': '🥗', 'type': 'Light'}
        ],
        'Dinner': [
            {'name': 'Light chapati + sabzi', 'calories': 250, 'icon': '🫓', 'type': 'Light'},
            {'name': 'Vegetable soup', 'calories': 150, 'icon': '🥣', 'type': 'Light'},
            {'name': 'Grilled paneer/chicken', 'calories': 400, 'icon': '🍗', 'type': 'Heavy'}
        ]
    }
    
    return render_template('suggestions.html', suggestions=suggestions_data)

@app.route('/qr')
def qr_code():
    # If running on Render/Cloud, use the actual host URL
    if os.environ.get("RENDER") or os.environ.get("PORT"):
        url = request.host_url
    else:
        # If running locally, use the local IP
        local_ip = get_local_ip()
        url = f"http://{local_ip}:5000"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png')

@app.context_processor
def inject_local_info():
    if os.environ.get("RENDER") or os.environ.get("PORT"):
        url = request.host_url
        display_ip = "Public Cloud"
    else:
        local_ip = get_local_ip()
        url = f"http://{local_ip}:5000"
        display_ip = local_ip
        
    return dict(local_ip=display_ip, local_url=url)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
