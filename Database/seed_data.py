import os
import re
import time
import sqlite3
from dotenv import load_dotenv
from PIL import Image, ImageOps
from werkzeug.security import generate_password_hash
from google import genai
from google.genai import types

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(BASE_DIR, 'database.db')
UPLOAD_DIR = os.path.join(PROJECT_ROOT, 'static', 'uploads')

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
gemini_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != 'your_google_gemini_api_key_here':
    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=15000)
    )

GEMINI_MODELS = [
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
]


def mask_sensitive_info(text):
    if not text:
        return ''
    return re.sub(
        r'((?:cert_key|api_key|key)=)([^&\s\'"\)]+)',
        r'\1***REDACTED***',
        str(text),
        flags=re.IGNORECASE
    )


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


def optimize_seed_image(filename):
    if not filename or filename == 'default_book.png':
        return filename

    stem, _ = os.path.splitext(filename)
    webp_name = f"{stem}.webp"
    webp_path = os.path.join(UPLOAD_DIR, webp_name)
    src_path = os.path.join(UPLOAD_DIR, filename)

    if os.path.exists(webp_path):
        return webp_name

    if os.path.exists(src_path):
        try:
            img = Image.open(src_path)
            img = normalize_to_rgb(img)
            img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
            img.save(webp_path, format='WEBP', quality=82, method=4)
            return webp_name
        except Exception:
            return filename

    return filename


def evaluate_seed_book(title, description, image_filename, fallback_text):
    if not gemini_client:
        return fallback_text

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
    img_path = os.path.join(UPLOAD_DIR, image_filename)
    if image_filename and image_filename != 'default_book.png' and os.path.exists(img_path):
        try:
            img = Image.open(img_path)
            img = normalize_to_rgb(img)
            img.thumbnail((800, 800), Image.Resampling.LANCZOS)
            contents.append(img)
        except Exception:
            pass

    for model_name in GEMINI_MODELS:
        for _ in range(2):
            try:
                print(f"   -> [{title}] {model_name} 호출 중...", flush=True)
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                result_text = response.text.strip()
                time.sleep(1.2)
                return result_text
            except Exception as e:
                print(f"   -> 오류 발생 ({model_name}): {mask_sensitive_info(e)}", flush=True)
                err_str = str(e)
                if '503' in err_str or 'UNAVAILABLE' in err_str or '429' in err_str:
                    time.sleep(1.5)
                    continue
                break

    return fallback_text


