import os
import re
import time
import uuid
import math
import sqlite3
import requests
from io import BytesIO
from PIL import Image, ImageOps
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_dev_key')
app.config['DATABASE'] = os.path.join(app.root_path, 'Database', 'database.db')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 60 * 60 * 24 * 7

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
gemini_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != 'your_google_gemini_api_key_here':
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

GEMINI_MODELS = [
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
]

NL_API_KEY = os.environ.get('NL_API_KEY')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def strip_html(text):
    if not text:
        return ''
    return re.sub(r'<[^>]+>', '', str(text)).strip()

def mask_sensitive_info(text):
    if not text:
        return ''
    return re.sub(
        r'((?:cert_key|api_key|key)=)([^&\s\'"\)]+)',
        r'\1***REDACTED***',
        str(text),
        flags=re.IGNORECASE
    )

def is_admin_user():
    return bool(g.user and g.user['is_admin'] == 1)

def normalize_to_rgb(img):
    img = ImageOps.exif_transpose(img)
    if img.mode in ('RGBA', 'LA', 'P'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        background.paste(img, mask=img.split()[-1] if 'A' in img.mode else None)
        return background
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img

def save_optimized_image(file_storage, upload_folder, max_size=(1000, 1000), quality=82):
    os.makedirs(upload_folder, exist_ok=True)
    img = Image.open(file_storage.stream)
    img = normalize_to_rgb(img)
    img.thumbnail(max_size, Image.Resampling.LANCZOS)

    filename = f"{uuid.uuid4().hex[:10]}.webp"
    save_path = os.path.join(upload_folder, filename)
    img.save(save_path, format='WEBP', quality=quality, method=6)
    return filename

def prepare_ai_image(file_storage, max_size=(800, 800)):
    img = Image.open(file_storage.stream)
    img = normalize_to_rgb(img)
    img.thumbnail(max_size, Image.Resampling.LANCZOS)
    return img

def generate_gemini_response(contents):
    last_error = None
    for model_name in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                return response.text.strip()
            except Exception as e:
                last_error = e
                err_str = str(e)
                if '503' in err_str or 'UNAVAILABLE' in err_str or '429' in err_str:
                    time.sleep(1.2)
                    continue
                break
    raise last_error

def download_cover_image(image_url, upload_folder):
    if not image_url or not image_url.startswith(('http://', 'https://')):
        return None
    try:
        resp = requests.get(image_url, timeout=5)
        if resp.status_code == 200 and resp.content:
            img = Image.open(BytesIO(resp.content))
            img = normalize_to_rgb(img)
            img.thumbnail((800, 800), Image.Resampling.LANCZOS)

            os.makedirs(upload_folder, exist_ok=True)
            filename = f"nl_{uuid.uuid4().hex[:10]}.webp"
            img.save(os.path.join(upload_folder, filename), format='WEBP', quality=85, method=6)
            return filename
    except Exception:
        return None
    return None

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db

def ensure_database_initialized():
    os.makedirs(os.path.dirname(app.config['DATABASE']), exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    conn = sqlite3.connect(app.config['DATABASE'])
    need_seed = False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        table_exists = cursor.fetchone() is not None

        if not table_exists:
            schema_path = os.path.join(app.root_path, 'Database', 'schema.sql')
            if os.path.exists(schema_path):
                with open(schema_path, 'r', encoding='utf-8') as f:
                    conn.executescript(f.read())
                need_seed = True

        conn.execute("""
            CREATE TABLE IF NOT EXISTS book_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                image_filename TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
    finally:
        conn.close()

    if need_seed and os.environ.get('AUTO_SEED', 'true').lower() == 'true':
        try:
            from Database.seed_data import insert_seed_data
            insert_seed_data()
        except Exception as e:
            app.logger.warning("초기 시드 데이터 자동 삽입 건너뜀: %s", mask_sensitive_info(e))

ensure_database_initialized()

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        cursor = get_db().cursor()
        cursor.execute(
            "SELECT id, student_id, name, trust_score, is_admin FROM users WHERE id = ?",
            (user_id,)
        )
        g.user = cursor.fetchone()

# ==========================================
# 1. 메인 & 검색
# ==========================================
@app.route('/')
def index():
    query = request.args.get('q', '').strip()
    db = get_db()
    cursor = db.cursor()

    if query:
        cursor.execute(
            """SELECT b.*, u.name, u.student_id, u.trust_score
               FROM books b
               JOIN users u ON b.seller_id = u.id
               WHERE b.status != 'DELETED'
                 AND (b.title LIKE ? OR b.author LIKE ? OR b.publisher LIKE ?)
               ORDER BY CASE b.status WHEN 'AVAILABLE' THEN 1 WHEN 'RESERVED' THEN 2 ELSE 3 END,
                        b.created_at DESC""",
            (f'%{query}%', f'%{query}%', f'%{query}%')
        )
    else:
        cursor.execute(
            """SELECT b.*, u.name, u.student_id, u.trust_score
               FROM books b
               JOIN users u ON b.seller_id = u.id
               WHERE b.status != 'DELETED'
               ORDER BY CASE b.status WHEN 'AVAILABLE' THEN 1 WHEN 'RESERVED' THEN 2 ELSE 3 END,
                        b.created_at DESC"""
        )

    books = cursor.fetchall()
    return render_template('index.html', books=books, query=query)

# ==========================================
# 2. 인증 (회원가입 / 로그인 / 로그아웃)
# ==========================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        student_id = request.form['student_id'].strip()
        name = request.form['name'].strip()
        password = request.form['password']

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE student_id = ?", (student_id,))
        if cursor.fetchone() is not None:
            flash('이미 등록된 학번입니다.', 'danger')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO users (student_id, name, password_hash) VALUES (?, ?, ?)",
            (student_id, name, hashed_password)
        )
        db.commit()
        flash('회원가입이 완료되었습니다. 로그인해주세요.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        student_id = request.form['student_id'].strip()
        password = request.form['password']

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE student_id = ?", (student_id,))
        user = cursor.fetchone()

        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['student_id'] = user['student_id']
            session['name'] = user['name']
            flash(f'{user["name"]}님 환영합니다.', 'success')
            return redirect(url_for('index'))

        flash('학번 또는 비밀번호가 올바르지 않습니다.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('로그아웃되었습니다.', 'info')
    return redirect(url_for('index'))

# ==========================================
# 3. 마이페이지 & 공개 프로필
# ==========================================
@app.route('/profile')
def my_profile():
    if not g.user:
        flash('로그인이 필요합니다.', 'warning')
        return redirect(url_for('login'))
    return redirect(url_for('user_profile', user_id=g.user['id'], tab=request.args.get('tab', 'purchases')))

@app.route('/profile/<int:user_id>')
def user_profile(user_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, student_id, name, trust_score, is_admin, created_at FROM users WHERE id = ?", (user_id,))
    target_user = cursor.fetchone()
    if not target_user:
        flash('존재하지 않는 사용자입니다.', 'danger')
        return redirect(url_for('index'))

    is_owner = bool(g.user and g.user['id'] == target_user['id'])
    can_manage = bool(is_owner or is_admin_user())
    active_tab = request.args.get('tab', 'purchases')

    cursor.execute(
        """SELECT * FROM books
           WHERE seller_id = ? AND status != 'DELETED'
           ORDER BY CASE status WHEN 'RESERVED' THEN 1 WHEN 'AVAILABLE' THEN 2 ELSE 3 END,
                    created_at DESC""",
        (target_user['id'],)
    )
    selling_books = cursor.fetchall()

    if can_manage:
        cursor.execute(
            """SELECT r.*, u.name AS reviewer_name, u.student_id AS reviewer_student_id, b.title AS book_title
               FROM reviews r
               JOIN users u ON r.reviewer_id = u.id
               JOIN trades t ON r.trade_id = t.id
               JOIN books b ON t.book_id = b.id
               WHERE r.reviewee_id = ?
               ORDER BY r.created_at DESC""",
            (target_user['id'],)
        )
    else:
        cursor.execute(
            """SELECT r.*, u.name AS reviewer_name, u.student_id AS reviewer_student_id, b.title AS book_title
               FROM reviews r
               JOIN users u ON r.reviewer_id = u.id
               JOIN trades t ON r.trade_id = t.id
               JOIN books b ON t.book_id = b.id
               WHERE r.reviewee_id = ? AND r.rating >= 3.0
               ORDER BY r.created_at DESC""",
            (target_user['id'],)
        )
    reviews = cursor.fetchall()

    purchase_trades = []
    sale_trades = []
    book_requests_map = {}

    if can_manage:
        cursor.execute(
            """SELECT t.*, b.title AS book_title, b.selling_price, b.status AS book_status,
                      u.name AS seller_name, u.student_id AS seller_student_id,
                      r.id AS review_id, r.rating AS my_rating
               FROM trades t
               JOIN books b ON t.book_id = b.id
               JOIN users u ON t.seller_id = u.id
               LEFT JOIN reviews r ON t.id = r.trade_id
               WHERE t.buyer_id = ?
               ORDER BY CASE t.status WHEN 'RESERVED' THEN 1 WHEN 'COMPLETED' THEN 2 ELSE 3 END,
                        t.created_at DESC""",
            (target_user['id'],)
        )
        purchase_trades = cursor.fetchall()

        cursor.execute(
            """SELECT t.*, b.title AS book_title, b.selling_price, b.status AS book_status,
                      u.name AS buyer_name, u.student_id AS buyer_student_id
               FROM trades t
               JOIN books b ON t.book_id = b.id
               JOIN users u ON t.buyer_id = u.id
               WHERE t.seller_id = ?
               ORDER BY CASE t.status WHEN 'RESERVED' THEN 1 WHEN 'COMPLETED' THEN 2 ELSE 3 END,
                        t.created_at DESC""",
            (target_user['id'],)
        )
        sale_trades = cursor.fetchall()

        for book in selling_books:
            cursor.execute(
                """SELECT c.*, u.name AS commenter_name, u.student_id AS commenter_student_id, u.trust_score
                   FROM comments c
                   JOIN users u ON c.user_id = u.id
                   WHERE c.book_id = ? AND c.parent_id IS NULL
                   ORDER BY c.is_approved DESC, c.created_at ASC""",
                (book['id'],)
            )
            book_requests_map[book['id']] = cursor.fetchall()

    return render_template(
        'profile.html',
        target_user=target_user,
        is_owner=is_owner,
        can_manage=can_manage,
        active_tab=active_tab,
        selling_books=selling_books,
        reviews=reviews,
        purchase_trades=purchase_trades,
        sale_trades=sale_trades,
        book_requests_map=book_requests_map
    )

# ==========================================
# 4. 도서 등록 / 상세 / 수정 / 상태 변경 / 삭제 / 복구
# ==========================================
@app.route('/book/register', methods=['GET', 'POST'])
def register_book():
    if not g.user:
        flash('로그인이 필요합니다.', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        title = request.form['title'].strip()
        author = request.form['author'].strip()
        publisher = request.form['publisher'].strip()
        original_price = request.form.get('original_price', type=int)
        selling_price = request.form.get('selling_price', type=int)
        condition_grade = request.form['condition_grade']
        description = request.form['description']
        ai_result = request.form.get('ai_evaluated_result', '').strip()

        api_image_url = request.form.get('api_image_url', '').strip()
        primary_key = request.form.get('primary_image_key', '').strip()

        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        saved_items = []

        if api_image_url:
            downloaded = download_cover_image(api_image_url, app.config['UPLOAD_FOLDER'])
            if downloaded:
                saved_items.append({'key': 'api', 'filename': downloaded})

        files = request.files.getlist('images')
        for idx, file in enumerate(files):
            if file and file.filename and allowed_file(file.filename):
                filename = save_optimized_image(file, app.config['UPLOAD_FOLDER'])
                saved_items.append({'key': f'file_{idx}', 'filename': filename})

        primary_filename = 'default_book.png'
        if saved_items:
            matched = next((item for item in saved_items if item['key'] == primary_key), None)
            primary_item = matched if matched else saved_items[0]
            primary_filename = primary_item['filename']

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """INSERT INTO books
               (seller_id, title, author, publisher, original_price, selling_price, condition_grade, ai_evaluated_result, description, image_filename)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (g.user['id'], title, author, publisher, original_price, selling_price, condition_grade, ai_result, description, primary_filename)
        )
        book_id = cursor.lastrowid

        for order, item in enumerate(saved_items):
            is_primary = 1 if item['filename'] == primary_filename else 0
            cursor.execute(
                """INSERT INTO book_images (book_id, image_filename, is_primary, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (book_id, item['filename'], is_primary, order)
            )

        db.commit()
        flash('도서가 성공적으로 등록되었습니다.', 'success')
        return redirect(url_for('index'))

    return render_template('register_book.html')

@app.route('/book/<int:book_id>')
def detail(book_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """SELECT b.*, u.name, u.student_id, u.trust_score
           FROM books b
           JOIN users u ON b.seller_id = u.id
           WHERE b.id = ? AND b.status != 'DELETED'""",
        (book_id,)
    )
    book = cursor.fetchone()
    if not book:
        flash('존재하지 않거나 삭제된 게시글입니다.', 'danger')
        return redirect(url_for('index'))

    cursor.execute(
        """SELECT * FROM book_images
           WHERE book_id = ?
           ORDER BY is_primary DESC, sort_order ASC, id ASC""",
        (book_id,)
    )
    book_images = [row['image_filename'] for row in cursor.fetchall()]
    if not book_images:
        book_images = [book['image_filename'] or 'default_book.png']

    cursor.execute(
        """SELECT t.*, buyer.name AS buyer_name, buyer.student_id AS buyer_student_id
           FROM trades t
           JOIN users buyer ON t.buyer_id = buyer.id
           WHERE t.book_id = ? AND t.status = 'RESERVED'
           ORDER BY t.id DESC LIMIT 1""",
        (book_id,)
    )
    active_trade = cursor.fetchone()

    cursor.execute(
        """SELECT b.*, u.name, u.student_id, u.trust_score
           FROM books b
           JOIN users u ON b.seller_id = u.id
           WHERE b.title = ? AND b.id != ? AND b.status = 'AVAILABLE'
           ORDER BY b.selling_price ASC""",
        (book['title'], book_id)
    )
    similar_books = cursor.fetchall()

    cursor.execute(
        """SELECT c.*, u.name, u.student_id, u.trust_score
           FROM comments c
           JOIN users u ON c.user_id = u.id
           WHERE c.book_id = ? AND c.parent_id IS NULL
           ORDER BY c.is_approved DESC, c.created_at ASC""",
        (book_id,)
    )
    top_comments = cursor.fetchall()

    cursor.execute(
        """SELECT c.*, u.name, u.student_id
           FROM comments c
           JOIN users u ON c.user_id = u.id
           WHERE c.book_id = ? AND c.parent_id IS NOT NULL
           ORDER BY c.created_at ASC""",
        (book_id,)
    )
    all_replies = cursor.fetchall()
    replies_map = {}
    for reply in all_replies:
        replies_map.setdefault(reply['parent_id'], []).append(reply)

    return render_template(
        'detail.html',
        book=book,
        book_images=book_images,
        active_trade=active_trade,
        similar_books=similar_books,
        comments=top_comments,
        replies_map=replies_map
    )

@app.route('/book/<int:book_id>/edit', methods=['GET', 'POST'])
def edit_post(book_id):
    if not g.user:
        flash('로그인이 필요합니다.', 'warning')
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ? AND status != 'DELETED'", (book_id,))
    book = cursor.fetchone()

    if not book or (book['seller_id'] != g.user['id'] and not is_admin_user()):
        flash('수정 권한이 없습니다.', 'danger')
        return redirect(url_for('index'))

    cursor.execute(
        """SELECT * FROM book_images
           WHERE book_id = ?
           ORDER BY is_primary DESC, sort_order ASC, id ASC""",
        (book_id,)
    )
    existing_images = cursor.fetchall()
    if not existing_images and book['image_filename'] and book['image_filename'] != 'default_book.png':
        cursor.execute(
            """INSERT INTO book_images (book_id, image_filename, is_primary, sort_order)
               VALUES (?, ?, 1, 0)""",
            (book_id, book['image_filename'])
        )
        db.commit()
        cursor.execute(
            """SELECT * FROM book_images
               WHERE book_id = ?
               ORDER BY is_primary DESC, sort_order ASC, id ASC""",
            (book_id,)
        )
        existing_images = cursor.fetchall()

    if request.method == 'POST':
        title = request.form['title'].strip()
        author = request.form['author'].strip()
        publisher = request.form['publisher'].strip()
        original_price = request.form.get('original_price', type=int)
        selling_price = request.form.get('selling_price', type=int)
        condition_grade = request.form['condition_grade']
        description = request.form['description']

        keep_filenames = request.form.getlist('keep_images')
        primary_key = request.form.get('primary_image_key', '').strip()

        cursor.execute("SELECT id, image_filename FROM book_images WHERE book_id = ?", (book_id,))
        for row in cursor.fetchall():
            if row['image_filename'] not in keep_filenames:
                cursor.execute("DELETE FROM book_images WHERE id = ?", (row['id'],))

        final_items = [{'key': f'existing:{fname}', 'filename': fname} for fname in keep_filenames]

        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        files = request.files.getlist('images')
        for idx, file in enumerate(files):
            if file and file.filename and allowed_file(file.filename):
                filename = save_optimized_image(file, app.config['UPLOAD_FOLDER'])
                final_items.append({'key': f'new_{idx}', 'filename': filename})

        primary_filename = 'default_book.png'
        if final_items:
            matched = next((item for item in final_items if item['key'] == primary_key), None)
            primary_item = matched if matched else final_items[0]
            primary_filename = primary_item['filename']

        cursor.execute("DELETE FROM book_images WHERE book_id = ?", (book_id,))
        for order, item in enumerate(final_items):
            is_primary = 1 if item['filename'] == primary_filename else 0
            cursor.execute(
                """INSERT INTO book_images (book_id, image_filename, is_primary, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (book_id, item['filename'], is_primary, order)
            )

        cursor.execute(
            """UPDATE books
               SET title = ?, author = ?, publisher = ?, original_price = ?,
                   selling_price = ?, condition_grade = ?, description = ?, image_filename = ?
               WHERE id = ?""",
            (title, author, publisher, original_price, selling_price, condition_grade, description, primary_filename, book_id)
        )
        db.commit()
        flash('게시글이 수정되었습니다.', 'success')
        return redirect(url_for('detail', book_id=book_id))

    return render_template('edit_post.html', book=book, existing_images=existing_images)

@app.route('/api/book/<int:book_id>/status', methods=['POST'])
def change_book_status(book_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    data = request.get_json() or {}
    new_status = data.get('status', '').strip()
    if new_status not in ('AVAILABLE', 'RESERVED', 'SOLD'):
        return jsonify({'success': False, 'message': '유효하지 않은 상태 값입니다.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ? AND status != 'DELETED'", (book_id,))
    book = cursor.fetchone()

    if not book or (book['seller_id'] != g.user['id'] and not is_admin_user()):
        return jsonify({'success': False, 'message': '상태 변경 권한이 없습니다.'}), 403

    if new_status == 'AVAILABLE':
        cursor.execute(
            """UPDATE trades
               SET status = 'CANCELLED', cancel_reason = '상태 변경으로 인한 예약 해제', cancelled_by = 'ADMIN'
               WHERE book_id = ? AND status = 'RESERVED'""",
            (book_id,)
        )
        cursor.execute("SELECT id FROM comments WHERE book_id = ? AND is_approved = 1", (book_id,))
        approved_comments = cursor.fetchall()
        for row in approved_comments:
            cursor.execute("DELETE FROM comments WHERE parent_id = ?", (row['id'],))
        cursor.execute("UPDATE comments SET is_approved = 0 WHERE book_id = ?", (book_id,))
    elif new_status == 'SOLD':
        cursor.execute(
            "UPDATE trades SET status = 'COMPLETED' WHERE book_id = ? AND status = 'RESERVED'",
            (book_id,)
        )

    cursor.execute("UPDATE books SET status = ? WHERE id = ?", (new_status, book_id))
    db.commit()
    return jsonify({'success': True, 'message': '도서 상태가 변경되었습니다.'})

@app.route('/api/book/<int:book_id>/delete', methods=['POST'])
def delete_post(book_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ? AND status != 'DELETED'", (book_id,))
    book = cursor.fetchone()

    if not book or (book['seller_id'] != g.user['id'] and not is_admin_user()):
        return jsonify({'success': False, 'message': '삭제 권한이 없습니다.'}), 403

    if book['status'] == 'RESERVED' and not is_admin_user():
        return jsonify({
            'success': False,
            'message': '예약 진행 중인 게시글은 삭제할 수 없습니다. 먼저 예약을 취소하거나 완료해주세요.'
        }), 400

    if book['status'] == 'RESERVED' and is_admin_user():
        cursor.execute(
            """UPDATE trades
               SET status = 'CANCELLED', cancel_reason = '게시글 삭제로 인한 예약 취소', cancelled_by = 'ADMIN'
               WHERE book_id = ? AND status = 'RESERVED'""",
            (book_id,)
        )

    cursor.execute("UPDATE books SET status = 'DELETED' WHERE id = ?", (book_id,))
    db.commit()
    return jsonify({'success': True, 'message': '게시글이 삭제되었습니다.'})

@app.route('/api/book/<int:book_id>/restore', methods=['POST'])
def restore_post(book_id):
    if not is_admin_user():
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE books SET status = 'AVAILABLE' WHERE id = ?", (book_id,))
    db.commit()
    return jsonify({'success': True, 'message': '삭제된 게시글이 판매중 상태로 복구되었습니다.'})

# ==========================================
# 5. 댓글(거래 요청) / 답글(거래 승인) / 댓글 삭제 AJAX API
# ==========================================
@app.route('/api/book/<int:book_id>/comment', methods=['POST'])
def add_comment(book_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    data = request.get_json() or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'message': '댓글 내용을 입력해주세요.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ? AND status != 'DELETED'", (book_id,))
    book = cursor.fetchone()

    if not book:
        return jsonify({'success': False, 'message': '존재하지 않는 도서입니다.'}), 404

    if book['status'] in ('RESERVED', 'SOLD') and g.user['id'] != book['seller_id'] and not is_admin_user():
        return jsonify({'success': False, 'message': '이미 거래 중인 도서입니다.'}), 400

    cursor.execute(
        "INSERT INTO comments (book_id, user_id, parent_id, content) VALUES (?, ?, NULL, ?)",
        (book_id, g.user['id'], content)
    )
    db.commit()
    return jsonify({'success': True})

@app.route('/api/book/<int:book_id>/comment/<int:comment_id>/reply', methods=['POST'])
def reply_and_approve(book_id, comment_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    data = request.get_json() or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'message': '답글 내용을 입력해주세요.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ? AND status != 'DELETED'", (book_id,))
    book = cursor.fetchone()

    if not book or (book['seller_id'] != g.user['id'] and not is_admin_user()):
        return jsonify({'success': False, 'message': '거래 승인 답글 권한이 없습니다.'}), 403

    cursor.execute("SELECT * FROM comments WHERE id = ? AND book_id = ? AND parent_id IS NULL", (comment_id, book_id))
    target_comment = cursor.fetchone()
    if not target_comment:
        return jsonify({'success': False, 'message': '대상 댓글을 찾을 수 없습니다.'}), 404

    if not is_admin_user() and (target_comment['user_id'] == book['seller_id'] or target_comment['user_id'] == g.user['id']):
        return jsonify({
            'success': False,
            'message': '자신의 댓글에는 거래 승인 답글을 달 수 없습니다.'
        }), 400

    if book['status'] == 'RESERVED':
        cursor.execute(
            "SELECT id FROM trades WHERE book_id = ? AND status = 'RESERVED' ORDER BY id DESC LIMIT 1",
            (book_id,)
        )
        active_trade = cursor.fetchone()
        trade_id = active_trade['id'] if active_trade else ''
        return jsonify({
            'success': False,
            'code': 'ALREADY_RESERVED',
            'message': '이미 예약 진행 중인 구매자가 있습니다.',
            'redirect_url': url_for('user_profile', user_id=book['seller_id'], tab='sales') + (f'#trade-{trade_id}' if trade_id else '')
        }), 400

    if book['status'] == 'SOLD':
        return jsonify({'success': False, 'message': '이미 거래 완료된 도서입니다.'}), 400

    cursor.execute(
        "INSERT INTO comments (book_id, user_id, parent_id, content) VALUES (?, ?, ?, ?)",
        (book_id, g.user['id'], comment_id, content)
    )
    cursor.execute("UPDATE comments SET is_approved = 1 WHERE id = ?", (comment_id,))
    cursor.execute(
        """INSERT INTO trades (book_id, buyer_id, seller_id, comment_id, status)
           VALUES (?, ?, ?, ?, 'RESERVED')""",
        (book_id, target_comment['user_id'], book['seller_id'], comment_id)
    )
    cursor.execute("UPDATE books SET status = 'RESERVED' WHERE id = ?", (book_id,))
    db.commit()

    return jsonify({'success': True, 'message': '답글이 등록되었으며 거래가 예약되었습니다.'})

@app.route('/api/comment/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM comments WHERE id = ?", (comment_id,))
    comment = cursor.fetchone()

    if not comment:
        return jsonify({'success': False, 'message': '존재하지 않는 댓글입니다.'}), 404

    if comment['user_id'] != g.user['id'] and not is_admin_user():
        return jsonify({'success': False, 'message': '댓글 삭제 권한이 없습니다.'}), 403

    if comment['parent_id'] is None:
        cursor.execute(
            "SELECT * FROM trades WHERE comment_id = ? AND status = 'RESERVED'",
            (comment_id,)
        )
        active_trade = cursor.fetchone()

        if active_trade:
            if not is_admin_user():
                return jsonify({
                    'success': False,
                    'message': '예약 진행 중인 댓글은 먼저 예약을 취소한 후 삭제할 수 있습니다.'
                }), 400

            cursor.execute(
                """UPDATE trades
                   SET status = 'CANCELLED', cancel_reason = '댓글 삭제로 인한 예약 취소', cancelled_by = 'ADMIN'
                   WHERE id = ?""",
                (active_trade['id'],)
            )
            cursor.execute("UPDATE books SET status = 'AVAILABLE' WHERE id = ?", (comment['book_id'],))

        cursor.execute("UPDATE trades SET comment_id = NULL WHERE comment_id = ?", (comment_id,))
        cursor.execute("DELETE FROM comments WHERE parent_id = ?", (comment_id,))
        cursor.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    else:
        cursor.execute("DELETE FROM comments WHERE id = ?", (comment_id,))

    db.commit()
    return jsonify({'success': True, 'message': '댓글이 삭제되었습니다.'})

# ==========================================
# 6. 거래 취소 / 완료 / 5단계 만족도 평가 AJAX API
# ==========================================
@app.route('/api/trade/<int:trade_id>/cancel', methods=['POST'])
def cancel_trade(trade_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    data = request.get_json() or {}
    cancel_reason = data.get('cancel_reason', '').strip()
    if not cancel_reason:
        return jsonify({'success': False, 'message': '취소 사유를 입력해주세요.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    trade = cursor.fetchone()

    if not trade:
        return jsonify({'success': False, 'message': '존재하지 않는 거래입니다.'}), 404

    if g.user['id'] not in (trade['buyer_id'], trade['seller_id']) and not is_admin_user():
        return jsonify({'success': False, 'message': '예약 취소 권한이 없습니다.'}), 403

    if trade['status'] != 'RESERVED':
        return jsonify({'success': False, 'message': '예약 중인 거래만 취소할 수 있습니다.'}), 400

    if g.user['id'] == trade['seller_id']:
        cancelled_by = 'SELLER'
    elif g.user['id'] == trade['buyer_id']:
        cancelled_by = 'BUYER'
    else:
        cancelled_by = 'ADMIN'

    cursor.execute(
        """UPDATE trades
           SET status = 'CANCELLED', cancel_reason = ?, cancelled_by = ?
           WHERE id = ?""",
        (cancel_reason, cancelled_by, trade_id)
    )
    cursor.execute("UPDATE books SET status = 'AVAILABLE' WHERE id = ?", (trade['book_id'],))

    if trade['comment_id']:
        cursor.execute("UPDATE comments SET is_approved = 0 WHERE id = ?", (trade['comment_id'],))
        cursor.execute("DELETE FROM comments WHERE parent_id = ?", (trade['comment_id'],))

    db.commit()
    return jsonify({'success': True, 'message': '예약이 취소되었으며 도서가 다시 판매중 상태로 변경되었습니다.'})

@app.route('/api/trade/<int:trade_id>/complete', methods=['POST'])
def complete_trade_by_seller(trade_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    trade = cursor.fetchone()

    if not trade or (trade['seller_id'] != g.user['id'] and not is_admin_user()):
        return jsonify({'success': False, 'message': '거래 완료 처리 권한이 없습니다.'}), 403

    if trade['status'] != 'RESERVED':
        return jsonify({'success': False, 'message': '예약 중인 거래만 완료 처리할 수 있습니다.'}), 400

    cursor.execute("UPDATE trades SET status = 'COMPLETED' WHERE id = ?", (trade_id,))
    cursor.execute("UPDATE books SET status = 'SOLD' WHERE id = ?", (trade['book_id'],))
    db.commit()
    return jsonify({'success': True, 'message': '거래가 완료 처리되었습니다.'})

@app.route('/api/trade/<int:trade_id>/review', methods=['POST'])
def submit_review(trade_id):
    if not g.user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    data = request.get_json() or {}
    try:
        rating = float(data.get('rating', 4.0))
    except (TypeError, ValueError):
        rating = 4.0
    comment = data.get('comment', '').strip()

    if not comment:
        return jsonify({'success': False, 'message': '거래 후기를 입력해주세요.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    trade = cursor.fetchone()

    if not trade or (trade['buyer_id'] != g.user['id'] and not is_admin_user()):
        return jsonify({'success': False, 'message': '평가 작성 권한이 없습니다.'}), 403

    if trade['status'] == 'CANCELLED':
        return jsonify({'success': False, 'message': '취소된 거래는 평가할 수 없습니다.'}), 400

    cursor.execute("SELECT id FROM reviews WHERE trade_id = ?", (trade_id,))
    if cursor.fetchone():
        return jsonify({'success': False, 'message': '이미 평가를 완료한 거래입니다.'}), 400

    delta_map = {5.0: 2.0, 4.0: 1.0, 3.0: 0.0, 2.0: -2.0, 1.0: -5.0}
    delta = delta_map.get(rating, 0.0)

    cursor.execute(
        """INSERT INTO reviews (trade_id, reviewer_id, reviewee_id, rating, comment)
           VALUES (?, ?, ?, ?, ?)""",
        (trade_id, g.user['id'], trade['seller_id'], rating, comment)
    )
    cursor.execute(
        """UPDATE users
           SET trust_score = ROUND(MIN(100.0, MAX(0.0, trust_score + ?)), 1)
           WHERE id = ?""",
        (delta, trade['seller_id'])
    )
    cursor.execute("UPDATE trades SET status = 'COMPLETED' WHERE id = ?", (trade_id,))
    cursor.execute("UPDATE books SET status = 'SOLD' WHERE id = ?", (trade['book_id'],))
    db.commit()

    return jsonify({'success': True, 'message': '거래 평가가 완료되었으며 판매자 신뢰 지수에 반영되었습니다.'})

# ==========================================
# 7. 통합 관리 대시보드 (/admin)
# ==========================================
@app.route('/admin')
def admin_dashboard():
    if not is_admin_user():
        flash('접근 권한이 없습니다.', 'danger')
        return redirect(url_for('index'))

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM users ORDER BY id ASC")
    all_users = cursor.fetchall()

    cursor.execute(
        """SELECT b.*, u.name AS seller_name, u.student_id AS seller_student_id
           FROM books b
           JOIN users u ON b.seller_id = u.id
           ORDER BY b.id DESC"""
    )
    all_books = cursor.fetchall()

    cursor.execute(
        """SELECT t.*, b.title AS book_title,
                  buyer.name AS buyer_name, buyer.student_id AS buyer_student_id,
                  seller.name AS seller_name, seller.student_id AS seller_student_id,
                  r.id AS review_id
           FROM trades t
           JOIN books b ON t.book_id = b.id
           JOIN users buyer ON t.buyer_id = buyer.id
           JOIN users seller ON t.seller_id = seller.id
           LEFT JOIN reviews r ON t.id = r.trade_id
           ORDER BY t.id DESC"""
    )
    all_trades = cursor.fetchall()

    cursor.execute(
        """SELECT c.*, u.name AS author_name, u.student_id AS author_student_id, b.title AS book_title
           FROM comments c
           JOIN users u ON c.user_id = u.id
           JOIN books b ON c.book_id = b.id
           ORDER BY c.id DESC"""
    )
    all_comments = cursor.fetchall()

    return render_template(
        'admin.html',
        users=all_users,
        books=all_books,
        trades=all_trades,
        comments=all_comments
    )

# ==========================================
# 8. 도서 & 표지 검색 API (국립중앙도서관 + 해외 서버용 Google Books 자동 폴백)
# ==========================================
@app.route('/api/nl-search', methods=['GET'])
def nl_book_search():
    query = request.args.get('q', '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    page_size = 5

    if not query:
        return jsonify({'success': False, 'message': '검색할 도서명을 입력해주세요.'}), 400

    results = []
    total_count = 0

    if NL_API_KEY and NL_API_KEY != 'your_national_library_api_key_here':
        try:
            seoji_url = 'https://www.nl.go.kr/seoji/SearchApi.do'
            seoji_params = {
                'cert_key': NL_API_KEY,
                'result_style': 'json',
                'page_no': page,
                'page_size': page_size,
                'title': query
            }
            resp = requests.get(seoji_url, params=seoji_params, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                raw_total = re.sub(r'[^0-9]', '', str(data.get('TOTAL_COUNT', '')))
                total_count = int(raw_total) if raw_total else 0

                for doc in data.get('docs', []):
                    raw_price = re.sub(r'[^0-9]', '', str(doc.get('PRE_PRICE', '')))
                    img_url = str(doc.get('TITLE_URL', '')).strip()
                    if img_url.startswith('http://cover.nl.go.kr'):
                        img_url = img_url.replace('http://', 'https://', 1)

                    results.append({
                        'title': strip_html(doc.get('TITLE', '')),
                        'author': strip_html(doc.get('AUTHOR', '')),
                        'publisher': strip_html(doc.get('PUBLISHER', '')),
                        'original_price': int(raw_price) if raw_price else '',
                        'image_url': img_url
                    })
        except requests.exceptions.RequestException as e:
            app.logger.warning("NL Seoji API 요청 실패(폴백 전환): %s", mask_sensitive_info(e))
        except Exception as e:
            app.logger.warning("NL Seoji API 응답 파싱 실패: %s", mask_sensitive_info(e))

    if not results:
        try:
            start_index = (page - 1) * page_size
            gb_url = 'https://www.googleapis.com/books/v1/volumes'
            gb_params = {
                'q': query,
                'startIndex': start_index,
                'maxResults': page_size,
                'printType': 'books'
            }
            resp = requests.get(gb_url, params=gb_params, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                total_count = int(data.get('totalItems', 0))

                for item in data.get('items', []):
                    info = item.get('volumeInfo', {})
                    sale = item.get('saleInfo', {})
                    authors = ', '.join(info.get('authors', []))
                    image_links = info.get('imageLinks', {})
                    img_url = (
                        image_links.get('thumbnail')
                        or image_links.get('smallThumbnail')
                        or ''
                    )
                    if img_url.startswith('http://'):
                        img_url = img_url.replace('http://', 'https://', 1)

                    list_price = sale.get('listPrice', {}).get('amount')
                    original_price = int(list_price) if isinstance(list_price, (int, float)) else ''

                    results.append({
                        'title': strip_html(info.get('title', '')),
                        'author': strip_html(authors),
                        'publisher': strip_html(info.get('publisher', '')),
                        'original_price': original_price,
                        'image_url': img_url
                    })
        except requests.exceptions.Timeout as e:
            app.logger.warning("Book Search API Timeout: %s", mask_sensitive_info(e))
            return jsonify({
                'success': False,
                'message': '도서 검색 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도하거나 도서 정보를 직접 입력해주세요.'
            }), 504
        except requests.exceptions.RequestException as e:
            app.logger.error("Book Search API Request Error: %s", mask_sensitive_info(e))
            return jsonify({
                'success': False,
                'message': '도서 정보를 불러오는 중 일시적인 네트워크 오류가 발생했습니다. 도서 정보를 직접 입력해주세요.'
            }), 502
        except Exception as e:
            app.logger.error("Book Search API Unexpected Error: %s", mask_sensitive_info(e))
            return jsonify({
                'success': False,
                'message': '도서 검색 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'
            }), 500

    if total_count == 0 and results:
        total_count = len(results)

    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0

    return jsonify({
        'success': True,
        'books': results,
        'page': page,
        'page_size': page_size,
        'total_count': total_count,
        'total_pages': total_pages
    })

@app.route('/api/nl-cover-proxy')
def nl_cover_proxy():
    img_url = request.args.get('url', '').strip()
    allowed_prefixes = (
        'http://cover.nl.go.kr/',
        'https://cover.nl.go.kr/',
        'http://books.google.com/',
        'https://books.google.com/',
        'https://books.googleusercontent.com/',
    )
    if not img_url.startswith(allowed_prefixes):
        return '', 400
    try:
        resp = requests.get(img_url, timeout=5)
        if resp.status_code == 200 and resp.content:
            return Response(resp.content, mimetype=resp.headers.get('Content-Type', 'image/jpeg'))
    except Exception:
        pass
    return '', 404

# ==========================================
# 9-1. 판매자용: 게시글 등록 시 Gemini AI 상태 자동 평가 API
# ==========================================
@app.route('/api/ai-eval', methods=['POST'])
def ai_eval():
    if not gemini_client:
        return jsonify({'error': 'AI 상태 평가 기능이 현재 비활성화되어 있습니다. 상태 설명을 직접 작성해주세요.'}), 500

    if request.content_type and 'multipart/form-data' in request.content_type:
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        uploaded_files = request.files.getlist('images')
    else:
        data = request.get_json() or {}
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        uploaded_files = []

    if not description and not uploaded_files:
        return jsonify({'error': 'AI 상태 평가를 위해 도서 설명을 입력하거나 상태 사진을 첨부해주세요.'}), 400

    try:
        prompt = (
            "당신은 대학 전공·교양 중고 서적의 물리적 상태를 판정하는 AI 검수원입니다.\n"
            "아래 도서명, 판매자의 상태 설명, 첨부된 실물 사진을 분석하여:\n"
            "1) 상태 등급을 '최상', '상', '중', '하' 중 하나로 판정하고,\n"
            "2) 구매자가 참고할 수 있도록 필기·밑줄·훼손·변색 여부를 포함한 객관적 상태 검수 요약을 한 문장(60자 이내)으로 작성하세요.\n"
            "주의: 출력은 일반 텍스트 필드(Plain Text)에 그대로 노출되므로 별표(**), 글머리 기호(*, -), 제목 기호(#) 등 마크다운 문법을 절대 사용하지 마세요.\n"
            "출력 형식: [추천등급: 상] 필기 흔적이 적고 표지 및 내지가 전반적으로 깔끔한 상태입니다.\n\n"
            f"도서명: {title or '미입력'}\n"
            f"판매자 상태 설명: {description or '설명 없음'}"
        )

        contents = [prompt]
        for file in uploaded_files[:3]:
            if file and file.filename and allowed_file(file.filename):
                contents.append(prepare_ai_image(file))

        result_text = generate_gemini_response(contents)

        detected_grade = ''
        for grade in ('최상', '상', '중', '하'):
            if f'추천등급: {grade}' in result_text or f'[{grade}]' in result_text or grade in result_text[:20]:
                detected_grade = grade
                break

        return jsonify({
            'result': result_text,
            'suggested_grade': detected_grade
        })
    except Exception as e:
        app.logger.error("Gemini AI Eval Error: %s", mask_sensitive_info(e))
        return jsonify({'error': 'AI 상태 평가 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'}), 500

# ==========================================
# 9-2. 구매자용: 상세 페이지 동일 도서 상품 비교 분석 API
# ==========================================
@app.route('/api/book/<int:book_id>/ai-compare', methods=['POST'])
def ai_compare_book(book_id):
    if not gemini_client:
        return jsonify({'error': 'AI 비교 분석 기능이 현재 비활성화되어 있습니다.'}), 500

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """SELECT b.*, u.name AS seller_name, u.student_id AS seller_student_id, u.trust_score
           FROM books b
           JOIN users u ON b.seller_id = u.id
           WHERE b.id = ? AND b.status != 'DELETED'""",
        (book_id,)
    )
    target = cursor.fetchone()
    if not target:
        return jsonify({'error': '존재하지 않는 도서입니다.'}), 404

    cursor.execute(
        """SELECT b.id, b.selling_price, b.original_price, b.condition_grade,
                  b.ai_evaluated_result, b.description,
                  u.name AS seller_name, u.student_id AS seller_student_id, u.trust_score
           FROM books b
           JOIN users u ON b.seller_id = u.id
           WHERE b.title = ? AND b.id != ? AND b.status = 'AVAILABLE'
           ORDER BY b.selling_price ASC""",
        (target['title'], book_id)
    )
    others = cursor.fetchall()

    if not others:
        return jsonify({'error': '비교할 동일 도서의 다른 판매중 상품이 없습니다.'}), 400

    target_ai = target['ai_evaluated_result'] or '미등록'
    original_price_text = f"{target['original_price']:,}원" if target['original_price'] else '미입력'

    others_lines = []
    for o in others:
        o_ai = o['ai_evaluated_result'] or '미등록'
        others_lines.append(
            f"- 상품 #{o['id']} ({o['seller_name']}): 판매가 {o['selling_price']:,}원 | "
            f"상태등급 [{o['condition_grade']}] | 판매자 신뢰지수 {o['trust_score']}점 | "
            f"AI 상태 검수: {o_ai} | 설명: {o['description'][:60]}"
        )

    try:
        prompt = (
            "당신은 대학생 중고 전공서적 구매자를 돕는 AI 쇼핑 분석가입니다.\n"
            "아래 [현재 보고 있는 상품]과 [동일 도서의 다른 판매중 상품 목록]의 판매가, 상태 등급, AI 상태 검수 결과, 판매자 신뢰 지수(100점 만점, 기본 점수 50점)를 비교 분석하여\n"
            "구매자에게 어떤 상품이 가장 합리적이고 최선의 선택인지 추천 근거와 함께 3~4문장으로 명확히 조언해주세요.\n"
            "주의: 출력은 일반 텍스트 필드(Plain Text)에 그대로 노출되므로 별표(**), 글머리 기호(*, -), 제목 기호(#) 등 마크다운 문법을 절대 사용하지 마세요.\n"
            "각 상품을 언급할 때는 '현재 상품' 또는 '상품 #번호(판매자명)'으로 구분해주세요.\n\n"
            f"[현재 보고 있는 상품 (상품 #{target['id']}, {target['seller_name']})]\n"
            f"- 도서명: {target['title']}\n"
            f"- 정가: {original_price_text} / 판매가: {target['selling_price']:,}원\n"
            f"- 상태등급: [{target['condition_grade']}] / 판매자 신뢰지수: {target['trust_score']}점\n"
            f"- AI 상태 검수 결과: {target_ai}\n"
            f"- 판매자 설명: {target['description']}\n\n"
            f"[동일 도서의 다른 판매중 상품 목록]\n" + "\n".join(others_lines)
        )

        result_text = generate_gemini_response([prompt])
        return jsonify({'result': result_text})
    except Exception as e:
        app.logger.error("Gemini AI Compare Error: %s", mask_sensitive_info(e))
        return jsonify({'error': 'AI 상품 비교 분석 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'}), 500

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_mode)