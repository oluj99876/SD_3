DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS trades;
DROP TABLE IF EXISTS comments;
DROP TABLE IF EXISTS books;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(50) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    trust_score REAL DEFAULT 50.0,
    is_admin INTEGER DEFAULT 0,              -- 추가된 관리자 권한 컬럼 (0: 일반, 1: 관리자)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL,
    title VARCHAR(150) NOT NULL,
    author VARCHAR(100) NOT NULL,
    publisher VARCHAR(100),
    original_price INTEGER,
    selling_price INTEGER NOT NULL,
    condition_grade VARCHAR(20) NOT NULL,
    ai_evaluated_result TEXT,
    description TEXT,
    image_filename VARCHAR(255) DEFAULT 'default_book.png',
    status VARCHAR(20) DEFAULT 'AVAILABLE', -- AVAILABLE, RESERVED, SOLD, DELETED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seller_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    parent_id INTEGER DEFAULT NULL,
    content TEXT NOT NULL,
    is_approved INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES comments (id) ON DELETE CASCADE
);

CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    buyer_id INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    comment_id INTEGER,
    status VARCHAR(20) DEFAULT 'RESERVED',   -- RESERVED, COMPLETED, CANCELLED
    cancel_reason TEXT,
    cancelled_by VARCHAR(20),                -- BUYER, SELLER, ADMIN
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE,
    FOREIGN KEY (buyer_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (seller_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (comment_id) REFERENCES comments (id) ON DELETE SET NULL
);

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER UNIQUE NOT NULL,
    reviewer_id INTEGER NOT NULL,
    reviewee_id INTEGER NOT NULL,
    rating REAL NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trade_id) REFERENCES trades (id) ON DELETE CASCADE,
    FOREIGN KEY (reviewer_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (reviewee_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX idx_books_status ON books(status);
CREATE INDEX idx_books_seller ON books(seller_id);
CREATE INDEX idx_books_title ON books(title);
CREATE INDEX idx_comments_book ON comments(book_id);
CREATE INDEX idx_comments_parent ON comments(parent_id);
CREATE INDEX idx_trades_buyer ON trades(buyer_id);
CREATE INDEX idx_trades_seller ON trades(seller_id);
CREATE INDEX idx_trades_book ON trades(book_id);
CREATE INDEX idx_reviews_reviewee ON reviews(reviewee_id);
CREATE INDEX idx_reviews_reviewer ON reviews(reviewer_id);