def insert_seed_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    users = [
        ('20211101', '김철수', generate_password_hash('1234'), 82.5, 0),
        ('20221102', '이영희', generate_password_hash('1234'), 50.0, 0),
        ('20231103', '박민수', generate_password_hash('1234'), 94.0, 0),
        ('20201104', '정지원', generate_password_hash('1234'), 68.0, 0),
        ('admin', '관리자', generate_password_hash('1234'), 100.0, 1)
    ]
    cursor.executemany(
        "INSERT INTO users (student_id, name, password_hash, trust_score, is_admin) VALUES (?, ?, ?, ?, ?)",
        users
    )

    raw_books = [
        (
            1, '컴퓨터 구조론 5판', '김종현', '생능출판', 30000, 15000, '상',
            '[추천등급: 상] 필기 흔적이 일부 있으나 전반적으로 깔끔한 상태입니다.',
            '컴퓨터구조 전공 수업 때 사용했습니다. 3장 일부에 연필 필기 있고 나머지는 깨끗합니다.',
            '1.jpg', 'AVAILABLE'
        ),
        (
            2, '컴퓨터 구조론 5판', '김종현', '생능출판', 30000, 11000, '중',
            '[추천등급: 중] 형광펜 및 밑줄 필기가 다수 있으나 학습에는 지장이 없는 상태입니다.',
            '시험 공부하면서 밑줄을 많이 쳤습니다. 공부용으로 막 보실 분 추천합니다.',
            '1.jpg', 'AVAILABLE'
        ),
        (
            3, '컴퓨터 구조론 5판', '김종현', '생능출판', 30000, 22000, '최상',
            '[추천등급: 최상] 사용 흔적이 거의 없는 새 책에 가까운 최상급 상태입니다.',
            '복수전공 철회로 교재 구매 후 펼쳐보지도 않은 완전 새 책입니다.',
            '1.jpg', 'AVAILABLE'
        ),
        (
            1, '파이썬 머신러닝 완벽 가이드 (개정2판)', '권철민', '위키북스', 38000, 24000, '최상',
            '[추천등급: 최상] 구김이나 필기 없이 표지와 내지 모두 매우 깨끗한 상태입니다.',
            '데이터분석 프로젝트 참고용으로 구입했던 책입니다. 상태 아주 좋습니다.',
            '2.jpg', 'RESERVED'
        ),
        (
            1, 'Operating System Concepts (공룡책 10판)', 'Abraham Silberschatz', '홍릉', 45000, 28000, '상',
            '[추천등급: 상] 표지 모서리에 약간의 사용감이 있으나 본문은 깨끗합니다.',
            '운영체제 강의 수강할 때 사용한 번역서입니다.',
            '3.jpg', 'SOLD'
        ),
        (
            1, '알고리즘 문제 해결 전략 세트', '구종만', '인사이트', 50000, 25000, '중',
            '[추천등급: 중] 전반적인 사용감이 있으나 본문 내용 확인에는 문제가 없습니다.',
            '알고리즘 학회 활동 때 본 책입니다.',
            '4.jpg', 'DELETED'
        ),
        (
            4, '쉽게 배우는 데이터베이스 개론 3판', '김연희', '한빛아카데미', 29000, 10000, '하',
            '[추천등급: 하] 필기감과 사용 흔적이 많아 저렴한 학습용으로 적합한 상태입니다.',
            '필기감 많지만 내용 파악에는 문제없습니다. 저렴하게 가져가세요.',
            '5.jpg', 'AVAILABLE'
        )
    ]

    if gemini_client:
        print("Gemini AI로 시드 도서 상태 평가를 생성하는 중입니다...", flush=True)
    else:
        print("GEMINI_API_KEY가 설정되지 않아 기본 AI 평가 문구를 사용합니다.", flush=True)

    books = []
    for item in raw_books:
        (
            seller_id, title, author, publisher,
            original_price, selling_price, condition_grade,
            fallback_ai, description, raw_img, status
        ) = item

        optimized_img = optimize_seed_image(raw_img)
        ai_result = evaluate_seed_book(title, description, optimized_img, fallback_ai)
        print(f" - [{title}] -> {ai_result}", flush=True)

        books.append((
            seller_id, title, author, publisher,
            original_price, selling_price, condition_grade,
            ai_result, description, optimized_img, status
        ))

    cursor.executemany(
        """INSERT INTO books 
           (seller_id, title, author, publisher, original_price, selling_price, condition_grade, ai_evaluated_result, description, image_filename, status) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        books
    )

    comments = [
        (1, 2, None, '안녕하세요! 내일 우당관 앞에서 직거래 가능할까요?', 0),
        (1, 3, None, '구매 희망합니다. 오늘 오후에 학교 도서관 앞에서 뵐 수 있을까요?', 0),
        (4, 2, None, '책 상태가 너무 좋네요! 바로 구매하고 싶습니다.', 1),
        (4, 1, 3, '네 좋습니다! 내일 오후 2시 학생회관 1층에서 거래 진행해요.', 0)
    ]
    cursor.executemany(
        "INSERT INTO comments (book_id, user_id, parent_id, content, is_approved) VALUES (?, ?, ?, ?, ?)",
        comments
    )

    trades = [
        (4, 2, 1, 3, 'RESERVED', None, None),
        (5, 3, 1, None, 'COMPLETED', None, None),
        (6, 4, 1, None, 'COMPLETED', None, None)
    ]
    cursor.executemany(
        "INSERT INTO trades (book_id, buyer_id, seller_id, comment_id, status, cancel_reason, cancelled_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        trades
    )

    reviews = [
        (2, 3, 1, 5.0, '약속 시간도 정확히 지켜주시고 책 상태가 설명보다 훨씬 깨끗했습니다!'),
        (3, 4, 1, 2.0, '답장이 조금 늦어서 일정을 다시 잡아야 했습니다.')
    ]
    cursor.executemany(
        "INSERT INTO reviews (trade_id, reviewer_id, reviewee_id, rating, comment) VALUES (?, ?, ?, ?, ?)",
        reviews
    )

    conn.commit()
    conn.close()
    print("시드 데이터가 입력되었습니다.", flush=True)


if __name__ == '__main__':
    insert_seed_data()