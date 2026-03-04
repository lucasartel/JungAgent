import sqlite3
import json

db_path = "c:/Users/conta/OneDrive/jungproject/data/jung_hybrid.db"

def check_db():
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # In identity_config.py: ADMIN_USER_ID = "lucasartel" ou outro ID de admin_users table?
        # Let's get distinct user_id from conversations just to be sure
        cursor.execute("SELECT DISTINCT user_id FROM conversations;")
        users = cursor.fetchall()
        print("Distinct users in conversations:", [u[0] for u in users])
        
        # Check conversations without extractions
        cursor.execute("""
            SELECT c.user_id, count(c.id)
            FROM conversations c
            LEFT JOIN agent_identity_extractions e ON c.id = e.conversation_id
            WHERE e.conversation_id IS NULL
            GROUP BY c.user_id
        """)
        pending = cursor.fetchall()
        print("\nPending extractions by user:")
        for row in pending:
            print(f"- User: {row[0]}, Pending: {row[1]}")
            
        conn.close()
    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    check_db()
