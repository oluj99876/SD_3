import os
import sqlite3

DB_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(DB_DIR, 'database.db')
SCHEMA_PATH = os.path.join(DB_DIR, 'schema.sql')

def reset_database():
    # 1. 데이터베이스 초기화 여부 확인
    confirm = input("기존 데이터베이스를 초기화하시겠습니까? (y/N): ").strip().lower()
    if confirm != 'y':
        print("초기화 작업이 취소되었습니다.")
        return

    # 기존 DB 삭제 및 스키마 재생성
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("기존 데이터베이스를 삭제했습니다.")

    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
    print("스키마 정보가 재설정되었습니다.")

    # 2. 시드 데이터 입력 여부 확인
    seed_confirm = input("시드 데이터를 입력하시겠습니까? (y/N): ").strip().lower()
    if seed_confirm == 'y':
        from seed_data import insert_seed_data
        insert_seed_data()
    else:
        print("시드 데이터를 입력하지 않았습니다.")

if __name__ == '__main__':
    reset_database()

    