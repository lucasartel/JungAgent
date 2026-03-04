import sqlite3
import json

db_path = "c:/Users/conta/OneDrive/jungproject/data/jung_hybrid.db"

def check_db():
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        tables = [
            "agent_identity_core",
            "agent_identity_contradictions",
            "agent_narrative_chapters",
            "agent_possible_selves",
            "agent_relational_identity",
            "agent_self_knowledge_meta",
            "agent_agency_memory"
        ]
        
        results = {}
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                results[table] = count
            except sqlite3.OperationalError as e:
                results[table] = f"Error: {e}"
                
        print(json.dumps(results, indent=2))
        conn.close()
    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    check_db()
