import os
import sqlite3
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'Database', 'database.db')


def insert_seed_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        conn.close()
        return

    users = [
        ('admin', '관리자', '소프트웨어학과', '010-0000-0000', generate_password_hash('admin1234'), 99.0, 1),
        ('20240001', '김민수', '소프트웨어학과', '010-1234-5678', generate_password_hash('1234'), 82.5, 0),
        ('20240002', '이서연', '컴퓨터공학과', '010-2345-6789', generate_password_hash('1234'), 68.0, 0),
        ('20240003', '박지훈', '인공지능학과', '010-3456-7890', generate_password_hash('1234'), 50.0, 0),
        ('20240004', '최유진', '정보보안학과', '010-4567-8901', generate_password_hash('1234'), 74.0, 0),
    ]
    cursor.executemany(
        """INSERT INTO users (student_id, name, department, phone, password_hash, trust_score, is_admin)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        users
    )

    books = [
        (
            2, '컴퓨터 구조론 5판', '김종현', '생능출판사', 34000, 18000, '최상',
            '[추천등급: 최상] 필기 흔적이 전혀 없고 표지 모서리와 내지 상태가 새 책과 다름없습니다.',
            '한 학기 동안 아이패드로만 필기해서 책은 거의 펴보지 않았습니다. 구김이나 밑줄 전혀 없습니다. 도서관 앞 직거래 가능합니다.',
            'default_book.png', 'AVAILABLE'
        ),
        (
            3, '컴퓨터 구조론 5판', '김종현', '생능출판사', 34000, 14000, '상',
            '[추천등급: 상] 앞부분 일부 단원에 연필 밑줄이 있으나 전반적으로 깔끔한 상태입니다.',
            '1~3단원 연습문제 풀이에 연필 체크 조금 있고 나머지 페이지는 깨끗합니다. 비대면 사물함 거래도 가능합니다.',
            'default_book.png', 'AVAILABLE'
        ),
        (
            4, '컴퓨터 구조론 5판', '김종현', '생능출판사', 34000, 10000, '중',
            '[추천등급: 중] 형광펜 필기가 다수 포함되어 있으나 페이지 훼손 없이 학습에 무리 없는 상태입니다.',
            '시험 대비용 필기와 형광펜 밑줄이 꽤 있습니다. 저렴하게 내용만 보실 분께 추천합니다.',
            'default_book.png', 'AVAILABLE'
        ),
        (
            2, '명품 운영체제', '황기태', '생능출판사', 33000, 17000, '상',
            '[추천등급: 상] 겉표지 생활 기스 외에 내지 필기와 오염이 적어 양호한 상태입니다.',
            '운영체제 전공 수업에서 사용했습니다. 챕터 요약 부분에만 샤프로 살짝 표시되어 있습니다.',
            'default_book.png', 'AVAILABLE'
        ),
        (
            5, '데이터베이스 개론 3판', '김연희', '한빛아카데미', 29000, 15000, '최상',
            '[추천등급: 최상] 사용감이 거의 느껴지지 않으며 제본 상태와 내지가 매우 깨끗합니다.',
            'SQL 실습 위주 수업이라 교재는 보관만 했습니다. 상태 매우 좋습니다.',
            'default_book.png', 'SOLD'
        ),
    ]
    cursor.executemany(
        """INSERT INTO books
           (seller_id, title, author, publisher, original_price, selling_price,
            condition_grade, ai_evaluated_result, description, image_filename, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        books
    )

    for book_id in range(1, 6):
        cursor.execute(
            """INSERT INTO book_images (book_id, image_filename, is_primary, sort_order)
               VALUES (?, 'default_book.png', 1, 0)""",
            (book_id,)
        )

    cursor.execute(
        """INSERT INTO comments (book_id, user_id, parent_id, content, is_approved)
           VALUES (5, 2, NULL, '구매 희망합니다! 오늘 오후 4시 학생회관 앞에서 거래 가능할까요?', 1)"""
    )
    cursor.execute(
        """INSERT INTO comments (book_id, user_id, parent_id, content, is_approved)
           VALUES (5, 5, 1, '네 좋습니다! 4시에 학생회관 1층에서 뵙겠습니다.', 0)"""
    )

    cursor.execute(
        """INSERT INTO trades (book_id, buyer_id, seller_id, comment_id, status)
           VALUES (5, 2, 5, 1, 'COMPLETED')"""
    )

    cursor.execute(
        """INSERT INTO reviews (trade_id, reviewer_id, reviewee_id, rating, comment)
           VALUES (1, 2, 5, 5.0, '책 상태가 설명 그대로 새 책처럼 깨끗했고 약속 시간도 정확히 지켜주셨습니다!')"""
    )

    conn.commit()
    conn.close()


if __name__ == '__main__':
    insert_seed_data()